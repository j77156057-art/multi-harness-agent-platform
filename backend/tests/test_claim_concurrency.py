"""CORE-CLAIM-002: two real SQLite connections racing for the same task.

R10 §26 requires this to use two genuine connections, not one connection with
serialized calls, so the assertion actually exercises ``BEGIN IMMEDIATE`` and
the conditional UPDATE.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from app.claim.service import (
    OUTCOME_CLAIMED,
    OUTCOME_REJECTED,
    ClaimRequest,
    ClaimService,
)
from tests.claim_support import count, make_database, seed_project, seed_task, task_row


def _race(database, request_ids: tuple[str, str], task_id: str = "T-1") -> list[object]:
    barrier = threading.Barrier(2)

    def attempt(request_id: str) -> object:
        service = ClaimService(database)
        barrier.wait(timeout=10)
        try:
            return service.claim(
                ClaimRequest(
                    claim_request_id=request_id,
                    harness_id=f"harness-{request_id}",
                    task_ids=(task_id,),
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced by the assertion
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, request_id) for request_id in request_ids]
        return [future.result(timeout=30) for future in futures]


def test_only_one_of_two_racing_claims_wins(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database)

    results = _race(database, ("cr-a", "cr-b"))
    for result in results:
        assert not isinstance(result, Exception), f"a racer raised: {result!r}"

    winners = [r for r in results if getattr(r, "outcome", None) == OUTCOME_CLAIMED]
    losers = [r for r in results if getattr(r, "outcome", None) == OUTCOME_REJECTED]
    assert len(winners) == 1, f"expected exactly one winner, got {len(winners)}"
    assert len(losers) == 1

    row = task_row(database, "T-1")
    assert row["status"] == "claimed"
    assert row["owner"] in {"harness-cr-a", "harness-cr-b"}
    assert row["current_attempt_id"] == "T-1.a1"
    assert row["current_epoch"] == 1, "the epoch must advance exactly once"
    assert row["attempt_no"] == 1, "exactly one attempt must be created"

    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 1
    assert (
        count(database, "SELECT COUNT(*) FROM leases WHERE task_id='T-1' AND released_at IS NULL")
        == 1
    )
    assert count(database, "SELECT COUNT(*) FROM task_claims WHERE task_id='T-1'") == 1
    assert count(database, "SELECT COUNT(*) FROM task_events WHERE event_name='claim'") == 1
    # The loser's all-or-nothing rollback must leave no request bookkeeping.
    assert count(database, "SELECT COUNT(*) FROM claim_requests") == 1


def test_two_racers_with_the_same_request_id_both_replay_one_result(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database)

    results = _race(database, ("cr-same", "cr-same"))
    for result in results:
        assert not isinstance(result, Exception), f"a racer raised: {result!r}"
        assert result.outcome == OUTCOME_CLAIMED

    replayed = [r.replayed for r in results]
    assert sorted(replayed) == [False, True], "one writer, one replayer"

    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 1
    assert count(database, "SELECT COUNT(*) FROM claim_requests") == 1
    assert count(database, "SELECT COUNT(*) FROM task_events WHERE event_name='claim'") == 1
    assert task_row(database, "T-1")["current_epoch"] == 1
