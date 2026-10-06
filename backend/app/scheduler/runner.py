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
from app.scheduler.report import SweepReport
from app.scheduler.sweeps import sweep_expired_leases

Sweep = Callable[[Database, datetime], SweepReport]

#: Fixed execution order. New sweeps append themselves here (and to the test
#: that pins this list) in the task that implements them.
SWEEPS: tuple[tuple[str, Sweep], ...] = (("expired_leases", sweep_expired_leases),)


def run_all_sweeps(database: Database, *, now: datetime | None = None) -> list[SweepReport]:
    """Run every sweep once, in order, and return their reports."""
    moment = now or datetime.now(UTC)
    return [sweep(database, moment) for _name, sweep in SWEEPS]
