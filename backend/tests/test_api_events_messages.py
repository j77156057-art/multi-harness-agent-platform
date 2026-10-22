"""MVP-08 read surface: the incremental event cursor and group chat.

The event cursor is the read path an Adapter and the frontend both use, so the
assertions here are about cursor semantics, not just about rows coming back.
"""

from __future__ import annotations

from tests.api_support import bootstrap, make_client, publish_plan, scheduler


def _prepare(client) -> None:
    bootstrap(client)
    publish_plan(
        client,
        plan_id="PL-1",
        tasks=[{"id": "T-1"}, {"id": "T-2", "depends_on": ["T-1"]}],
    )
    client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "h"})


def test_event_cursor_is_exclusive_and_reports_more(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)

    first = client.get("/api/v1/events", params={"limit": 1})
    assert first.status_code == 200
    page = first.json()
    assert len(page["events"]) == 1
    assert page["has_more"] is True
    seq0 = page["events"][0]["seq"]
    assert page["next_seq"] == seq0

    second = client.get("/api/v1/events", params={"since_seq": seq0, "limit": 100})
    later = second.json()
    assert later["events"], "since_seq is an exclusive lower bound"
    assert all(event["seq"] > seq0 for event in later["events"])
    assert later["has_more"] is False

    # The two pages do not overlap and together cover the log.
    ids = {event["seq"] for event in page["events"]} | {e["seq"] for e in later["events"]}
    assert len(ids) == 1 + len(later["events"])


def test_events_are_ordered_and_carry_the_approval(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)
    page = client.get("/api/v1/events").json()
    seqs = [event["seq"] for event in page["events"]]
    assert seqs == sorted(seqs)
    names = [event["event_name"] for event in page["events"]]
    assert "plan.approve" in names
    assert "plan.approve_dependency_pending" in names, "T-2 depends on T-1"


def test_task_events_are_filtered_to_that_task(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)
    page = client.get("/api/v1/tasks/T-1/events").json()
    assert page["events"]
    assert {event["aggregate_id"] for event in page["events"]} == {"T-1"}


def test_tasks_can_be_listed_and_filtered_by_status(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)
    listed = client.get("/api/v1/groups/G-1/tasks").json()["tasks"]
    assert sorted(task["task_id"] for task in listed) == ["T-1", "T-2"]

    ready = client.get("/api/v1/groups/G-1/tasks", params={"status": "ready"}).json()["tasks"]
    assert [task["task_id"] for task in ready] == ["T-1"]

    waiting = client.get(
        "/api/v1/groups/G-1/tasks", params={"status": "ready_waiting_deps"}
    ).json()["tasks"]
    assert [task["task_id"] for task in waiting] == ["T-2"]


def test_messages_share_the_global_seq_space_with_events(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)

    # Approving PL-1 wrote two events (approve + dependency_pending), so the
    # global counter stands where the event log ends.
    event_page = client.get("/api/v1/events").json()
    last_event_seq = max(event["seq"] for event in event_page["events"])

    first = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "human-1",
            "body": "starting T-1",
            "mentions": ["harness-1"],
            "task_id": "T-1",
            "refs": ["T-1"],
        },
    )
    assert first.status_code == 200, first.text
    assert first.json()["seq"] == last_event_seq + 1

    second = client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "harness", "author_id": "harness-1", "body": "ack"},
    )
    assert second.json()["seq"] == last_event_seq + 2

    page = client.get("/api/v1/groups/G-1/messages").json()
    assert [message["created_seq"] for message in page["messages"]] == [
        last_event_seq + 1,
        last_event_seq + 2,
    ]
    assert page["messages"][0]["task_id"] == "T-1"
    assert page["next_seq"] == last_event_seq + 2
    assert page["has_more"] is False

    incremental = client.get(
        "/api/v1/groups/G-1/messages", params={"since_seq": last_event_seq + 1}
    ).json()
    assert [message["created_seq"] for message in incremental["messages"]] == [last_event_seq + 2]

    deliveries = client.get("/api/v1/groups/G-1/messages").json()
    assert deliveries["messages"][0]["message_id"] == first.json()["message_id"]


