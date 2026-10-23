"""R10 §39 硬规则 2 的强制口径：按群开关的 Claim 分配门禁（R10-fix-13）。

群主 2026-10-17 选定"分群开关"而不是全局强制：列在
``AP_ALLOCATION_REQUIRED_GROUPS`` 里的群，**第一份** attempt 必须由一份
"已 accepted 且尚未用掉"的分配作授权；其余群维持既有行为。

为什么要按群开关（写在这里，免得后人以为是在偷懒）：§39 硬规则 2 的字面口径是
"只有已 accepted 的分配才构成 §11 Claim 的授权"。全局强制会让存量任务一次性全部
不可领取，连带把已经跑通的合并链和 ``test_real_adapters`` 全打红 —— 那不是把规则
做实，那是换掉领取机制。取舍记录在
``docs/prep/ALLOCATION-CIRCULATION-01/R10-FIX-13-DRAFT.md`` §10。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.claim.service import (
    OUTCOME_CLAIMED,
    OUTCOME_QUEUED,
    OUTCOME_REJECTED,
    ClaimRequest,
    ClaimService,
    RejectCode,
)
from app.config import settings
from tests.claim_support import (
    DEFAULT_GROUP,
    count,
    event_names,
    make_database,
    seed_project,
    seed_task,
    task_row,
)

GATED_GROUP = DEFAULT_GROUP


@pytest.fixture
def database(tmp_path):
    db = make_database(tmp_path)
    seed_project(db)
    return db


def claim_one(database, *, task_id="T-1", request_id="cr-1", harness="harness-1"):
    return ClaimService(database).claim(
        ClaimRequest(
            claim_request_id=request_id,
            harness_id=harness,
            task_ids=(task_id,),
            partial_allowed=False,
        )
    )


def gate_on(monkeypatch, *groups: str) -> None:
    monkeypatch.setattr(settings, "allocation_required_groups", frozenset(groups))


def attach_snapshot(database, task_id: str, *, group_id: str = GATED_GROUP) -> str:
    """给任务挂一份方案快照并返回其 id。

    §39 的分配轮必须绑定快照（`plan_snapshot_id` 是 NOT NULL），而硬规则 2 要求 Claim
    校验"计划版本"：分配轮绑定的快照必须就是任务当前的快照。所以门禁的测试任务也得
    真的有一份快照，否则测的是"没有快照时恰好通过"这种假绿。
    """
    now = datetime.now(UTC).isoformat()
    snapshot_id = f"snap-{task_id}"
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
            " VALUES (?, ?, 'approved', ?, ?)",
            (f"plan-{task_id}", group_id, f"hash-{task_id}", now),
        )
        conn.execute(
            "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash,"
            " content_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                snapshot_id,
                f"plan-{task_id}",
                f"hash-{task_id}",
                f'{{"plan_version": "v1", "tasks": [{{"id": "{task_id}"}}]}}',
                now,
            ),
        )
        conn.execute(
            "UPDATE tasks SET plan_snapshot_id = ? WHERE task_id = ?", (snapshot_id, task_id)
        )
    return snapshot_id


def grant_allocation(
    database,
    *,
    task_id: str = "T-1",
    harness_id: str = "harness-1",
    group_id: str = GATED_GROUP,
    state: str = "accepted",
    claimed_at: str | None = None,
    claim_id: str | None = None,
    plan_snapshot_id: str | None = None,
) -> str:
    """直接落一份分配行。

    刻意不走 ``AllocationService`` 的完整流程：这一组测的是**门禁本身**（它只读
    ``allocations`` 与所属轮的 ``plan_snapshot_id``），把传阅→申请→裁决整套搭起来只会
    让失败原因变得难查。完整流程与门禁的对接另有 ``test_allocation_service.py``
    与 ``test_allocation_routes.py``。

    默认用任务当前的快照（没有就顺手补一份），需要测"计划版本对不上"时显式传别的值。
    """
    if plan_snapshot_id is None:
        existing = task_row(database, task_id)["plan_snapshot_id"]
        plan_snapshot_id = (
            str(existing) if existing else attach_snapshot(database, task_id, group_id=group_id)
        )
    now = datetime.now(UTC).isoformat()
    round_id = f"ar-{uuid.uuid4().hex}"
    allocation_id = f"al-{uuid.uuid4().hex}"
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO allocation_rounds(round_id, group_id, plan_snapshot_id,"
            " opened_by, opened_at, state, revision) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (round_id, group_id, plan_snapshot_id, "office-admin", now, "closed"),
        )
        conn.execute(
            "INSERT INTO allocations(allocation_id, round_id, task_id, harness_id,"
            " state, accepted_at, claimed_at, claim_id, revision)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
            (
                allocation_id,
                round_id,
                task_id,
                harness_id,
                state,
                now if state == "accepted" else None,
                claimed_at,
                claim_id,
            ),
        )
    return allocation_id


def allocation_row(database, allocation_id: str) -> dict[str, object]:
    rows = database.read("SELECT * FROM allocations WHERE allocation_id = ?", (allocation_id,))
    assert rows
    return dict(rows[0])


# --------------------------------------------------------------------- off


def test_a_group_outside_the_switch_is_completely_unaffected(database):
    """默认空集合 = 一个群都不强制。存量行为必须逐字不变。"""
    seed_task(database)
    assert settings.allocation_required_groups == frozenset()

    result = claim_one(database)

    assert result.outcome == OUTCOME_CLAIMED
    assert count(database, "SELECT COUNT(*) FROM allocations") == 0


def test_only_the_listed_group_is_gated(database, monkeypatch):
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO groups(group_id, project_id, name, created_at)"
            " VALUES ('G-other', 'P-1', 'other', ?)",
            (datetime.now(UTC).isoformat(),),
        )
    seed_task(database, task_id="T-1", group_id="G-other")
    grant_allocation(database, task_id="T-1", group_id="G-other")
    seed_task(database, task_id="T-2", group_id=GATED_GROUP)
    gate_on(monkeypatch, GATED_GROUP)

    other = claim_one(database, task_id="T-1", request_id="cr-other")
    gated = claim_one(database, task_id="T-2", request_id="cr-gated")

    assert other.outcome == OUTCOME_CLAIMED, "G-other 不在名单里，不该受影响"
    assert gated.outcome == OUTCOME_REJECTED
    assert gated.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


# --------------------------------------------------------------------- on


def test_a_gated_group_refuses_a_claim_without_any_allocation(database, monkeypatch):
    seed_task(database)
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database)

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


def test_the_refusal_leaves_no_attempt_no_lease_and_no_claim_event(database, monkeypatch):
    """拒绝必须什么都不写：§11 的 Claim 要么成立，要么不留痕。

    全部回滚是全有全无口径的既有行为（见 ``test_all_or_nothing_rolls_back_the_whole_request``），
    这里连同"门禁拒绝"一起把它钉住：被门禁拒绝的请求不得改动任何任务状态。
    """
    seed_task(database)
    gate_on(monkeypatch, GATED_GROUP)

    claim_one(database)

    row = task_row(database, "T-1")
    assert row["status"] == "ready", "被拒绝的 Claim 不得改动 tasks.status"
    assert row["current_attempt_id"] is None
    assert row["attempt_no"] == 0
    assert row["owner"] is None
    assert count(database, "SELECT COUNT(*) FROM task_attempts") == 0
    assert count(database, "SELECT COUNT(*) FROM leases") == 0
    assert event_names(database, aggregate_id="T-1") == []
    assert count(database, "SELECT COUNT(*) FROM claim_requests") == 0
    assert count(database, "SELECT COUNT(*) FROM task_claims") == 0


def test_a_partial_claim_leaves_the_rejected_item_row_with_our_code(database, monkeypatch):
    """宽松口径下失败项仍留一行 task_claims，原因就是门禁的拒绝码（§11）。"""
    seed_task(database)
    gate_on(monkeypatch, GATED_GROUP)

    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-partial",
            harness_id="harness-1",
            task_ids=("T-1",),
            partial_allowed=True,
        )
    )

    assert result.outcome == OUTCOME_REJECTED
    rows = database.read("SELECT * FROM task_claims WHERE task_id = 'T-1'")
    assert len(rows) == 1
    assert rows[0]["attempt_id"] is None, "未领取用 NULL 表示，不用空串哨兵"
    assert rows[0]["owner"] is None
    assert rows[0]["release_reason"] == f"rejected:{RejectCode.NOT_AUTHORIZED.value}"
    assert count(database, "SELECT COUNT(*) FROM task_attempts") == 0


def test_an_accepted_allocation_authorizes_the_claim_and_is_consumed(database, monkeypatch):
    seed_task(database)
    allocation_id = grant_allocation(database)
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database)

    assert result.outcome == OUTCOME_CLAIMED
    assert result.items[0].attempt_id == "T-1.a1"

    allocation = allocation_row(database, allocation_id)
    claim_rows = database.read("SELECT * FROM task_claims WHERE task_id = 'T-1'")
    assert len(claim_rows) == 1
    assert allocation["claimed_at"] is not None, "授权被这次 Claim 用掉"
    assert allocation["claim_id"] == claim_rows[0]["claim_id"], (
        "同一个 claim_id 必须同时落在 allocations 与 task_claims 上，"
        "否则分不出'撤销一份没人用的授权'和'撤掉一个正在跑的 Lease'"
    )


def test_an_invited_allocation_is_not_an_authorization(database, monkeypatch):
    """§39 只认 accepted：邀请（invited）还没被本人接受，不构成授权。"""
    seed_task(database)
    grant_allocation(database, state="invited")
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database)

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


@pytest.mark.parametrize("state", ["invited", "declined", "revoked"])
def test_only_the_accepted_state_authorizes(database, monkeypatch, state):
    seed_task(database)
    grant_allocation(database, state=state)
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database)

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


def test_a_plan_version_mismatch_invalidates_the_allocation(database, monkeypatch):
    """硬规则 2 的"计划版本"一格：方案换版后旧授权不得再用（硬规则 5）。

    分配轮绑定的是**旧**快照，任务已经被新方案重绑 → 那份 accepted 分配不再构成授权。
    """
    seed_task(database)
    stale_snapshot = attach_snapshot(database, "T-1")
    grant_allocation(database, plan_snapshot_id=stale_snapshot)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
            " VALUES ('plan-T1-v2', 'G-1', 'approved', 'hash-v2', ?)",
            (datetime.now(UTC).isoformat(),),
        )
        conn.execute(
            "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash, content_json,"
            " created_at) VALUES ('snap-T-1-v2', 'plan-T1-v2', 'hash-v2',"
            ' \'{"plan_version": "v2", "tasks": [{"id": "T-1"}]}\', ?)',
            (datetime.now(UTC).isoformat(),),
        )
        conn.execute("UPDATE tasks SET plan_snapshot_id = 'snap-T-1-v2' WHERE task_id = 'T-1'")
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database)

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


def test_an_allocation_for_another_harness_does_not_count(database, monkeypatch):
    seed_task(database)
    grant_allocation(database, harness_id="harness-2")
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database, harness="harness-1")

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


def test_an_allocation_for_another_task_does_not_count(database, monkeypatch):
    seed_task(database, task_id="T-1")
    seed_task(database, task_id="T-2")
    grant_allocation(database, task_id="T-2")
    gate_on(monkeypatch, GATED_GROUP)

    result = claim_one(database, task_id="T-1")

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.NOT_AUTHORIZED.value


def test_a_consumed_authorization_cannot_authorize_a_second_attempt(database, monkeypatch):
    """用过一次的授权不再算授权 —— 重开后的再领取必须由管理员重新分配。

    这正是门禁的意义（不许自选）。同时保住一件事：这里必须是**拒绝**，
    不能因为 `claim_id` 不同而在事务里抛错（那就成了 500）。
    """
    seed_task(database)
    grant_allocation(database)
    gate_on(monkeypatch, GATED_GROUP)
    assert claim_one(database, request_id="cr-1").outcome == OUTCOME_CLAIMED

    # 放弃这次尝试，任务回到 ready（等价于 reopen 后的可领取状态）
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'ready', current_attempt_id = NULL,"
            " owner = NULL WHERE task_id = 'T-1'"
        )

    again = claim_one(database, request_id="cr-2")

    assert again.outcome == OUTCOME_REJECTED
    assert again.items[0].error_code == RejectCode.NOT_AUTHORIZED.value
    assert count(database, "SELECT COUNT(*) FROM task_attempts") == 1, "没有开出第二份 attempt"


def test_a_rework_claim_is_not_gated(database, monkeypatch):
    """返工继续的是同一份已被授权的作业。

    若连返工也要重新分配，一次 verify 失败就会把任务钉在 changes_requested 上等
    管理员分配 —— 那正是本仓已经踩过的"死端"。所以门禁只拦**第一份** attempt。
    """
    seed_task(database)
    grant_allocation(database)
    gate_on(monkeypatch, GATED_GROUP)
    assert claim_one(database, request_id="cr-1").outcome == OUTCOME_CLAIMED

    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'changes_requested' WHERE task_id = 'T-1'")

    rework = claim_one(database, request_id="cr-2")

    assert rework.outcome == OUTCOME_CLAIMED
    assert rework.items[0].attempt_id == "T-1.a2"


def test_a_deferred_claim_does_not_consume_the_authorization(database, monkeypatch):
    """排队等资源不是开工：授权要留到真正开出 attempt 时才算用掉。"""
    seed_task(database, exclusive_resources=["gpu-1"])
    allocation_id = grant_allocation(database)
    gate_on(monkeypatch, GATED_GROUP)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO resource_leases(resource_lease_id, resource_id, attempt_id,"
            " lease_until, epoch, released_at, release_reason)"
            " VALUES ('rl-holder', 'gpu-1', 'T-other.a1', '2999-01-01T00:00:00+00:00', 1,"
            " NULL, NULL)"
        )

    queued = claim_one(database, request_id="cr-1")

    assert queued.outcome == OUTCOME_QUEUED
    assert allocation_row(database, allocation_id)["claimed_at"] is None
    assert count(database, "SELECT COUNT(*) FROM task_attempts") == 0
