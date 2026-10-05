"""OpenCode adapter (R10 §25 phase 5, priority 3).

The argv contract targets ``opencode run`` (verified against OpenCode v2.0.22
on the development machine):

    opencode run --format json --auto "<work order>"

* ``run``            non-interactive: send one message and exit
* ``--format json``  machine-readable output
* ``--auto``         auto-approve permissions that are not explicitly denied
                     -- an unattended attempt cannot answer prompts; what a
                     deployment denies stays denied (R10 §17).

OpenCode's JSON output is event-shaped but its exact schema is not pinned by
this adapter; the parser takes the session id if one is present and keeps the
last text-like event as the final message, falling back to the raw tail.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

OPENCODE_REGISTRATION = HarnessRegistration(
    harness_id="opencode",
    name="OpenCode",
    provider="sst",
    adapter="opencode-run",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


class OpenCodeAdapter(CliHarness):
    binary = "opencode"

    def __init__(self, client: Any, *, prefix: str = "/api/v1") -> None:
        super().__init__(client, OPENCODE_REGISTRATION, prefix=prefix)

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return ["opencode", "run", "--format", "json", "--auto", work_order]

    def parse_output(self, stdout: str) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        final_message: str | None = None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            for key in ("session_id", "sessionID"):
                if isinstance(event.get(key), str) and event[key]:
                    parsed.setdefault("session_id", event[key])
            for key in ("text", "message", "content", "part"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    final_message = value
        if final_message is not None:
            parsed["final_message"] = final_message
        elif stdout.strip():
            parsed["final_message"] = stdout.strip()
        return parsed
