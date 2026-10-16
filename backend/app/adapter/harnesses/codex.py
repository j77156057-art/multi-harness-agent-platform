"""Codex CLI adapter (R10 §25 phase 5, priority 1).

The argv contract targets the documented ``codex exec`` headless mode::

    codex exec --json --full-auto "<work order>"                    # POSIX
    codex exec --json --sandbox danger-full-access "<work order>"   # Windows

* ``exec``          non-interactive run; no TTY needed
* ``--json``        events on stdout as JSONL, machine-readable
* ``--full-auto``   workspace-write sandbox, no approval prompts -- what an
                    unattended attempt needs; the worktree is its blast radius

**Windows cannot use the CLI's own sandbox (drill F3, verified on codex
0.130.0-alpha.5)**: ``workspace-write`` fails when the sandbox tries to spawn
a shell (``CreateProcessAsUserW failed: 2``), so an unattended run gets
nowhere. The drill's recipe -- ``--sandbox danger-full-access`` -- is now the
Windows default, and with it comes the honest statement of the boundary:

* the CLI's filesystem sandbox is NOT part of the Windows boundary;
* what still holds: the loopback bind + Host-header guard (R10 §5), the API
  token layer when ``AP_AUTH_MODE=enforce`` (R10 §17), and the worktree as the
  working directory;
* the missing isolation is the multi-account OS-ACL piece (ADR-DEV-002 / the
  permission-matrix report's slice C), not something this adapter fakes.

``AP_CODEX_SANDBOX`` overrides the platform default: ``workspace-write`` maps
to ``--full-auto`` (for a deployment where the sandbox works, e.g. a future
codex release), ``danger-full-access`` forces the Windows default everywhere.
An unknown value refuses to start (fail-closed, same as the other settings).

The working directory is set by the worker's subprocess (``cwd=worktree``),
not a CLI flag, to keep the contract minimal. The argv contract was verified
against the real CLI in the e2e drill (codex 0.130.0-alpha.5, launched from
``AppData/Local/OpenAI/Codex/bin`` via PATH); the argv tests pin both
platforms so a version without these flags fails loudly instead of silently
misbehaving. Note: with the model generating shell commands, quoted commands
can still be mangled by cmd (drill F3's ``pathspec 'sub''``) -- that is the
CLI's own execution surface, recorded, not worked around here.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration
from app.config import settings

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

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        platform: str | None = None,
        harness_id: str = "codex",
        display_name: str | None = None,
    ) -> None:
        registration = replace(
            CODEX_REGISTRATION,
            harness_id=harness_id,
            name=display_name
            or ("Codex CLI" if harness_id == "codex" else f"Codex CLI · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)
        # Injectable so the argv tests can pin both platform branches in-process.
        self.platform = platform if platform is not None else os.name

    def sandbox_mode(self) -> str:
        """The effective sandbox mode: the setting, or the platform default."""
        mode = settings.codex_sandbox
        if mode != "auto":
            return mode
        return "danger-full-access" if self.platform == "nt" else "workspace-write"

    def command(self, work_order: str, worktree: Path) -> list[str]:
        if self.sandbox_mode() == "danger-full-access":
            return ["codex", "exec", "--json", "--sandbox", "danger-full-access", work_order]
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