def test_interleaved_events_and_messages_keep_one_global_order(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)

    def latest_event_seq() -> int:
        rows = client.get("/api/v1/events").json()["events"]
        return max(event["seq"] for event in rows)

    message_a = client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "human", "author_id": "h", "body": "one"},
    ).json()["seq"]

    # A claim appends a real task event between the two messages.
    client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-seq", "harness_id": "h1", "task_ids": ["T-1"]},
    )
    in_between_event = latest_event_seq()

    message_b = client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "harness", "author_id": "h1", "body": "two"},
    ).json()["seq"]

    assert message_a < in_between_event < message_b
    assert message_b == message_a + 2, "the event consumed the number in between"

    # A second group does NOT restart numbering: messages are global.
    client.post("/api/v1/projects", json={"project_id": "P-2", "name": "p2"})
    client.post("/api/v1/projects/P-2/groups", json={"group_id": "G-2", "name": "g2"})
    other_group = client.post(
        "/api/v1/groups/G-2/messages",
        json={"author_kind": "human", "author_id": "h", "body": "elsewhere"},
    ).json()["seq"]
    assert other_group > message_b

    event_seqs = {event["seq"] for event in client.get("/api/v1/events").json()["events"]}
    message_seqs = set()
    for group in ("G-1", "G-2"):
        rows = client.get(f"/api/v1/groups/{group}/messages").json()["messages"]
        message_seqs.update(int(message["created_seq"]) for message in rows)
    assert event_seqs & message_seqs == set(), "the two streams never reuse a number"
    # Both streams are subsets of one contiguous global interval.
    assert min(event_seqs | message_seqs) == 1
    assert max(event_seqs | message_seqs) == other_group
    assert sorted(event_seqs | message_seqs) == list(range(1, other_group + 1))


def test_message_refs_are_persisted_and_read_back(tmp_path):
    """Legacy 3b: refs must survive POST -> GET, not just echo off the request."""
    client, _ = make_client(tmp_path)
    _prepare(client)

    posted = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "human-1",
            "body": "see these",
            "task_id": "T-1",
            "refs": ["T-1", "decision:7", "artifact://doc/42"],
        },
    )
    assert posted.status_code == 200, posted.text
    # The POST response keeps echoing refs (as before), but now storage agrees.
    assert posted.json()["refs"] == ["T-1", "decision:7", "artifact://doc/42"]

    messages = client.get("/api/v1/groups/G-1/messages").json()["messages"]
    assert messages[0]["refs"] == ["T-1", "decision:7", "artifact://doc/42"]

    # A message without refs carries an empty array, never a missing/null field.
    client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "harness", "author_id": "h1", "body": "no refs here"},
    )
    messages = client.get("/api/v1/groups/G-1/messages").json()["messages"]
    assert messages[1]["refs"] == []


def test_message_refs_must_be_an_array_of_nonblank_strings(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)

    wrong_shape = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "h",
            "body": "x",
            "refs": [1, 2],
        },
    )
    assert wrong_shape.status_code == 400, "pydantic edge validation maps to 400"
    assert wrong_shape.json()["error"]["code"] == "request.invalid"

    blank_entry = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "h",
            "body": "x",
            "refs": ["  "],
        },
    )
    assert blank_entry.status_code == 422, "blank refs are rejected by the service"
    assert blank_entry.json()["error"]["code"] == "request.invalid"


def test_message_mention_creates_a_delivery_row(tmp_path):
    client, database = make_client(tmp_path)
    _prepare(client)
    client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "human-1",
            "body": "please take a look",
            "mentions": ["harness-7"],
        },
    )
    rows = database.read("SELECT * FROM message_deliveries")
    assert len(rows) == 1
    assert rows[0]["recipient_id"] == "harness-7"
    assert rows[0]["status"] == "pending"


def test_message_on_an_unknown_group_is_not_found(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post(
        "/api/v1/groups/NOPE/messages",
        json={"author_kind": "human", "author_id": "h", "body": "hi"},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource.not_found"


def test_message_body_must_not_be_blank(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)
    response = client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "human", "author_id": "h", "body": "   "},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request.invalid"


def test_direct_messages_are_only_visible_to_the_two_ends(tmp_path):
    """定向消息（R10 §19）：只有发送方与收件方看得到。

    普通群消息对整群可见；定向的那条不能被第三方看到——否则它不叫私聊。
    """
    client, database = make_client(tmp_path)
    _prepare(client)
    service = scheduler(database)

    posted = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "human-1",
            "body": "只给管理员看的一句",
            "mentions": ["harness-1"],
            "direct": True,
        },
    )
    assert posted.status_code == 200, posted.text
    assert "direct" in posted.json()["refs"], "定向标记要写在 refs 里"

    def bodies(viewer):
        return [m["body"] for m in service.list_messages("G-1", viewer=viewer)]

    assert bodies("human-1") == ["只给管理员看的一句"], "发送方看得到"
    assert bodies("harness-1") == ["只给管理员看的一句"], "收件方看得到"
    assert bodies("harness-2") == [], "第三方看不到"
    assert bodies(None) == [], "认证关闭时看不到（失败关闭）"

    # 群消息不受影响：三方照看得到。
    client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "human", "author_id": "human-1", "body": "群里的话"},
    )
    assert bodies("harness-2") == ["群里的话"]

    # 路由层：没有令牌的读者拿不到定向消息。
    page = client.get("/api/v1/groups/G-1/messages").json()
    assert [m["body"] for m in page["messages"]] == ["群里的话"]


def test_a_direct_message_without_a_recipient_is_refused(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)
    response = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "human",
            "author_id": "human-1",
            "body": "没有收件人的定向消息",
            "direct": True,
        },
    )
    assert response.status_code == 422, response.text
