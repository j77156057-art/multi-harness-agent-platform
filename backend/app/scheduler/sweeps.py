"""The individual sweeps of the family (design §2).

Each sweep is a pure function ``(database, now) -> SweepReport``: it reads
time/condition columns, applies at most one conditional transition per match
(via the owning service or :func:`apply_transition`, never by hand), and
reports what it did. ``now`` is always injected so tests can move the clock
instead of waiting for it.

Implemented so far, in the runner's fixed order:

* ``sweeps.expired_leases`` (sweeper-runner): the family's prototype.
* ``sweeps.dependencies`` (sweeper-deps-resources, design §2.1): the three
  exits of ``ready_waiting_deps``.
* ``sweeps.resources`` (sweeper-deps-resources, design §2.2): completes a
  resource-deferred claim once capacity allows.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from app.claim.service import QUEUE_REASON_RESOURCE, ClaimService
from app.claim.transitions import apply_transition
from app.config import settings
from app.db.connection import Database
from app.events.store import EventStore
from app.lease.service import expire_stale_leases, parse_iso
from app.scheduler.report import SweepReport
from app.spec import EventKind, TaskStatus

#: Upstream states that will never produce a merged output (R10 §10's
#: ``orphan_detected`` parenthetical, verbatim).
_DEAD_UPSTREAM_STATES = frozenset(
    {
        TaskStatus.CANCELLED.value,
        TaskStatus.ABANDONED.value,
        TaskStatus.DRAFT_REJECTED.value,
    }
)


def sweep_expired_leases(database: Database, now: datetime) -> SweepReport:
    """Owner leases past their deadline: lease freed, owners' tasks → ``stale``.

    R10 §10's ``lease_expire`` edge and R10 §13's "renewal rejection means out
    of contact". The transition logic stays in ``app/lease`` — this function
    only adapts it to the runner's report shape.
    """
    transitioned = expire_stale_leases(database, now=now)
    return SweepReport(
        sweep="expired_leases",
        actions=len(transitioned),
        detail={"tasks": transitioned},
    )


def sweep_dependencies(database: Database, now: datetime) -> SweepReport:
    """Exit ``ready_waiting_deps`` (design §2.1, guards quoted from R10 §10).

    Order of judgement per task: all upstreams merged → ``dependencies_satisfied``;
    else all upstreams dead-ended → ``orphan_detected``; else waited past
    2× the upstreams' expected durations → ``block_timeout``; else stay.

    The output-hash half of the ``dependencies_satisfied`` guard is recorded,
    not implemented: the MVP has no output registry (proposal §7).

    ``ready_waiting_deps`` deliberately has no aging edge (R10 §10 note), so
    this sweep is its only time-driven exit besides the two escalations.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id, waiting_since, plan_snapshot_id FROM tasks WHERE status = ?",
        (TaskStatus.READY_WAITING_DEPS.value,),
    )
    satisfied: list[str] = []
    orphaned: list[str] = []
    timed_out: list[str] = []
    for row in rows:
        task_id = str(row["task_id"])
        upstream = database.read(
            "SELECT td.child_task_id AS upstream_id, t.status AS status"
            "  FROM task_dependencies td"
            "  LEFT JOIN tasks t ON t.task_id = td.child_task_id"
            " WHERE td.parent_task_id = ?",
            (task_id,),
        )
        statuses = [str(item["status"]) for item in upstream]
        all_merged = bool(statuses) and all(
            status == TaskStatus.MERGED.value for status in statuses
        )
        if all_merged:
            if _apply(
                database,
                store,
                task_id=task_id,
                expected=TaskStatus.READY_WAITING_DEPS,
                event=EventKind.DEPENDENCIES_SATISFIED,
                payload={"upstreams": [str(item["upstream_id"]) for item in upstream]},
                extra_sets={"waiting_since": None},
            ):
                satisfied.append(task_id)
            continue
        if statuses and all(
            status in _DEAD_UPSTREAM_STATES or status == TaskStatus.MERGED.value
            for status in statuses
        ):
            # All upstreams are finished forever, and not all of them merged:
            # waiting longer cannot help (R10 §10 orphan_detected).
            if _apply(
                database,
                store,
                task_id=task_id,
                expected=TaskStatus.READY_WAITING_DEPS,
                event=EventKind.ORPHAN_DETECTED,
                payload={"upstreams": [str(item["upstream_id"]) for item in upstream]},
                extra_sets={"escalated_at": now.isoformat()},
            ):
                orphaned.append(task_id)
            continue
        if _waited_past_block_threshold(database, row, now) and _apply(
            database,
            store,
            task_id=task_id,
            expected=TaskStatus.READY_WAITING_DEPS,
            event=EventKind.BLOCK_TIMEOUT,
            payload={"waited_seconds": _waited_seconds(row["waiting_since"], now)},
            extra_sets={"escalated_at": now.isoformat()},
        ):
            timed_out.append(task_id)
    return SweepReport(
        sweep="dependencies",
        actions=len(satisfied) + len(orphaned) + len(timed_out),
        detail={"satisfied": satisfied, "orphaned": orphaned, "block_timeout": timed_out},
    )


