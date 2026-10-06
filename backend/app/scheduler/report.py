"""What one sweep did, in the shape the runner and the CLI report.

Kept in its own module so ``sweeps.py`` and ``runner.py`` can both import it
without a cycle: sweeps produce reports, the runner consumes them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SweepReport:
    """One sweep's outcome. ``actions`` counts applied transitions, not findings."""

    sweep: str
    actions: int
    detail: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"sweep": self.sweep, "actions": self.actions, "detail": dict(self.detail)}
