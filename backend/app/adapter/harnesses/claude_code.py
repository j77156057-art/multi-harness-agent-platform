"""Claude Code adapter (R10 §25 phase 5, priority 2).

The argv contract targets the ``claude -p`` print mode (verified against
Claude Code 2.1.274 on the development machine):

    claude -p --output-format json --permission-mode <mode> "<work order>"

* ``-p``                    non-interactive: print the result and exit
* ``--output-format json``  one result object on stdout, easy to parse
* ``--permission-mode``     default ``acceptEdits``: the harness may edit and
                            commit in its worktree without a human at the
                            prompt. Passing ``bypassPermissions`` is an
                            explicit deployment decision (R10 §17), not a
                            default this adapter makes.

With ``--output-format json`` stdout is a single object: ``result`` is the
final message, ``session_id`` and ``total_cost_usd`` are kept for
traceability.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

CLAUDE_CODE_REGISTRATION = HarnessRegistration(
    harness_id="claude-code",
    name="Claude Code",
    provider="anthropic",
    adapter="claude-print",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


class ClaudeCodeAdapter(CliHarness):
    binary = "claude"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        permission_mode: str = "acceptEdits",
    ) -> None:
        super().__init__(client, CLAUDE_CODE_REGISTRATION, prefix=prefix)
        self.permission_mode = permission_mode

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return [
            "claude",
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
            work_order,
        ]

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """``--output-format json`` yields one result object; be tolerant."""
        payload = stdout.strip()
        if not payload:
            return {}
        try:
            result = json.loads(payload)
        except json.JSONDecodeError:
            return {}
        if not isinstance(result, dict):
            return {}
        parsed: dict[str, Any] = {}
        if isinstance(result.get("session_id"), str):
            parsed["session_id"] = result["session_id"]
        if isinstance(result.get("result"), str):
            parsed["final_message"] = result["result"]
        if isinstance(result.get("total_cost_usd"), int | float):
            parsed["cost_usd"] = result["total_cost_usd"]
        if result.get("is_error") is True:
            parsed["is_error"] = True
        return parsed
