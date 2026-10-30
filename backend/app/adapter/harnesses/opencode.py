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
        model: str | None = None,
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
        self._pool_config_root: Path | None = None
        #: 不走池子时用哪个模型。不写死：这台机器的默认模型在地区上被拒
        #: （``This model is not available in your country``），窗口需要能说清
        #: 自己该用哪一个。
        self.model = model or os.environ.get("AP_OPENCODE_MODEL", "")

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
        # The script has to carry the **model** as well: when this window runs
        # on the platform's quota pool, the model decides which upstream answers
        # -- and a script without it quietly used the CLI's own default, which
        # on this machine is a model the provider refuses by region.
        model_flag = ""
        route = self.provider_route
        if route is not None:
            model_flag = f"--model pool/{route.model} "
        (bundle / "run.cmd").write_text(
            "@echo off\r\n"
            f'opencode run --standalone --format json --auto {model_flag}--file "%~dp0work-order.txt" '
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
        root, self._pool_config_root = self._pool_config_root, None
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)

    def use_shell_shim(self) -> bool:
        """Effective launcher: the setting, or the platform default (drill F4)."""
        launch = settings.opencode_launch
        if launch == "direct":
            return False
        if launch == "bash":
            return self.bash_path is not None
        return self.platform == "nt" and self.bash_path is not None

    def command(self, work_order: str, worktree: Path) -> list[str]:
        self._inject_mcp(worktree)
        if self._work_order_bundle is not None:
            script = self._work_order_bundle / "run.cmd"
            # Keep the script path as its own argv item.  Passing a quoted
            # ``call \"path\"`` string through Python's Windows quoting makes
            # cmd.exe see the backslashes as literal command text.
            return ["cmd.exe", "/d", "/s", "/c", "call", str(script)]
        # ``--standalone``: the CLI must run its **own** server. Without it the
        # CLI attaches to whatever background service happens to be running (on
        # this machine: the OpenCode desktop app), and a service with a newer
        # session schema than the CLI makes every run die with "Database is not
        # empty and has no session table" -- after minutes of silence.
        argv = ["opencode", "run", "--standalone", "--format", "json", "--auto"]
        route = self.provider_route
        if route is not None:
            # The platform's quota pool: the model comes from the pool, the
            # work still happens here.  The provider name matches the isolated
            # config written by ``route_environment``.
            argv += ["--model", f"pool/{route.model}"]
        elif self.model:
            # 自己 provider 的模型：``<provider>/<model>``，由人指定，平台不猜。
            argv += ["--model", self.model]
        argv.append(work_order)
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

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """A review runs with the **plan** agent: it may look, not touch.

        The default agent edits files and runs commands; a reviewer that edits
        the repository is not a reviewer.
        """
        return self._with_agent(self.command(prompt, workdir), "plan")

    @staticmethod
    def _with_agent(argv: list[str], agent: str) -> list[str]:
        if "--agent" in argv:
            index = argv.index("--agent")
            argv[index + 1] = agent
            return argv
        return [*argv[:1], "--agent", agent, *argv[1:]]

    # ---------------------------------------------------------------- 平台工具

    #: 写进工作树的配置文件（相对路径）。跑完由 ``ScopedRunner`` 删掉，
    #: 并且不算"未提交改动" —— 否则每次带工具的交付都会被判成留了脏东西。
    _MCP_FILE = "opencode.json"

    def mcp_binding(self) -> dict[str, object] | None:
        from app.agenttools import mcp_config

        binding = mcp_config.binding_for(self)
        return None if binding is None else {"url": binding.url()}

    def transient_paths(self) -> tuple[str, ...]:
        return (self._MCP_FILE,)

    def _inject_mcp(self, worktree: Path) -> None:
        """把平台工具的端点交给 OpenCode。

        两条路，取决于这个窗口怎么拿模型：

        * 走了额度池 —— 平台已经在 ``route_environment`` 里写了一份**隔离**的
          ``XDG_CONFIG_HOME``，MCP 块并进那份文件即可，用户自己的配置一点不碰；
        * 没走池子 —— 用它自己 provider 时必须读**工作目录**里的
          ``opencode.json``。那个文件写进工作树，所以声明成临时文件，跑完删掉。
        """
        from app.agenttools import mcp_config

        binding = mcp_config.binding_for(self)
        if binding is None:
            return
        fragment = binding.opencode_document()
        if self._pool_config_root is not None:
            path = self._pool_config_root / "opencode" / "opencode.json"
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
            mcp_config.write_json(path, mcp_config.merge(existing, fragment))
            return
        target = worktree / self._MCP_FILE
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        mcp_config.write_json(target, mcp_config.merge(existing, fragment))

    def route_environment(self) -> dict[str, str]:
        """The pool route for this window: an **isolated** CLI config.

        The OpenCode CLI takes its providers from its own config file, and the
        built-in ``openai`` provider only knows OpenAI's own model names -- so
        pointing ``OPENAI_BASE_URL`` at the pool is not enough: the CLI answers
        "Model unavailable" before it ever calls the pool.  (This machine's
        own config also points its ``openai`` provider at a local chain that
        was not running, which made the window look broken.)

        So the window gets a config of its own -- the user's own config is
        never touched -- with a ``pool`` provider that speaks to the platform,
        and the model comes from there.
        """
        route = self.provider_route
        if route is None:
            return {}
        root = Path(tempfile.mkdtemp(prefix="agent-platform-opencode-"))
        config = {
            "provider": {
                "pool": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "platform pool",
                    "options": {"baseURL": route.base_url, "apiKey": route.token},
                    "models": {route.model: {"name": route.model}},
                }
            }
        }
        (root / "opencode").mkdir(parents=True, exist_ok=True)
        (root / "opencode" / "opencode.json").write_text(
            json.dumps(config, indent=2), encoding="utf-8"
        )
        (root / "opencode" / "auth.json").write_text("{}", encoding="utf-8")
        self._pool_config_root = root
        return {"XDG_CONFIG_HOME": str(root)}

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """The answer the CLI actually gave.

        Real opencode (the version this platform runs) prints **one event per
        line** and puts the words in a nested ``part.text``::

            {"type":"text", "part": {"type":"text", "text":"<the answer>"}}

        Reading only top-level string fields found nothing there, and the
        fallback then handed the **whole event stream** back as if it were the
        answer: the member's opinion was recorded as raw JSON, and the group
        chat showed a wall of ``{"type":"step_start"...}``.

        So: read the nested part, and when the output really is an event
        stream, do not fall back to the raw text -- "no answer" must stay
        distinguishable from "the answer is everything the CLI printed".
        """
        parsed: dict[str, Any] = {}
        final_message: str | None = None
        saw_event = False
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
            saw_event = True
            for key in ("session_id", "sessionID"):
                if isinstance(event.get(key), str) and event[key]:
                    parsed.setdefault("session_id", event[key])
            for key in ("text", "message", "content"):
                value = event.get(key)
                if isinstance(value, str) and value.strip():
                    final_message = value
            part = event.get("part")
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    final_message = text
            message = event.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    final_message = content
                elif isinstance(content, list):
                    blocks = [
                        block.get("text")
                        for block in content
                        if isinstance(block, dict)
                        and isinstance(block.get("text"), str)
                        and block["text"].strip()
                    ]
                    if blocks:
                        final_message = "\n".join(blocks)
        if final_message is not None:
            parsed["final_message"] = final_message
        elif stdout.strip() and not saw_event:
            # 只有整段输出根本不是事件流时，原文才当答案（纯文本输出的 CLI）。
            parsed["final_message"] = stdout.strip()
        return parsed
