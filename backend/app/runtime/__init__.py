"""The runtime driver (runtime-driver card, F6).

``RuntimeDriver`` is what actually runs Verify and Merge in the live platform;
:mod:`app.runtime.__main__` is its CLI. See :mod:`app.runtime.driver` for what
v1 covers and what it deliberately leaves to the sweeper family.
"""

from __future__ import annotations

from app.runtime.driver import (
    MAX_MERGES_PER_ROUND,
    MergeCandidate,
    MergeRun,
    RoundReport,
    RuntimeDriver,
    RuntimePolicy,
    RuntimePolicyError,
    ScanResult,
    Skipped,
    VerifyCandidate,
    VerifyRun,
    load_runtime_policy,
)

__all__ = [
    "MAX_MERGES_PER_ROUND",
    "MergeCandidate",
    "MergeRun",
    "RoundReport",
    "RuntimeDriver",
    "RuntimePolicy",
    "RuntimePolicyError",
    "ScanResult",
    "Skipped",
    "VerifyCandidate",
    "VerifyRun",
    "load_runtime_policy",
]
