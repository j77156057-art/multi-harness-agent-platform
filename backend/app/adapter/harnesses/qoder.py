"""Qoder CLI CN adapter for unattended formal task execution."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

QODER_REGISTRATION = HarnessRegistration(
    harness_id="qoder",
    name="Qoder CLI CN",
    provider="qoder",
    adapter="qoderclicn-print",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


def _qoder_binary() -> str:
    configured = os.environ.get("AP_QODER_BIN")
    if configured:
        return configured
    for name in ("qoderclicn", "qodercn", "qoder"):
        found = shutil.which(name)
        if found:
            return found
    # The CN installer writes this path but a long-running worker may have an
    # older PATH. Keep the fallback local and deterministic.
    candidate = Path.home() / ".qoder-cn" / "bin" / "qoderclicn" / "qoderclicn.exe"
    return str(candidate) if candidate.exists() else "qoderclicn"


def qoder_command(work_order: str, worktree: Path) -> list[str]:
    """The one unattended argv contract for the Qoder CN CLI.

    Module-level so a second caller (the admin harness's refine call) reuses the
    exact argv the worker uses instead of keeping a drifting copy.

    ``--output-format stream-json`` (not ``json``): measured against the real
    binary (qoderclicn 1.1.71), ``json`` writes **one** object when the run
    ends -- 0 bytes for 21 seconds, then 1598 -- while ``stream-json`` writes an
    event per line while the model works (0 bytes at 3s, 3.6 KB at 6s, 10 KB at
    15s). The stream's last event is a ``result`` event with the same fields
    the single object carried, and this CLI's own notices ("Skill command ...
    was renamed") arrive as plain lines that the parser skips.

    Unlike claude, this CLI has no ``--verbose`` and rejects it.
    """
    return [
        _qoder_binary(),
        "-p",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "-w",
        str(worktree),
        work_order,
    ]


class QoderAdapter(CliHarness):
    binary = "qoderclicn"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        harness_id: str = "qoder",
        display_name: str | None = None,
    ) -> None:
        registration = replace(
            QODER_REGISTRATION,
            harness_id=harness_id,
            name=display_name
            or ("Qoder CLI CN" if harness_id == "qoder" else f"Qoder CLI CN · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return qoder_command(work_order, worktree)

    def parse_output(self, stdout: str) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for line in stdout.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            for key in ("session_id", "sessionId", "conversation_id"):
                if isinstance(payload.get(key), str) and payload[key]:
                    parsed.setdefault("session_id", payload[key])
            for key in ("result", "text", "message", "content"):
                if isinstance(payload.get(key), str) and payload[key].strip():
                    parsed["final_message"] = payload[key]
        if "final_message" not in parsed and stdout.strip():
            parsed["final_message"] = stdout.strip()[-4000:]
        return parsed
