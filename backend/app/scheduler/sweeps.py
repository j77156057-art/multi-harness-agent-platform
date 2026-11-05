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

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.budget.service import BudgetGuard
from app.claim.service import QUEUE_REASON_RESOURCE, ClaimService
from app.claim.transitions import apply_transition
from app.config import settings
from app.db.connection import Database
from app.events.store import EventStore
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeError, WorktreeManager, _force_rmtree
from app.lease.service import expire_stale_leases, parse_iso
from app.merge.outputs import dependency_output_satisfied
from app.projects.purge import TRASH_RETAIN_DAYS, purge_due_projects
from app.scheduler.report import SweepReport
from app.spec import EventKind, TaskStatus
from app.verify.worker import STATUS_ABORTED, STATUS_QUEUED, STATUS_RUNNING

_AGING_STATES = (TaskStatus.READY.value, TaskStatus.QUEUED.value)

#: R10 §10: the seven mid-flight states the drift detectors watch.
_DRIFT_STATES = (
    TaskStatus.CLAIMED.value,
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.CHANGES_REQUESTED.value,
    TaskStatus.AWAITING_REVIEW.value,
    TaskStatus.VERIFYING.value,
    TaskStatus.APPROVED.value,
    TaskStatus.MERGING.value,
)

#: R10 §10: the states the stuck fingerprint watches.
_STUCK_STATES = (
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.NEEDS_HELP.value,
    TaskStatus.BLOCKED.value,
    TaskStatus.VERIFYING.value,
)

#: Upstream states that will never produce a merged output (R10 §10's
#: ``orphan_detected`` parenthetical, verbatim).
_DEAD_UPSTREAM_STATES = frozenset(
    {
        TaskStatus.CANCELLED.value,
        TaskStatus.ABANDONED.value,
        TaskStatus.DRAFT_REJECTED.value,
    }
)


def sweep_expired_leases(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Owner leases past their deadline: lease freed, the owner is **asked about**, not declared lost.

    R10-fix-14（2026-10-31）起，`lease_expire` 不再直接从 owner 状态跳到 `stale`：
    到期先写 `lease_uncertain` 让任务停在 `awaiting_human`，并在「需要你处理」里留
    一条问题；宽限期（`lease_uncertain_grace_minutes`）过后没人回答，才走
    `lease_expire` 兜底转 `stale`。判错的代价（把还在干的活判死）比多等一会儿大得多。
    迁移逻辑仍在 ``app/lease`` 里，这里只把结果适配成 runner 的报告形状。
    """
    budget_escalated: list[str] = []
    transitioned = expire_stale_leases(database, now=now, escalated_out=budget_escalated)
    return SweepReport(
        sweep="expired_leases",
        actions=len(transitioned) + len(budget_escalated),
        detail={"tasks": transitioned, "budget_escalated": budget_escalated},
    )


def sweep_dependencies(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Exit ``ready_waiting_deps``; wake split parents whose children finished.

    Two passes, both on ``task_dependencies`` (R10 §10 L587: the parent column
    is the *waiting* task, the child column is the upstream it waits for):

    * **``ready_waiting_deps``** (design §2.1): all upstreams merged →
      ``dependencies_satisfied``; else all upstreams dead-ended →
      ``orphan_detected``; else waited past 2× the upstreams' expected
      durations → ``block_timeout``; else stay. Dependencies with a persisted
      output contract additionally need the latest live merge's canonical hash;
      legacy edges with no ``output_name`` keep the merged-only rule.
    * **``blocked`` split parents** (R10 §10 ``all_children_merged``): every
      child merged → ``ready``. Only split parents qualify -- the
      discriminator is a blocked-entry of ``split_parent_blocked``/``split``
      rather than an owner's ``dependency_missing``, so a task parked on an
      external blocker is never lifted by upstreams that merged long ago.
      Children that dead-end (cancelled/draft_rejected) never satisfy this
      edge; the parent's exits for that case are the stuck sweep and the
      human surface (recorded, matching the spec's edges).

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
            "SELECT td.parent_task_id, td.child_task_id AS upstream_id, td.output_name,"
            " t.status AS status"
            "  FROM task_dependencies td"
            "  LEFT JOIN tasks t ON t.task_id = td.child_task_id"
            " WHERE td.parent_task_id = ?",
            (task_id,),
        )
        statuses = [str(item["status"]) for item in upstream]
        all_merged = bool(statuses) and all(
            status == TaskStatus.MERGED.value for status in statuses
        )
        if all_merged and _dependency_outputs_satisfied(database, upstream, repo):
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
        if not all_merged and statuses and all(
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

    unblocked: list[str] = []
    for row in database.read(
        "SELECT task_id FROM tasks WHERE status = ?", (TaskStatus.BLOCKED.value,)
    ):
        task_id = str(row["task_id"])
        with database.connect() as conn:
            if not _is_split_parent(conn, task_id):
                continue
        children = database.read(
            "SELECT td.child_task_id AS child_id, t.status AS status"
            "  FROM task_dependencies td"
            "  LEFT JOIN tasks t ON t.task_id = td.child_task_id"
            " WHERE td.parent_task_id = ?",
            (task_id,),
        )
        child_statuses = [str(item["status"]) for item in children]
        if (
            child_statuses
            and all(status == TaskStatus.MERGED.value for status in child_statuses)
            and _apply(
                database,
                store,
                task_id=task_id,
                expected=TaskStatus.BLOCKED,
                event=EventKind.ALL_CHILDREN_MERGED,
                payload={"children": [str(item["child_id"]) for item in children]},
                extra_sets={"waiting_since": None},
            )
        ):
            unblocked.append(task_id)

    return SweepReport(
        sweep="dependencies",
        actions=len(satisfied) + len(orphaned) + len(timed_out) + len(unblocked),
        detail={
            "satisfied": satisfied,
            "orphaned": orphaned,
            "block_timeout": timed_out,
            "unblocked": unblocked,
        },
    )


def sweep_resources(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Complete resource-deferred claims once capacity allows (design §2.2).

    The recovery half of ``claim_deferred_resource``: for every ``queued``
    resource deferral the claim service re-checks the resource blocker and, in
    one transaction, reserves the resource, opens the attempt and takes
    ownership (``resource_available`` → ``claimed``). Idempotent by
    construction: a second round finds no queued task and does nothing.

    **Worktree deferrals are deliberately not completed here.**  A worktree
    lease is only useful to the worker that claimed it: the sweeper can open
    the attempt but has no harness to run in it, and ``formal_worker`` only
    discovers tasks through ``list_ready_tasks`` — the 2026-11-06 E2E left
    such tasks ``claimed`` with no worktree and no harness ever running.  The
    queued task is instead listed to its own requester (see
    ``eligible_tasks``); its retried claim completes the deferral in place and
    goes straight into provisioning.
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


def sweep_aging(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Starvation for ``ready``/``queued`` (design §2.3, R10 §20).

    Waiting longer than ``expected_duration_min × aging_multiplier`` (the
    task's own snapshot entry; missing or 0 counts as the default) escalates.
    ``queued`` lands here rather than in the resource sweep: the deferral that
    queued it was that sweep's business, but "waited too long" is aging's.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id, status, waiting_since, plan_snapshot_id FROM tasks"
        " WHERE status IN (?, ?)",
        _AGING_STATES,
    )
    escalated: list[str] = []
    for row in rows:
        expected = _task_expected_minutes(database, row["plan_snapshot_id"], str(row["task_id"]))
        threshold_seconds = int(expected * settings.aging_multiplier * 60)
        if _waited_seconds(row["waiting_since"], now) > threshold_seconds and _apply(
            database,
            store,
            task_id=str(row["task_id"]),
            expected=TaskStatus(str(row["status"])),
            event=EventKind.AGING_TIMEOUT,
            payload={
                "waited_seconds": _waited_seconds(row["waiting_since"], now),
                "threshold_minutes": expected * settings.aging_multiplier,
            },
            extra_sets={"escalated_at": now.isoformat()},
        ):
            escalated.append(str(row["task_id"]))
    return SweepReport("aging", len(escalated), {"escalated": escalated})


def sweep_blocked_sla(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """``blocked`` past its SLA (design §2.4, R10 §10 ``block_timeout``).

    Distinct from the dependency sweep's ``block_timeout``: that one fires out
    of ``ready_waiting_deps`` on upstream durations; this one on the block SLA.

    Split parents are skipped: their "blocker" is their in-flight children,
    each watched by the whole family individually, so the SLA would only
    escalate healthy long-running splits. The safety net for dead-end children
    (cancelled/draft_rejected) is the stuck sweep, whose fingerprint for split
    parents tracks the children's activity.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id, waiting_since FROM tasks WHERE status = ?",
        (TaskStatus.BLOCKED.value,),
    )
    escalated: list[str] = []
    for row in rows:
        if _waited_seconds(row["waiting_since"], now) <= settings.block_sla_minutes * 60:
            continue
        task_id = str(row["task_id"])
        with database.connect() as conn:
            if _is_split_parent(conn, task_id):
                continue
        if _apply(
            database,
            store,
            task_id=task_id,
            expected=TaskStatus.BLOCKED,
            event=EventKind.BLOCK_TIMEOUT,
            payload={"waited_seconds": _waited_seconds(row["waiting_since"], now)},
            extra_sets={"escalated_at": now.isoformat()},
        ):
            escalated.append(task_id)
    return SweepReport("blocked_sla", len(escalated), {"escalated": escalated})


