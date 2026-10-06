"""The individual sweeps of the family (design §2).

Each sweep is a pure function ``(database, now) -> SweepReport``: it reads
time/condition columns, applies at most one conditional transition per match
(via the owning service, never by hand), and reports what it did. ``now`` is
always injected so tests can move the clock instead of waiting for it.

This task (sweeper-runner) wires the existing lease sweep — the family's
prototype — and nothing else; the remaining sweeps arrive one task at a time
per the design's split, each adding itself to the runner's pinned order.
"""

from __future__ import annotations

from datetime import datetime

from app.db.connection import Database
from app.lease.service import expire_stale_leases
from app.scheduler.report import SweepReport


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
