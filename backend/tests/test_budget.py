"""OPS-BUDGET-001 (budget-guard): task token budgets, dispatch-time
reservations, settlement, reconciliation, incidents.

Pinned here, in the card's completion-criteria order:

* the pure policy -- three evidence buckets, admission boundaries (equality
  allowed), inclusive incident thresholds on the **observed** bucket;
* admission: a claim on a budgeted task grants + holds; a claim that cannot
  fit is rejected with ``claim.token_budget_exhausted`` and leaves no claim
  state behind;
* settlement: reported usage becomes the actual; an unreported one stays NULL
  and is counted at the hold amount (never a zero), exactly once;
* reconciliation: a hold whose attempt left the task's active ownership is
  settled by the budget sweep;
* incidents: thresholds open/auto-resolve; alerts queue once per incident and
  never queue when the webhook is unconfigured;
* split: children budgets must fit the parent's unused budget;
* the CLI's exit codes.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.alerts.service import AlertNotifier
from app.budget.policy import (
    BudgetState,
    UsageBuckets,
    admission_blocked,
    classify_observed,
    reservation_amount,
)
from app.budget.service import (
    BudgetGuard,
    committed_buckets,
    settle_attempt,
    task_budget,
    usage_tokens,
)
from app.claim.service import ClaimRequest, ClaimService, RejectCode
from app.config import settings
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.scheduler import sweep_budget, sweep_expired_leases, sweep_resources
from app.tasks.service import Conflict, SchedulerService
from tests.claim_support import hold_resource, make_database, seed_project, seed_task

BACKEND_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime.now(UTC)


# ------------------------------------------------------------------- policy


def test_buckets_and_admission_boundaries():
    buckets = UsageBuckets(settled_tokens=30, unreported_tokens=20, held_tokens=50)
    assert buckets.observed == 50
    assert buckets.committed == 100

    assert admission_blocked(committed=99, amount=1, limit=100) is False
    assert admission_blocked(committed=100, amount=0, limit=100) is True
    assert admission_blocked(committed=60, amount=41, limit=100) is True
    assert admission_blocked(committed=60, amount=40, limit=100) is False
    assert admission_blocked(committed=10**9, amount=10**9, limit=0) is False


def test_reservation_amount_is_capped_at_the_limit():
    assert reservation_amount(50_000, 100_000) == 50_000
    assert reservation_amount(50_000, 30_000) == 30_000
    assert reservation_amount(0, 30_000) == 0
    assert reservation_amount(50_000, 0) == 0


def test_classify_observed_boundaries_are_inclusive():
    assert classify_observed(observed=79, limit=100, warn_percent=80) is BudgetState.OK
    assert classify_observed(observed=80, limit=100, warn_percent=80) is BudgetState.WARNING
    assert classify_observed(observed=99, limit=100, warn_percent=80) is BudgetState.WARNING
    assert classify_observed(observed=100, limit=100, warn_percent=80) is BudgetState.EXHAUSTED
    assert classify_observed(observed=0, limit=0, warn_percent=80) is BudgetState.OK


def test_usage_tokens_only_believes_the_harness():
    assert usage_tokens({}) is None
    assert usage_tokens({"harness_output": {}}) is None
    assert usage_tokens({"harness_output": {"usage": {}}}) is None
    assert (
        usage_tokens({"harness_output": {"usage": {"input_tokens": 7}}}) is None
    ), "a partial report is treated as unreported, not as seven"
    assert (
        usage_tokens({"harness_output": {"usage": {"input_tokens": 7, "output_tokens": 3}}}) == 10
    )
    assert usage_tokens({"harness_output": {"usage": {"total_tokens": 42}}}) == 42
    assert usage_tokens({"harness_output": {"usage": {"total_tokens": -1}}}) is None
    assert usage_tokens({"harness_output": {"usage": {"total_tokens": True}}}) is None


# ---------------------------------------------------------------------- rigs


def _claim(database, *, request_id="cr-1", task_id="T-1"):
    return (
        ClaimService(database)
        .claim(
            ClaimRequest(claim_request_id=request_id, harness_id="harness-1", task_ids=(task_id,))
        )
        .items[0]
    )


def _budget_rig(tmp_path, *, budget_tokens: int | None = 100_000):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", budget_tokens=budget_tokens)
    return database


def _worktree_rig(tmp_path, *, budget_tokens: int | None = 100_000):
    """claim -> start = in_progress with a real worktree (state-coverage rig)."""
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    seed_project(database, integration_commit=base)
    seed_task(database, task_id="T-1", budget_tokens=budget_tokens)
    item = _claim(database)
    assert item.outcome == "claimed"
    service.start_task(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=manager,
    )
    return database, service, item


def _reservation_rows(database) -> list[dict[str, object]]:
    return [dict(row) for row in database.read("SELECT * FROM budget_reservations ORDER BY rowid")]


def _incidents(database) -> list[dict[str, object]]:
    return [dict(row) for row in database.read("SELECT * FROM budget_incidents ORDER BY rowid")]


# ------------------------------------------------------------------- admission


def test_claim_on_a_budgeted_task_grants_and_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database = _budget_rig(tmp_path)

    item = _claim(database)
    assert item.outcome == "claimed"
    rows = _reservation_rows(database)
    assert len(rows) == 1
    assert rows[0]["attempt_id"] == "T-1.a1"
    assert rows[0]["state"] == "held"
    assert rows[0]["amount_tokens"] == 50_000

    replay = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    assert replay.outcome == "claimed"
    assert len(_reservation_rows(database)) == 1, "a replay never double-reserves"


def test_claim_is_refused_when_budget_cannot_admit_and_writes_no_claim_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 30_000)
    database = _budget_rig(tmp_path)
    with database.transaction() as conn:
        # Preload accountable history: a settled attempt that reported 90k.
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
            " fencing_epoch, base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a0', 'T-1', 0, 'harness-0', 0, NULL, NULL, NULL, 'verifying', ?)",
            (NOW.isoformat(),),
        )
        conn.execute(
            "INSERT INTO budget_reservations(reservation_id, task_id, attempt_id,"
            " amount_tokens, state, actual_tokens, source, note, created_at, settled_at)"
            " VALUES ('br-old', 'T-1', 'T-1.a0', 30000, 'settled', 90000, 'claim', NULL, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        task_row = conn.execute("UPDATE tasks SET attempt_no = 1 WHERE task_id = 'T-1'").rowcount
        assert task_row == 1

    item = (
        ClaimService(database)
        .claim(
            ClaimRequest(
                claim_request_id="cr-2",
                harness_id="harness-1",
                task_ids=("T-1",),
                partial_allowed=True,
            )
        )
        .items[0]
    )
    assert item.outcome == "rejected"
    assert item.error_code == RejectCode.TOKEN_BUDGET_EXHAUSTED.value

    # The per-item row is kept (R10 §11), carrying the rejection code -- but
    # no claim event, no attempt, no hold and no ownership: the item never
    # reached an attempt.
    rows = [dict(row) for row in database.read("SELECT * FROM task_claims WHERE task_id = 'T-1'")]
    assert len(rows) == 1
    assert rows[0]["attempt_id"] is None and rows[0]["owner"] is None
    assert rows[0]["release_reason"] == f"rejected:{RejectCode.TOKEN_BUDGET_EXHAUSTED.value}"
    assert not database.read(
        "SELECT 1 FROM task_events WHERE event_name = 'claim' AND aggregate_id = 'T-1'"
    )
    assert not database.read("SELECT 1 FROM task_attempts WHERE task_id = 'T-1' AND attempt_no = 2")
    assert len(_reservation_rows(database)) == 1, "denial writes no new hold"
    assert database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"] == "ready"

    # Observed is 90k >= 80k warn but < 100k limit -> a warning incident, not
    # an exhausted one (Paperclip: thresholds are observed-based).
    incidents = _incidents(database)
    assert [row["threshold"] for row in incidents] == ["warning"]


def test_tasks_without_a_budget_are_untouched(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1")  # no snapshot at all
    item = _claim(database)
    assert item.outcome == "claimed"
    assert _reservation_rows(database) == []

    zero = tmp_path / "zero"
    zero.mkdir()
    database = _budget_rig(zero, budget_tokens=0)  # declared but not enforced
    assert _claim(database).outcome == "claimed"
    assert _reservation_rows(database) == []


def test_task_budget_reads_the_plan_snapshot(tmp_path):
    database = _budget_rig(tmp_path, budget_tokens=12_345)
    with database.transaction() as conn:
        assert task_budget(conn, "T-1") == 12_345
        assert task_budget(conn, "missing") is None


def test_deferred_resume_rechecks_the_budget(tmp_path, monkeypatch):
    """A deferred claim is a real dispatch: the hold is re-checked at resume."""
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", budget_tokens=60_000, exclusive_resources=["port:8188"])
    hold_resource(
        database,
        "port:8188",
        attempt_id="T-0.a1",
        # The hold must be valid **now**: the module-level NOW is captured at
        # import time, and a long suite can be more than ten minutes past it
        # -- then the hold would have expired and the claim would be granted
        # instead of deferred, for a reason that has nothing to do with the
        # budget.
        lease_until=(datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
    )

    # Budget passes, the resource blocks -> queued without any hold.
    queued = (
        ClaimService(database)
        .claim(
            ClaimRequest(
                claim_request_id="cr-1",
                harness_id="harness-1",
                task_ids=("T-1",),
                partial_allowed=True,
            )
        )
        .items[0]
    )
    assert queued.outcome == "queued"
    assert _reservation_rows(database) == []

    # History arrives while queued: 50k accounted of a 60k budget.
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
            " fencing_epoch, base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a0', 'T-1', 0, 'harness-0', 0, NULL, NULL, NULL, 'verifying', ?)",
            (NOW.isoformat(),),
        )
        conn.execute(
            "INSERT INTO budget_reservations(reservation_id, task_id, attempt_id,"
            " amount_tokens, state, actual_tokens, source, note, created_at, settled_at)"
            " VALUES ('br-old', 'T-1', 'T-1.a0', 50000, 'settled', 50000, 'claim', NULL, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
        conn.execute(
            "UPDATE resource_leases SET released_at = 'gone' WHERE resource_id = 'port:8188'"
        )

    report = sweep_resources(database, NOW)
    assert report.actions == 0, "the resume must stay queued: 50k + 50k > 60k"
    task = database.read("SELECT status, current_attempt_id FROM tasks WHERE task_id = 'T-1'")[0]
    assert task["status"] == "queued" and task["current_attempt_id"] is None
    assert not database.read("SELECT 1 FROM task_attempts WHERE attempt_id = 'T-1.a1'")
    assert len(_reservation_rows(database)) == 1, "no hold for a resume that never granted"
    assert [row["threshold"] for row in _incidents(database)] == ["warning"]


# ------------------------------------------------------------------ settlement


def test_submit_settles_the_hold_with_reported_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database, service, item = _worktree_rig(tmp_path)

    service.submit_result(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        completion={
            "commit": "c-1",
            "harness_output": {"usage": {"input_tokens": 30_000, "output_tokens": 5_000}},
        },
    )

    rows = _reservation_rows(database)
    assert len(rows) == 1
    assert rows[0]["state"] == "settled"
    assert rows[0]["actual_tokens"] == 35_000
    with database.transaction() as conn:
        buckets = committed_buckets(conn, "T-1")
    assert buckets.settled_tokens == 35_000
    assert buckets.held_tokens == 0

    # Settling twice is a no-op (replay cannot double-count).
    with database.transaction() as conn:
        assert (
            settle_attempt(
                conn,
                task_id="T-1",
                attempt_id=item.attempt_id,
                actual_tokens=35_000,
                now=NOW,
            )
            is False
        )
    assert _reservation_rows(database)[0]["actual_tokens"] == 35_000


def test_submit_without_usage_settles_unreported_not_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database, service, item = _worktree_rig(tmp_path)

    service.submit_result(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        completion={"commit": "c-1"},
    )

    rows = _reservation_rows(database)
    assert rows[0]["state"] == "settled"
    assert rows[0]["actual_tokens"] is None, "no report, no invented zero"
    with database.transaction() as conn:
        buckets = committed_buckets(conn, "T-1")
    assert buckets.unreported_tokens == 50_000, "conservative count at the hold"
    assert buckets.committed == 50_000


def test_threshold_incidents_open_on_settle_and_alert_once(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 10_000)
    database, service, item = _worktree_rig(tmp_path)
    service.submit_result(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        completion={
            "commit": "c-1",
            "harness_output": {"usage": {"total_tokens": 85_000}},
        },
    )
    incidents = _incidents(database)
    assert [(row["threshold"], row["status"]) for row in incidents] == [("warning", "open")]
    assert incidents[0]["observed_tokens"] == 85_000

    notifier = AlertNotifier(
        database, webhook_url="https://hooks.example.invalid/alert", secret="s3cret"
    )
    guard = BudgetGuard(database, notifier=notifier)
    first = guard.scan(now=NOW)
    assert first.alerts_queued == 1
    assert first.incidents_opened == 0, "already open; scan must not duplicate"
    queued = database.read("SELECT kind, source_key FROM alert_deliveries")
    assert [dict(row) for row in queued] == [
        {"kind": "budget.warning", "source_key": f"budget:{incidents[0]['incident_id']}"}
    ]
    assert guard.scan(now=NOW).alerts_queued == 0, "one alert per incident, ever"


def test_unconfigured_webhook_keeps_incidents_but_queues_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 10_000)
    database, service, item = _worktree_rig(tmp_path)
    service.submit_result(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        completion={
            "commit": "c-1",
            "harness_output": {"usage": {"total_tokens": 85_000}},
        },
    )
    report = BudgetGuard(database, notifier=AlertNotifier(database)).scan(now=NOW)
    assert report.alerts_queued == 0
    assert len(_incidents(database)) == 1
    assert database.read("SELECT COUNT(*) AS n FROM alert_deliveries")[0]["n"] == 0


# ----------------------------------------------------------------- reconciliation


def test_sweep_reconciles_holds_of_attempts_that_left_active_ownership(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database = _budget_rig(tmp_path)
    item = _claim(database)
    assert _reservation_rows(database)[0]["state"] == "held"
    assert item.attempt_id == "T-1.a1"

    # The worker dies without a submit: the lease expires (R10 §10/§13), the
    # task leaves active ownership, and the same round's budget sweep closes
    # the hold -- closure without a report stays unknown, never zero. The
    # sweep clock must clear the lease's *grant* clock (real time at claim),
    # not the import clock, hence the hour.
    after = NOW + timedelta(hours=1)
    sweep_expired_leases(database, after)
    report = sweep_budget(database, after)
    assert report.detail["held_reconciled"] == 1
    row = _reservation_rows(database)[0]
    assert row["state"] == "settled"
    assert row["actual_tokens"] is None, "closure without a report stays unknown"
    assert "reconciled" in str(row["note"])

    # Idempotent: a second round has nothing to reconcile.
    assert sweep_budget(database, after).actions == 0


def test_sweep_leaves_active_attempts_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database = _budget_rig(tmp_path)
    _claim(database)

    assert sweep_budget(database, NOW).actions == 0
    assert _reservation_rows(database)[0]["state"] == "held"


def test_release_drops_the_hold_and_scan_auto_resolves_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 80_000)
    database = _budget_rig(tmp_path, budget_tokens=100_000)
    item = _claim(database)  # held 80k; observed 0 -> no incident yet
    guard = BudgetGuard(database)

    # A reported settlement of 85k crosses the soft threshold.
    with database.transaction() as conn:
        conn.execute(
            "UPDATE budget_reservations SET state = 'settled', actual_tokens = 85000,"
            " settled_at = ? WHERE attempt_id = ?",
            (NOW.isoformat(), item.attempt_id),
        )
    guard.scan(now=NOW)
    assert [(row["threshold"], row["status"]) for row in _incidents(database)] == [
        ("warning", "open")
    ]

    # Human correction: release the hold (or raise the budget) -> resolved.
    assert guard.resolve("T-1", note="accepted", resolved_by="cli") == 1
    assert [(row["threshold"], row["status"]) for row in _incidents(database)] == [
        ("warning", "resolved")
    ]

    # And the exhausted boundary: actual >= limit opens exhausted.
    with database.transaction() as conn:
        conn.execute(
            "UPDATE budget_reservations SET actual_tokens = 100000 WHERE attempt_id = ?",
            (item.attempt_id,),
        )
    guard.scan(now=NOW)
    thresholds = {(row["threshold"], row["status"]) for row in _incidents(database)}
    assert ("exhausted", "open") in thresholds


# ----------------------------------------------------------------------- split


def _split_rig(tmp_path, *, parent_budget: int | None = 100_000):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status="in_progress", budget_tokens=parent_budget)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET owner = 'harness-1', current_attempt_id = 'T-1.a1',"
            " current_epoch = 1, attempt_no = 1 WHERE task_id = 'T-1'"
        )
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a1', 'T-1', 1, 'harness-1', 1, NULL, NULL, NULL, 'in_progress', ?)",
            (NOW.isoformat(),),
        )
        conn.execute(
            "INSERT INTO budget_reservations(reservation_id, task_id, attempt_id,"
            " amount_tokens, state, actual_tokens, source, note, created_at, settled_at)"
            " VALUES ('br-1', 'T-1', 'T-1.a1', 50000, 'settled', 50000, 'claim', NULL, ?, ?)",
            (NOW.isoformat(), NOW.isoformat()),
        )
    return database


def _split(database, child_specs, *, key="sp-1"):
    return SchedulerService(database).split_task(
        task_id="T-1",
        child_specs=child_specs,
        idempotency_key=key,
        attempt_id="T-1.a1",
        fencing_epoch=1,
    )


def test_split_refuses_children_over_the_parents_remaining_budget(tmp_path):
    database = _split_rig(tmp_path)  # 100k limit, 50k committed -> 50k remaining

    with pytest.raises(Conflict, match="exceeds parent"):
        _split(
            database,
            [{"id": "C-1", "budget_tokens": 40_000}, {"id": "C-2", "budget_tokens": 20_000}],
        )
    assert not database.read("SELECT 1 FROM tasks WHERE task_id LIKE 'C-%'")
    assert not database.read("SELECT 1 FROM plans WHERE plan_id LIKE 'plan-split-%'")

    split = _split(
        database, [{"id": "C-1", "budget_tokens": 30_000}, {"id": "C-2", "budget_tokens": 20_000}]
    )
    assert split["child_ids"] == ["C-1", "C-2"], "equality fits"
    assert len(database.read("SELECT 1 FROM tasks WHERE task_id LIKE 'C-%'")) == 2


def test_split_ignores_budgets_on_unenforced_parents(tmp_path):
    database = _split_rig(tmp_path, parent_budget=None)
    split = _split(database, [{"id": "C-1", "budget_tokens": 1_000_000}])
    assert split["child_ids"] == ["C-1"]


# ------------------------------------------------------------------------- CLI


def _run_module(*args: str, env: dict[str, str]):
    return subprocess.run(
        [sys.executable, "-m", "app.budget", *args],
        cwd=BACKEND_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=60,
    )


def _init_database(tmp_path) -> tuple[dict[str, str], Path]:
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
    return env, Path(env["AP_DATA_DIR"]) / "state.db"


def _seed_budget_state(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    connection.execute("INSERT INTO projects(project_id, name, created_at) VALUES ('P-X','x','t')")
    connection.execute(
        "INSERT INTO groups(group_id, project_id, name, created_at) VALUES ('G-X','P-X','g','t')"
    )
    connection.execute(
        "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
        " VALUES ('plan-X','G-X','approved','h','t')"
    )
    snapshot = json.dumps({"plan_version": "v1", "tasks": [{"id": "T-X", "budget_tokens": 1000}]})
    connection.execute(
        "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash, content_json,"
        " created_at) VALUES ('snap-X','plan-X','h',?,'t')",
        (snapshot,),
    )
    connection.execute(
        "INSERT INTO tasks(task_id, group_id, plan_snapshot_id, status, created_at, updated_at)"
        " VALUES ('T-X','G-X','snap-X','ready','t','t')"
    )
    connection.execute(
        "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
        " base_commit, base_locked_seq, head_commit, status, created_at)"
        " VALUES ('T-X.a1','T-X',1,'harness-1',1,NULL,NULL,NULL,'claimed','t')"
    )
    connection.execute(
        "INSERT INTO budget_reservations(reservation_id, task_id, attempt_id, amount_tokens,"
        " state, actual_tokens, source, note, created_at, settled_at)"
        " VALUES ('br-x','T-X','T-X.a1',500,'settled',900,'claim',NULL,'t','t')"
    )
    connection.execute(
        "INSERT INTO budget_incidents(incident_id, task_id, threshold, observed_tokens,"
        " limit_tokens, status, note, created_at, resolved_at, resolved_by)"
        " VALUES ('bi-x','T-X','warning',900,1000,'open',NULL,'t',NULL,NULL)"
    )
    connection.commit()
    connection.close()


def test_the_cli_refuses_without_a_database(tmp_path):
    env = dict(os.environ)
    env["AP_DATA_DIR"] = str(tmp_path / "data")
    proc = _run_module("--check", env=env)
    assert proc.returncode == 2
    assert "no database" in proc.stderr


def test_the_cli_check_and_human_verbs(tmp_path):
    env, database_path = _init_database(tmp_path)

    clean = _run_module("--check", env=env)
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout.strip()) == {"tasks": [], "open_incidents": 0}

    _seed_budget_state(database_path)
    flagged = _run_module("--check", env=env)
    assert flagged.returncode == 1
    report = json.loads(flagged.stdout.strip())
    assert report["open_incidents"] == 1
    assert report["tasks"][0]["state"] == "warning"
    assert report["tasks"][0]["settled_tokens"] == 900

    resolved = _run_module("--resolve", "T-X", "--note", "raised", env=env)
    assert resolved.returncode == 0
    assert json.loads(resolved.stdout.strip())["resolved"] == 1
    assert _run_module("--check", env=env).returncode == 0

    missing = _run_module("--release", "T-X.a1", env=env)
    assert missing.returncode == 2, "no held row -> nothing to release"
    settled = _run_module("--settle", "T-X.a1", "--tokens", "1234", env=env)
    assert settled.returncode == 2, "already settled -> nothing to settle"
