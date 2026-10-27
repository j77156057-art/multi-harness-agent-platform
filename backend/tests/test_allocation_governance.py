"""R10 §39 分配治理的**闭环**：方案改版 → 传阅换绑 → 开轮 → 申请 → 裁决 → 接受 → 领取。

这组测试钉的是三件以前会锁死、而现在必须走通的事：

1. **方案改版后，在途的传阅轮跟着换绑到新快照。** 不换绑的话，分配轮开的
   永远是旧快照，而任务已经指向新快照 —— 硬规则 2 的"计划版本"一格于是永远
   对不上，**传阅与派单会一起锁死**。
2. **成员 worker 会申请、会接受。** §39 硬规则 2 把三步分给三个主体：成员申请、
   管理员裁决、被选中者自己接受。少任何一步，谁都领不动那条活。
3. **裁决看得见"冻结后的候选"。** 轮次一关闭，申请会从 ``submitted`` 变成
   ``frozen``；只认 ``submitted`` 的裁决逻辑会让已经申请的人凭空消失。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.admin.worker import AdminWorker, WorkerState
from app.allocation.service import AllocationService
from app.claim.service import ClaimRequest, ClaimService
from app.config import settings
from app.tasks.service import SchedulerService
from tests.claim_support import make_database, seed_project

GROUP = "G-1"
ADMIN = "admin-window"
MEMBER = "member-window"
OTHER = "other-window"


# --------------------------------------------------------------------- 夹具


@pytest.fixture
def database(tmp_path):
    db = make_database(tmp_path)
    seed_project(db)
    return db


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _register_harness(database, harness_id: str, *, provider: str = "tencent") -> None:
    """A registered, credentialed window (the platform refuses unregistered ones).

    两张表都要有：``harnesses`` 是登记名，``harness_registrations`` 是这一次
    登记的**版本**。Claim 的资格检查读的是后者 —— 只往 ``harnesses`` 塞一行
    会得到 ``registration_missing``，那是在测"没登记"，不是在这条测它。
    """
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO harnesses(harness_id, provider, adapter, status, registration_version,"
            " created_at) VALUES (?, ?, ?, 'online', 'v1', ?)",
            (harness_id, provider, provider, _now()),
        )
        conn.execute(
            "INSERT INTO harness_registrations(registration_id, harness_id, registration_version,"
            " provider, adapter, adapter_version, capabilities_json, status, created_at)"
            " VALUES (?, ?, 'v1', ?, ?, '1.0.0', '[\"coding\"]', 'online', ?)",
            (f"reg-{harness_id}", harness_id, provider, provider, _now()),
        )
        conn.execute(
            "INSERT INTO auth_tokens(token_hash, subject_type, subject_id, created_at)"
            " VALUES (?, 'harness', ?, ?)",
            (f"hash-{harness_id}", harness_id, _now()),
        )


def _make_plan(
    database,
    *,
    plan_id: str = "plan-1",
    snapshot_id: str = "snap-1",
    tasks: list[dict] | None = None,
    status: str = "approved",
) -> str:
    """A plan with a snapshot holding tasks, plus a materialised task row each.

    The task rows are what the allocation service checks ("申请的任务必须真实存在
    且属于本群"), so they cannot be skipped.
    """
    items = tasks or [
        {
            "id": "T-1",
            "title": "写作品页",
            "assigned_harness_ids": [MEMBER],
            "exclusive_paths": ["office/index.html"],
        }
    ]
    now = _now()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (plan_id, GROUP, status, f"hash-{plan_id}", now),
        )
        conn.execute(
            "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash, content_json,"
            " created_at) VALUES (?, ?, ?, ?, ?)",
            (
                snapshot_id,
                plan_id,
                f"hash-{plan_id}",
                json.dumps({"plan_version": "v1", "tasks": items}, ensure_ascii=False),
                now,
            ),
        )
        for item in items:
            conn.execute(
                "INSERT INTO tasks(task_id, group_id, plan_snapshot_id, status, attempt_no,"
                " max_attempts, created_at, updated_at)"
                " VALUES (?, ?, ?, 'ready', 0, 3, ?, ?)",
                (item["id"], GROUP, snapshot_id, now, now),
            )
    return snapshot_id


def _open_circulation(database, *, plan_id="plan-1", snapshot_id="snap-1", reviewers=(MEMBER,)):
    return AllocationService(database).open_circulation(
        group_id=GROUP,
        plan_id=plan_id,
        plan_snapshot_id=snapshot_id,
        reviewer_harness_ids=list(reviewers),
        opened_by=ADMIN,
    )


def _complete_circulation(database, *, plan_id="plan-1", snapshot_id="snap-1", reviewers=(MEMBER,)):
    """A circulation round that ran to the end: 意见 → 收轮 → 定稿.

    分配轮的硬门禁（§39 硬规则 1）要求它绑定的快照上有一轮**已完成**的传阅，
    所以每个要开分配轮的测试都得先真的走完一轮传阅。
    """
    service = AllocationService(database)
    opened = _open_circulation(
        database, plan_id=plan_id, snapshot_id=snapshot_id, reviewers=reviewers
    )
    for reviewer in reviewers:
        service.submit_circulation_review(
            circulation_id=opened["circulation_id"], harness_id=reviewer, body="看着行"
        )
    service.close_circulation_round(circulation_id=opened["circulation_id"], closed_by=ADMIN)
    service.complete_circulation(circulation_id=opened["circulation_id"], completed_by=ADMIN)
    return opened


# ----------------------------------------------------------------- 换绑


def test_a_revised_plan_rebinds_the_in_flight_circulation_round(database):
    """方案改版 → 在途的传阅轮必须跟着换绑到新快照。

    这是硬规则 1 与硬规则 5 撞在一起时唯一自洽的解法：下一轮读草案的人读的
    正是**最新**那一版方案，而"已完成的那轮传阅"要能证明最终被批准的那一版
    被传阅过。不换绑 = 两台机器一起锁死。
    """
    _make_plan(database)
    opened = _open_circulation(database)
    service = AllocationService(database)

    rebound = service.rebind_open_circulation(plan_id="plan-1", snapshot_id="snap-2")

    assert rebound == [opened["circulation_id"]]
    row = database.read(
        "SELECT plan_snapshot_id FROM circulation_rounds WHERE circulation_id = ?",
        (opened["circulation_id"],),
    )[0]
    assert row["plan_snapshot_id"] == "snap-2"


def test_a_completed_circulation_round_is_never_rebound(database):
    """已经完成的轮是历史事实：它的快照不能被改写。"""
    _make_plan(database)
    opened = _open_circulation(database)
    service = AllocationService(database)
    service.submit_circulation_review(
        circulation_id=opened["circulation_id"], harness_id=MEMBER, body="看着行"
    )
    service.close_circulation_round(circulation_id=opened["circulation_id"], closed_by=ADMIN)
    service.complete_circulation(circulation_id=opened["circulation_id"], completed_by=ADMIN)

    rebound = service.rebind_open_circulation(plan_id="plan-1", snapshot_id="snap-2")

    assert rebound == []
    row = database.read(
        "SELECT plan_snapshot_id, state FROM circulation_rounds WHERE circulation_id = ?",
        (opened["circulation_id"],),
    )[0]
    assert row["plan_snapshot_id"] == "snap-1"
    assert row["state"] == "completed"


def test_the_allocation_round_may_be_opened_on_the_rebound_snapshot(database):
    """换绑之后，硬门禁才过得去：分配轮要绑的那个快照必须有已完成的传阅轮。"""
    _make_plan(database)
    opened = _open_circulation(database)
    service = AllocationService(database)
    service.rebind_open_circulation(plan_id="plan-1", snapshot_id="snap-2")
    service.submit_circulation_review(
        circulation_id=opened["circulation_id"], harness_id=MEMBER, body="看着行"
    )
    service.close_circulation_round(circulation_id=opened["circulation_id"], closed_by=ADMIN)
    service.complete_circulation(circulation_id=opened["circulation_id"], completed_by=ADMIN)

    # 硬门禁：没有已完成的传阅轮就开不了分配轮。
    with pytest.raises(Exception, match="no completed circulation round"):
        service.open_allocation_round(
            group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
        )

    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-2", opened_by=ADMIN
    )
    assert round_info["state"] == "opened"


# --------------------------------------------------------------- 读取面


def test_the_round_read_face_names_the_tasks_and_their_windows(database):
    """成员要靠这一面知道"这一轮有我的活吗"。"""
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    service.open_allocation_round(group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN)

    rounds = service.list_rounds(group_id=GROUP)

    assert len(rounds) == 1
    tasks = rounds[0]["tasks"]
    assert [item["task_id"] for item in tasks] == ["T-1"]
    assert tasks[0]["assigned_harness_ids"] == [MEMBER]
    assert tasks[0]["exclusive_paths"] == ["office/index.html"]


# ------------------------------------------------------- 成员：申请与接受


def test_a_member_applies_for_the_task_the_plan_named(database):
    from app.adapter.formal_worker import submit_allocation_applications

    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    service.open_allocation_round(group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN)

    class _Client:
        def __init__(self):
            self.posts = []

        def get(self, path, **params):
            assert path == "/api/v1/allocation/rounds"
            return type(
                "R",
                (),
                {
                    "json": lambda self: {
                        "rounds": service.list_rounds(group_id=GROUP),
                    },
                },
            )()

        def post(self, path, json=None, **kwargs):
            self.posts.append((path, json))
            return {"application_id": "app-1"}

    client = _Client()
    assert submit_allocation_applications(
        client, harness_id=MEMBER, group=GROUP
    ) is True
    assert len(client.posts) == 1
    path, body = client.posts[0]
    assert path == "/api/v1/allocation/application"
    assert body["harness_id"] == MEMBER
    assert body["task_ids"] == ["T-1"]
    assert body["round_id"]


def test_a_member_does_not_apply_for_a_task_named_for_somebody_else(database):
    from app.adapter.formal_worker import submit_allocation_applications

    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    service.open_allocation_round(group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN)

    class _Client:
        def __init__(self):
            self.posts = []

        def get(self, path, **params):
            return type(
                "R",
                (),
                {
                    "json": lambda self: {
                        "rounds": service.list_rounds(group_id=GROUP),
                    },
                },
            )()

        def post(self, path, json=None, **kwargs):
            self.posts.append((path, json))
            return {"application_id": "app-1"}

    client = _Client()
    assert submit_allocation_applications(client, harness_id=OTHER, group=GROUP) is False
    assert client.posts == []


def test_a_member_does_not_apply_twice_in_the_same_round(database):
    from app.adapter.formal_worker import submit_allocation_applications

    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    service.submit_application(
        round_id=round_info["round_id"],
        harness_id=MEMBER,
        task_ids=["T-1"],
        reason="我来",
    )

    class _Client:
        def __init__(self):
            self.posts = []

        def get(self, path, **params):
            return type(
                "R",
                (),
                {
                    "json": lambda self: {
                        "rounds": service.list_rounds(group_id=GROUP),
                    },
                },
            )()

        def post(self, path, json=None, **kwargs):
            self.posts.append((path, json))
            return {"application_id": "app-2"}

    client = _Client()
    assert submit_allocation_applications(client, harness_id=MEMBER, group=GROUP) is False
    assert client.posts == []


def test_a_member_accepts_the_assignment_made_to_it(database):
    from app.adapter.formal_worker import accept_allocation

    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    service.submit_application(
        round_id=round_info["round_id"], harness_id=MEMBER, task_ids=["T-1"], reason="我来"
    )
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    service.decide(
        round_id=round_info["round_id"],
        task_id="T-1",
        application_id=database.read(
            "SELECT application_id FROM applications WHERE round_id = ?", (round_info["round_id"],)
        )[0]["application_id"],
        decided_by=ADMIN,
        expected_revision=int(
            database.read(
                "SELECT revision FROM allocation_rounds WHERE round_id = ?",
                (round_info["round_id"],),
            )[0]["revision"]
        ),
        reason="按方案指派",
    )

    class _Client:
        def __init__(self):
            self.posts = []

        def get(self, path, **params):
            return type(
                "R",
                (),
                {
                    "json": lambda self: {
                        "rounds": service.list_rounds(group_id=GROUP),
                    },
                },
            )()

        def post(self, path, json=None, **kwargs):
            self.posts.append((path, json))
            return {"allocation_state": "accepted"}

    client = _Client()
    assert accept_allocation(client, harness_id=MEMBER, group=GROUP) is True
    assert len(client.posts) == 1
    path, body = client.posts[0]
    assert path == "/api/v1/allocation/accept"
    assert body["harness_id"] == MEMBER
    assert body["allocation_id"]


# ------------------------------------------------------------- 领取授权


def test_the_accepted_allocation_authorises_the_claim_and_is_consumed(database, monkeypatch):
    """完整链条的最后一环：accepted 的分配 = Claim 的授权，且用掉即失效。"""
    _register_harness(database, MEMBER)
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    application = service.submit_application(
        round_id=round_info["round_id"], harness_id=MEMBER, task_ids=["T-1"], reason="我来"
    )
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    service.decide(
        round_id=round_info["round_id"],
        task_id="T-1",
        application_id=application["application_id"],
        decided_by=ADMIN,
        expected_revision=int(
            database.read(
                "SELECT revision FROM allocation_rounds WHERE round_id = ?",
                (round_info["round_id"],),
            )[0]["revision"]
        ),
        reason="按方案指派",
    )
    allocation = database.read(
        "SELECT allocation_id FROM allocations WHERE round_id = ?", (round_info["round_id"],)
    )[0]
    service.accept_assignment(allocation_id=allocation["allocation_id"], harness_id=MEMBER)
    monkeypatch.setattr(settings, "allocation_required_groups", frozenset({GROUP}))

    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-1",
            harness_id=MEMBER,
            task_ids=("T-1",),
            partial_allowed=False,
        )
    )

    assert result.outcome == "claimed"
    assert result.items[0].attempt_id == "T-1.a1"
    used = database.read(
        "SELECT claimed_at, claim_id FROM allocations WHERE allocation_id = ?",
        (allocation["allocation_id"],),
    )[0]
    assert used["claimed_at"] is not None


def test_an_attempt_that_produced_nothing_hands_the_authorization_back(database, monkeypatch):
    """什么都没干就把活还回来，授权必须还能再用一次。

    授权是在**开出 attempt** 的那一刻被标记用掉的。那次 attempt 如果立刻因为基础
    设施原因死掉（没有空闲工作树、provider 挂了），活回到 ready，授权却已经"用过"
    —— 于是门禁拒绝，而管理员也开不出第二轮（一个快照一轮就够），这条活就永远停
    在那里。门禁要防的是"换一个窗口自选"，不是"同一份已经答应的分配在什么都没干
    完之后作废"。
    """
    _register_harness(database, MEMBER)
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    application = service.submit_application(
        round_id=round_info["round_id"], harness_id=MEMBER, task_ids=["T-1"], reason="我来"
    )
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    service.decide(
        round_id=round_info["round_id"],
        task_id="T-1",
        application_id=application["application_id"],
        decided_by=ADMIN,
        expected_revision=int(
            database.read(
                "SELECT revision FROM allocation_rounds WHERE round_id = ?",
                (round_info["round_id"],),
            )[0]["revision"]
        ),
        reason="按方案指派",
    )
    allocation = database.read(
        "SELECT allocation_id FROM allocations WHERE round_id = ?", (round_info["round_id"],)
    )[0]
    service.accept_assignment(allocation_id=allocation["allocation_id"], harness_id=MEMBER)
    monkeypatch.setattr(settings, "allocation_required_groups", frozenset({GROUP}))

    claim = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-1",
            harness_id=MEMBER,
            task_ids=("T-1",),
            partial_allowed=False,
        )
    )
    assert claim.outcome == "claimed"
    attempt_id = claim.items[0].attempt_id

    # 基础设施原因死掉：什么都没产出，活还回来了。
    SchedulerService(database).release(
        task_id="T-1",
        attempt_id=attempt_id,
        fencing_epoch=1,
        reason="retryable failure (WorktreeCapacityExceeded)",
    )
    returned = service.return_authorization(
        attempt_id=attempt_id, reason="retryable failure (WorktreeCapacityExceeded)"
    )
    assert returned is not None
    assert returned["task_id"] == "T-1"

    # 授权回来了：同一条活可以被再领一次（第二次领取用的是新的 claim id）。
    again = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-2",
            harness_id=MEMBER,
            task_ids=("T-1",),
            partial_allowed=False,
        )
    )
    assert again.outcome == "claimed"
    assert again.items[0].attempt_id == "T-1.a2"


def test_an_invited_allocation_still_does_not_authorise_the_claim(database, monkeypatch):
    """裁决只到 `invited` 为止：本人没接受，授权就不成立。"""
    _register_harness(database, MEMBER)
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    application = service.submit_application(
        round_id=round_info["round_id"], harness_id=MEMBER, task_ids=["T-1"], reason="我来"
    )
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    service.decide(
        round_id=round_info["round_id"],
        task_id="T-1",
        application_id=application["application_id"],
        decided_by=ADMIN,
        expected_revision=int(
            database.read(
                "SELECT revision FROM allocation_rounds WHERE round_id = ?",
                (round_info["round_id"],),
            )[0]["revision"]
        ),
        reason="按方案指派",
    )
    monkeypatch.setattr(settings, "allocation_required_groups", frozenset({GROUP}))

    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-1",
            harness_id=MEMBER,
            task_ids=("T-1",),
            partial_allowed=False,
        )
    )

    assert result.outcome == "rejected"
    assert result.items[0].error_code == "claim.not_authorized"


# ------------------------------------------------- 管理员：裁决看得见冻结候选


class _AdminClient:
    """A fake HTTP client for the administrator's allocation steps."""

    def __init__(self, service: AllocationService):
        self.service = service
        self.posts: list[tuple[str, dict]] = []

    def get(self, path, **params):
        if path == "/allocation/rounds":
            return {"rounds": self.service.list_rounds(group_id=GROUP)}
        if path == "/allocation/candidates":
            return {
                "candidates": self.service.list_candidates(
                    round_id=params["round_id"], task_id=params["task_id"]
                )
            }
        raise AssertionError(path)

    def post(self, path, body):
        self.posts.append((path, body))
        if path == "/allocation/round/open":
            return self.service.open_allocation_round(
                group_id=body["group_id"],
                plan_snapshot_id=body["plan_snapshot_id"],
                opened_by=body["opened_by"],
            )
        if path == "/allocation/round/close":
            return self.service.close_allocation_round(
                round_id=body["round_id"], closed_by=body["harness_id"]
            )
        if path == "/allocation/decision":
            return self.service.decide(
                round_id=body["round_id"],
                task_id=body["task_id"],
                application_id=body["application_id"],
                decided_by=body["decided_by"],
                expected_revision=body["expected_revision"],
                reason=body.get("reason", ""),
                compared_harness_ids=body.get("compared_harness_ids"),
            )
        if path == "/allocation/invite":
            return self.service.invite(
                round_id=body["round_id"],
                task_id=body["task_id"],
                harness_id=body["harness_id"],
                invited_by=body["invited_by"],
                reason=body.get("reason", ""),
            )
        if path.endswith("/messages"):
            return {"message_id": "m-1"}
        raise AssertionError(path)


def _admin_worker(tmp_path, client, database=None) -> AdminWorker:
    return AdminWorker(
        client=client,
        database=database,
        group_id=GROUP,
        admin_harness_id=ADMIN,
        state=WorkerState(),
        state_path=tmp_path / "admin-worker.json",
    )


def test_the_administrator_opens_a_round_for_the_approved_plan_without_one(tmp_path, database):
    """人批准之后，管理员自己认得出"哪个已批准的方案还没开分配轮"，并开轮。

    这条路径要读平台自己的库（方案表 + 快照表），所以它拿的是真数据库，
    不是假客户端 —— 少了这一步，"人批了方案却没人开分配轮"就永远静悄悄地卡住。
    """
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    client = _AdminClient(service)
    worker = _admin_worker(tmp_path, client, database=database)

    worker.open_allocation_and_decide()

    rounds = service.list_rounds(group_id=GROUP)
    assert len(rounds) == 1
    assert rounds[0]["plan_snapshot_id"] == "snap-1"
    # 开轮之后，worker 要记住"这一轮要分配哪些活"—— 否则它不知道谁该申请。
    pending = worker.state.pending_allocation
    assert pending is not None
    assert [task["id"] for task in pending["tasks"]] == ["T-1"]


def test_the_administrator_does_not_open_a_second_round_for_the_same_plan(tmp_path, database):
    """一个方案只开一轮：已经开过的快照不会再被认成"还没有轮"。"""
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    client = _AdminClient(service)
    worker = _admin_worker(tmp_path, client, database=database)

    worker.open_allocation_and_decide()
    worker.state.pending_allocation = None
    worker.open_allocation_and_decide()

    assert len(service.list_rounds(group_id=GROUP)) == 1


def test_the_administrator_decides_for_a_window_that_applied(tmp_path, database):
    """已申请 → 裁决 → 被选中者仍要自己接受。"""
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    service.submit_application(
        round_id=round_info["round_id"], harness_id=MEMBER, task_ids=["T-1"], reason="我来"
    )
    # 裁决发生在**关轮之后**：候选集合先冻结，管理员再看冻结后的名单。
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    client = _AdminClient(service)
    worker = _admin_worker(tmp_path, client)
    worker.state.pending_allocation = {
        "plan_id": "plan-1",
        "plan_snapshot_id": "snap-1",
        "round_id": round_info["round_id"],
        "opened_at": 0.0,
        "decided": [],
        "tasks": [{"id": "T-1", "assigned_harness_ids": [MEMBER]}],
    }

    worker.open_allocation_and_decide()

    decisions = [body for path, body in client.posts if path == "/allocation/decision"]
    assert len(decisions) == 1
    assert decisions[0]["task_id"] == "T-1"
    assert decisions[0]["decided_by"] == ADMIN
    allocations = database.read("SELECT * FROM allocations WHERE round_id = ?", (round_info["round_id"],))
    assert len(allocations) == 1
    assert allocations[0]["harness_id"] == MEMBER
    assert allocations[0]["state"] == "invited", "裁决只到 invited：接受是本人的事"


def test_the_administrator_invites_the_named_window_when_nobody_applied(tmp_path, database):
    """没人申请 → 改派邀请给方案点名的窗口。"""
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    client = _AdminClient(service)
    worker = _admin_worker(tmp_path, client)
    worker.state.pending_allocation = {
        "plan_id": "plan-1",
        "plan_snapshot_id": "snap-1",
        "round_id": round_info["round_id"],
        "opened_at": 0.0,
        "decided": [],
        "tasks": [{"id": "T-1", "assigned_harness_ids": [MEMBER]}],
    }

    worker.open_allocation_and_decide()

    invites = [body for path, body in client.posts if path == "/allocation/invite"]
    assert len(invites) == 1
    assert invites[0]["harness_id"] == MEMBER


def test_the_administrator_sees_a_frozen_application_as_a_candidate(tmp_path, database):
    """轮次一关闭，申请会从 submitted 变成 frozen —— 冻结的申请**仍然算候选**。

    只认 submitted 的裁决逻辑会让"已经申请的人"在关轮之后凭空消失：分配轮
    关掉了、候选没了、任务永远没人接。这是本组测试钉住的最隐蔽的一条。
    """
    _make_plan(database)
    _complete_circulation(database)
    service = AllocationService(database)
    round_info = service.open_allocation_round(
        group_id=GROUP, plan_snapshot_id="snap-1", opened_by=ADMIN
    )
    service.submit_application(
        round_id=round_info["round_id"], harness_id=MEMBER, task_ids=["T-1"], reason="我来"
    )
    service.close_allocation_round(round_id=round_info["round_id"], closed_by=ADMIN)
    frozen = database.read(
        "SELECT state FROM applications WHERE round_id = ?", (round_info["round_id"],)
    )[0]["state"]
    assert frozen == "frozen"

    client = _AdminClient(service)
    worker = _admin_worker(tmp_path, client)
    worker.state.pending_allocation = {
        "plan_id": "plan-1",
        "plan_snapshot_id": "snap-1",
        "round_id": round_info["round_id"],
        "opened_at": 0.0,
        "decided": [],
        "tasks": [{"id": "T-1", "assigned_harness_ids": [MEMBER]}],
    }

    worker.open_allocation_and_decide()

    decisions = [body for path, body in client.posts if path == "/allocation/decision"]
    assert len(decisions) == 1, "冻结的申请仍然是候选"
