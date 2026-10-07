"""SCHED-SWEEP-002: the dependency sweep and the resource sweep.

Entering ``ready_waiting_deps`` / ``queued`` by their real producers is covered
by the plan-approval and claim tests; these tests seed those states directly
(one UPDATE, commented) and prove the *exits*: three dependency edges, the
resource resume (including the O1 ruling's end state), and the runner's pinned
order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.claim.service import ClaimRequest, ClaimService
from app.scheduler import SWEEPS, run_all_sweeps, sweep_dependencies, sweep_resources
from app.spec import TaskStatus
from tests.claim_support import hold_resource, make_database, seed_project, seed_task

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _status(database, task_id: str) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


def _event_count(database, task_id: str, event_name: str) -> int:
    return database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE aggregate_id = ? AND event_name = ?",
        (task_id, event_name),
    )[0]["n"]


def _dep_rig(tmp_path, *, upstream_status: str, waited_minutes: int = 0):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-UP")
    seed_task(database, task_id="T-1")
    waiting_since = (NOW - timedelta(minutes=waited_minutes)).isoformat()
    # Direct seeding of the waiting state; the plan-approval path into it is
    # covered by test_state_plan / test_api_flow.
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE task_id = 'T-UP'", (upstream_status,))
        conn.execute(
            "UPDATE tasks SET status = ?, waiting_since = ? WHERE task_id = 'T-1'",
            (TaskStatus.READY_WAITING_DEPS.value, waiting_since),
        )
        conn.execute(
            "INSERT INTO task_dependencies(parent_task_id, child_task_id, relation)"
            " VALUES ('T-1', 'T-UP', 'depends_on')"
        )
    return database


def test_dependencies_satisfied_moves_the_task_to_ready(tmp_path):
    database = _dep_rig(tmp_path, upstream_status=TaskStatus.MERGED.value)

    report = sweep_dependencies(database, NOW)

    assert report.actions == 1
    assert report.detail["satisfied"] == ["T-1"]
    assert _status(database, "T-1") == "ready"
    assert _event_count(database, "T-1", "dependencies_satisfied") == 1
    waiting = database.read("SELECT waiting_since FROM tasks WHERE task_id = 'T-1'")[0]
    assert waiting["waiting_since"] is None


def test_orphan_detection_escalates_when_upstreams_died_without_output(tmp_path):
    database = _dep_rig(tmp_path, upstream_status=TaskStatus.CANCELLED.value)

    report = sweep_dependencies(database, NOW)

    assert report.detail["orphaned"] == ["T-1"]
    assert _status(database, "T-1") == "escalated"
    assert _event_count(database, "T-1", "orphan_detected") == 1
    row = database.read("SELECT escalated_at FROM tasks WHERE task_id = 'T-1'")[0]
    assert row["escalated_at"] is not None


def test_block_timeout_fires_only_past_twice_the_expected_duration(tmp_path):
    # Default expected duration is 90 minutes per upstream → threshold 180.
    fresh = _dep_rig(tmp_path / "fresh", upstream_status="in_progress", waited_minutes=30)
    assert sweep_dependencies(fresh, NOW).actions == 0
    assert _status(fresh, "T-1") == "ready_waiting_deps"

    stale = _dep_rig(tmp_path / "stale", upstream_status="in_progress", waited_minutes=200)
    report = sweep_dependencies(stale, NOW)
    assert report.detail["block_timeout"] == ["T-1"]
    assert _status(stale, "T-1") == "escalated"
    assert _event_count(stale, "T-1", "block_timeout") == 1


def _queued_rig(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", exclusive_resources=["port:8188"])
    hold_resource(
        database,
        "port:8188",
        attempt_id="T-9.a1",
        lease_until=(NOW + timedelta(hours=1)).isoformat(),
    )
    claimed = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )
    assert claimed.items[0].outcome == "queued"
    return database


def test_a_queued_task_resumes_to_claimed_when_the_resource_frees(tmp_path):
    database = _queued_rig(tmp_path)
    with database.transaction() as conn:
        # The holder goes away; the resource row is released (its own lease
        # expiry sweep is a separate concern).
        conn.execute(
            "UPDATE resource_leases SET released_at = 'gone' WHERE resource_id = 'port:8188'"
        )

    report = sweep_resources(database, NOW)

    assert report.actions == 1
    assert report.detail["claimed"] == ["T-1"]
    assert _status(database, "T-1") == "claimed"
    row = database.read(
        "SELECT owner, current_attempt_id, queue_reason FROM tasks WHERE task_id = 'T-1'"
    )[0]
    assert row["owner"] == "harness-1", "the requester from the deferred event gets the attempt"
    assert row["current_attempt_id"] == "T-1.a1"
    assert row["queue_reason"] is None
    assert _event_count(database, "T-1", "resource_available") == 1
    lease = database.read(
        "SELECT COUNT(*) AS n FROM leases WHERE task_id = 'T-1' AND released_at IS NULL"
    )[0]
    assert lease["n"] == 1

    # The deferred claim row became the live claim: replay answers with the
    # completed attempt and hands a fresh token (the 10b rotation path).
    replay = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )
    item = replay.items[0]
    assert replay.replayed is True
    assert item.outcome == "claimed"
    assert item.attempt_id == "T-1.a1"
    assert item.lease_token

    assert sweep_resources(database, NOW).actions == 0, "idempotent second round"


def test_the_offline_requester_ends_in_the_existing_stale_chain(tmp_path):
    """O1 ruling (2026-10-07): grant literally; an absent requester simply
    lets the development lease expire, which the lease sweep already handles."""
    database = _queued_rig(tmp_path)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE resource_leases SET released_at = 'gone' WHERE resource_id = 'port:8188'"
        )

    first = run_all_sweeps(database, now=NOW)
    assert _status(database, "T-1") == "claimed"
    assert [report.sweep for report in first] == [
        "expired_leases",
        "dependencies",
        "resources",
        "aging",
        "blocked_sla",
        "stuck",
        "verify_lease",
        "verify_queue",
        "review_lease",
        "merge_recovery",
        "drift",
        "human_wait_timeout",
        "escalation_reminder",
    ]

    run_all_sweeps(database, now=NOW + timedelta(hours=1))
    assert _status(database, "T-1") == "stale"
    assert _event_count(database, "T-1", "lease_expire") == 1


def test_the_runner_order_includes_the_new_sweeps():
    assert [name for name, _sweep in SWEEPS] == [
        "expired_leases",
        "dependencies",
        "resources",
        "aging",
        "blocked_sla",
        "stuck",
        "verify_lease",
        "verify_queue",
        "review_lease",
        "merge_recovery",
        "drift",
        "human_wait_timeout",
        "escalation_reminder",
    ]
