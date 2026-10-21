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
import shutil
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


def _codex_binary() -> str:
    """The codex executable: PATH first, then the Windows install directory."""
    configured = os.environ.get("AP_CODEX_BIN")
    if configured:
        return configured
    found = shutil.which("codex")
    if found:
        return found
    if os.name == "nt":
        candidate = Path.home() / "AppData" / "Local" / "OpenAI" / "Codex" / "bin" / "codex.exe"
        if candidate.exists():
            return str(candidate)
    return "codex"


class CodexAdapter(CliHarness):
    binary = "codex"

    #: The work order travels on **stdin** (``codex exec -``), not in argv.
    #: The administrator's review prompt carries the whole diff: it was 45 KB
    #: as an argument, and Windows refuses a command line longer than 32,767
    #: characters -- the spawn then failed with a misleading "not found on
    #: PATH".  A pipe has no such limit.
    prompt_via_stdin = True

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
        # The Codex CLI installs itself into ``AppData/Local/OpenAI/Codex/bin``,
        # which is not on PATH unless the user adds it. Without this fallback
        # the administrator harness fails with "codex: not found on PATH" and
        # no task is ever refined. Same shape as the CodeBuddy/Qoder adapters.
        binary = _codex_binary()
        route = self.provider_route
        # ``--skip-git-repo-check`` lets the administrator's scratch directory
        # work; member worktrees are git repos, so the flag is harmless there.
        if self.sandbox_mode() == "danger-full-access":
            argv = [
                binary,
                "exec",
                "--json",
                "--skip-git-repo-check",
                "--sandbox",
                "danger-full-access",
            ]
        else:
            argv = [binary, "exec", "--json", "--skip-git-repo-check", "--full-auto"]
        if route is not None:
            # The platform's quota pool: the model comes from the pool, the
            # work still happens here.  The overrides are per attempt, so the
            # person's own codex configuration is never touched.
            argv += [
                "-c", f"model={route.model}",
                "-c", "model_provider=pool",
                "-c", "model_providers.pool.name=platform-pool",
                "-c", f"model_providers.pool.base_url={route.base_url}",
                "-c", "model_providers.pool.wire_api=responses",
                "-c", "model_providers.pool.env_key=OPENAI_API_KEY",
            ]
        # The work order is on stdin (``-``): argv has a hard length limit on
        # Windows, and a review prompt with the whole diff does not fit.
        argv.append("-")
        return argv

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """A review runs **read-only**: the reviewer may look, not write."""
        argv = self.command(prompt, workdir)
        if "--sandbox" in argv:
            index = argv.index("--sandbox")
            argv[index + 1] = "read-only"
        return argv

    def route_environment(self) -> dict[str, str]:
        route = self.provider_route
        if route is None:
            return {}
        return {"OPENAI_API_KEY": route.token}

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
            for key in ("session_id", "conversation_id", "thread_id"):
                if isinstance(event.get(key), str) and event[key]:
                    parsed.setdefault("session_id", event[key])
            for key in ("msg", "message", "text", "content"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    final_message = value
            # Real codex 0.130 puts the assistant answer one level down:
            #   {"type":"item.completed",
            #    "item":{"type":"agent_message","text":"..."}}
            # Without this branch parse_output returns no final_message, the
            # caller falls back to raw stdout, and extract_json_object grabs
            # the first JSON line (``thread.started``) as the verdict.
            item = event.get("item")
            if isinstance(item, dict) and str(item.get("type") or "") in {
                "agent_message",
                "assistant_message",
            }:
                value = item.get("text")
                if isinstance(value, str) and value.strip():
                    final_message = value
        if final_message is not None:
            parsed["final_message"] = final_message
        return parsed
