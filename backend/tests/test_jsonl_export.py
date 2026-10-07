"""CORE-EXPORT-001 (jsonl-export): the async SQLite -> JSONL substrate (R10 §15).

What this file pins:

* ordered, idempotent export with the watermark advancing only after the file
  is atomically replaced;
* the two crash windows the spec names -- failure before the replace (old
  file + watermark intact, no temp residue) and failure after it but before
  the watermark advance (retry trims the uncommitted tail by seq: no duplicate
  and no missing fact);
* the retry queue (status/attempts/last_error) and automatic backfill;
* reconcile() detecting lag, tampering, holes and hash drift;
* the module entry point's exit codes.

Global seq is shared with other streams (messages), so this stream's seqs are
strictly increasing but not necessarily contiguous -- the file is validated
against the database count through the watermark, never against 1..N.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.events.store import EventStore
from app.exporter import JsonlExporter
from app.exporter.service import STREAM_TASK_EVENTS
from tests.claim_support import make_database, seed_project, seed_task

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _append_events(database, count: int) -> list[int]:
    store = EventStore(database)
    seqs: list[int] = []
    for n in range(count):
        record = store.append(
            event_name="human.question",
            aggregate_type="task",
            aggregate_id="T-1",
            actor_type="harness",
            actor_id="h1",
            payload={"n": n},
        )
        seqs.append(record.seq)
    return seqs


def _rig(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")
    exporter = JsonlExporter(database, export_dir=tmp_path / "exports")
    return database, exporter


def _lines(exporter: JsonlExporter) -> list[dict]:
    text = exporter.path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _row(database) -> dict:
    return dict(
        database.read("SELECT * FROM jsonl_exports WHERE stream = ?", (STREAM_TASK_EVENTS,))[0]
    )


# ------------------------------------------------------------------ happy path


def test_export_writes_ordered_lines_and_advances_the_watermark(tmp_path):
    database, exporter = _rig(tmp_path)
    seqs = _append_events(database, 3)

    report = exporter.export_once()

    assert not report.failed and report.records_read == 3
    assert report.exported_seq == seqs[-1] and report.lag == 0
    exported = _lines(exporter)
    assert [item["seq"] for item in exported] == seqs
    assert exported[0]["event_name"] == "human.question"
    assert exported[0]["payload"] == {"n": 0}
    assert exported[0]["payload_hash"] and exported[0]["schema_version"] == 1
    row = _row(database)
    assert row["status"] == "ok" and row["attempts"] == 0 and row["last_error"] is None
    assert row["exported_seq"] == seqs[-1]
    assert row["file_hash"] == __import__("hashlib").sha256(exporter.path.read_bytes()).hexdigest()
    assert exporter.reconcile().ok is True


def test_a_second_run_without_new_events_is_a_no_op(tmp_path):
    database, exporter = _rig(tmp_path)
    _append_events(database, 2)
    exporter.export_once()
    before = exporter.path.read_bytes()

    report = exporter.export_once()

    assert report.records_read == 0 and report.lag == 0 and not report.failed
    assert exporter.path.read_bytes() == before

    _append_events(database, 1)
    report = exporter.export_once()
    assert report.records_read == 1 and report.lag == 0
    assert len(_lines(exporter)) == 3


# ------------------------------------------------------------- crash windows


def test_recovery_trims_the_uncommitted_tail_after_a_crash(tmp_path, monkeypatch):
    database, exporter = _rig(tmp_path)
    _append_events(database, 2)
    exporter.export_once()
    watermark = _row(database)["exported_seq"]

    _append_events(database, 1)
    original = exporter._advance
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before the watermark advance")
        return original(**kwargs)

    monkeypatch.setattr(exporter, "_advance", flaky)
    failed = exporter.export_once()

    assert failed.failed is True
    assert "simulated crash" in str(failed.detail)
    row = _row(database)
    assert row["exported_seq"] == watermark, "the watermark must not advance"
    assert row["status"] == "failed" and row["attempts"] == 1
    assert len(_lines(exporter)) == 3, "the file already carries the uncommitted tail"

    retried = exporter.export_once()

    assert not retried.failed and retried.lag == 0
    seqs = [item["seq"] for item in _lines(exporter)]
    assert len(seqs) == len(set(seqs)) == 3, "no duplicate facts after recovery"
    row = _row(database)
    assert row["status"] == "ok" and row["attempts"] == 0 and row["last_error"] is None
    assert exporter.reconcile().ok is True


def test_failure_before_the_replace_keeps_file_and_watermark_intact(tmp_path, monkeypatch):
    database, exporter = _rig(tmp_path)
    _append_events(database, 2)
    exporter.export_once()
    before = exporter.path.read_bytes()
    watermark = _row(database)["exported_seq"]
    _append_events(database, 1)

    import app.exporter.service as service_module

    def boom(src, dst):  # noqa: ARG001 - signature is the point
        raise OSError("simulated replace failure")

    monkeypatch.setattr(service_module.os, "replace", boom)
    failed = exporter.export_once()
    monkeypatch.undo()

    assert failed.failed is True
    assert exporter.path.read_bytes() == before, "atomicity: the old file stands"
    assert list(exporter.export_dir.glob("*.tmp")) == [], "no temp residue"
    row = _row(database)
    assert row["exported_seq"] == watermark and row["status"] == "failed"

    retried = exporter.export_once()

    assert not retried.failed and retried.lag == 0
    assert len(_lines(exporter)) == 3
    assert _row(database)["status"] == "ok"


def test_export_rebuilds_when_the_file_disagrees_with_the_database(tmp_path):
    database, exporter = _rig(tmp_path)
    _append_events(database, 3)
    exporter.export_once()

    lines = exporter.path.read_text(encoding="utf-8").splitlines()
    exporter.path.write_text("\n".join(lines[:1] + lines[2:]) + "\n", encoding="utf-8")

    report = exporter.export_once()

    assert not report.failed
    assert report.detail is not None and "rebuilt" in report.detail
    assert len(_lines(exporter)) == 3
    assert exporter.reconcile().ok is True


# --------------------------------------------------------------- reconcile


def test_reconcile_flags_lag_tampering_and_partial_tails(tmp_path):
    database, exporter = _rig(tmp_path)
    _append_events(database, 2)
    exporter.export_once()

    assert exporter.reconcile().ok is True

    _append_events(database, 1)
    lagged = exporter.reconcile()
    assert lagged.ok is False
    assert any("lag" in problem for problem in lagged.problems)

    exporter.export_once()
    with exporter.path.open("a", encoding="utf-8") as handle:
        handle.write("junk\n")
    tampered = exporter.reconcile()
    assert tampered.ok is False
    assert any(
        "unparseable" in problem or "beyond watermark" in problem for problem in tampered.problems
    )
    assert tampered.hash_matches is False

    exporter.path.write_text(
        exporter.path.read_text(encoding="utf-8").rstrip("\n"), encoding="utf-8"
    )
    no_tail = exporter.reconcile()
    assert no_tail.ok is False
    assert any("newline" in problem for problem in no_tail.problems)


# --------------------------------------------------------------------- CLI


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


def _cli_env(tmp_path) -> dict[str, str]:
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    return env


def test_the_module_entry_point_refuses_without_a_database(tmp_path):
    proc = _run_module("app.exporter", "--once", env=_cli_env(tmp_path))
    assert proc.returncode == 2
    assert "no database" in proc.stderr


def test_the_module_entry_point_exports_and_checks(tmp_path):
    env = _cli_env(tmp_path)
    prepared = _run_module("app.db", env=env)
    assert prepared.returncode == 0, prepared.stderr

    once = _run_module("app.exporter", "--once", env=env)
    assert once.returncode == 0, once.stderr
    report = json.loads(once.stdout.strip().splitlines()[0])
    assert report["failed"] is False and report["stream"] == STREAM_TASK_EVENTS

    checked = _run_module("app.exporter", "--check", env=env)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout.strip())["ok"] is True

    # Drift the fact source forward behind the exporter's back: the next --check
    # must say so with exit 1, and --once must backfill it.
    import sqlite3

    database_path = Path(env["AP_DATA_DIR"]) / "state.db"
    payload = "{}"
    import hashlib

    connection = sqlite3.connect(database_path)
    connection.execute(
        "INSERT INTO task_events(seq, event_name, aggregate_type, aggregate_id, actor_type,"
        " actor_id, payload_json, payload_hash, schema_version, created_at)"
        " VALUES (9999, 'human.question', 'task', 'T-1', 'harness', 'h1', ?, ?, 1,"
        " '2026-10-08T00:00:00+00:00')",
        (payload, hashlib.sha256(payload.encode()).hexdigest()),
    )
    connection.commit()
    connection.close()

    drifted = _run_module("app.exporter", "--check", env=env)
    assert drifted.returncode == 1
    assert "lag" in drifted.stderr

    backfilled = _run_module("app.exporter", "--once", env=env)
    assert backfilled.returncode == 0, backfilled.stderr
    healed = _run_module("app.exporter", "--check", env=env)
    assert healed.returncode == 0, healed.stderr
