"""SCHED-SWEEP-001: the sweeper runner (design docs/proposals/sweeper-family.md §3).

What is pinned here: the runner actually drives the family (with the existing
lease sweep as its first member, run for real over an expired lease), the
execution order is a deliberate list rather than an accident, a second round
over the same clock is a no-op, the ``sweep_progress`` carrier is migrated,
and the module entry point behaves as operations will use it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.claim.service import ClaimRequest, ClaimService
from app.scheduler import SWEEPS, run_all_sweeps
from tests.claim_support import make_database, seed_project, seed_task

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _claimed(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",))
    )
    return database


def _status(database) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])


def test_the_runner_drives_the_lease_sweep_end_to_end(tmp_path):
    database = _claimed(tmp_path)
    future = datetime.now(UTC) + timedelta(hours=1)

    reports = run_all_sweeps(database, now=future)

    assert [report.sweep for report in reports] == [
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
        "budget",
        "worktree_recycle",
        "human_wait_timeout",
        "escalation_reminder",
    ]
    assert reports[0].actions == 1
    assert reports[0].detail["tasks"] == ["T-1"]
    assert _status(database) == "stale"
    events = database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'lease_expire'"
    )
    assert events[0]["n"] == 1


def test_the_runner_order_is_pinned():
    """Inserting a sweep in the middle must be a deliberate edit (design §7.1)."""
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
        "budget",
        "worktree_recycle",
        "human_wait_timeout",
        "escalation_reminder",
    ]


def test_a_second_round_over_the_same_clock_is_a_no_op(tmp_path):
    database = _claimed(tmp_path)
    future = datetime.now(UTC) + timedelta(hours=1)

    run_all_sweeps(database, now=future)
    second = run_all_sweeps(database, now=future)

    assert [report.actions for report in second] == [0] * 15
    events = database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'lease_expire'"
    )
    assert events[0]["n"] == 1, "no duplicate transition may be written"


def test_the_sweep_progress_carrier_is_migrated(tmp_path):
    database = _claimed(tmp_path)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO sweep_progress(task_id, fingerprint, unchanged_count, last_checked_at)"
            " VALUES ('T-1', 'fp-1', 0, '2026-10-06T00:00:00+00:00')"
        )
    row = database.read("SELECT * FROM sweep_progress WHERE task_id = 'T-1'")[0]
    assert row["fingerprint"] == "fp-1"
    assert row["unchanged_count"] == 0


def _run_module(module: str, *args: str, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


def test_the_module_entry_point_refuses_without_a_database(tmp_path):
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    proc = _run_module("app.scheduler", "--once", env=env)
    assert proc.returncode == 2
    assert "no database" in proc.stderr


def test_the_module_entry_point_runs_once_with_json_lines(tmp_path):
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    prepared = _run_module("app.db", env=env)
    assert prepared.returncode == 0, prepared.stderr

    proc = _run_module("app.scheduler", "--once", env=env)
    assert proc.returncode == 0, proc.stderr
    line = json.loads(proc.stdout.strip().splitlines()[0])
    assert line["sweep"] == "expired_leases"
    assert line["actions"] == 0


def test_the_loop_refuses_when_the_sweeper_is_disabled(tmp_path):
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    env["AP_SWEEPER_ENABLED"] = "0"
    proc = _run_module("app.scheduler", "--loop", "--interval", "1", env=env)
    assert proc.returncode == 2
    assert "sweeper disabled" in proc.stderr
