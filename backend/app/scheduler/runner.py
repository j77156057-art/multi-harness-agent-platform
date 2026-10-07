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
)

Sweep = Callable[[Database, datetime, "CanonicalRepo | None"], SweepReport]

#: Fixed execution order. New sweeps append themselves here (and to the test
#: that pins this list) in the task that implements them. The lease sweep runs
#: first so later sweeps observe the states it just produced (design §7.1);
#: the resource resume runs before aging so a task whose capacity just freed
#: is claimed rather than aged out in the same round; the blocked SLA runs
#: before the generic stuck check so the specific edge wins; the budget sweep
#: runs after the state-producing sweeps so holds whose attempts they just
#: ended are settled in the same round; the two reminder sweeps run last so a
#: task parked by an earlier sweep in this very round is observed in its
#: final state (they only ever write next_reminder_at).
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
    """
    moment = now or datetime.now(UTC)
    return [sweep(database, moment, repo) for _name, sweep in SWEEPS]
