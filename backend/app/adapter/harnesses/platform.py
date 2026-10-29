"""The platform's own Harness: the platform runs the loop, the model only talks.

Every other adapter in this package launches a CLI that brings its own tools.
This one launches ``python -m app.agenttools.cli`` -- which is also a CLI, so
nothing in the worker has to change -- and that process *is* the loop: it asks
the model what to do next, executes the tool itself inside this attempt's
worktree, and answers with the result.

Why this is an adapter and not a special case in the worker: the worker's
contract is "claim, provision a worktree, run a command in it, check what
changed inside scope, submit".  A platform harness satisfies all of it.  The
only thing that differs is *who* executes tool calls, and that is exactly what
an adapter is for.

Two facts it must not fudge:

* **The model comes from the pool.**  A platform harness has no provider of
  its own -- the whole point is to work on whatever quota the platform routed
  this attempt to, including a CLI-as-model slot that cannot call tools.  With
  no route there is no channel, and the process says so instead of producing
  an empty-handed success.
* **The token never travels in argv.**  Windows hands argv to every process on
  the machine through the command line: a token in there is a token in the
  task list.  It goes into a per-attempt file that is removed afterwards --
  the same shape the Claude adapter uses for its pool settings.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

PLATFORM_REGISTRATION = HarnessRegistration(
    harness_id="platform",
    name="平台工具",
    provider="pool",
    adapter="platform-tools",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


class PlatformToolsAdapter(CliHarness):
    """Run the platform's own tool loop as this attempt's command."""

    binary = sys.executable or "python"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        harness_id: str = "platform",
        display_name: str | None = None,
    ) -> None:
        registration = replace(
            PLATFORM_REGISTRATION,
            harness_id=harness_id,
            name=display_name
            or ("平台工具" if harness_id == "platform" else f"平台工具 · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)
        self._attempt_dir: Path | None = None
        self._order_file: Path | None = None

    # ------------------------------------------------------------ per attempt

    def _ensure_bundle(self) -> Path:
        """The model channel, in a file only this attempt reads."""
        if self._attempt_dir is not None:
            return self._attempt_dir
        root = Path(tempfile.mkdtemp(prefix="agent-platform-tools-"))
        route = self.provider_route
        bundle = {
            "base_url": "" if route is None else route.base_url,
            "token": "" if route is None else route.token,
            "model": "" if route is None else route.model,
            "max_tokens": int(os.environ.get("AP_TOOL_MAX_TOKENS", "3000") or 3000),
            "timeout": float(os.environ.get("AP_TOOL_CHANNEL_TIMEOUT", "180") or 180),
        }
        path = root / "channel.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - Windows ignores POSIX modes
            pass
        self._attempt_dir = root
        return root

    def prepare_work_order(self, work_order: str, worktree: Path) -> str:
        root = self._ensure_bundle()
        self._order_file = root / "work-order.md"
        self._order_file.write_text(work_order, encoding="utf-8", newline="\n")
        return ""

    def cleanup_work_order(self) -> None:
        root, self._attempt_dir = self._attempt_dir, None
        self._order_file = None
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)

    # ---------------------------------------------------------------- command

    def _argv(self, *, read_only: bool) -> list[str]:
        if self._attempt_dir is None or self._order_file is None:
            raise RuntimeError("the platform tool bundle was not prepared")
        executable = shutil.which(self.binary) or self.binary
        argv = [
            executable,
            "-m",
            "app.agenttools.cli",
            "--bundle",
            str(self._attempt_dir / "channel.json"),
            "--order",
            str(self._order_file),
        ]
        if read_only:
            argv.append("--read-only")
        return argv

    def command(self, work_order: str, worktree: Path) -> list[str]:
        return self._argv(read_only=False)

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """A review looks and reports; it does not edit (see ``--read-only``)."""
        return self._argv(read_only=True)

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """Read the loop's own receipt off the last marked line."""
        from app.agenttools.cli import RESULT_MARKER

        for line in reversed(stdout.splitlines()):
            stripped = line.strip()
            if not stripped.startswith(RESULT_MARKER):
                continue
            try:
                parsed = json.loads(stripped[len(RESULT_MARKER) :].strip())
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def process_environment(self) -> dict[str, str] | None:
        """Import path only: the child must find ``app.agenttools``."""
        backend = Path(__file__).resolve().parents[3]
        current = os.environ.get("PYTHONPATH", "")
        joined = str(backend) + (os.pathsep + current if current else "")
        return {"PYTHONPATH": joined}