def sweep_stuck(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """No-progress detection by fingerprint (design §2.5, R10 §13 3/5 sweeps).

    Fingerprint = hash(latest task event seq, attempt head_commit, heartbeat
    progress_hash); one row per watched task in ``sweep_progress`` counts
    consecutive sightings without change. The literal guards are evaluated in
    order: ``>= stuck_human_sweeps`` (default 5) → ``stuck_human_threshold`` →
    ``awaiting_human``; else ``>= stuck_sweeps`` (default 3) →
    ``stuck_threshold_reached`` → ``escalated``. With +1-per-round counting the
    3-guard normally fires first (the task then leaves the watched set); the
    5-guard is the spec's literal edge for counts that arrive larger, and both
    are implemented rather than chosen between.
    """
    store = EventStore(database)
    rows = database.read("SELECT task_id FROM tasks WHERE status IN (?, ?, ?, ?)", _STUCK_STATES)
    escalated: list[str] = []
    to_human: list[str] = []
    for row in rows:
        task_id = str(row["task_id"])
        outcome = _stuck_round(database, store, task_id=task_id, now=now)
        if outcome == "escalated":
            escalated.append(task_id)
        elif outcome == "awaiting_human":
            to_human.append(task_id)
    return SweepReport(
        "stuck",
        len(escalated) + len(to_human),
        {"escalated": escalated, "awaiting_human": to_human},
    )


def sweep_verify_lease(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Re-queue verify runs whose worker died (design §2.6, O2 ruling).

    **Approximation, stated plainly (O2 ruling, 2026-10-07)**: the literal
    guard asks for a ``verify_run`` intent reconciliation confirming the
    process is dead. The MVP has no process evidence -- the pid/pgid/boot_id
    columns on ``verify_jobs`` have no producer and the worker runs inline --
    so the proxy is: a job still in ``running`` whose task's newest event is
    older than the verify lease. A crashed worker leaves exactly that shape.
    No fake autopsy is performed; this paragraph is the disclosure.

    On firing: the old job is marked ``aborted``, a fresh job is queued (no new
    attempt, no epoch change -- R10 §10's note on this edge, verbatim), and the
    task self-loops back into ``verifying`` for the next runner.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ?",
        (TaskStatus.VERIFYING.value,),
    )
    re_queued: list[str] = []
    for row in rows:
        if _verify_lease_round(database, store, task_id=str(row["task_id"]), now=now):
            re_queued.append(str(row["task_id"]))
    return SweepReport("verify_lease", len(re_queued), {"re_queued": re_queued})


def sweep_verify_queue(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Queued verify jobs past their SLA (design §2.6).

    Producer note: the MVP never leaves a job ``queued`` (the ADR-030 queue is
    not implemented), so this condition is implemented literally against the
    state the schema and R10 §10 name, and tests seed it directly -- recorded,
    not silently skipped.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ?",
        (TaskStatus.VERIFYING.value,),
    )
    escalated: list[str] = []
    for row in rows:
        if _verify_queue_round(database, store, task_id=str(row["task_id"]), now=now):
            escalated.append(str(row["task_id"]))
    return SweepReport("verify_queue", len(escalated), {"escalated": escalated})


def sweep_review_lease(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Expired review claims go back on the shelf (design §2.7).

    The guard's "no in-flight review intent" clause is trivially true in the
    MVP: intents are recorded after the fact, nothing is ever ``pending``.
    Recorded here instead of pretending the clause did work.

    The expired claim row is marked ``expired`` so the next reviewer claim
    starts clean; the task self-loops and keeps its waiting clock (the wait
    for a verdict has not gotten any shorter because a reviewer vanished).
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ?",
        (TaskStatus.AWAITING_REVIEW.value,),
    )
    expired: list[str] = []
    for row in rows:
        if _review_lease_round(database, store, task_id=str(row["task_id"]), now=now):
            expired.append(str(row["task_id"]))
    return SweepReport("review_lease", len(expired), {"expired": expired})


def sweep_merge_recovery(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """A merge that stopped mid-flight either retries or escalates (design §2.7).

    First match wins, retry before escalation (design's order): past
    ``merge_lease_seconds`` → ``merge_lease_expire`` → back to ``approved``
    (worker-death recovery); otherwise past
    ``integration_test_timeout_minutes`` → ``merge_timeout`` → ``escalated``.
    With the equal defaults both conditions become true at the same moment and
    the retry wins; an operator who configures the lease longer than the test
    timeout opens an escalation window in between. The ``approved`` self-loop
    of ``merge_lease_expire`` is a no-op and is documented, not implemented.

    Approximation note (same family as verify): "no in-flight merge intent" is
    trivially true in the MVP -- ``side_effect_intents`` rows are written with
    status ``done`` after the fact, never ``pending``.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ?",
        (TaskStatus.MERGING.value,),
    )
    retried: list[str] = []
    escalated: list[str] = []
    for row in rows:
        outcome = _merge_recovery_round(database, store, task_id=str(row["task_id"]), now=now)
        if outcome == "approved":
            retried.append(str(row["task_id"]))
        elif outcome == "escalated":
            escalated.append(str(row["task_id"]))
    return SweepReport(
        "merge_recovery",
        len(retried) + len(escalated),
        {"retried": retried, "escalated": escalated},
    )


def sweep_drift(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """Drift detectors for the seven mid-flight states (design §2.10, O3 ruling).

    Two independent checks, first match wins:

    * **plan drift**: the task's bound snapshot's ``snapshot_hash`` differs from
      the plan's current ``plan_hash``. Current producers never update
      ``plans.plan_hash`` after creation, so this detector guards the day a
      revision flow lands -- tested by seeding the divergence.
    * **impl drift**: the task's ``base_commit`` is no longer an ancestor of
      the integration HEAD. Forward merges keep the base an ancestor and stay
      silent; a revert or invalidation breaks ancestry and fires -- which is
      R10 §21's revert-impact story, not normal collaboration noise.

    Both only *mark* (the task enters ``plan_drift``/``impl_drift`` for a human
    decision); nothing is auto-corrected. Runs every cycle: the ancestry check
    is one cheap git call per mid-flight task.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status IN (?, ?, ?, ?, ?, ?, ?)",
        _DRIFT_STATES,
    )
    to_plan: list[str] = []
    to_impl: list[str] = []
    undecided: list[str] = []
    for row in rows:
        outcome = _drift_round(database, store, task_id=str(row["task_id"]), repo=repo)
        if outcome == "plan":
            to_plan.append(str(row["task_id"]))
        elif outcome == "impl":
            to_impl.append(str(row["task_id"]))
        elif outcome == "undecided":
            undecided.append(str(row["task_id"]))
    return SweepReport(
        "drift",
        len(to_plan) + len(to_impl),
        {"plan_drift": to_plan, "impl_drift": to_impl, "undecided": undecided},
    )


def sweep_human_wait_timeout(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """A parked task past the reminder window: remind, never release (§18/§13).

    The first window runs from ``waiting_since`` (the moment the task parked);
    every later window from ``next_reminder_at``, which each reminder pushes
    out by ``min(base x mult^count, cap)`` where ``count`` is how many
    reminders this stint already emitted -- event counting, no new table
    (design §2.9, card criterion 4). The self-loop is R10 §10's
    ``awaiting_human | human_wait_timeout`` and writes only the reminder
    field, never ``state_changed_seq`` or ``waiting_since`` (R10-fix-11).

    R10 §13: the reminder never auto-releases or auto-cancels; a human verdict
    (resume/reopen/cancel) is the only way out.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ?", (TaskStatus.AWAITING_HUMAN.value,)
    )
    reminded: list[str] = []
    for row in rows:
        if _reminder_round(
            database,
            store,
            task_id=str(row["task_id"]),
            now=now,
            expected=TaskStatus.AWAITING_HUMAN,
            event=EventKind.HUMAN_WAIT_TIMEOUT,
            base_field="waiting_since",
        ):
            reminded.append(str(row["task_id"]))
    return SweepReport("human_wait_timeout", len(reminded), {"reminded": reminded})


def sweep_escalation_reminder(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """An escalation still undecided: back-off reminder on the same schedule.

    Same mechanism as ``human_wait_timeout``, anchored on ``escalated_at``
    (R10 §10's ``escalated | escalation_reminder`` self-loop; design §2.9's
    "still not adjudicated"). Escalation edges that predate the column -- the
    merge worker's, which sets no anchor -- fall back to the task's latest
    event, which at entry *is* the escalation event; later reminders leave
    ``next_reminder_at`` set, so the fallback cannot drift with them.
    """
    store = EventStore(database)
    rows = database.read(
        "SELECT task_id FROM tasks WHERE status = ?", (TaskStatus.ESCALATED.value,)
    )
    reminded: list[str] = []
    for row in rows:
        if _reminder_round(
            database,
            store,
            task_id=str(row["task_id"]),
            now=now,
            expected=TaskStatus.ESCALATED,
            event=EventKind.ESCALATION_REMINDER,
            base_field="escalated_at",
        ):
            reminded.append(str(row["task_id"]))
    return SweepReport("escalation_reminder", len(reminded), {"reminded": reminded})


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
        if event in _ESCALATION_EVENTS:
            _close_escalated_owner(conn, task_id=task_id, reason=event.value)
        return True


#: 所有把任务推进 ``escalated`` 的 sweep 边。07 现场只修了 stuck 一条，但
#: blocked/aging 同样会升级；漏一条就还会留下 ``escalated + in_progress``。
_ESCALATION_EVENTS = frozenset(
    {
        EventKind.AGING_TIMEOUT,
        EventKind.BLOCK_TIMEOUT,
        EventKind.STUCK_THRESHOLD_REACHED,
        EventKind.ATTEMPT_BUDGET_EXHAUSTED,
    }
)


def _close_escalated_owner(
    conn: sqlite3.Connection, *, task_id: str, reason: str
) -> None:
    """Close the owner phase of an escalated task: attempt + lease + owner.

    07 现场：``FD-c3fb`` 已 escalated，``task_attempts`` 仍 ``in_progress``，
    lease ``lease_until=06:56:49`` / ``released_at=null``，owner 还挂着。
    升级之后没有人再会来续租，所以这三样必须在同一个事务里收口。
    """
    from app.lease.service import release_attempt_resources, release_lease
    from app.tasks.service import close_attempt

    row = conn.execute(
        "SELECT current_attempt_id FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    attempt_id = None if row is None or row["current_attempt_id"] is None else str(
        row["current_attempt_id"]
    )
    if attempt_id is None:
        return
    close_attempt(
        conn,
        attempt_id=attempt_id,
        status="escalated",
        reason=reason,
        now=datetime.now(UTC),
    )
    release_lease(conn, task_id=task_id, attempt_id=attempt_id, reason=reason)
    release_attempt_resources(conn, attempt_id=attempt_id, reason=reason)
    conn.execute(
        "UPDATE tasks SET owner = NULL, current_attempt_id = NULL WHERE task_id = ?",
        (task_id,),
    )


def _drift_round(
    database: Database,
    store: EventStore,
    *,
    task_id: str,
    repo: CanonicalRepo | None,
) -> str | None:
    """One drift round for one task; returns \"plan\" / \"impl\" / \"undecided\" / None."""
    with database.transaction() as conn:
        task = conn.execute(
            "SELECT status, current_epoch, plan_snapshot_id, base_commit"
            "  FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task is None or str(task["status"]) not in _DRIFT_STATES:
            return None
        if task["plan_snapshot_id"] is not None:
            snapshot = conn.execute(
                "SELECT snapshot_hash, plan_id FROM plan_snapshots WHERE snapshot_id = ?",
                (str(task["plan_snapshot_id"]),),
            ).fetchone()
            if snapshot is not None:
                plan = conn.execute(
                    "SELECT plan_hash FROM plans WHERE plan_id = ?",
                    (str(snapshot["plan_id"]),),
                ).fetchone()
                if plan is not None and str(snapshot["snapshot_hash"]) != str(plan["plan_hash"]):
                    _drift_transition(
                        conn,
                        store,
                        task,
                        task_id=task_id,
                        event=EventKind.PLAN_HASH_CHANGED,
                        payload={
                            "snapshot_hash": str(snapshot["snapshot_hash"]),
                            "plan_hash": str(plan["plan_hash"]),
                        },
                    )
                    return "plan"
        if repo is None:
            # No repository injected: the plan half still ran; the ancestry
            # half is declared undecided rather than guessed.
            return "undecided" if task["base_commit"] is not None else None
        base = task["base_commit"]
        if base is None:
            return None
        head = repo.ref_commit(repo.integration_ref)
        if head is None:
            return None
        proc = run_git(
            "merge-base", "--is-ancestor", str(base), str(head), cwd=repo.path, check=False
        )
        if proc.returncode == 0:
            return None
        if proc.returncode != 1:
            return "undecided"
        _drift_transition(
            conn,
            store,
            task,
            task_id=task_id,
            event=EventKind.IMPL_HASH_CHANGED,
            payload={"base_commit": str(base), "integration_head": str(head)},
        )
        return "impl"


def _drift_transition(
    conn: Any,
    store: EventStore,
    task_row: Any,
    *,
    task_id: str,
    event: EventKind,
    payload: dict[str, Any],
) -> None:
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
        current=TaskStatus(str(task_row["status"])),
        event=event,
        expected_epoch=int(task_row["current_epoch"]),
        extra_sets={},
        state_seq=record.seq,
    )


def _latest_event_iso(conn: Any, task_id: str) -> datetime | None:
    row = conn.execute(
        "SELECT created_at FROM task_events WHERE aggregate_id = ? ORDER BY seq DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return None if row is None else parse_iso(str(row["created_at"]))


def _is_split_parent(conn: Any, task_id: str) -> bool:
    """True when the task's most recent blocked-entry is a split.

    ``blocked`` has two entries: ``split_parent_blocked`` (R10 §10's split
    edge, whose ``split`` preamble also counts so a split from ``blocked`` is
    recognised) and ``dependency_missing`` (an owner reporting an external
    blocker). Split parents wait on their own children, so the blocked SLA and
    the stuck fingerprint judge them against the children's liveness;
    owner-reported blockers keep the plain rules.
    """
    row = conn.execute(
        "SELECT event_name FROM task_events WHERE aggregate_id = ?"
        " AND event_name IN (?, ?, ?) ORDER BY seq DESC LIMIT 1",
        (
            task_id,
            EventKind.SPLIT.value,
            EventKind.SPLIT_PARENT_BLOCKED.value,
            EventKind.DEPENDENCY_MISSING.value,
        ),
    ).fetchone()
    if row is None:
        return False
    return str(row["event_name"]) in {
        EventKind.SPLIT.value,
        EventKind.SPLIT_PARENT_BLOCKED.value,
    }


def _reminder_count(conn: Any, task_id: str, event: EventKind, since_seq: object) -> int:
    """Reminders this stint already emitted: events after the entry transition.

    ``state_changed_seq`` is the entry event's seq (both reminder targets are
    entered by a status change), so counting beyond it scopes the back-off to
    the current stint without a new table.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM task_events"
        " WHERE aggregate_id = ? AND event_name = ? AND seq > ?",
        (task_id, event.value, int(since_seq or 0)),
    ).fetchone()
    return int(row["n"])


def _reminder_round(
    database: Database,
    store: EventStore,
    *,
    task_id: str,
    now: datetime,
    expected: TaskStatus,
    event: EventKind,
    base_field: str,
) -> bool:
    """One reminder round for one parked task; returns True when one was sent."""
    with database.transaction() as conn:
        task = conn.execute(
            "SELECT status, current_epoch, state_changed_seq, waiting_since, escalated_at,"
            " next_reminder_at FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task is None or str(task["status"]) != expected.value:
            return False
        base = task[base_field]
        moment = parse_iso(str(base)) if base is not None else _latest_event_iso(conn, task_id)
        if moment is None:
            return False
        next_at = task["next_reminder_at"]
        if next_at is None:
            if now < moment + timedelta(minutes=settings.human_reminder_minutes):
                return False
        elif now < parse_iso(str(next_at)):
            return False
        prior = _reminder_count(conn, task_id, event, task["state_changed_seq"])
        gap_minutes = int(
            min(
                settings.reminder_cap_minutes,
                settings.human_reminder_minutes
                * settings.escalation_backoff_multiplier ** (prior + 1),
            )
        )
        record = store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload={
                "reminder_no": prior + 1,
                "interval_minutes": gap_minutes,
                "waited_seconds": max(0, int((now - moment).total_seconds())),
            },
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=expected,
            event=event,
            expected_epoch=int(task["current_epoch"]),
            extra_sets={"next_reminder_at": (now + timedelta(minutes=gap_minutes)).isoformat()},
            state_seq=record.seq,
        )
        return True


def _current_task(conn: Any, task_id: str, expected: TaskStatus) -> Any:
    task = conn.execute(
        "SELECT status, current_epoch, current_attempt_id FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if task is None or str(task["status"]) != expected.value:
        return None
    return task


def _verify_lease_round(
    database: Database, store: EventStore, *, task_id: str, now: datetime
) -> bool:
    with database.transaction() as conn:
        task = _current_task(conn, task_id, TaskStatus.VERIFYING)
        if task is None or task["current_attempt_id"] is None:
            return False
        job = conn.execute(
            "SELECT * FROM verify_jobs WHERE task_id = ? AND attempt_id = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (task_id, str(task["current_attempt_id"])),
        ).fetchone()
        if job is None or str(job["status"]) != STATUS_RUNNING:
            return False
        anchor = _latest_event_iso(conn, task_id)
        if anchor is None or now - anchor <= timedelta(seconds=settings.verify_lease_seconds):
            return False
        conn.execute(
            "UPDATE verify_jobs SET status = ? WHERE job_id = ?",
            (STATUS_ABORTED, str(job["job_id"])),
        )
        new_job_id = f"job-{uuid.uuid4().hex}"
        conn.execute(
            'INSERT INTO verify_jobs(job_id, task_id, attempt_id, "commit", base_commit,'
            " status, queue_reason, fencing_epoch, verify_profile, created_seq)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                new_job_id,
                task_id,
                str(task["current_attempt_id"]),
                str(job["commit"]),
                job["base_commit"],
                STATUS_QUEUED,
                "verify_lease_expire",
                job["fencing_epoch"],
                job["verify_profile"],
            ),
        )
        record = store.append(
            event_name=EventKind.VERIFY_LEASE_EXPIRE.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload={
                "previous_job_id": str(job["job_id"]),
                "new_job_id": new_job_id,
                "anchor": anchor.isoformat(),
            },
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus.VERIFYING,
            event=EventKind.VERIFY_LEASE_EXPIRE,
            expected_epoch=int(task["current_epoch"]),
            extra_sets={},
            state_seq=record.seq,
        )
        return True


def _verify_queue_round(
    database: Database, store: EventStore, *, task_id: str, now: datetime
) -> bool:
    with database.transaction() as conn:
        task = _current_task(conn, task_id, TaskStatus.VERIFYING)
        if task is None:
            return False
        job = conn.execute(
            "SELECT * FROM verify_jobs WHERE task_id = ? AND status = ?"
            " ORDER BY rowid DESC LIMIT 1",
            (task_id, STATUS_QUEUED),
        ).fetchone()
        if job is None:
            return False
        anchor: datetime | None = None
        if job["created_seq"] is not None:
            row = conn.execute(
                "SELECT created_at FROM task_events WHERE seq = ?", (int(job["created_seq"]),)
            ).fetchone()
            if row is not None:
                anchor = parse_iso(str(row["created_at"]))
        if anchor is None:
            anchor = _latest_event_iso(conn, task_id)
        if anchor is None or now - anchor <= timedelta(minutes=settings.verify_queue_sla_minutes):
            return False
        record = store.append(
            event_name=EventKind.VERIFY_QUEUE_TIMEOUT.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload={
                "job_id": str(job["job_id"]),
                "waited_seconds": int((now - anchor).total_seconds()),
            },
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus.VERIFYING,
            event=EventKind.VERIFY_QUEUE_TIMEOUT,
            expected_epoch=int(task["current_epoch"]),
            extra_sets={"escalated_at": now.isoformat()},
            state_seq=record.seq,
        )
        return True


def _review_lease_round(
    database: Database, store: EventStore, *, task_id: str, now: datetime
) -> bool:
    with database.transaction() as conn:
        task = _current_task(conn, task_id, TaskStatus.AWAITING_REVIEW)
        if task is None:
            return False
        claimed = conn.execute(
            "SELECT review_id, reviewer_harness_id, lease_until FROM reviews"
            " WHERE task_id = ? AND status = 'claimed' ORDER BY rowid DESC",
            (task_id,),
        ).fetchall()
        expired = [
            row
            for row in claimed
            if row["lease_until"] is not None and parse_iso(str(row["lease_until"])) <= now
        ]
        if not expired:
            return False
        for row in expired:
            conn.execute(
                "UPDATE reviews SET status = 'expired' WHERE review_id = ?",
                (str(row["review_id"]),),
            )
        record = store.append(
            event_name=EventKind.REVIEW_LEASE_EXPIRE.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload={"expired_reviews": [str(row["review_id"]) for row in expired]},
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus.AWAITING_REVIEW,
            event=EventKind.REVIEW_LEASE_EXPIRE,
            expected_epoch=int(task["current_epoch"]),
            extra_sets={},
            state_seq=record.seq,
        )
        return True


def _merge_recovery_round(
    database: Database, store: EventStore, *, task_id: str, now: datetime
) -> str | None:
    with database.transaction() as conn:
        task = _current_task(conn, task_id, TaskStatus.MERGING)
        if task is None:
            return None
        row = conn.execute(
            "SELECT created_at FROM task_events WHERE aggregate_id = ? AND event_name = ?"
            " ORDER BY seq DESC LIMIT 1",
            (task_id, EventKind.MERGE_START.value),
        ).fetchone()
        if row is None:
            return None
        age = now - parse_iso(str(row["created_at"]))
        if age > timedelta(seconds=settings.merge_lease_seconds):
            event = EventKind.MERGE_LEASE_EXPIRE
            extra: dict[str, object] = {}
            outcome = "approved"
        elif age > timedelta(minutes=settings.integration_test_timeout_minutes):
            event = EventKind.MERGE_TIMEOUT
            extra = {"escalated_at": now.isoformat()}
            outcome = "escalated"
        else:
            return None
        record = store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload={"waited_seconds": int(age.total_seconds())},
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus.MERGING,
            event=event,
            expected_epoch=int(task["current_epoch"]),
            extra_sets=extra,
            state_seq=record.seq,
        )
        return outcome


def _stuck_round(
    database: Database, store: EventStore, *, task_id: str, now: datetime
) -> str | None:
    """One fingerprint round for one task; returns the outcome when one fired."""
    with database.transaction() as conn:
        task = conn.execute(
            "SELECT status, current_epoch, current_attempt_id, progress_hash"
            "  FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task is None or str(task["status"]) not in _STUCK_STATES:
            return None
        latest_seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS s FROM task_events WHERE aggregate_id = ?",
                (task_id,),
            ).fetchone()["s"]
        )
        head: str | None = None
        if task["current_attempt_id"] is not None:
            attempt = conn.execute(
                "SELECT head_commit FROM task_attempts WHERE attempt_id = ?",
                (str(task["current_attempt_id"]),),
            ).fetchone()
            head = None if attempt is None else attempt["head_commit"]
        # R10 §13's division of labour: a lease renewal proves the owner is
        # alive, so it counts as progress here -- without this, every healthy
        # long run escalated three sweeps after it started (our CLI worker
        # heartbeats without progress_hash, and nothing else moves mid-run).
        # "Alive but committing nothing" is the zero-commit alarm's job
        # (R10 L706, implemented in app.liveness), not the stuck edge; a task
        # whose lease is not renewed still reaches the lease-expiry path.
        lease_until = conn.execute(
            "SELECT MAX(lease_until) AS until FROM leases"
            " WHERE task_id = ? AND released_at IS NULL",
            (task_id,),
        ).fetchone()["until"]
        material = f"{latest_seq}:{head}:{task['progress_hash']}:{lease_until}"
        if str(task["status"]) == TaskStatus.BLOCKED.value and _is_split_parent(conn, task_id):
            # A split parent's progress IS its children's progress (R10 §13's
            # 无进展 judged on the waiting task): without this the parent would
            # escalate three sweeps after every healthy split.
            children = conn.execute(
                "SELECT td.child_task_id AS child_id, t.status AS child_status,"
                " t.progress_hash AS child_progress,"
                " (SELECT COALESCE(MAX(seq), 0) FROM task_events"
                "   WHERE aggregate_id = td.child_task_id) AS child_seq"
                "  FROM task_dependencies td"
                "  JOIN tasks t ON t.task_id = td.child_task_id"
                " WHERE td.parent_task_id = ? ORDER BY td.child_task_id",
                (task_id,),
            ).fetchall()
            child_state = ";".join(
                f"{row['child_id']}={row['child_status']}:{row['child_progress']}:{row['child_seq']}"
                for row in children
            )
            material += f":children[{child_state}]"
        fingerprint = hashlib.sha256(material.encode()).hexdigest()
        stored = conn.execute(
            "SELECT fingerprint, unchanged_count FROM sweep_progress WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        unchanged = (
            0
            if stored is None or str(stored["fingerprint"]) != fingerprint
            else int(stored["unchanged_count"]) + 1
        )
        conn.execute(
            "INSERT INTO sweep_progress(task_id, fingerprint, unchanged_count, last_checked_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(task_id) DO UPDATE SET fingerprint = excluded.fingerprint,"
            " unchanged_count = excluded.unchanged_count,"
            " last_checked_at = excluded.last_checked_at",
            (task_id, fingerprint, unchanged, now.isoformat()),
        )
        if unchanged >= settings.stuck_human_sweeps:
            event = EventKind.STUCK_HUMAN_THRESHOLD
            extra: dict[str, object] = {
                "waiting_since": now.isoformat(),
                "next_reminder_at": None,
            }
            outcome = "awaiting_human"
        elif unchanged >= settings.stuck_sweeps:
            event = EventKind.STUCK_THRESHOLD_REACHED
            extra = {"escalated_at": now.isoformat()}
            outcome = "escalated"
        else:
            return None
        record = store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="sweeper",
            payload={"unchanged_sweeps": unchanged, "fingerprint": fingerprint},
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus(str(task["status"])),
            event=event,
            expected_epoch=int(task["current_epoch"]),
            extra_sets=extra,
            state_seq=record.seq,
        )
        conn.execute(
            "UPDATE tasks SET updated_at = ? WHERE task_id = ?",
            (now.isoformat(), task_id),
        )
        if outcome == "escalated":
            # 07 现场：这里只改了任务状态，attempt 仍 in_progress、lease 仍挂着。
            _close_escalated_owner(conn, task_id=task_id, reason=event.value)
        return outcome


def _task_expected_minutes(database: Database, snapshot_id: object, task_id: str) -> int:
    """The task's own expected duration, with the shared default as fallback."""
    entry = _snapshot_tasks(database, snapshot_id).get(task_id) or {}
    minutes = int(entry.get("expected_duration_min") or 0)
    return minutes if minutes > 0 else settings.expected_duration_default_minutes


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


def _dependency_outputs_satisfied(
    database: Database, upstream: list[Any], repo: CanonicalRepo | None
) -> bool:
    return all(
        item["output_name"] is None
        or dependency_output_satisfied(
            database,
            parent_task_id=str(item["parent_task_id"]),
            upstream_task_id=str(item["upstream_id"]),
            output_name=str(item["output_name"]),
            repo=repo,
        )
        for item in upstream
    )


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


def sweep_budget(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """budget-guard: reconcile stale holds and keep incidents in step.

    Runs late in the round so holds whose attempts the earlier sweeps just
    closed (lease expiry, reclaim) are settled in the same pass. Writes
    reservations/incidents only -- no task state moves, and alert queueing
    stays in ``python -m app.budget`` (sweeps never do outbound).
    """
    report = BudgetGuard(database).scan(now=now, enqueue=False)
    return SweepReport(
        sweep="budget",
        actions=report.held_reconciled + report.incidents_opened + report.incidents_resolved,
        detail={
            "held_reconciled": report.held_reconciled,
            "incidents_opened": report.incidents_opened,
            "incidents_resolved": report.incidents_resolved,
            "open_incidents": report.open_incidents,
        },
    )


#: 孤儿扫描白名单（相对 ``data_dir`` 的父目录，即项目根）。
#:
#: 2026-11-06 事故：root 是从「每条 workspace 行的父目录」推导出来的，于是
#: 一次 sweep 跨 root 删了 75 个目录，包括 ``<tmp>\e2e-real-20261105\wt``
#: 67 个和 ``.worktrees\codex\wt`` 6 个 —— 那不是本实例该动的东西。
#: 现在只认这三个显式路径；``<tmp>\*``、``.worktrees\codex\*`` 默认拒绝，
#: 即使目录存在、为空、且本库恰好有行指向它。
ORPHAN_ROOT_ALLOWLIST = ("wt", "data/fw", "data/fw-op")


def _is_allowed_orphan_root(path: Path) -> bool:
    base = Path(settings.data_dir).parent
    allowed = {
        os.path.normcase(os.path.abspath(str(base / rel)))
        for rel in ORPHAN_ROOT_ALLOWLIST
    }
    return os.path.normcase(os.path.abspath(str(path))) in allowed


def _orphan_roots(database: Database) -> list[Path]:
    """本库真正拥有的、且在白名单内的 root。

    两个条件缺一不可：目录在白名单里，且本库至少有一行 workspace 指向它。
    空库因此一个 root 都不扫——2026-11-05 的测试库就是回落到真实 ``wt/``
    才误删了生产目录。
    """
    owned = [
        os.path.normcase(os.path.abspath(str(row["path"])))
        for row in database.read("SELECT path FROM workspaces")
    ]
    roots: list[Path] = []
    for candidate in (Path(settings.data_dir).parent / rel for rel in ORPHAN_ROOT_ALLOWLIST):
        if not candidate.is_dir():
            continue
        prefix = os.path.normcase(os.path.abspath(str(candidate))) + os.sep
        if any(path.startswith(prefix) for path in owned):
            roots.append(candidate)
    return roots


def _recycle_orphan_worktrees(
    database: Database, roots: list[Path]
) -> tuple[list[str], list[dict[str, str]]]:
    """Clean directories under each allow-listed worktree root that no active row owns.

    "目录残留但无租约"：a crash can leave a tree behind after its row was
    removed.  A *dirty* leftover is never deleted -- it is somebody's
    unarchived work; it is reported as ``orphans_kept`` every round.  A clean
    one is bundled (all refs) and only then removed, so commits created in the
    orphan gitdir are not lost with the directory.

    ``roots`` is the intersection of :data:`ORPHAN_ROOT_ALLOWLIST` with the
    roots this database has rows for (see :func:`_orphan_roots`).  It is never
    derived from an arbitrary workspace row's parent again.
    """
    active = {
        os.path.normcase(os.path.abspath(str(row["path"])))
        for row in database.read("SELECT path FROM workspaces WHERE status = 'active'")
    }
    removed: list[str] = []
    kept: list[dict[str, str]] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir() or child.is_symlink() or child.name.endswith(".git"):
                continue
            if os.path.normcase(os.path.abspath(str(child))) in active:
                continue
            status = run_git("status", "--porcelain", cwd=child, check=False)
            if status.returncode != 0:
                kept.append(
                    {
                        "path": str(child),
                        "reason": f"git status failed: {status.stderr.strip()[:200]}",
                    }
                )
                continue
            if status.stdout.strip():
                kept.append({"path": str(child), "reason": "uncommitted_changes"})
                continue
            archive_root = Path(settings.data_dir) / "archives" / "orphans"
            archive_root.mkdir(parents=True, exist_ok=True)
            bundle = archive_root / f"{child.name}-{uuid.uuid4().hex[:8]}.bundle"
            refs = run_git("for-each-ref", "--format=%(refname)", cwd=child, check=False)
            head = run_git("rev-parse", "--verify", "HEAD", cwd=child, check=False)
            if refs.stdout.strip():
                created = run_git(
                    "bundle", "create", str(bundle), "--all", cwd=child, check=False
                )
            elif head.returncode == 0 and head.stdout.strip():
                created = run_git(
                    "bundle", "create", str(bundle), "HEAD", cwd=child, check=False
                )
            else:
                # 无 ref 也无 HEAD：没有可丢的提交，干净目录直接清理。
                created = None
            if created is not None and created.returncode != 0:
                kept.append(
                    {
                        "path": str(child),
                        "reason": f"bundle failed: {created.stderr.strip()[:200]}",
                    }
                )
                continue
            if created is not None:
                verified = run_git("bundle", "verify", str(bundle), cwd=child, check=False)
                if verified.returncode != 0:
                    kept.append(
                        {
                            "path": str(child),
                            "reason": f"bundle verify failed: {verified.stderr.strip()[:200]}",
                        }
                    )
                    continue
            gitdir = root / f"{child.name}.git"
            _force_rmtree(child)
            if gitdir.exists() and not gitdir.is_symlink():
                _force_rmtree(gitdir)
            removed.append(str(child))
    return removed, kept


def sweep_worktree_recycle(
    database: Database,
    now: datetime,
    repo: CanonicalRepo | None = None,
    root: Path | None = None,
) -> SweepReport:
    """Recycle worktrees whose attempt is dead (rules-tighten, drill F7).

    R10 §14 L795 pins both the trigger states and the safety order:
    merged / abandoned / cancelled and the rework freeze put the old worktree
    into ``pending_archive``, and "归档校验通过后才回收 worktree" -- so the
    only path used here is ``archive_and_recycle`` (bundle -> verify ->
    remove). A failed archive leaves the tree in place for a human and is
    reported as skipped rather than retried blind.

    ``escalated`` is included because a merge-gate failure parks the task for a
    human: no worker will touch that attempt again, and without this the
    workspace sits at the global cap until someone recycles it by hand. A
    human ``release`` moves the task back to ``ready`` and the next claim
    provisions a fresh attempt, so archiving here cannot strand a live run.

    Deliberately does NOT touch the current attempt of a live task: a
    restarted harness may still replay-claim it (F9), and the stale/reclaiming
    window keeps its workspace until a successor attempt exists -- which this
    same sweep recycles on a later round. Capacity falls back naturally: the
    active-workspace count is what ``provision`` checks, and the drill's F7
    exhaustion was exactly a pile of merged/stale attempts.
    """
    rows = database.read(
        "SELECT w.attempt_id FROM workspaces w JOIN tasks t ON t.task_id = w.task_id"
        " WHERE w.status = 'active'"
        # ``stale`` 也在内：租约一过期，那个 attempt 就死了，它的工位还占着容量
        # 只会把平台自己锁死（"并发工位上限 8/8"，新任务连工位都开不出来）。
        # 归档仍会留底（``archive_and_recycle`` 先打包再删），证据不丢。
        "   AND (t.status IN ('merged', 'cancelled', 'abandoned', 'changes_requested',"
        "                    'escalated', 'stale')"
        # 没有当前 attempt 的任务，它手上那个 attempt 同样已经死了
        "        OR t.current_attempt_id IS NULL"
        "        OR (t.current_attempt_id IS NOT NULL"
        "            AND w.attempt_id != t.current_attempt_id)"
        # 2026-11-05: 8 个历史工位停在 awaiting_review/approved，租约早已
        # release、lease_until 也早过期，但没有任何规则回收它们。这里按
        # 「没有活租约 + 租约已过期」回收：归档校验通过才删（见
        # archive_and_recycle）。awaiting_human/plan_drift/impl_drift 是可
        # resume 的暂停态，不自动删，等持有人决定。
        "        OR (t.status NOT IN ('awaiting_human', 'plan_drift', 'impl_drift')"
        "            AND EXISTS (SELECT 1 FROM leases l"
        "                         WHERE l.attempt_id = w.attempt_id"
        "                           AND l.kind = 'development'"
        "                           AND l.lease_until <= ?)"
        "            AND NOT EXISTS (SELECT 1 FROM leases l"
        "                            WHERE l.attempt_id = w.attempt_id"
        "                              AND l.kind = 'development'"
        "                              AND l.released_at IS NULL"
        "                              AND l.lease_until > ?))"
        # 「目录残留但无租约」：active 行却一行 development lease 都没有，
        # 说明领取/租约写入在崩溃里丢了，工位是孤儿。
        "        OR (t.status NOT IN ('awaiting_human', 'plan_drift', 'impl_drift')"
        "            AND NOT EXISTS (SELECT 1 FROM leases l"
        "                            WHERE l.attempt_id = w.attempt_id"
        "                              AND l.kind = 'development'))"
        "       )"
        " ORDER BY w.rowid",
        (now.isoformat(), now.isoformat()),
    )
    # 孤儿扫描只认白名单（ORPHAN_ROOT_ALLOWLIST），并且只扫本库有 workspace
    # 行的 root。绝不再从任意 workspace 行的父目录推导 —— 2026-11-06 的
    # <tmp>\* / .worktrees\codex\* 越界删除就是那样来的。
    # ``root`` 是测试的显式注入，同样要过白名单。
    if root is not None:
        explicit_root = Path(root)
        manager_root = explicit_root
        roots = [explicit_root] if _is_allowed_orphan_root(explicit_root) else []
    else:
        roots = _orphan_roots(database)
        manager_root = roots[0] if roots else None
    manager = WorktreeManager(
        database,
        repo or CanonicalRepo.from_settings(),
        root=manager_root,
    )
    recycled: list[str] = []
    reconciled_missing: list[str] = []
    skipped: list[dict[str, str]] = []
    for row in rows:
        attempt_id = str(row["attempt_id"])
        try:
            if manager.reconcile_missing_workspace(attempt_id=attempt_id):
                reconciled_missing.append(attempt_id)
                continue
            manager.archive_and_recycle(attempt_id=attempt_id)
            recycled.append(attempt_id)
        except WorktreeError as exc:
            skipped.append({"attempt_id": attempt_id, "reason": str(exc)})
    orphans_removed, orphans_kept = _recycle_orphan_worktrees(database, roots)
    return SweepReport(
        "worktree_recycle",
        len(recycled) + len(reconciled_missing) + len(orphans_removed),
        {
            "recycled": recycled,
            "reconciled_missing": reconciled_missing,
            "skipped": skipped,
            "orphans_removed": orphans_removed,
            "orphans_kept": orphans_kept,
        },
    )


def sweep_project_trash(
    database: Database, now: datetime, repo: CanonicalRepo | None = None
) -> SweepReport:
    """回收站满 `TRASH_RETAIN_DAYS` 天的项目：连同任务一起删掉，**事件流留底**。

    为什么留 `task_events`：它是 append-only 的"发生过什么"。删掉之后，这条活为什么
    被卡住、谁审过、合入过什么提交，就再也查不到了。而"这条活现在是什么状态"是投影，
    投影跟着项目一起走没什么可惜的。

    只删自己认得的东西：归不到某个项目上的表（`command_idempotency`、`jsonl_exports`）
    一律不碰 —— 宁可留几行孤儿，也不能顺手清掉别的项目还在用的记账。
    """
    purged = purge_due_projects(database, now=now)
    return SweepReport(
        sweep="project_trash",
        actions=len(purged),
        detail={"retain_days": TRASH_RETAIN_DAYS, "purged": purged},
    )