def sweep_resources(database: Database, now: datetime) -> SweepReport:
    """Complete resource-deferred claims once capacity allows (design §2.2).

    The recovery half of ``claim_deferred_resource``: for every ``queued``
    resource deferral the claim service re-checks the resource blocker and, in
    one transaction, reserves the resource, opens the attempt and takes
    ownership (``resource_available`` → ``claimed``). Idempotent by
    construction: a second round finds no queued task and does nothing.
    """
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ? AND queue_reason = ?",
        (TaskStatus.QUEUED.value, QUEUE_REASON_RESOURCE),
    )
    service = ClaimService(database)
    resumed: list[str] = []
    still_held: list[str] = []
    for row in rows:
        task_id = str(row["task_id"])
        item = service.resume_deferred(task_id, now=now)
        if item is None:
            still_held.append(task_id)
        else:
            resumed.append(task_id)
    return SweepReport(
        sweep="resources",
        actions=len(resumed),
        detail={"claimed": resumed, "held": still_held},
    )


# ------------------------------------------------------------------ helpers


def _apply(
    database: Database,
    store: EventStore,
    *,
    task_id: str,
    expected: TaskStatus,
    event: EventKind,
    payload: dict[str, Any],
    extra_sets: dict[str, object] | None = None,
) -> bool:
    """Append the safety event and transition, re-checked inside the transaction.

    The row was read before the transaction; between then and now a heartbeat,
    submission or another sweep may have moved it. Re-reading under
    ``BEGIN IMMEDIATE`` and matching the status is what makes a stale decision
    a no-op instead of a double transition.
    """
    with database.transaction() as conn:
        task = conn.execute(
            "SELECT status, current_epoch FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if task is None or TaskStatus(str(task["status"])) is not expected:
            return False
        record = store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload=payload,
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=expected,
            event=event,
            expected_epoch=int(task["current_epoch"]),
            extra_sets=extra_sets or {},
            state_seq=record.seq,
        )
        return True


def _waited_seconds(waiting_since: object, now: datetime) -> int:
    if waiting_since is None:
        return 0
    return max(0, int((now - parse_iso(str(waiting_since))).total_seconds()))


def _waited_past_block_threshold(database: Database, task_row: Any, now: datetime) -> bool:
    """R10 §10: waited longer than 2× the upstreams' expected durations.

    Durations come from the plan snapshot; a missing or non-positive value is
    counted as ``Settings.expected_duration_default_minutes`` (R10 §8's
    example value is 90), so the threshold is never zero-width.
    """
    if task_row["waiting_since"] is None:
        return False
    snapshot = _snapshot_tasks(database, task_row["plan_snapshot_id"])
    upstream_ids = [
        str(item["upstream_id"])
        for item in database.read(
            "SELECT child_task_id AS upstream_id FROM task_dependencies WHERE parent_task_id = ?",
            (str(task_row["task_id"]),),
        )
    ]
    default = settings.expected_duration_default_minutes
    total = 0
    for upstream_id in upstream_ids:
        entry = snapshot.get(upstream_id) or {}
        minutes = int(entry.get("expected_duration_min") or 0)
        total += minutes if minutes > 0 else default
    threshold = timedelta(minutes=2 * total)
    return now - parse_iso(str(task_row["waiting_since"])) > threshold


def _snapshot_tasks(database: Database, snapshot_id: object) -> dict[str, dict[str, Any]]:
    if not snapshot_id:
        return {}
    rows = database.read(
        "SELECT content_json FROM plan_snapshots WHERE snapshot_id = ?", (str(snapshot_id),)
    )
    if not rows:
        return {}
    try:
        content = json.loads(str(rows[0]["content_json"]))
    except json.JSONDecodeError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for entry in content.get("tasks", []):
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out[entry["id"]] = entry
    return out
