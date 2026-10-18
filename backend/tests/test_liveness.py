"""OPS-LIVENESS-001 (liveness-watch): survival readings, the zero-commit
alarm, and the stuck-fingerprint fix.

Three things pinned here:

* the pure policy (learned fallback-chain shape, two injected thresholds,
  inclusive boundaries);
* the scan's evidence -- status/owner/attempt/lease-derived -- and the
  zero-commit alarm (R10 §13 L706) firing exactly once per attempt through
  the alert queue;
* the stuck sweep no longer escalates a healthy long run: a renewed lease is
  progress evidence, while a lease nobody renews still escalates exactly as
  before (both directions under test).
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.alerts.service import AlertNotifier
from app.config import settings
from app.liveness import (
    SILENCE_CRITICAL,
    SILENCE_NOT_APPLICABLE,
    SILENCE_OK,
    SILENCE_SUSPICIOUS,
    LivenessWatch,
    classify_silence,
    silence_started_at,
)
from app.scheduler import sweep_stuck
from tests.claim_support import make_database, seed_project, seed_task

BACKEND_ROOT = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ policy


def test_silence_clock_follows_the_fallback_chain():
    created = datetime(2026, 10, 1, tzinfo=UTC)
    attempt = datetime(2026, 10, 2, tzinfo=UTC)
    renewal = datetime(2026, 10, 3, tzinfo=UTC)
    event = datetime(2026, 10, 4, tzinfo=UTC)

    assert (
        silence_started_at(
            last_event_at=None, renewal_at=None, attempt_started_at=None, task_created_at=created
        )
        == created
    )
    assert (
        silence_started_at(
            last_event_at=None, renewal_at=None, attempt_started_at=attempt, task_created_at=created
        )
        == attempt
    )
    assert (
        silence_started_at(
            last_event_at=None,
            renewal_at=renewal,
            attempt_started_at=attempt,
            task_created_at=created,
        )
        == renewal
    )
    assert (
        silence_started_at(
            last_event_at=event,
            renewal_at=renewal,
            attempt_started_at=attempt,
            task_created_at=created,
        )
        == event
    )


def test_classify_boundaries_are_inclusive():
    base = {
        "is_active": True,
        "silence_seconds": 0,
        "lease_remaining_seconds": None,
        "suspicion_seconds": 600,
        "critical_seconds": 1200,
        "lease_low_seconds": 300,
    }
    assert classify_silence(**base) == SILENCE_OK
    assert classify_silence(**{**base, "silence_seconds": 599}) == SILENCE_OK
    assert classify_silence(**{**base, "silence_seconds": 600}) == SILENCE_SUSPICIOUS
    assert classify_silence(**{**base, "silence_seconds": 1199}) == SILENCE_SUSPICIOUS
    assert classify_silence(**{**base, "silence_seconds": 1200}) == SILENCE_CRITICAL
    assert (
        classify_silence(**{**base, "lease_remaining_seconds": 0}) == SILENCE_CRITICAL
    ), "an expired lease is critical regardless of silence"
    assert classify_silence(**{**base, "lease_remaining_seconds": 299}) == SILENCE_SUSPICIOUS
    assert classify_silence(**{**base, "is_active": False}) == SILENCE_NOT_APPLICABLE


# --------------------------------------------------------------------- rig


def _rig(
    tmp_path,
    *,
    attempt_age_minutes: int = 25,
    renewal_age_minutes: int = 1,
    head: str | None = None,
    status: str = "in_progress",
    with_lease: bool = True,
    with_attempt: bool = True,
):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    now = datetime.now(UTC)
    attempt_started = now - timedelta(minutes=attempt_age_minutes)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, owner = 'harness-1', current_attempt_id = ?,"
            " current_epoch = 1 WHERE task_id = 'T-1'",
            (status, "T-1.a1" if with_attempt else None),
        )
        if with_attempt:
            conn.execute(
                "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
                " base_commit, base_locked_seq, head_commit, status, created_at)"
                " VALUES ('T-1.a1', 'T-1', 1, 'harness-1', 1, NULL, NULL, ?, ?, ?)",
                (head, status, attempt_started.isoformat()),
            )
        if with_lease:
            lease_until = (
                now
                - timedelta(minutes=renewal_age_minutes)
                + timedelta(seconds=settings.lease_seconds)
            )
            conn.execute(
                "INSERT INTO leases(lease_id, task_id, attempt_id, kind, owner, token_hash,"
                " fencing_epoch, lease_until, grant_wall, grant_mono, boot_id, released_at)"
                " VALUES ('l-1', 'T-1', 'T-1.a1', 'development', 'harness-1', 'x', 1, ?, ?, 0,"
                " 'boot', NULL)",
                (lease_until.isoformat(), now.isoformat()),
            )
    return database, now


def _reading(tmp_path=None, **kwargs):
    database, now = _rig(tmp_path, **kwargs)
    (reading,) = LivenessWatch(database).scan(now=now)
    return database, now, reading


# ------------------------------------------------------------------- scan


def test_a_heartbeating_run_is_ok_even_without_events(tmp_path):
    _database, _now, reading = _reading(tmp_path)

    assert reading.state == SILENCE_OK
    assert reading.silence_seconds == 60, "silence counts from the last renewal"
    assert reading.lease_remaining_seconds and reading.lease_remaining_seconds > 0
    assert reading.no_commit_windows == 5
    assert reading.alarm_eligible is True, "R10 L706: 2 windows without a commit alarms"


def test_a_published_commit_resets_the_zero_commit_windows(tmp_path):
    _database, _now, reading = _reading(tmp_path, head="c0ffee")

    assert reading.no_commit_windows == 0
    assert reading.alarm_eligible is False


def test_a_stale_lease_reads_critical(tmp_path):
    # The silence has to outlive the lease TTL, whatever that TTL is set to.
    stale_minutes = settings.lease_seconds // 60 + 5
    _database, _now, reading = _reading(tmp_path, renewal_age_minutes=stale_minutes)

    assert reading.state == SILENCE_CRITICAL
    assert "lease expired" in reading.reason
    assert reading.alarm_eligible is False, "a dead owner is the expiry path's business"


def test_a_quiet_but_alive_run_reads_suspicious(tmp_path):
    _database, _now, reading = _reading(tmp_path, renewal_age_minutes=14)

    assert reading.state == SILENCE_SUSPICIOUS


def test_a_task_without_a_lease_uses_the_attempt_clock(tmp_path):
    _database, _now, reading = _reading(tmp_path, with_lease=False, attempt_age_minutes=5)

    assert reading.lease_remaining_seconds is None
    assert reading.silence_seconds == 5 * 60
    assert reading.state == SILENCE_OK


# ------------------------------------------------------------ zero-commit


def _notifier(database):
    return AlertNotifier(database, webhook_url="https://example.invalid/hook", secret="s")


def _alerts(database):
    return [
        dict(row)
        for row in database.read("SELECT * FROM alert_deliveries WHERE kind = 'run.no_commit'")
    ]


def test_the_zero_commit_alarm_fires_exactly_once_per_attempt(tmp_path):
    database, now = _rig(tmp_path)
    watch = LivenessWatch(database, notifier=_notifier(database))

    first = watch.run_once(now=now)
    second = watch.run_once(now=now + timedelta(minutes=1))

    assert first.alarms_queued == 1
    assert second.alarms_queued == 0
    (row,) = _alerts(database)
    assert row["source_key"] == "T-1.a1" and row["status"] == "pending"
    assert row["task_id"] == "T-1"

    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET current_attempt_id = 'T-1.a2' WHERE task_id = 'T-1'")
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a2', 'T-1', 2, 'harness-1', 1, NULL, NULL, NULL, 'in_progress', ?)",
            ((now - timedelta(minutes=25)).isoformat(),),
        )
    third = watch.run_once(now=now + timedelta(minutes=2))

    assert third.alarms_queued == 1, "a new attempt counts afresh"
    assert {row["source_key"] for row in _alerts(database)} == {"T-1.a1", "T-1.a2"}


def test_no_notifier_means_no_queueing(tmp_path):
    database, now = _rig(tmp_path)
    watch = LivenessWatch(database, notifier=None)

    report = watch.run_once(now=now)

    assert report.alarms_queued == 0
    assert _alerts(database) == []


# ------------------------------------------------------------ stuck fix


def _status(database) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])


def _stuck_events(database) -> int:
    return int(
        database.read(
            "SELECT COUNT(*) AS n FROM task_events"
            " WHERE aggregate_id = 'T-1' AND event_name = 'stuck_threshold_reached'"
        )[0]["n"]
    )


def test_a_renewed_lease_keeps_a_healthy_run_out_of_stuck(tmp_path):
    database, now = _rig(tmp_path, attempt_age_minutes=1)

    for round_number in range(4):
        moment = now + timedelta(minutes=round_number)
        with database.transaction() as conn:
            conn.execute(
                "UPDATE leases SET lease_until = ? WHERE lease_id = 'l-1'",
                ((moment + timedelta(seconds=settings.lease_seconds)).isoformat(),),
            )
        sweep_stuck(database, moment)

    assert _status(database) == "in_progress"
    assert _stuck_events(database) == 0


def test_a_task_with_no_renewals_still_escalates(tmp_path):
    database, now = _rig(tmp_path, attempt_age_minutes=1)

    for round_number in range(4):
        sweep_stuck(database, now + timedelta(minutes=round_number))

    assert _status(database) == "escalated"
    assert _stuck_events(database) == 1


def test_a_task_without_a_lease_still_escalates(tmp_path):
    database, now = _rig(tmp_path, attempt_age_minutes=1, with_lease=False)

    for round_number in range(4):
        sweep_stuck(database, now + timedelta(minutes=round_number))

    assert _status(database) == "escalated"


# --------------------------------------------------------------------- CLI


def _run_module(*args: str, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, "-m", "app.liveness", *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


def test_the_cli_refuses_without_a_database(tmp_path):
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    proc = _run_module("--check", env=env)
    assert proc.returncode == 2
    assert "no database" in proc.stderr


def test_the_cli_reads_clean_and_critical_databases(tmp_path):
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    prepared = subprocess.run(
        [sys.executable, "-m", "app.db"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    assert prepared.returncode == 0, prepared.stderr

    clean = _run_module("--check", env=env)
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout.strip())["tasks"] == 0

    database_path = Path(env["AP_DATA_DIR"]) / "state.db"
    connection = sqlite3.connect(database_path)
    connection.execute("INSERT INTO projects(project_id, name, created_at) VALUES ('P-X','x','t')")
    connection.execute(
        "INSERT INTO groups(group_id, project_id, name, created_at) VALUES ('G-X','P-X','g','t')"
    )
    old = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    connection.execute(
        "INSERT INTO tasks(task_id, group_id, status, owner, created_at, updated_at)"
        " VALUES ('T-X','G-X','in_progress','harness-1',?,?)",
        (old, old),
    )
    connection.commit()
    connection.close()

    critical = _run_module("--check", env=env)
    assert critical.returncode == 1
    report = json.loads(critical.stdout.strip())
    assert report["critical"] == 1 and report["readings"][0]["task_id"] == "T-X"
