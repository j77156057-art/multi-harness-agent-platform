"""MVP-04 lease lifecycle and recovery: renew, expire, reclaim, take_over.

Covers CORE-LEASE-001 (create / renew / expire / reclaim) and CORE-FENCE-001
(stale fencing epochs are refused on every guarded write).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.claim.guarded_write import AttemptMismatch, StaleFencingEpoch, guarded_write
from app.claim.service import OUTCOME_CLAIMED, ClaimRequest, ClaimService
from app.lease.service import (
    LeaseExpired,
    LeaseTokenMismatch,
    expire_stale_leases,
    renew_lease,
)
from app.scheduler import sweep_stuck
from app.tasks.service import SchedulerService, close_attempt
from tests.claim_support import (
    count,
    event_names,
    make_database,
    seed_project,
    seed_task,
    task_row,
)


@pytest.fixture
def claimed(tmp_path):
    """A database holding one successfully claimed task."""
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database)
    result = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )
    assert result.outcome == OUTCOME_CLAIMED
    return database, result.items[0]


def sweep_to_stale(database, *, at: datetime) -> list[str]:
    """把「到期 → 先问人 → 宽限期过 → 判失联」两段跑完，返回转过的任务。

    R10-fix-14 之后，**一次**清扫不再产生 stale：第一段只把任务停到
    `awaiting_human` 等人确认，第二段（过了 `lease_uncertain_grace_minutes`）
    才按失联兜底。要构造 stale 的测试都得走完这两段，不能只扫一次。
    """
    from app.config import settings

    first = expire_stale_leases(database, now=at)
    later = at + timedelta(minutes=settings.lease_uncertain_grace_minutes + 1)
    second = expire_stale_leases(database, now=later)
    return first + second


def test_renew_extends_the_lease(claimed):
    database, item = claimed
    later = datetime.now(UTC) + timedelta(seconds=60)
    with database.transaction() as conn:
        new_until = renew_lease(
            conn,
            task_id="T-1",
            attempt_id=item.attempt_id,
            lease_token=item.lease_token,
            fencing_epoch=item.fencing_epoch,
            now=later,
        )
    assert datetime.fromisoformat(new_until) > datetime.fromisoformat(item.lease_until)


def test_renew_with_a_stale_fencing_epoch_is_refused(claimed):
    database, item = claimed
    with database.transaction() as conn, pytest.raises(StaleFencingEpoch):
        renew_lease(
            conn,
            task_id="T-1",
            attempt_id=item.attempt_id,
            lease_token=item.lease_token,
            fencing_epoch=item.fencing_epoch - 1,
        )


def test_renew_with_the_wrong_attempt_is_refused(claimed):
    database, item = claimed
    with database.transaction() as conn, pytest.raises(AttemptMismatch):
        renew_lease(
            conn,
            task_id="T-1",
            attempt_id="T-1.a99",
            lease_token=item.lease_token,
            fencing_epoch=item.fencing_epoch,
        )


def test_renew_with_a_bad_token_is_refused(claimed):
    database, item = claimed
    with database.transaction() as conn, pytest.raises(LeaseTokenMismatch):
        renew_lease(
            conn,
            task_id="T-1",
            attempt_id=item.attempt_id,
            lease_token="not-the-token",
            fencing_epoch=item.fencing_epoch,
        )


def test_renew_after_the_deadline_is_refused(claimed):
    database, item = claimed
    way_later = datetime.now(UTC) + timedelta(days=1)
    with database.transaction() as conn, pytest.raises(LeaseExpired):
        renew_lease(
            conn,
            task_id="T-1",
            attempt_id=item.attempt_id,
            lease_token=item.lease_token,
            fencing_epoch=item.fencing_epoch,
            now=way_later,
        )


def test_guarded_write_refuses_a_stale_epoch_outside_the_lease_path(claimed):
    database, item = claimed
    with database.transaction() as conn, pytest.raises(StaleFencingEpoch):
        guarded_write(conn, task_id="T-1", fencing_epoch=item.fencing_epoch - 1)
    with database.transaction() as conn:
        guard = guarded_write(conn, task_id="T-1", fencing_epoch=item.fencing_epoch)
    assert guard.owner == "harness-1"


def test_expiry_asks_a_person_before_declaring_the_task_lost(claimed):
    """到期先问人：任务停在 awaiting_human，租约释放，但**不判失联**。

    R10-fix-14 把原来「到期即 stale」改成两段。理由写在清扫器的 docstring 里：
    判错的代价（把还在干的活判死、人以为它没了）比多等一会儿大得多。
    """
    database, item = claimed
    later = datetime.now(UTC) + timedelta(hours=1)

    transitioned = expire_stale_leases(database, now=later)

    assert transitioned == ["T-1"]
    row = task_row(database, "T-1")
    assert row["status"] == "awaiting_human", "到期不再直接判失联"
    # 仍然记着是谁、哪一次尝试 —— 人要在界面上看到"是谁那次断的"。
    assert row["owner"] == "harness-1"
    assert row["current_attempt_id"] == item.attempt_id
    assert row["current_epoch"] == item.fencing_epoch, "expiry must not bump the epoch"
    assert row["waiting_since"] is not None

    # 租约一定释放：那个进程不能再续期（这是机械事实，不是判断）。
    assert count(database, "SELECT COUNT(*) FROM leases WHERE released_at IS NULL") == 0
    lease_row = database.read("SELECT released_at FROM leases WHERE task_id='T-1'")[0]
    assert lease_row["released_at"] is not None, "leases has no release_reason column"
    assert event_names(database, aggregate_id="T-1") == ["claim", "lease_uncertain"]

    # 而且要在「需要你处理」里留一条 —— 停线不提问等于把人蒙在鼓里。
    decisions = database.read(
        "SELECT question, options_json, answer, scope FROM task_decisions WHERE task_id='T-1'"
    )
    assert len(decisions) == 1
    assert decisions[0]["scope"] == "lease_uncertain"
    assert "换人" in decisions[0]["question"]
    assert decisions[0]["answer"] is None

    claim_row = database.read("SELECT * FROM task_claims WHERE task_id='T-1'")[0]
    assert claim_row["released_at"] is not None
    assert claim_row["release_reason"] == "lease_expire"


def test_the_grace_window_falls_back_to_stale_when_nobody_answers(claimed):
    """人一直不答 → 宽限期过后才按失联兜底，队列不会被人不在卡死。"""
    from app.config import settings

    database, item = claimed
    later = datetime.now(UTC) + timedelta(hours=1)
    expire_stale_leases(database, now=later)
    assert task_row(database, "T-1")["status"] == "awaiting_human"

    # 宽限期未到：什么都不做（否则"先问人"就又变回"到期即判"）。
    within = later + timedelta(minutes=max(0, settings.lease_uncertain_grace_minutes - 1))
    assert expire_stale_leases(database, now=within) == []
    assert task_row(database, "T-1")["status"] == "awaiting_human"

    after = later + timedelta(minutes=settings.lease_uncertain_grace_minutes + 1)
    assert expire_stale_leases(database, now=after) == ["T-1"]
    row = task_row(database, "T-1")
    assert row["status"] == "stale"
    assert row["owner"] is None
    assert row["current_attempt_id"] is None
    assert row["current_epoch"] == item.fencing_epoch
    assert event_names(database, aggregate_id="T-1") == [
        "claim",
        "lease_uncertain",
        "lease_expire",
    ]
    # 兜底这件事本身也要在决策记录里写清，别让人以为是自己答的。
    decided = database.read(
        "SELECT answer, decided_seq FROM task_decisions WHERE task_id='T-1'"
    )[0]
    assert decided["answer"] and "超时" in decided["answer"]
    assert decided["decided_seq"] is not None


def test_expiry_releases_resources_even_when_the_status_is_past_the_owner_states(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, exclusive_resources=["port:8188"])
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )
    assert count(database, "SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL") == 1

    later = datetime.now(UTC) + timedelta(hours=1)
    transitioned = expire_stale_leases(database, now=later)

    assert transitioned == ["T-1"]
    assert count(database, "SELECT COUNT(*) FROM resource_leases WHERE released_at IS NULL") == 0


def test_a_second_sweep_is_a_no_op(claimed):
    database, _ = claimed
    later = datetime.now(UTC) + timedelta(hours=1)
    assert expire_stale_leases(database, now=later) == ["T-1"]
    # 第二遍：租约已经释放、任务已经停在等人确认，不该再产生第二个事件。
    assert expire_stale_leases(database, now=later) == []
    assert count(database, "SELECT COUNT(*) FROM task_events WHERE event_name='lease_uncertain'") == 1
    assert count(database, "SELECT COUNT(*) FROM task_decisions WHERE task_id='T-1'") == 1


def test_budget_exhaustion_escalates_from_stale(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, max_attempts=1)
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )

    later = datetime.now(UTC) + timedelta(hours=1)
    # 两段走完才算失联（先问人、宽限期过），预算耗尽在变成 stale 的那一轮就升级。
    sweep_to_stale(database, at=later)

    row = task_row(database, "T-1")
    assert row["status"] == "escalated", "1/1 attempts used, so stale escalates immediately"
    assert event_names(database, aggregate_id="T-1") == [
        "claim",
        "lease_uncertain",
        "lease_expire",
        "attempt_budget_exhausted",
    ]


@pytest.mark.parametrize("status", ["ready", "queued"])
def test_budget_exhaustion_escalates_from_waiting_states(tmp_path, status):
    """A task that used its last attempt and is back in the pool cannot linger.

    R10 §10's attempt_budget_exhausted edge covers ready/queued, not just
    stale/reclaiming: without it the operator never sees the release control
    and the only exit is the generic aging timer.
    """
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, status=status, attempt_no=3, max_attempts=3)

    expire_stale_leases(database, now=datetime.now(UTC))

    row = task_row(database, "T-1")
    assert row["status"] == "escalated"
    assert event_names(database, aggregate_id="T-1") == ["attempt_budget_exhausted"]


def test_budget_override_keeps_a_ready_task_out_of_escalation(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, attempt_no=3, max_attempts=3, budget_override_by="human-1")

    expire_stale_leases(database, now=datetime.now(UTC))

    assert task_row(database, "T-1")["status"] == "ready"
    assert event_names(database, aggregate_id="T-1") == []


def test_stale_task_can_be_reclaimed_and_taken_over(claimed):
    database, item = claimed
    service = ClaimService(database)
    later = datetime.now(UTC) + timedelta(hours=1)
    sweep_to_stale(database, at=later)

    assert service.reclaim("T-1", grace_elapsed=True).value == "reclaiming"

    taken = service.take_over(
        "T-1", harness_id="harness-2", fencing_epoch=task_row(database, "T-1")["current_epoch"]
    )
    assert taken.owner == "harness-2"
    assert taken.attempt_id == "T-1.a2"
    assert taken.fencing_epoch == item.fencing_epoch + 1, "takeover creates a new epoch"
    assert taken.base_commit == item.base_commit, "the frozen base must be reused"

    row = task_row(database, "T-1")
    assert row["status"] == "claimed"
    assert row["owner"] == "harness-2"
    assert row["attempt_no"] == 2
    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 2
    assert event_names(database, aggregate_id="T-1") == [
        "claim",
        "lease_uncertain",
        "lease_expire",
        "reclaim",
        "take_over",
    ]


# ------------------------------------------------------- owner phase closes


def _attempt_status(database, attempt_id: str) -> dict[str, object]:
    return database.read(
        "SELECT * FROM task_attempts WHERE attempt_id = ?", (attempt_id,)
    )[0]


def _lease_row(database, task_id: str = "T-1") -> dict[str, object]:
    return database.read(
        "SELECT * FROM leases WHERE task_id = ? ORDER BY rowid DESC LIMIT 1",
        (task_id,),
    )[0]


def _start_attempt(database, item) -> None:
    """Claim 只把 attempt 写进 ``claimed``；worker 起来后才进 ``in_progress``。

    07 现场的四条 attempt 都是 in_progress（worker 已经跑过），所以这里的
    收口测试也从那个状态开始。
    """
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'in_progress', updated_at = ? WHERE task_id = 'T-1'",
            (datetime.now(UTC).isoformat(),),
        )
        conn.execute(
            "UPDATE task_attempts SET status = 'in_progress' WHERE attempt_id = ?",
            (item.attempt_id,),
        )


def test_abandon_closes_attempt(claimed):
    """07 现场：abandon 之后 attempt 仍 in_progress，任务 already abandoned。"""
    database, item = claimed
    _start_attempt(database, item)
    before = task_row(database, "T-1")["updated_at"]
    service = SchedulerService(database)

    service.abandon(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        reason="out_of_scope: README.md",
    )

    assert task_row(database, "T-1")["status"] == "abandoned"
    assert _attempt_status(database, item.attempt_id)["status"] == "abandoned"
    assert task_row(database, "T-1")["updated_at"] != before


def test_escalate_closes_attempt_and_releases_lease(claimed):
    """07 现场：escalated 的 FD-c3fb 仍占 lease、attempt 仍 in_progress。"""
    database, item = claimed
    _start_attempt(database, item)
    now = datetime.now(UTC)

    sweep_stuck(database, now)
    sweep_stuck(database, now + timedelta(minutes=1))
    sweep_stuck(database, now + timedelta(minutes=2))
    sweep_stuck(database, now + timedelta(minutes=3))

    row = task_row(database, "T-1")
    assert row["status"] == "escalated"
    assert row["owner"] is None, "升级后没有人会再续租，owner 必须清空"
    assert row["current_attempt_id"] is None
    assert _attempt_status(database, item.attempt_id)["status"] == "escalated"
    assert _lease_row(database)["released_at"] is not None


def test_expire_after_escalate_closes_attempt(claimed):
    """Escalated 的任务在 lease 到期后仍要被 expire 收口，而不是直接 continue。"""
    database, item = claimed
    _start_attempt(database, item)
    # 模拟一次没有收口的升级（旧实现留下的现场）。
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'escalated' WHERE task_id = 'T-1'")

    due = datetime.fromisoformat(item.lease_until) + timedelta(seconds=1)
    expire_stale_leases(database, now=due)

    assert _attempt_status(database, item.attempt_id)["status"] == "failed"
    assert _lease_row(database)["released_at"] is not None


def test_timeout_keeps_attempt_open(claimed):
    """timeout 故意保留 attempt/worktree：租约没到期前不能被清扫器收口。"""
    database, item = claimed
    _start_attempt(database, item)
    early = datetime.now(UTC)
    assert datetime.fromisoformat(item.lease_until) > early

    expire_stale_leases(database, now=early)

    assert _attempt_status(database, item.attempt_id)["status"] == "in_progress"
    assert _lease_row(database)["released_at"] is None


def test_close_attempt_is_idempotent(claimed):
    database, item = claimed
    _start_attempt(database, item)
    moment = datetime.now(UTC)
    with database.transaction() as conn:
        assert close_attempt(
            conn,
            attempt_id=item.attempt_id,
            status="abandoned",
            reason="probe_reason",
            now=moment,
        )
        assert not close_attempt(
            conn,
            attempt_id=item.attempt_id,
            status="abandoned",
            reason="second_reason",
        )
    row = database.read(
        "SELECT status, closed_at, close_reason FROM task_attempts"
        " WHERE attempt_id = ?",
        (item.attempt_id,),
    )[0]
    assert row["status"] == "abandoned"
    assert row["closed_at"] == moment.isoformat(), "关闭时间必须落在 attempt 行上"
    assert row["close_reason"] == "probe_reason", "第二次调用不得改写第一次原因"


def test_a_fresh_attempt_has_no_close_facts(claimed):
    """旧行/新行的 closed_at 与 close_reason 都是 NULL —— 不回填猜测值。"""
    database, item = claimed
    row = _attempt_status(database, item.attempt_id)
    assert row["closed_at"] is None
    assert row["close_reason"] is None
    assert database.initialize() == 0, "025 已经应用过，重复 initialize 必须是 0"


def test_release_closes_a_claimed_attempt_that_never_got_provisioned(claimed):
    """2026-11-06 ``seq 2562``：领取成功、provision 失败、release 成功，attempt 停在 claimed。

    provision 失败时 ``start_task`` 从未执行，attempt 始终不是 ``in_progress``；
    而 release 是 owner 阶段的结束，无论 attempt 走到哪一步都必须写
    ``closed_at`` / ``close_reason`` —— 否则「这次 attempt 何时关、为什么关」
    只能再翻三份日志。
    """
    database, item = claimed
    reason = "GitError：git checkout -b fd-283b9880abb0ab34-a1 FETCH_HEAD failed"
    assert _attempt_status(database, item.attempt_id)["status"] == "claimed"

    SchedulerService(database).release(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        reason=reason,
    )

    assert task_row(database, "T-1")["status"] == "ready"
    row = _attempt_status(database, item.attempt_id)
    assert row["status"] == "released"
    assert row["closed_at"] is not None, "release 必须把领取未跑的 attempt 收口"
    assert row["close_reason"] == reason, "关闭原因写人给的那句话，不是事件名"


def test_close_attempt_leaves_a_verifying_attempt_alone(claimed):
    """提交后的 ``verifying`` 归 ``submit_result`` 收口，sweeper 不得替它关。

    收口名单只含 ``claimed`` / ``in_progress``：把 ``verifying`` 一起收掉会让
    在途提交被判成失败，且覆盖掉它自己的 close_reason。
    """
    database, item = claimed
    with database.transaction() as conn:
        conn.execute(
            "UPDATE task_attempts SET status = 'verifying' WHERE attempt_id = ?",
            (item.attempt_id,),
        )
        assert not close_attempt(
            conn,
            attempt_id=item.attempt_id,
            status="failed",
            reason="lease_expire",
        )
    row = _attempt_status(database, item.attempt_id)
    assert row["status"] == "verifying"
    assert row["closed_at"] is None
    assert row["close_reason"] is None


def test_every_attempt_answers_when_and_why_it_closed(claimed):
    """运维要能直接从表里回答：某 task 的每次 attempt 何时关、为什么关。"""
    database, item = claimed
    _start_attempt(database, item)
    later = datetime.now(UTC) + timedelta(minutes=5)
    with database.transaction() as conn:
        close_attempt(
            conn,
            attempt_id=item.attempt_id,
            status="failed",
            reason="lease_expire",
            now=later,
        )
    rows = database.read(
        "SELECT attempt_no, status, closed_at, close_reason FROM task_attempts"
        " WHERE task_id = 'T-1' ORDER BY attempt_no"
    )
    assert rows and rows[-1]["closed_at"] is not None
    assert rows[-1]["close_reason"] == "lease_expire"


def test_the_owner_closing_invariants_hold(tmp_path):
    """07 现场的两条不变量 SQL：任务终态 + attempt 不能还 in_progress，lease 不能挂着。"""
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database)
    item = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    ).items[0]
    _start_attempt(database, item)
    service = SchedulerService(database)
    with database.transaction() as conn:
        item = conn.execute(
            "SELECT attempt_id FROM task_attempts WHERE task_id = 'T-1'"
        ).fetchone()
        epoch = conn.execute(
            "SELECT current_epoch FROM tasks WHERE task_id = 'T-1'"
        ).fetchone()
    service.abandon(
        task_id="T-1",
        attempt_id=str(item["attempt_id"]),
        fencing_epoch=int(epoch["current_epoch"]),
        reason="probe",
    )

    open_attempts = database.read(
        "SELECT t.task_id, t.status, a.status FROM tasks t"
        "  JOIN task_attempts a ON a.task_id = t.task_id"
        " WHERE t.status IN ('escalated','abandoned','delivered','merged','ready')"
        "   AND a.status = 'in_progress'"
    )
    open_leases = database.read(
        "SELECT t.task_id, l.lease_until, l.released_at FROM tasks t"
        "  JOIN leases l ON l.task_id = t.task_id"
        " WHERE t.status = 'escalated' AND l.released_at IS NULL"
    )
    assert open_attempts == []
    assert open_leases == []


def test_take_over_with_a_stale_epoch_is_refused(claimed):
    database, item = claimed
    service = ClaimService(database)
    later = datetime.now(UTC) + timedelta(hours=1)
    sweep_to_stale(database, at=later)
    service.reclaim("T-1", grace_elapsed=True)

    with pytest.raises(Exception) as caught:
        service.take_over("T-1", harness_id="harness-2", fencing_epoch=item.fencing_epoch - 1)
    assert "epoch" in str(caught.value)
    assert task_row(database, "T-1")["status"] == "reclaiming"


def test_reclaim_requires_the_grace_window(claimed):
    database, _ = claimed
    service = ClaimService(database)
    with pytest.raises(ValueError):
        service.reclaim("T-1", grace_elapsed=False)
