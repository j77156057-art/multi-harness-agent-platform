"""MVP-04 claim semantics: two-layer idempotency, deferred resources, budget.

Covers CORE-CLAIM-001 (duplicate claim_request_id returns the first result,
per task item) and the queue/budget edges of R10 §10 / §11.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.claim.service import (
    OUTCOME_CLAIMED,
    OUTCOME_QUEUED,
    OUTCOME_REJECTED,
    ClaimRequest,
    ClaimService,
    RejectCode,
    canonical_payload_hash,
)
from tests.claim_support import (
    count,
    event_names,
    hold_resource,
    make_database,
    seed_project,
    seed_task,
    task_row,
)


@pytest.fixture
def database(tmp_path):
    db = make_database(tmp_path)
    seed_project(db)
    return db


def claim_one(database, *, task_id="T-1", request_id="cr-1", harness="harness-1"):
    service = ClaimService(database)
    return service.claim(
        ClaimRequest(
            claim_request_id=request_id,
            harness_id=harness,
            task_ids=(task_id,),
            partial_allowed=False,
        )
    )


def test_claim_grants_owner_attempt_and_lease(database):
    seed_task(database)
    result = claim_one(database)

    assert result.outcome == OUTCOME_CLAIMED
    item = result.items[0]
    assert item.owner == "harness-1"
    assert item.attempt_id == "T-1.a1"
    assert item.fencing_epoch == 1
    assert item.lease_token
    assert item.base_commit == "base-commit-0001"

    row = task_row(database, "T-1")
    assert row["status"] == "claimed"
    assert row["owner"] == "harness-1"
    assert row["current_attempt_id"] == "T-1.a1"
    assert row["current_epoch"] == 1
    assert row["attempt_no"] == 1
    assert row["state_changed_seq"] == item.base_locked_seq

    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 1
    assert (
        count(database, "SELECT COUNT(*) FROM leases WHERE task_id='T-1' AND released_at IS NULL")
        == 1
    )
    assert count(database, "SELECT COUNT(*) FROM task_claims WHERE task_id='T-1'") == 1
    assert event_names(database, aggregate_id="T-1") == ["claim"]


def test_duplicate_claim_request_returns_first_result_without_new_attempt(database):
    seed_task(database)
    first = claim_one(database)
    second = claim_one(database)

    assert second.replayed is True
    assert second.outcome == OUTCOME_CLAIMED
    assert second.created_seq == first.created_seq
    assert second.items[0].attempt_id == first.items[0].attempt_id
    assert second.items[0].fencing_epoch == first.items[0].fencing_epoch

    row = task_row(database, "T-1")
    assert row["attempt_no"] == 1, "replay must not burn an attempt"
    assert row["current_epoch"] == 1, "replay must not advance the epoch"
    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 1
    assert count(database, "SELECT COUNT(*) FROM task_events") == 1


def test_replay_reissues_a_live_lease_token(database):
    """The recovery path for a harness that lost its token (deferred to 10b).

    Only the hash is stored, so replay cannot return the original token -- it
    rotates: the new token is live, the old one stops working.
    """
    from app.lease.service import LeaseTokenMismatch, renew_lease

    seed_task(database)
    first = claim_one(database)
    second = claim_one(database)

    assert first.items[0].lease_token is not None
    recovered = second.items[0].lease_token
    assert recovered is not None, "a live lease must be recoverable by replay"
    assert recovered != first.items[0].lease_token

    with database.transaction() as conn:
        renewed = renew_lease(
            conn,
            task_id="T-1",
            attempt_id="T-1.a1",
            lease_token=recovered,
            fencing_epoch=1,
        )
    assert renewed, "the reissued token must authorise renewal"

    with pytest.raises(LeaseTokenMismatch), database.transaction() as conn:
        renew_lease(
            conn,
            task_id="T-1",
            attempt_id="T-1.a1",
            lease_token=first.items[0].lease_token,
            fencing_epoch=1,
        )


def test_replay_does_not_reissue_an_expired_lease_token(database):
    """An unswept-but-expired lease is not reissued: recovery goes via reclaim."""
    seed_task(database)
    first = claim_one(database)
    assert first.items[0].lease_token is not None

    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE leases SET lease_until = ? WHERE task_id = 'T-1'", (past,))

    second = claim_one(database)
    assert second.replayed is True
    assert second.items[0].lease_token is None


def test_same_request_id_with_different_payload_rejects_the_whole_request(database):
    seed_task(database, task_id="T-1")
    seed_task(database, task_id="T-2")
    claim_one(database)

    service = ClaimService(database)
    with pytest.raises(Exception) as caught:
        service.claim(
            ClaimRequest(
                claim_request_id="cr-1",  # same id
                harness_id="harness-1",
                task_ids=("T-2",),  # different task set
                partial_allowed=False,
            )
        )
    assert caught.value.code is RejectCode.IDEMPOTENCY_CONFLICT

    conflicts = database.read(
        "SELECT event_name FROM task_events WHERE event_name = 'idempotency_conflict'"
    )
    assert len(conflicts) == 1
    assert task_row(database, "T-2")["status"] == "ready"


def test_declared_payload_hash_is_recomputed_and_mismatch_rejected(database):
    seed_task(database)
    service = ClaimService(database)
    with pytest.raises(Exception) as caught:
        service.claim(
            ClaimRequest(
                claim_request_id="cr-x",
                harness_id="harness-1",
                task_ids=("T-1",),
                payload_hash="sha256:deadbeef",
            )
        )
    assert caught.value.code is RejectCode.IDEMPOTENCY_CONFLICT


def test_accepts_a_correctly_declared_payload_hash(database):
    seed_task(database)
    expected = canonical_payload_hash(
        harness_id="harness-1", task_ids=("T-1",), partial_allowed=False
    )
    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-ok",
            harness_id="harness-1",
            task_ids=("T-1",),
            payload_hash=expected,
        )
    )
    assert result.outcome == OUTCOME_CLAIMED


def test_every_item_leaves_a_task_claims_row_including_failures(database):
    seed_task(database, task_id="T-1")
    seed_task(database, task_id="T-2", budget_override_by="human-1")

    # T-2 is claimed by someone else first, so the batch item fails.
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-other", harness_id="h-2", task_ids=("T-2",))
    )

    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-batch",
            harness_id="harness-1",
            task_ids=("T-1", "T-2"),
            partial_allowed=True,
        )
    )

    assert [item.outcome for item in result.items] == [OUTCOME_CLAIMED, OUTCOME_REJECTED]
    assert result.items[1].error_code == RejectCode.ALREADY_OWNED.value
    assert (
        count(database, "SELECT COUNT(*) FROM task_claims WHERE claim_request_id='cr-batch'") == 2
    )

    failed = database.read(
        "SELECT * FROM task_claims WHERE claim_request_id='cr-batch' AND task_id='T-2'"
    )[0]
    assert failed["release_reason"] == f"rejected:{RejectCode.ALREADY_OWNED.value}"
    # R10-fix-11：attempt_id / owner 改为可空，失败项用 NULL 表示「未领取」。
    # 此前用空串哨兵，现由数据层直接区分「未领取」与「已领取」。
    assert failed["attempt_id"] is None, "no attempt must be recorded for a failed item"
    assert failed["owner"] is None, "no owner must be recorded for a failed item"


def test_replay_reads_per_item_results_from_task_claims(database):
    seed_task(database, task_id="T-1")
    seed_task(database, task_id="T-2", budget_override_by="human-1")
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-other", harness_id="h-2", task_ids=("T-2",))
    )
    request = ClaimRequest(
        claim_request_id="cr-batch",
        harness_id="harness-1",
        task_ids=("T-1", "T-2"),
        partial_allowed=True,
    )
    service = ClaimService(database)
    first = service.claim(request)
    replay = service.claim(request)

    assert replay.replayed is True
    assert [item.outcome for item in replay.items] == [OUTCOME_CLAIMED, OUTCOME_REJECTED]
    assert replay.items[0].attempt_id == first.items[0].attempt_id
    assert replay.items[1].error_code == RejectCode.ALREADY_OWNED.value

    # Prove the replay came from task_claims, not from the JSON column: wiping
    # the request-level blob must not change a replayed per-item result.
    with database.transaction() as conn:
        conn.execute("UPDATE claim_requests SET first_result_json = NULL")
    after_wipe = service.claim(request)
    assert [item.outcome for item in after_wipe.items] == [OUTCOME_CLAIMED, OUTCOME_REJECTED]


def test_all_or_nothing_rolls_back_the_whole_request(database):
    seed_task(database, task_id="T-1")
    seed_task(database, task_id="T-2")
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-other", harness_id="h-2", task_ids=("T-2",))
    )

    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id="cr-batch",
            harness_id="harness-1",
            task_ids=("T-1", "T-2"),
            partial_allowed=False,
        )
    )

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.BATCH_ABORTED.value
    assert task_row(database, "T-1")["status"] == "ready", "T-1 must not be half-claimed"
    assert (
        count(database, "SELECT COUNT(*) FROM claim_requests WHERE claim_request_id='cr-batch'")
        == 0
    )
    assert (
        count(database, "SELECT COUNT(*) FROM task_claims WHERE claim_request_id='cr-batch'") == 0
    )
    assert count(database, "SELECT COUNT(*) FROM task_events") == 1  # only cr-other's claim


def test_deferred_resource_queues_without_owner_attempt_or_lease(database):
    seed_task(database, exclusive_resources=["port:8188"])
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    hold_resource(database, "port:8188", attempt_id="T-9.a1", lease_until=future)

    result = claim_one(database)

    assert result.outcome == OUTCOME_QUEUED
    item = result.items[0]
    assert item.queue_reason == "resource"
    assert item.owner is None
    assert item.attempt_id is None
    assert item.lease_token is None

    row = task_row(database, "T-1")
    assert row["status"] == "queued"
    assert row["queue_reason"] == "resource"
    assert row["owner"] is None
    assert row["current_attempt_id"] is None
    assert row["current_epoch"] == 0, "a deferred claim must not advance the epoch"
    assert row["attempt_no"] == 0
    assert row["base_commit"] is None, "base is frozen only on a successful claim"

    assert count(database, "SELECT COUNT(*) FROM leases WHERE task_id='T-1'") == 0
    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 0
    assert event_names(database, aggregate_id="T-1") == ["claim_deferred_resource"]

    deferred = database.read("SELECT * FROM task_claims WHERE task_id='T-1'")[0]
    assert deferred["release_reason"] == "queued:resource"


def test_queued_task_can_be_claimed_again_after_the_resource_frees(database):
    seed_task(database, exclusive_resources=["port:8188"])
    future = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    hold_resource(database, "port:8188", attempt_id="T-9.a1", lease_until=future)
    assert claim_one(database, request_id="cr-1").outcome == OUTCOME_QUEUED

    # The holder releases, and the task is put back to ready by the scheduler.
    with database.transaction() as conn:
        conn.execute(
            "UPDATE resource_leases SET released_at = 'now' WHERE resource_id = 'port:8188'"
        )
        conn.execute("UPDATE tasks SET status = 'ready' WHERE task_id = 'T-1'")

    result = claim_one(database, request_id="cr-2")
    assert result.outcome == OUTCOME_CLAIMED
    assert (
        count(
            database,
            "SELECT COUNT(*) FROM resource_leases WHERE attempt_id='T-1.a1' AND released_at IS NULL",
        )
        == 1
    )


def test_attempt_budget_exhausted_rejects_without_an_event(database):
    seed_task(database, attempt_no=3, max_attempts=3)
    result = claim_one(database)

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.ATTEMPT_BUDGET_EXHAUSTED.value
    # The claim itself writes no state on a refusal; the lease sweep performs
    # the ready -> escalated transition on its next round.
    assert event_names(database, aggregate_id="T-1") == []


def test_budget_override_lets_an_exhausted_task_be_claimed(database):
    seed_task(database, attempt_no=3, max_attempts=3, budget_override_by="human-1")
    result = claim_one(database)

    assert result.outcome == OUTCOME_CLAIMED
    assert task_row(database, "T-1")["attempt_no"] == 4


def test_the_budget_override_removes_the_cap_rather_than_adding_one_try(database):
    """豁免的口径是**解除次数上限**，不是「只多给一次」。

    R10 §10 的领取门禁是 `attempt_no < max_attempts OR budget_override_by IS NOT
    NULL` —— 所以人豁免过之后，这条活的尝试计数就不再是上限（2026-10-29 的真实
    演练里有一条从 3 次上限一路跑到第 8 次）。界面上写「不再限制尝试次数」，
    就是因为「只多给一次」是平台兑现不了的承诺。
    """
    seed_task(database, attempt_no=8, max_attempts=3, budget_override_by="human-1")
    result = claim_one(database)

    assert result.outcome == OUTCOME_CLAIMED
    assert task_row(database, "T-1")["attempt_no"] == 9


def test_claim_rejects_a_task_that_is_not_ready(database):
    seed_task(database, status="draft")
    result = claim_one(database)

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.TASK_NOT_READY.value


def test_claim_rejects_an_already_owned_task(database):
    seed_task(database)
    claim_one(database, request_id="cr-1")
    result = claim_one(database, request_id="cr-2", harness="harness-2")

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.ALREADY_OWNED.value
    assert task_row(database, "T-1")["owner"] == "harness-1"


def test_claim_reports_a_missing_task_as_rejected(database):
    result = claim_one(database, task_id="T-nope")

    assert result.outcome == OUTCOME_REJECTED
    assert result.items[0].error_code == RejectCode.TASK_NOT_FOUND.value
    assert count(database, "SELECT COUNT(*) FROM task_claims") == 0
