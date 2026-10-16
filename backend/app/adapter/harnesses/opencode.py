"""OpenCode adapter (R10 §25 phase 5, priority 3).

The argv contract targets ``opencode run`` (verified against OpenCode v2.0.22
on the development machine):

    opencode run --format json --auto "<work order>"

* ``run``            non-interactive: send one message and exit
* ``--format json``  machine-readable output
* ``--auto``         auto-approve permissions that are not explicitly denied
                     -- an unattended attempt cannot answer prompts; what a
                     deployment denies stays denied (R10 §17).

**Windows launches through a shell (drill F4)**: a direct spawn of the exe
from a Python parent is unreliable here -- opencode starts its background
service and dies with ``Database is not empty and has no session table``
(reproducible with linked worktrees) or dumps usage instead of running the
prompt. The drill's working recipe, ``bash -lc 'exec "$0" "$@"' opencode ...``,
is now the Windows default: ``bash`` replaces this process as the parent, and
the exe ends up with a plain console parent. ``AP_OPENCODE_LAUNCH`` overrides:
``direct`` forces the old argv (POSIX default; usable on Windows if a future
version fixes the parent-process issue), ``bash`` forces the shim. When the
shim is requested but no ``bash`` is on PATH, the adapter falls back to the
direct argv rather than failing the attempt at launch time.

Recorded, not worked around: opencode aborts on the first provider error
(no retry loop, unlike codex's five) -- under a jittery relay that shows up
as a failed attempt, and the platform's rework loop is what absorbs it.

OpenCode's JSON output is event-shaped but its exact schema is not pinned by
this adapter; the parser takes the session id if one is present and keeps the
last text-like event as the final message, falling back to the raw tail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration
from app.config import settings

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

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        platform: str | None = None,
        bash_path: str | None = None,
        harness_id: str = "opencode",
        display_name: str | None = None,
    ) -> None:
        registration = replace(
            OPENCODE_REGISTRATION,
            harness_id=harness_id,
            name=display_name
            or ("OpenCode" if harness_id == "opencode" else f"OpenCode · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)
        # Injectable for the argv tests; production defaults read the host.
        # ``bash_path=None`` probes the host; an empty string means "there is
        # no bash" (how the tests pin the fallback without faking the machine).
        self.platform = platform if platform is not None else os.name
        self.bash_path = shutil.which("bash") if bash_path is None else (bash_path or None)
        self._work_order_bundle: Path | None = None

    def _needs_cmd_transport_file(self) -> bool:
        return (
            self.platform == "nt"
            and self.bash_path is not None
            and Path(self.bash_path).name.lower() == "bash.exe"
            and "windowsapps" in str(self.bash_path).lower()
        )

    def prepare_work_order(self, work_order: str, worktree: Path) -> str:
        """Move arbitrary text out of ``cmd /c`` when WindowsApps bash is used.

        ``cmd.exe`` does not preserve newlines, quotes, or metacharacters in a
        single ``/c`` argument.  OpenCode can attach a file, so the command
        line carries only a fixed sentence and a generated script path.
        """
        if not self._needs_cmd_transport_file():
            return work_order
        bundle = Path(tempfile.mkdtemp(prefix="agent-platform-opencode-"))
        (bundle / "work-order.txt").write_text(work_order, encoding="utf-8", newline="\n")
        (bundle / "run.cmd").write_text(
            "@echo off\r\n"
            'opencode run --format json --auto --file "%~dp0work-order.txt" '
            '"Read and follow the attached work order exactly."\r\n'
            "exit /b %errorlevel%\r\n",
            encoding="utf-8",
            newline="",
        )
        self._work_order_bundle = bundle
        return "Read and follow the attached work order exactly."

    def cleanup_work_order(self) -> None:
        bundle, self._work_order_bundle = self._work_order_bundle, None
        if bundle is not None:
            shutil.rmtree(bundle, ignore_errors=True)

    def use_shell_shim(self) -> bool:
        """Effective launcher: the setting, or the platform default (drill F4)."""
        launch = settings.opencode_launch
        if launch == "direct":
            return False
        if launch == "bash":
            return self.bash_path is not None
        return self.platform == "nt" and self.bash_path is not None

    def command(self, work_order: str, worktree: Path) -> list[str]:
        if self._work_order_bundle is not None:
            script = self._work_order_bundle / "run.cmd"
            # Keep the script path as its own argv item.  Passing a quoted
            # ``call \"path\"`` string through Python's Windows quoting makes
            # cmd.exe see the backslashes as literal command text.
            return ["cmd.exe", "/d", "/s", "/c", "call", str(script)]
        argv = ["opencode", "run", "--format", "json", "--auto", work_order]
        if self.use_shell_shim():
            # The WindowsApps ``bash.exe`` alias is a WSL launcher that drops
            # argv after ``-c``.  That turns the documented ``$0/$@`` shim into
            # ``/bin/bash: : No such file or directory``.  Use cmd.exe for
            # that launcher while retaining the bash recipe for real POSIX/Git
            # Bash installations and the existing adapter contract tests.
            if self.platform == "nt" and self.bash_path:
                bash_path = Path(self.bash_path)
                if bash_path.name.lower() == "bash.exe" and "windowsapps" in str(bash_path).lower():
                    # Arbitrary work orders are prepared as an attached file
                    # by ``prepare_work_order``.  Keep this fallback for
                    # callers that invoke ``command`` directly.
                    return ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)]
            # $0 = opencode, $@ = the remaining argv: the exe's parent becomes
            # this shell instead of the Python worker (see the module docstring).
            return ["bash", "-lc", 'exec "$0" "$@"', *argv]
        return argv

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
