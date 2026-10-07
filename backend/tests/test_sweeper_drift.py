"""SCHED-SWEEP-005: the drift detectors (O3 ruling).

Plan drift is DB-decidable and seeded by making the stored plan_hash diverge
from the bound snapshot. Impl drift needs real git history: the integration
ref is REWRITTEN to an unrelated root -- the shape a revert or invalidation
leaves, where the task's base is no longer an ancestor of the head. The
forward-merge control stays silent, which is what keeps this detector from
firing on normal collaboration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.events.store import EventStore
from app.git.repo import CanonicalRepo, run_git
from app.scheduler import run_all_sweeps, sweep_drift
from app.spec import EventKind
from tests.claim_support import make_database, seed_project, seed_task

NOW = datetime.now(UTC)


def _status(database, task_id: str = "T-1") -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


def _event_count(database, event_name: str) -> int:
    return database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE aggregate_id = 'T-1' AND event_name = ?",
        (event_name,),
    )[0]["n"]


def _plan_rig(tmp_path, *, snapshot_hash: str, plan_hash: str):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
            " VALUES ('PL-X', 'G-1', 'approved', ?, ?)",
            (plan_hash, NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash, content_json,"
            " created_at) VALUES ('snap-1', 'PL-X', ?, '{}', ?)",
            (snapshot_hash, NOW.isoformat()),
        )
        conn.execute(
            "UPDATE tasks SET status = 'in_progress', current_epoch = 1,"
            " plan_snapshot_id = 'snap-1' WHERE task_id = 'T-1'"
        )
    return database


def _impl_rig(tmp_path, *, diverge: bool):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    if diverge:
        # Rewrite integration to an unrelated root: the base is no longer an
        # ancestor of the head (revert / invalidation shape).
        work = tmp_path / "rewritten"
        work.mkdir()
        run_git("init", "--initial-branch", "integration", str(work))
        (work / "f.txt").write_text("rewritten\n", encoding="utf-8")
        run_git("add", "f.txt", cwd=work)
        run_git("commit", "-m", "chore: rewritten root", cwd=work)
        head = run_git("rev-parse", "HEAD", cwd=work).stdout.strip()
        repo.import_objects(work, head)
        repo.update_ref(repo.integration_ref, head)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = 'in_progress', current_epoch = 1, base_commit = ?"
            " WHERE task_id = 'T-1'",
            (base,),
        )
    return database, repo


# ------------------------------------------------------------------- plan


def test_plan_drift_fires_on_hash_mismatch(tmp_path):
    database = _plan_rig(tmp_path, snapshot_hash="H-A", plan_hash="H-B")

    report = sweep_drift(database, NOW)

    assert report.detail["plan_drift"] == ["T-1"]
    assert _status(database) == "plan_drift"
    assert _event_count(database, "plan_hash_changed") == 1


def test_matching_plan_hashes_do_not_drift(tmp_path):
    database = _plan_rig(tmp_path, snapshot_hash="H-A", plan_hash="H-A")
    assert sweep_drift(database, NOW).actions == 0
    assert _status(database) == "in_progress"


# ------------------------------------------------------------------- impl


def test_impl_drift_fires_when_the_base_leaves_the_integration_history(tmp_path):
    database, repo = _impl_rig(tmp_path, diverge=True)

    report = sweep_drift(database, NOW, repo)

    assert report.detail["impl_drift"] == ["T-1"]
    assert _status(database) == "impl_drift"
    assert _event_count(database, "impl_hash_changed") == 1

    rerun = sweep_drift(database, NOW, repo)
    assert rerun.actions == 0, "the task left the watched set"


def test_forward_merges_do_not_fire_impl_drift(tmp_path):
    database, repo = _impl_rig(tmp_path, diverge=False)
    assert sweep_drift(database, NOW, repo).actions == 0
    assert _status(database) == "in_progress"


def test_missing_repo_declares_the_impl_check_undecided(tmp_path):
    database, _repo = _impl_rig(tmp_path, diverge=True)

    report = sweep_drift(database, NOW)  # no repo injected

    assert report.actions == 0
    assert report.detail["undecided"] == ["T-1"]
    assert _status(database) == "in_progress", "no repo means no guess"


# ---------------------------------------------------------------- recovery


def test_drift_states_have_a_recovery_exit(tmp_path):
    """Enter plan_drift by the detector, leave by the ordinary lease sweep."""
    database = _plan_rig(tmp_path, snapshot_hash="H-A", plan_hash="H-B")
    past = (NOW - timedelta(minutes=5)).isoformat()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a1', 'T-1', 1, 'harness-1', 1, NULL, NULL, NULL, 'in_progress', ?)",
            (past,),
        )
        conn.execute(
            "INSERT INTO leases(lease_id, task_id, attempt_id, kind, owner, token_hash,"
            " fencing_epoch, lease_until, grant_wall, grant_mono, boot_id, released_at)"
            " VALUES ('l-1', 'T-1', 'T-1.a1', 'development', 'harness-1', 'x', 1, ?, ?, 0,"
            " 'boot', NULL)",
            (past, past),
        )

    reports = run_all_sweeps(database, now=NOW)

    names = [report.sweep for report in reports]
    assert (
        names.index("drift") < len(names) - 3
    ), "drift runs before the trailing group (budget + the two reminders)"
    assert names[-2:] == ["human_wait_timeout", "escalation_reminder"]
    assert _status(database) == "stale"
    assert _event_count(database, "lease_expire") == 1


def test_the_drift_marker_writes_the_safety_event(tmp_path):
    database = _plan_rig(tmp_path, snapshot_hash="H-A", plan_hash="H-B")
    sweep_drift(database, NOW)
    events = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = ?",
        (EventKind.PLAN_HASH_CHANGED.value,),
    )
    assert len(events) == 1
    assert "H-A" in str(events[0]["payload_json"])
    assert "H-B" in str(events[0]["payload_json"])
    # The store remains the only writer of task state: the marker is an event
    # plus a transition, nothing in between.
    assert EventStore(database).latest_seq() > 0
