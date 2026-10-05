"""MVP-08 read surface: the incremental event cursor and group chat.

The event cursor is the read path an Adapter and the frontend both use, so the
assertions here are about cursor semantics, not just about rows coming back.
"""

from __future__ import annotations

from tests.api_support import bootstrap, make_client, publish_plan


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


def test_messages_carry_their_own_cursor_and_mentions(tmp_path):
    client, _ = make_client(tmp_path)
    _prepare(client)

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
    assert first.json()["seq"] == 1

    second = client.post(
        "/api/v1/groups/G-1/messages",
        json={"author_kind": "harness", "author_id": "harness-1", "body": "ack"},
    )
    assert second.json()["seq"] == 2

    page = client.get("/api/v1/groups/G-1/messages").json()
    assert [message["created_seq"] for message in page["messages"]] == [1, 2]
    assert page["messages"][0]["task_id"] == "T-1"
    assert page["next_seq"] == 2
    assert page["has_more"] is False

    incremental = client.get("/api/v1/groups/G-1/messages", params={"since_seq": 1}).json()
    assert [message["created_seq"] for message in incremental["messages"]] == [2]

    deliveries = client.get("/api/v1/groups/G-1/messages").json()
    assert deliveries["messages"][0]["message_id"] == first.json()["message_id"]


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
