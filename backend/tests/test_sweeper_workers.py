"""SCHED-SWEEP-004: verify / review / merge wait-and-lease recovery.

All rigs seed the state directly (the producers exist elsewhere: submit sets
verifying, review_claim claims a review, the merge worker enters merging) and
backdate the anchor event, because the sweeps' only input is the clock. The O2
proxy is exercised through its real shape: a leftover ``running`` verify job.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.config import settings
from app.events.store import EventStore
from app.scheduler import (
    sweep_merge_recovery,
    sweep_review_lease,
    sweep_verify_lease,
    sweep_verify_queue,
)
from app.spec import EventKind, TaskStatus
from app.verify.worker import STATUS_ABORTED, STATUS_QUEUED, STATUS_RUNNING
from tests.claim_support import make_database, seed_project, seed_task

#: Seeding uses the real clock; the sweeps are called with ``now`` moved
#: forward instead. task_events is append-only (trigger-enforced), so tests
#: may not backdate anchors -- and they do not need to.
NOW = datetime.now(UTC)


def _status(database, task_id: str = "T-1") -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


def _event_count(database, event_name: str) -> int:
    return database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE aggregate_id = 'T-1' AND event_name = ?",
        (event_name,),
    )[0]["n"]


def _seed_task_row(database, *, status: TaskStatus, attempt: bool = False) -> None:
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, current_attempt_id = ?, current_epoch = ?"
            " WHERE task_id = 'T-1'",
            (status.value, "T-1.a1" if attempt else None, 1),
        )
        if attempt:
            conn.execute(
                "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
                " fencing_epoch, base_commit, base_locked_seq, head_commit, status, created_at)"
                " VALUES ('T-1.a1', 'T-1', 1, 'harness-1', 1, NULL, NULL, NULL, ?, ?)",
                (status.value, NOW.isoformat()),
            )


def _verifying_rig(tmp_path, *, job_status: str):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    _seed_task_row(database, status=TaskStatus.VERIFYING, attempt=True)
    EventStore(database).append(
        event_name=EventKind.SUBMIT_RESULT.value,
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="harness",
        actor_id="harness-1",
        payload={"attempt_id": "T-1.a1"},
    )
    with database.transaction() as conn:
        conn.execute(
            'INSERT INTO verify_jobs(job_id, task_id, attempt_id, "commit", base_commit,'
            " status, queue_reason, fencing_epoch, verify_profile, created_seq)"
            " VALUES ('job-old', 'T-1', 'T-1.a1', 'c1', NULL, ?, NULL, 1,"
            " 'flag-check', NULL)",
            (job_status,),
        )
    return database


# ------------------------------------------------------------- verify lease


def test_verify_lease_requeues_a_dead_run(tmp_path):
    database = _verifying_rig(tmp_path, job_status=STATUS_RUNNING)

    report = sweep_verify_lease(database, NOW + timedelta(hours=2))

    assert report.detail["re_queued"] == ["T-1"]
    assert _status(database) == "verifying", "the edge is a self-loop"
    old = database.read("SELECT status FROM verify_jobs WHERE job_id = 'job-old'")[0]
    assert old["status"] == STATUS_ABORTED
    new_jobs = database.read(
        "SELECT * FROM verify_jobs WHERE job_id != 'job-old' AND status = ?",
        (STATUS_QUEUED,),
    )
    assert len(new_jobs) == 1
    assert new_jobs[0]["queue_reason"] == "verify_lease_expire"
    assert new_jobs[0]["attempt_id"] == "T-1.a1"
    assert _event_count(database, "verify_lease_expire") == 1
    task = database.read(
        "SELECT current_attempt_id, current_epoch FROM tasks WHERE task_id = 'T-1'"
    )[0]
    assert (
        task["current_attempt_id"] == "T-1.a1" and task["current_epoch"] == 1
    ), "no new attempt, no epoch change (R10 §10's note on this edge)"


def test_verify_lease_waits_for_the_deadline(tmp_path):
    database = _verifying_rig(tmp_path, job_status=STATUS_RUNNING)
    assert sweep_verify_lease(database, NOW + timedelta(minutes=5)).actions == 0
    assert _status(database) == "verifying"


def test_verify_queue_timeout_escalates(tmp_path):
    database = _verifying_rig(tmp_path, job_status=STATUS_QUEUED)

    report = sweep_verify_queue(database, NOW + timedelta(minutes=45))

    assert report.detail["escalated"] == ["T-1"]
    assert _status(database) == "escalated"
    assert _event_count(database, "verify_queue_timeout") == 1

    fresh = _verifying_rig(tmp_path / "fresh", job_status=STATUS_QUEUED)
    assert sweep_verify_queue(fresh, NOW + timedelta(minutes=5)).actions == 0


# ------------------------------------------------------------- review lease


def _review_rig(tmp_path, *, lease_delta_minutes: int):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    _seed_task_row(database, status=TaskStatus.AWAITING_REVIEW, attempt=True)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO reviews(review_id, task_id, attempt_id, reviewer_harness_id,"
            " reviewer_provider, status, lease_until, fencing_epoch, review_revision)"
            " VALUES ('rv-1', 'T-1', 'T-1.a1', 'reviewer-1', 'unknown', 'claimed', ?, 1, 1)",
            ((NOW + timedelta(minutes=lease_delta_minutes)).isoformat(),),
        )
    return database


def test_review_lease_expiry_puts_the_claim_back(tmp_path):
    database = _review_rig(tmp_path, lease_delta_minutes=-5)

    report = sweep_review_lease(database, NOW)

    assert report.detail["expired"] == ["T-1"]
    assert _status(database) == "awaiting_review"
    row = database.read("SELECT status FROM reviews WHERE review_id = 'rv-1'")[0]
    assert row["status"] == "expired"
    assert _event_count(database, "review_lease_expire") == 1


def test_review_lease_is_left_alone_before_the_deadline(tmp_path):
    database = _review_rig(tmp_path, lease_delta_minutes=5)
    assert sweep_review_lease(database, NOW).actions == 0
    row = database.read("SELECT status FROM reviews WHERE review_id = 'rv-1'")[0]
    assert row["status"] == "claimed"


# --------------------------------------------------------- merge recovery


def _merging_rig(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    _seed_task_row(database, status=TaskStatus.MERGING, attempt=True)
    EventStore(database).append(
        event_name=EventKind.MERGE_START.value,
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="merge-worker",
        actor_id="merge-worker-1",
        payload={"attempt_id": "T-1.a1"},
    )
    return database


def test_merge_lease_expire_retries_before_escalating(tmp_path):
    """Defaults make both thresholds equal; the retry wins at the same moment."""
    database = _merging_rig(tmp_path)

    report = sweep_merge_recovery(database, NOW + timedelta(minutes=40))

    assert report.detail["retried"] == ["T-1"]
    assert _status(database) == "approved"
    assert _event_count(database, "merge_lease_expire") == 1


def test_merge_timeout_fires_in_the_operator_window(tmp_path, monkeypatch):
    """lease > test-timeout opens the escalation window; 45m sits inside it."""
    monkeypatch.setattr(settings, "merge_lease_seconds", 7200)
    database = _merging_rig(tmp_path)

    report = sweep_merge_recovery(database, NOW + timedelta(minutes=45))

    assert report.detail["escalated"] == ["T-1"]
    assert _status(database) == "escalated"
    assert _event_count(database, "merge_timeout") == 1


def test_merge_recovery_waits_inside_both_deadlines(tmp_path):
    database = _merging_rig(tmp_path)
    assert sweep_merge_recovery(database, NOW + timedelta(minutes=5)).actions == 0
    assert _status(database) == "merging"


def test_each_worker_sweep_is_idempotent(tmp_path):
    database = _verifying_rig(tmp_path, job_status=STATUS_RUNNING)
    later = NOW + timedelta(hours=2)
    sweep_verify_lease(database, later)
    rerun = sweep_verify_lease(database, later)
    assert rerun.actions == 0, "the fresh job is queued, not running"
