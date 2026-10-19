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


def test_expiry_moves_an_owner_state_to_stale_and_frees_everything(claimed):
    database, item = claimed
    later = datetime.now(UTC) + timedelta(hours=1)

    transitioned = expire_stale_leases(database, now=later)

    assert transitioned == ["T-1"]
    row = task_row(database, "T-1")
    assert row["status"] == "stale"
    assert row["owner"] is None
    assert row["current_attempt_id"] is None
    assert row["current_epoch"] == item.fencing_epoch, "expiry must not bump the epoch"
    assert row["waiting_since"] is not None

    assert count(database, "SELECT COUNT(*) FROM leases WHERE released_at IS NULL") == 0
    lease_row = database.read("SELECT released_at FROM leases WHERE task_id='T-1'")[0]
    assert lease_row["released_at"] is not None, "leases has no release_reason column"
    assert event_names(database, aggregate_id="T-1") == ["claim", "lease_expire"]

    claim_row = database.read("SELECT * FROM task_claims WHERE task_id='T-1'")[0]
    assert claim_row["released_at"] is not None
    assert claim_row["release_reason"] == "lease_expire"


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
    assert expire_stale_leases(database, now=later) == []
    assert count(database, "SELECT COUNT(*) FROM task_events WHERE event_name='lease_expire'") == 1


def test_budget_exhaustion_escalates_from_stale(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, max_attempts=1)
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )

    later = datetime.now(UTC) + timedelta(hours=1)
    expire_stale_leases(database, now=later)

    row = task_row(database, "T-1")
    assert row["status"] == "escalated", "1/1 attempts used, so stale escalates immediately"
    assert event_names(database, aggregate_id="T-1") == [
        "claim",
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
    expire_stale_leases(database, now=later)

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
        "lease_expire",
        "reclaim",
        "take_over",
    ]


def test_take_over_with_a_stale_epoch_is_refused(claimed):
    database, item = claimed
    service = ClaimService(database)
    later = datetime.now(UTC) + timedelta(hours=1)
    expire_stale_leases(database, now=later)
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
