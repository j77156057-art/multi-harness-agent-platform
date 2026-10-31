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
