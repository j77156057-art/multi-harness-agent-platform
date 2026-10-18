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
    """
    return [
        _qoder_binary(),
        "-p",
        "--output-format",
        "json",
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
