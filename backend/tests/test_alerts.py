"""OPS-ALERT-001 (alert-webhook): outbound alert delivery, natively built.

The mechanism was learned from Paperclip's outbound-webhook pattern (signed,
HTTPS-only, retryable -- report §2.8) and reimplemented here; this file pins
the native contract:

* detection derives its event set from the frozen transition table
  (``next is ESCALATED``), enqueues deduplicated deliveries and advances a
  resumable cursor;
* delivery signs the exact body (HMAC-SHA256) and posts it -- verified against
  a real local HTTP server, not a mock;
* failures retry across runs until ``max_attempts``, then park as ``failed``;
* the security floor holds: https only (loopback http tolerated), secret
  required, redirects refused.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.alerts import AlertConfigError, AlertNotifier, escalation_events
from app.events.store import EventStore
from tests.claim_support import make_database, seed_project, seed_task

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SECRET = "test-secret"


# ------------------------------------------------------------- local server


class _Catcher(BaseHTTPRequestHandler):
    status_code = 200
    records: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).records.append({"path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(type(self).status_code)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *args) -> None:  # noqa: ANN002 - silence
        pass


@contextmanager
def _serve(status: int = 200):
    _Catcher.status_code = status
    _Catcher.records = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Catcher)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/hook", _Catcher.records
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------- rigs


def _rig(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    return database


def _append_event(database, name: str) -> int:
    record = EventStore(database).append(
        event_name=name,
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="scheduler",
        actor_id="sweeper",
        payload={"why": name},
    )
    return record.seq


def _deliveries(database) -> list[dict]:
    return [
        dict(row)
        for row in database.read("SELECT * FROM alert_deliveries ORDER BY created_at, delivery_id")
    ]


# ------------------------------------------------------------ detection


def test_escalation_events_come_from_the_frozen_table():
    events = escalation_events()

    assert {"merge_timeout", "stuck_threshold_reached", "aging_timeout"} <= events
    assert "claim" not in events and "release" not in events


def test_scan_enqueues_one_delivery_per_escalation_event(tmp_path):
    database = _rig(tmp_path)
    _append_event(database, "claim")  # filtered out, cursor still advances over it
    first = _append_event(database, "merge_timeout")
    second = _append_event(database, "stuck_threshold_reached")
    notifier = AlertNotifier(database, webhook_url="https://example.invalid/hook", secret=SECRET)

    detected = notifier._scan()

    assert detected == 2
    rows = _deliveries(database)
    assert [row["kind"] for row in rows] == ["task.escalated", "task.escalated"]
    assert [row["source_seq"] for row in rows] == [first, second]
    assert all(row["status"] == "pending" for row in rows)
    assert notifier._scan() == 0, "the cursor makes rescanning a no-op"


def test_cursor_resumes_across_instances(tmp_path):
    database = _rig(tmp_path)
    _append_event(database, "merge_timeout")
    with _serve() as (url, records):
        notifier = AlertNotifier(database, webhook_url=url, secret=SECRET)
        notifier.run_once()
        assert len(records) == 1

        resumed = AlertNotifier(database, webhook_url=url, secret=SECRET)
        report = resumed.run_once()
        assert report.detected == 0 and len(records) == 1

        _append_event(database, "aging_timeout")
        report = resumed.run_once()
        assert report.detected == 1 and len(records) == 2


# ------------------------------------------------------------- delivery


def test_delivery_signs_the_exact_body(tmp_path):
    database = _rig(tmp_path)
    _append_event(database, "merge_timeout")
    with _serve() as (url, records):
        report = AlertNotifier(database, webhook_url=url, secret=SECRET).run_once()

    assert report.configured and report.detected == 1 and report.delivered == 1
    (record,) = records
    assert record["path"] == "/hook"
    # Header names are case-insensitive on the wire (urllib normalizes casing).
    headers = {name.lower(): value for name, value in record["headers"].items()}
    expected = hmac.new(SECRET.encode(), record["body"], hashlib.sha256).hexdigest()
    assert headers["x-ap-signature"] == f"sha256={expected}"
    body = json.loads(record["body"])
    assert body["alert"] == "task.escalated"
    assert body["task_id"] == "T-1"
    assert body["payload"]["event_name"] == "merge_timeout"
    assert body["payload"]["payload"] == {"why": "merge_timeout"}
    assert headers["x-ap-delivery"]
    row = _deliveries(database)[0]
    assert row["status"] == "delivered" and row["delivered_at"] is not None


def test_failed_delivery_retries_then_parks(tmp_path):
    database = _rig(tmp_path)
    _append_event(database, "merge_timeout")
    with _serve(status=500) as (url, records):
        notifier = AlertNotifier(database, webhook_url=url, secret=SECRET, max_attempts=3)
        first = notifier.run_once()
        second = notifier.run_once()
        third = notifier.run_once()
        fourth = notifier.run_once()

    assert [first.failed, second.failed, third.failed, fourth.failed] == [1, 1, 1, 0]
    row = _deliveries(database)[0]
    assert row["status"] == "failed" and row["attempts"] == 3
    assert "HTTP 500" in str(row["last_error"])
    assert len(records) == 3, "a parked delivery is not retried again"
    assert fourth.pending == 0


# ------------------------------------------------------------ safety floor


def test_non_loopback_http_is_refused(tmp_path):
    database = _rig(tmp_path)

    with pytest.raises(AlertConfigError):
        AlertNotifier(database, webhook_url="http://example.com/hook", secret=SECRET).run_once()

    with pytest.raises(AlertConfigError):
        AlertNotifier(database, webhook_url="https://example.com/hook", secret="").run_once()

    AlertNotifier(
        database, webhook_url="https://example.com/hook", secret=SECRET
    )._validate_target()
    AlertNotifier(database, webhook_url="http://127.0.0.1:9/hook", secret=SECRET)._validate_target()


def test_unconfigured_notifier_is_an_explicit_noop(tmp_path):
    database = _rig(tmp_path)
    _append_event(database, "merge_timeout")

    report = AlertNotifier(database, webhook_url=None).run_once()

    assert report.as_json() == {
        "configured": False,
        "detected": 0,
        "delivered": 0,
        "failed": 0,
        "pending": 0,
    }
    assert _deliveries(database) == []


# --------------------------------------------------------------------- CLI


def _run_module(*args: str, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, "-m", "app.alerts", *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


def _cli_env(tmp_path) -> dict[str, str]:
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    return env


def test_the_cli_refuses_without_a_database(tmp_path):
    proc = _run_module("--once", env=_cli_env(tmp_path))
    assert proc.returncode == 2
    assert "no database" in proc.stderr


def test_the_cli_noops_unconfigured_and_refuses_bad_targets(tmp_path):
    env = _cli_env(tmp_path)
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

    once = _run_module("--once", env=env)
    assert once.returncode == 0, once.stderr
    assert json.loads(once.stdout.strip())["configured"] is False

    bad = dict(env)
    bad["AP_ALERT_WEBHOOK_URL"] = "http://example.com/hook"
    bad["AP_ALERT_WEBHOOK_SECRET"] = SECRET
    refused = _run_module("--once", env=bad)
    assert refused.returncode == 2
    assert "refused" in refused.stderr


def test_the_cli_delivers_an_escalation_event_end_to_end(tmp_path):
    env = _cli_env(tmp_path)
    subprocess.run(
        [sys.executable, "-m", "app.db"],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=60,
    )
    # Seed one escalation event with raw SQL: the scan reads payload_json only.
    payload = "{}"
    connection = sqlite3.connect(Path(env["AP_DATA_DIR"]) / "state.db")
    connection.execute(
        "INSERT INTO task_events(seq, event_name, aggregate_type, aggregate_id, actor_type,"
        " actor_id, payload_json, payload_hash, schema_version, created_at)"
        " VALUES (1, 'merge_timeout', 'task', 'T-1', 'scheduler', 'sweeper', ?, ?, 1,"
        " '2026-10-08T00:00:00+00:00')",
        (payload, hashlib.sha256(payload.encode()).hexdigest()),
    )
    connection.commit()
    connection.close()

    with _serve() as (url, records):
        configured = dict(env)
        configured["AP_ALERT_WEBHOOK_URL"] = url
        configured["AP_ALERT_WEBHOOK_SECRET"] = SECRET
        first = _run_module("--once", env=configured)
        second = _run_module("--once", env=configured)

    assert first.returncode == 0, first.stderr
    report = json.loads(first.stdout.strip())
    assert report["detected"] == 1 and report["delivered"] == 1
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout.strip())["delivered"] == 0
    assert len(records) == 1
