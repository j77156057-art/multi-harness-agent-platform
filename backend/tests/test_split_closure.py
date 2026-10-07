"""CORE-SPLIT-001 (split-closure): the split lifecycle runs to completion.

What this file pins, end to end:

* split materialises its children through a draft **child plan**; the standard
  ``plan.approve`` moves them ``draft -> ready`` (R10 §10's only entry);
* the dependency sweep wakes a split parent whose children all merged
  (``blocked / all_children_merged``), and refuses to fabricate that wake for
  owner-reported blockers or unfinished children;
* ``task_dependencies`` rows sit in R10 §10 L587's direction (the parent
  column waits on the child column) -- the invariant ``_materialise_task``
  had inverted, untested, before this task;
* the liveness nets do not kill a healthy split (the stuck fingerprint follows
  the children's activity; the blocked SLA skips split parents), while silent
  children still escalate.

Rigs seed the owned states directly -- producers live elsewhere -- and every
other hop goes through a real entry point.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.scheduler import sweep_blocked_sla, sweep_dependencies, sweep_stuck
from app.tasks.service import InvalidInput, SchedulerService
from tests.claim_support import make_database, seed_project, seed_task

NOW = datetime.now(UTC)


# ----------------------------------------------------------------------- rigs


def _rig(tmp_path, *, status: str = "in_progress"):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=status)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET owner = 'harness-1', current_attempt_id = 'T-1.a1',"
            " current_epoch = 1 WHERE task_id = 'T-1'"
        )
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a1', 'T-1', 1, 'harness-1', 1, NULL, NULL, NULL, ?, ?)",
            (status, NOW.isoformat()),
        )
    return database


def _split(database, *, key: str = "sp-1") -> dict:
    return SchedulerService(database).split_task(
        task_id="T-1",
        child_specs=[{"id": "C-1"}, {"id": "C-2"}],
        idempotency_key=key,
        attempt_id="T-1.a1",
        fencing_epoch=1,
    )


def _status(database, task_id: str) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


def _set_status(database, task_id: str, status: str) -> None:
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))


def _event_names(database, task_id: str) -> list[str]:
    rows = database.read(
        "SELECT event_name FROM task_events WHERE aggregate_id = ? ORDER BY seq", (task_id,)
    )
    return [str(row["event_name"]) for row in rows]


# ------------------------------------------------------- child plan lifecycle


def test_split_materialises_children_through_a_draft_child_plan(tmp_path):
    database = _rig(tmp_path)

    result = _split(database)

    child_plan_id = str(result["child_plan_id"])
    child_snapshot_id = str(result["child_snapshot_id"])
    (plan,) = database.read("SELECT * FROM plans WHERE plan_id = ?", (child_plan_id,))
    assert str(plan["status"]) == "draft"
    (snapshot,) = database.read(
        "SELECT * FROM plan_snapshots WHERE snapshot_id = ?", (child_snapshot_id,)
    )
    assert str(snapshot["snapshot_hash"]) == str(plan["plan_hash"]), "the drift invariant holds"
    assert [item["id"] for item in json.loads(str(snapshot["content_json"]))["tasks"]] == [
        "C-1",
        "C-2",
    ]
    children = database.read(
        "SELECT task_id, status, plan_snapshot_id FROM tasks WHERE task_id LIKE 'C-%'"
    )
    assert all(str(row["plan_snapshot_id"]) == child_snapshot_id for row in children)
    assert all(str(row["status"]) == "draft" for row in children)

    outcome = SchedulerService(database).approve_plan(plan_id=child_plan_id, decided_by="ops")

    assert outcome.ready == ("C-1", "C-2") and outcome.waiting == ()
    assert _status(database, "C-1") == "ready" and _status(database, "C-2") == "ready"

    replay = _split(database)
    assert replay["replayed"] is True and replay["child_plan_id"] == child_plan_id
    count = database.read("SELECT COUNT(*) AS n FROM plans WHERE plan_id LIKE 'plan-split-%'")[0][
        "n"
    ]
    assert count == 1, "a replay must not mint a second child plan"


def test_split_refuses_child_specs_the_plan_validator_rejects(tmp_path):
    database = _rig(tmp_path)

    with pytest.raises(InvalidInput, match="unknown task"):
        SchedulerService(database).split_task(
            task_id="T-1",
            child_specs=[{"id": "C-1", "depends_on": ["ghost"]}],
            idempotency_key="sp-bad",
            attempt_id="T-1.a1",
            fencing_epoch=1,
        )

    assert _event_names(database, "T-1") == []
    assert not database.read("SELECT 1 FROM plans WHERE plan_id LIKE 'plan-split-%'")
    assert not database.read("SELECT 1 FROM tasks WHERE task_id = 'C-1'")


# ------------------------------------------------------------- the wake edge


def test_all_children_merged_wakes_the_parent(tmp_path):
    database = _rig(tmp_path)
    result = _split(database)
    SchedulerService(database).approve_plan(plan_id=str(result["child_plan_id"]), decided_by="ops")
    parent = database.read("SELECT status, waiting_since FROM tasks WHERE task_id = 'T-1'")[0]
    assert str(parent["status"]) == "blocked" and parent["waiting_since"] is not None

    _set_status(database, "C-1", "merged")
    assert sweep_dependencies(database, NOW).detail["unblocked"] == [], "one child is still open"

    _set_status(database, "C-2", "merged")
    report = sweep_dependencies(database, NOW)

    assert report.detail["unblocked"] == ["T-1"]
    assert _status(database, "T-1") == "ready"
    after = database.read("SELECT waiting_since FROM tasks WHERE task_id = 'T-1'")[0]
    assert after["waiting_since"] is None, "R10: the wake clears the wait clock"
    assert _event_names(database, "T-1").count("all_children_merged") == 1
    assert sweep_dependencies(database, NOW).actions == 0, "idempotent second round"


def test_an_owner_reported_blocker_is_never_lifted_by_merged_upstreams(tmp_path):
    database = _rig(tmp_path)
    service = SchedulerService(database)
    seed_task(database, task_id="T-9", status="merged")
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_dependencies(parent_task_id, child_task_id, relation)"
            " VALUES ('T-1', 'T-9', 'depends_on')"
        )
    service.report_blocked(
        task_id="T-1", attempt_id="T-1.a1", fencing_epoch=1, blocker_ref="ext:maintenance"
    )
    assert _status(database, "T-1") == "blocked"

    report = sweep_dependencies(database, NOW)

    assert report.detail["unblocked"] == [], "dependency_missing is not a split parent"
    assert _status(database, "T-1") == "blocked"
    assert "all_children_merged" not in _event_names(database, "T-1")


# --------------------------------------------------- the direction regression


def test_plan_dependencies_sit_in_the_r10_direction_and_wake(tmp_path):
    """R10 §10 L587: the parent column is the waiting task. The old
    ``_materialise_task`` wrote the pair inverted (and untested), so the wait
    could never be satisfied and block_timeout escalated it instead."""
    database = make_database(tmp_path)
    seed_project(database)
    service = SchedulerService(database)
    service.create_plan(
        plan_id="PL-1",
        group_id="G-1",
        snapshot={"tasks": [{"id": "T-A"}, {"id": "T-B", "depends_on": ["T-A"]}]},
    )
    outcome = service.approve_plan(plan_id="PL-1", decided_by="ops")

    assert outcome.ready == ("T-A",) and outcome.waiting == ("T-B",)
    rows = database.read("SELECT parent_task_id, child_task_id FROM task_dependencies")
    assert [(str(row["parent_task_id"]), str(row["child_task_id"])) for row in rows] == [
        ("T-B", "T-A")
    ]

    _set_status(database, "T-A", "merged")
    report = sweep_dependencies(database, NOW)

    assert report.detail["satisfied"] == ["T-B"]
    assert _status(database, "T-B") == "ready"


# ------------------------------------------------------------ liveness nets


def test_a_healthy_split_is_not_killed_by_the_liveness_nets(tmp_path):
    database = _rig(tmp_path)
    result = _split(database)
    SchedulerService(database).approve_plan(plan_id=str(result["child_plan_id"]), decided_by="ops")

    for round_number, progress in enumerate(("p1", "p2", "p3", "p4")):
        with database.transaction() as conn:
            conn.execute("UPDATE tasks SET progress_hash = ? WHERE task_id = 'C-1'", (progress,))
        sweep_stuck(database, NOW + timedelta(minutes=round_number))

    # Four rounds: without child activity in the fingerprint this would be the
    # third consecutive no-change sighting and the parent would escalate.
    assert _status(database, "T-1") == "blocked", "children progressing keeps the parent alive"
    assert "stuck_threshold_reached" not in _event_names(database, "T-1")
    assert (
        sweep_blocked_sla(database, NOW + timedelta(hours=2)).actions == 0
    ), "the block SLA must not escalate a split parent"


def test_silent_children_still_escalate_the_split_parent(tmp_path):
    database = _rig(tmp_path)
    _split(database)

    for round_number in range(4):
        sweep_stuck(database, NOW + timedelta(minutes=round_number))

    assert _status(database, "T-1") == "escalated", "dead-end children keep the old出口"
    assert "stuck_threshold_reached" in _event_names(database, "T-1")
