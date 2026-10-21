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
    found = shutil.which("codebuddy") or shutil.which("cbc")
    if found and os.name == "nt" and not found.lower().endswith((".exe", ".cmd", ".bat", ".com")):
        # npm installs a bash shim next to the .cmd; `shutil.which` may hand back
        # the extensionless one, which Windows cannot execute at all (the child
        # dies with "system cannot find the file"). Prefer the .cmd.
        for suffix in (".cmd", ".exe", ".bat"):
            candidate = found + suffix
            if os.path.exists(candidate):
                return candidate
    return found or "codebuddy"


def codebuddy_command(worktree: Path) -> list[str]:
    """The one unattended argv contract for the CodeBuddy CLI.

    Module-level for the same reason as `qoder_command`: a second caller reuses
    the exact argv the worker uses instead of keeping a drifting copy.

    **The work order is not in this argv.** Two separate CLI defects were
    measured against the real binary:

    * ``--add-dir`` is variadic (``--add-dir <directories...>``), so anything
      positional written after it is swallowed as another directory — the CLI
      then ran with an empty prompt and returned ``"result": ""``
      (``data/formal-runs/FD-d5c10f46dae09e42.failure.json``).
    * A *multi-line positional* prompt is truncated at the first newline. With
      a four-line prompt the CLI's own transcript contained only
      ``<user_query>Line1 alpha</user_query>``; the model therefore never saw
      the task specification and correctly refused to invent one
      (``data/formal-runs/FD-8438d596ff9225e6.failure.json``).

    CodeBuddy's documented pipe mode (``-p`` is "useful for pipes") preserves
    newlines, so the order goes on stdin and the argv stays path-only. That also
    keeps the argv short, away from the Windows command-line length limit.
    """
    return [
        _codebuddy_binary(),
        "-p",
        "--output-format",
        "stream-json",
        "--permission-mode",
        "bypassPermissions",
        "--add-dir",
        str(worktree),
    ]


def _iter_json_payloads(stdout: str):
    """Yield the JSON objects a CodeBuddy transcript contains.

    The CLI is not NDJSON-stable across modes/versions: the installation here
    prints **one pretty-printed JSON array** covering the whole session, while
    other invocations stream one object per line. Splitting on lines first made
    the array form fall through to the raw-text fallback, so `final_message`
    became a tail of JSON syntax instead of the answer -- and `session_id` was
    never captured.
    """
    text = (stdout or "").strip()
    if text:
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = None
        if isinstance(document, dict):
            yield document
            return
        if isinstance(document, list):
            for item in document:
                if isinstance(item, dict):
                    yield item
            return

    for line in (stdout or "").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            yield payload


class CodeBuddyAdapter(CliHarness):
    binary = "codebuddy"
    #: The order travels on stdin (see :func:`codebuddy_command`).
    prompt_via_stdin = True

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
        # `work_order` is deliberately unused: it goes on stdin (prompt_via_stdin).
        return codebuddy_command(worktree)

    def parse_output(self, stdout: str) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for payload in _iter_json_payloads(stdout):
            for key in ("session_id", "sessionId", "conversation_id"):
                if isinstance(payload.get(key), str) and payload[key]:
                    parsed.setdefault("session_id", payload[key])
            for key in ("result", "text", "message", "content"):
                if isinstance(payload.get(key), str) and payload[key].strip():
                    parsed["final_message"] = payload[key]
        if "final_message" not in parsed and stdout.strip():
            parsed["final_message"] = stdout.strip()[-4000:]
        return parsed
