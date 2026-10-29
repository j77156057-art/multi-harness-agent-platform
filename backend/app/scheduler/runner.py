"""The sweeper runner (design §3): one loop, an ordered family, no hidden state.

Every sweep is a function from :mod:`app.scheduler.sweeps`; the runner only
fixes the order and stamps the clock. The order is load-bearing, not
convention (design §7.1): the lease sweep runs first so later sweeps observe
the states it just produced, and a test pins the sequence so an insertion in
the middle must be deliberate.

Restart-safe by construction: the runner keeps no state between calls — all
conditions live in database columns, and the only sweeper-owned state is the
``sweep_progress`` rows the stuck sweep will keep.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.db.connection import Database
from app.git.repo import CanonicalRepo
from app.scheduler.report import SweepReport
from app.scheduler.sweeps import (
    sweep_aging,
    sweep_blocked_sla,
    sweep_budget,
    sweep_dependencies,
    sweep_drift,
    sweep_escalation_reminder,
    sweep_expired_leases,
    sweep_human_wait_timeout,
    sweep_merge_recovery,
    sweep_resources,
    sweep_review_lease,
    sweep_stuck,
    sweep_verify_lease,
    sweep_verify_queue,
    sweep_worktree_recycle,
)

Sweep = Callable[[Database, datetime, "CanonicalRepo | None"], SweepReport]

#: Fixed execution order. New sweeps append themselves here (and to the test
#: that pins this list) in the task that implements them. The lease sweep runs
#: first so later sweeps observe the states it just produced (design §7.1);
#: the resource resume runs before aging so a task whose capacity just freed
#: is claimed rather than aged out in the same round; the blocked SLA runs
#: before the generic stuck check so the specific edge wins; the budget sweep
#: runs after the state-producing sweeps so holds whose attempts they just
#: ended are settled in the same round; the recycle sweep follows so a task
#: that just went terminal/rework has its dead worktree archived in the same
#: pass; the two reminder sweeps run last so a task parked by an earlier sweep
#: in this very round is observed in its final state (they only ever write
#: next_reminder_at).
SWEEPS: tuple[tuple[str, Sweep], ...] = (
    ("expired_leases", sweep_expired_leases),
    ("dependencies", sweep_dependencies),
    ("resources", sweep_resources),
    ("aging", sweep_aging),
    ("blocked_sla", sweep_blocked_sla),
    ("stuck", sweep_stuck),
    ("verify_lease", sweep_verify_lease),
    ("verify_queue", sweep_verify_queue),
    ("review_lease", sweep_review_lease),
    ("merge_recovery", sweep_merge_recovery),
    ("drift", sweep_drift),
    ("budget", sweep_budget),
    ("worktree_recycle", sweep_worktree_recycle),
    ("human_wait_timeout", sweep_human_wait_timeout),
    ("escalation_reminder", sweep_escalation_reminder),
)


def run_all_sweeps(
    database: Database,
    *,
    now: datetime | None = None,
    repo: CanonicalRepo | None = None,
) -> list[SweepReport]:
    """Run every sweep once, in order, and return their reports.

    ``repo`` reaches the drift sweep's ancestry check; the other sweeps take it
    and ignore it (one uniform signature beats a special case).

    One sweep that raises must **not** take the others down with it.  The
    sweeper is this group's periodic maintenance (recycle worktrees, recover
    half-finished merges, remind about escalations): one bad row — a worktree
    whose git config a member CLI corrupted, say — used to kill the process,
    and then *nothing* was maintained any more, silently
    (2026-10-29: ``sweep_worktree_recycle`` raised on a poisoned worktree and
    the scheduler exited; the platform stopped recycling worktrees and stopped
    recovering stuck merges, with nothing in the group saying so).
    """
    moment = now or datetime.now(UTC)
    reports: list[SweepReport] = []
    for name, sweep in SWEEPS:
        try:
            reports.append(sweep(database, moment, repo))
        except Exception as exc:  # noqa: BLE001 - one sweep must not kill the rest
            # 少跑一个 sweep 只是少做一件事；进程死了，下一件也没人做了。
            reports.append(
                SweepReport(
                    name,
                    0,
                    {"error": f"{type(exc).__name__}: {exc}"},
                )
            )
    return reports
