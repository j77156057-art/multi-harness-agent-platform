"""Codex CLI adapter (R10 §25 phase 5, priority 1).

The argv contract targets the documented ``codex exec`` headless mode:

    codex exec --json --full-auto "<work order>"

* ``exec``          non-interactive run; no TTY needed
* ``--json``        events on stdout as JSONL, machine-readable
* ``--full-auto``   workspace-write sandbox, no approval prompts -- what an
                    unattended attempt needs; the worktree is its blast radius

The working directory is set by the worker's subprocess (``cwd=worktree``),
not a CLI flag, to keep the contract minimal. ``codex`` was not installed on
the development machine, so unlike the other two adapters this contract is
from documentation rather than a live ``--help``; the argv test pins it so a
version without these flags fails loudly instead of silently misbehaving.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

CODEX_REGISTRATION = HarnessRegistration(
    harness_id="codex",
    name="Codex CLI",
    provider="openai",
    adapter="codex-exec",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


class CodexAdapter(CliHarness):
    binary = "codex"

    def __init__(self, client: Any, *, prefix: str = "/api/v1") -> None:
        super().__init__(client, CODEX_REGISTRATION, prefix=prefix)

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return ["codex", "exec", "--json", "--full-auto", work_order]

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """Pull what is stable out of the JSONL event stream, tolerantly.

        Codex emits one JSON object per line. Unknown event shapes are
        skipped, not fatal: the loop must not die because the CLI added a
        field. ``session_id`` is kept for traceability, the last text-like
        event as the final message.
        """
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
            for key in ("session_id", "conversation_id"):
                if isinstance(event.get(key), str) and event[key]:
                    parsed.setdefault("session_id", event[key])
            for key in ("msg", "message", "text", "content"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    final_message = value
        if final_message is not None:
            parsed["final_message"] = final_message
        return parsed
