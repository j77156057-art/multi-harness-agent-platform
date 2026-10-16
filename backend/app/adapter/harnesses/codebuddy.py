"""CodeBuddy CLI adapter for unattended formal task execution."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

CODEBUDDY_REGISTRATION = HarnessRegistration(
    harness_id="codebuddy",
    name="CodeBuddy",
    provider="tencent",
    adapter="codebuddy-print",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


def _codebuddy_binary() -> str:
    configured = os.environ.get("AP_CODEBUDDY_BIN")
    if configured:
        return configured
    return shutil.which("codebuddy") or shutil.which("cbc") or "codebuddy"


class CodeBuddyAdapter(CliHarness):
    binary = "codebuddy"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        harness_id: str = "codebuddy",
        display_name: str | None = None,
    ) -> None:
        registration = replace(
            CODEBUDDY_REGISTRATION,
            harness_id=harness_id,
            name=display_name
            or ("CodeBuddy" if harness_id == "codebuddy" else f"CodeBuddy · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return [
            _codebuddy_binary(),
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--add-dir",
            str(worktree),
            work_order,
        ]

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
