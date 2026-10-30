"""Claude Code adapter (R10 §25 phase 5, priority 2).

The argv contract targets the ``claude -p`` print mode (verified against
Claude Code 2.1.274 on the development machine):

    claude -p --output-format stream-json --verbose --permission-mode <mode> "<work order>"

* ``-p``                    non-interactive: print the result and exit
* ``--output-format stream-json``  one JSON event per line, **as it happens**
* ``--verbose``             required by ``--print`` together with ``stream-json``
                            (the CLI refuses the combination otherwise)
* ``--permission-mode``     default ``acceptEdits``: the harness may edit and
                            commit in its worktree without a human at the
                            prompt. Passing ``bypassPermissions`` is an
                            explicit deployment decision (R10 §17), not a
                            default this adapter makes.

Why ``stream-json`` and not ``json``: with ``--output-format json`` the CLI
writes **one** object at the very end, so a run that lasts ten minutes says
nothing for ten minutes. ``stream-json`` writes an event per line while the
model works (``system`` / ``assistant`` / ``user`` / ``result``), and the final
``result`` event still carries ``result`` (the final message), ``session_id``,
``total_cost_usd`` and ``is_error`` -- the same fields the old single-object
format provided. The office shows those lines while the run is going, so
"what is this harness doing" is answered by the harness itself.

``parse_output`` therefore reads the last ``result`` event, and still accepts
a single JSON object (an older CLI, or a run that emitted exactly one line).
"""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

CLAUDE_CODE_REGISTRATION = HarnessRegistration(
    harness_id="claude-code",
    name="Claude Code",
    provider="anthropic",
    adapter="claude-print",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


class ClaudeCodeAdapter(CliHarness):
    binary = "claude"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        permission_mode: str = "acceptEdits",
        harness_id: str = "claude-code",
        display_name: str | None = None,
    ) -> None:
        registration = replace(
            CLAUDE_CODE_REGISTRATION,
            harness_id=harness_id,
            name=display_name
            or ("Claude Code" if harness_id == "claude-code" else f"Claude Code · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)
        self.permission_mode = permission_mode

    #: The Claude CLI speaks Anthropic, so the pool's Anthropic surface
    #: (``/api/v1/pool``) is the one it is pointed at.
    pool_protocol = "anthropic"

    def command(self, work_order: str, worktree: Path) -> list[str]:
        argv = ["claude"]
        git_pointer = worktree / ".git"
        if git_pointer.is_file():
            pointer = git_pointer.read_text(encoding="utf-8").strip()
            if pointer.startswith("gitdir:"):
                git_dir = Path(pointer.partition(":")[2].strip())
                if not git_dir.is_absolute():
                    git_dir = (worktree / git_dir).resolve()
                argv.extend(["--add-dir", str(git_dir)])
        command = [
            *argv,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            self.permission_mode,
        ]
        route = self.provider_route
        if route is not None:
            # The platform's quota pool.  The Claude CLI reads its provider
            # from its settings, and the *settings file given on the command
            # line* wins over the user's own settings -- so the pool is a
            # per-attempt override and the person's configuration is never
            # rewritten.
            settings_path = self._write_pool_settings(worktree, route)
            command += ["--settings", str(settings_path)]
        # 平台工具：Claude 用 ``--mcp-config`` 收一个 JSON 文件。文件写在临时
        # 目录里（``_write_mcp_config``），**不进工作树** —— 工作树里多一个文件
        # 会让这次交付被判成"留了脏东西"；令牌更不该出现在命令行上。
        if self.mcp_binding() is not None:
            command += ["--mcp-config", str(self._write_mcp_config())]
        command.append(work_order)
        return command

    # ---------------------------------------------------------------- 平台工具

    def mcp_binding(self) -> dict[str, object] | None:
        """Claude 的工具端点；没有这次尝试就返回 ``None``。"""
        from app.agenttools import mcp_config

        binding = mcp_config.binding_for(self)
        return None if binding is None else {"url": binding.url()}

    def _write_mcp_config(self) -> Path:
        """把 ``--mcp-config`` 要的那份文件写到临时目录里。"""
        from app.agenttools import mcp_config

        binding = mcp_config.binding_for(self)
        if binding is None:
            raise RuntimeError("no task context: cannot build the MCP binding")
        bundle = Path(tempfile.mkdtemp(prefix="agent-platform-claude-mcp-"))
        self._mcp_bundle = bundle
        return mcp_config.write_json(bundle / "mcp.json", binding.claude_document())

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """A review runs in **plan** mode: the reviewer may look, not touch."""
        argv = self.command(prompt, workdir)
        if "--permission-mode" in argv:
            index = argv.index("--permission-mode")
            argv[index + 1] = "plan"
        return argv

    def _write_pool_settings(self, worktree: Path, route: Any) -> Path:
        bundle = Path(tempfile.mkdtemp(prefix="agent-platform-claude-pool-"))
        self._pool_bundle = bundle
        path = bundle / "pool-settings.json"
        path.write_text(
            json.dumps(
                {
                    "env": {
                        "ANTHROPIC_BASE_URL": route.base_url,
                        "ANTHROPIC_AUTH_TOKEN": route.token,
                        "ANTHROPIC_MODEL": route.model,
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

    def cleanup_work_order(self) -> None:
        """Remove the per-attempt pool settings file."""
        bundle, self._pool_bundle = getattr(self, "_pool_bundle", None), None
        if bundle is not None:
            shutil.rmtree(bundle, ignore_errors=True)
        mcp, self._mcp_bundle = getattr(self, "_mcp_bundle", None), None
        if mcp is not None:
            shutil.rmtree(mcp, ignore_errors=True)

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """Read the final ``result`` event out of the JSONL stream.

        ``--output-format stream-json`` writes one event per line; the last
        ``result`` event carries the final message and the cost. A single JSON
        object (the old format) is still accepted, so a CLI that only emits
        one line does not lose its final message.
        """
        result = _last_result_event(stdout)
        if result is None:
            return {}
        parsed: dict[str, Any] = {}
        if isinstance(result.get("session_id"), str):
            parsed["session_id"] = result["session_id"]
        if isinstance(result.get("result"), str):
            parsed["final_message"] = result["result"]
        if isinstance(result.get("total_cost_usd"), int | float):
            parsed["cost_usd"] = result["total_cost_usd"]
        if result.get("is_error") is True:
            parsed["is_error"] = True
        return parsed


def _last_result_event(stdout: str) -> dict[str, Any] | None:
    """The last JSON object that is a ``result`` event, or the last object.

    The stream is one event per line; a line may be a whole JSON object
    (``{"type":"result",...}``) or, for a single-object run, the object
    itself. Unparseable lines are skipped rather than fatal: the stream is
    the harness's, and the CLI is free to add a field we do not know.
    """
    fallback: dict[str, Any] | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        fallback = value
        if value.get("type") == "result":
            return value
    return fallback
