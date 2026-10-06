"""SCHED-SWEEP-003: aging, the block SLA, and the stuck fingerprint.

The fingerprint is the only stateful sweep: its counter lives in
``sweep_progress``, so these tests drive multiple rounds with explicit clocks
and assert the counts between rounds, not just the final transition.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.claim.service import ClaimRequest, ClaimService
from app.scheduler import sweep_aging, sweep_blocked_sla, sweep_stuck
from app.spec import TaskStatus
from tests.api_support import make_client
from tests.claim_support import make_database, seed_project, seed_task

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _status(database, task_id: str = "T-1") -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


def _event_count(database, event_name: str, task_id: str = "T-1") -> int:
    return database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE aggregate_id = ? AND event_name = ?",
        (task_id, event_name),
    )[0]["n"]


def _counter(database, task_id: str = "T-1"):
    rows = database.read("SELECT * FROM sweep_progress WHERE task_id = ?", (task_id,))
    return rows[0] if rows else None


def _watched_rig(tmp_path, *, status: TaskStatus, waited_minutes: int | None = None):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    waiting_since = (
        None if waited_minutes is None else (NOW - timedelta(minutes=waited_minutes)).isoformat()
    )
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, waiting_since = ? WHERE task_id = 'T-1'",
            (status.value, waiting_since),
        )
    return database


# ------------------------------------------------------------------- aging


def test_aging_escalates_ready_past_the_configured_multiple(tmp_path):
    fresh = _watched_rig(tmp_path / "fresh", status=TaskStatus.READY, waited_minutes=30)
    assert sweep_aging(fresh, NOW).actions == 0
    assert _status(fresh) == "ready"

    stale = _watched_rig(tmp_path / "stale", status=TaskStatus.READY, waited_minutes=200)
    report = sweep_aging(stale, NOW)
    assert report.detail["escalated"] == ["T-1"]
    assert _status(stale) == "escalated"
    assert _event_count(stale, "aging_timeout") == 1


def test_aging_escalates_queued_starvation(tmp_path):
    """The queued half of aging, deferred here from the resource sweep."""
    database = _watched_rig(tmp_path, status=TaskStatus.QUEUED, waited_minutes=500)
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET queue_reason = 'resource' WHERE task_id = 'T-1'")

    report = sweep_aging(database, NOW)

    assert report.detail["escalated"] == ["T-1"]
    assert _status(database) == "escalated"
    assert _event_count(database, "aging_timeout") == 1


# ------------------------------------------------------------------ blocked


def test_blocked_past_the_sla_escalates(tmp_path):
    fresh = _watched_rig(tmp_path / "fresh", status=TaskStatus.BLOCKED, waited_minutes=30)
    assert sweep_blocked_sla(fresh, NOW).actions == 0

    stale = _watched_rig(tmp_path / "stale", status=TaskStatus.BLOCKED, waited_minutes=61)
    report = sweep_blocked_sla(stale, NOW)
    assert report.detail["escalated"] == ["T-1"]
    assert _status(stale) == "escalated"
    assert _event_count(stale, "block_timeout") == 1


# -------------------------------------------------------------------- stuck


def test_stuck_counts_silent_rounds_then_escalates(tmp_path):
    database = _watched_rig(tmp_path, status=TaskStatus.IN_PROGRESS)

    first = sweep_stuck(database, NOW)
    assert first.actions == 0
    assert _counter(database)["unchanged_count"] == 0, "first sighting is the baseline"
    for round_no in (1, 2):
        report = sweep_stuck(database, NOW + timedelta(minutes=round_no))
        assert report.actions == 0, report.detail
        assert _counter(database)["unchanged_count"] == round_no

    third = sweep_stuck(database, NOW + timedelta(minutes=3))
    assert third.detail["escalated"] == ["T-1"]
    assert _status(database) == "escalated"
    assert _event_count(database, "stuck_threshold_reached") == 1

    fourth = sweep_stuck(database, NOW + timedelta(minutes=4))
    assert fourth.actions == 0, "escalated tasks have left the watched set"


def test_a_new_event_resets_the_stuck_counter(tmp_path):
    database = _watched_rig(tmp_path, status=TaskStatus.IN_PROGRESS)
    sweep_stuck(database, NOW)
    sweep_stuck(database, NOW + timedelta(minutes=1))
    assert _counter(database)["unchanged_count"] == 1

    # Any new task-scoped event moves the fingerprint; a claim event stands in
    # for the many real producers (submit, help, verify, ...).
    from app.events.store import EventStore

    EventStore(database).append(
        event_name="help_requested",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="harness-1",
        payload={},
    )
    report = sweep_stuck(database, NOW + timedelta(minutes=2))
    assert report.actions == 0
    assert _counter(database)["unchanged_count"] == 0, "progress resets the count"


def test_progress_hash_from_heartbeat_moves_the_fingerprint(tmp_path):
    database = _watched_rig(tmp_path, status=TaskStatus.IN_PROGRESS)
    sweep_stuck(database, NOW)
    assert _counter(database)["unchanged_count"] == 0

    # The third fingerprint component: what the heartbeat route now stores.
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET progress_hash = 'h-2' WHERE task_id = 'T-1'")
    sweep_stuck(database, NOW + timedelta(minutes=1))
    assert _counter(database)["unchanged_count"] == 0, "a new progress hash is progress"

    sweep_stuck(database, NOW + timedelta(minutes=2))
    assert _counter(database)["unchanged_count"] == 1, "then silence starts counting again"


def test_stuck_human_threshold_from_a_higher_count(tmp_path):
    database = _watched_rig(tmp_path, status=TaskStatus.NEEDS_HELP)
    sweep_stuck(database, NOW)  # stores the baseline fingerprint
    with database.transaction() as conn:
        conn.execute("UPDATE sweep_progress SET unchanged_count = 4 WHERE task_id = 'T-1'")

    report = sweep_stuck(database, NOW + timedelta(minutes=1))

    assert report.detail["awaiting_human"] == ["T-1"]
    assert _status(database) == "awaiting_human"
    assert _event_count(database, "stuck_human_threshold") == 1


def test_the_heartbeat_route_stores_the_progress_hash(tmp_path):
    client, database = make_client(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    # A claimed task with a lease is enough for the route's renewal path.
    response = client.post(
        "/api/v1/adapter/heartbeat",
        json={
            "task_id": "T-1",
            "attempt_id": item.attempt_id,
            "lease_token": item.lease_token,
            "fencing_epoch": item.fencing_epoch,
            "progress_hash": "h-route-1",
        },
    )
    assert response.status_code == 200, response.text
    stored = database.read("SELECT progress_hash FROM tasks WHERE task_id = 'T-1'")[0]
    assert stored["progress_hash"] == "h-route-1"
