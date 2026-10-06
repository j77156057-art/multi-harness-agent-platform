"""The sweeper family (docs/proposals/sweeper-family.md).

`sweeps.py` holds the individual sweeps, `runner.py` orders them, and
`python -m app.scheduler` runs them as a process.
"""

from app.scheduler.report import SweepReport
from app.scheduler.runner import SWEEPS, run_all_sweeps
from app.scheduler.sweeps import (
    sweep_aging,
    sweep_blocked_sla,
    sweep_dependencies,
    sweep_expired_leases,
    sweep_merge_recovery,
    sweep_resources,
    sweep_review_lease,
    sweep_stuck,
    sweep_verify_lease,
    sweep_verify_queue,
)

__all__ = [
    "SWEEPS",
    "SweepReport",
    "run_all_sweeps",
    "sweep_aging",
    "sweep_blocked_sla",
    "sweep_dependencies",
    "sweep_expired_leases",
    "sweep_merge_recovery",
    "sweep_resources",
    "sweep_review_lease",
    "sweep_stuck",
    "sweep_verify_lease",
    "sweep_verify_queue",
]
