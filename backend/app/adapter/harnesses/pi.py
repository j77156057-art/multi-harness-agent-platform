"""Pi CLI adapter using an isolated local Ollama provider configuration."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness
from app.adapter.protocol import HarnessRegistration

PI_REGISTRATION = HarnessRegistration(
    harness_id="pi",
    name="Pi CLI",
    provider="ollama",
    adapter="pi-print",
    adapter_version="1.0.0",
    capabilities=("coding",),
    registration_version="reg-001",
)


class PiAdapter(CliHarness):
    """Run Pi without touching the operator's global ``~/.pi/agent``."""

    binary = "pi"

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        model: str | None = None,
        base_url: str | None = None,
        config_root: Path | None = None,
        harness_id: str = "pi",
        display_name: str | None = None,
    ) -> None:
        # 平台现在按**注册身份**建适配器（``adapter_for_harness`` 会传 harness_id），
        # 所以这里照 CodexAdapter 的样子用 ``replace`` 换掉登记名 —— 否则平台上
        # 登记的窗口名与 id 对不上。
        registration = replace(
            PI_REGISTRATION,
            harness_id=harness_id,
            name=display_name or ("Pi CLI" if harness_id == "pi" else f"Pi CLI · {harness_id}"),
        )
        super().__init__(client, registration, prefix=prefix)
        self._configured_model = model or os.environ.get("AP_PI_MODEL", "")
        self.model = self._configured_model or "ollama/qwen3:8b"
        self.base_url = base_url or os.environ.get(
            "AP_PI_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"
        )
        self.config_root = Path(config_root) if config_root is not None else None
        self._attempt_dir: Path | None = None
        self._work_order_file: Path | None = None
        self._installed_models: list[str] | None = None

    # ------------------------------------------------------------ 本地模型

    def installed_models(self) -> list[str]:
        """The models this machine's Ollama actually has.

        The default (``ollama/qwen3:8b``) is a *guess*: a machine that never
        pulled that exact tag has it not, and the CLI then dies with a model
        error that says nothing about the platform.  So the window asks the
        local provider what it has and picks one of those.
        """
        if self._installed_models is not None:
            return list(self._installed_models)
        names: list[str] = []
        try:
            import httpx

            root = self.base_url.rstrip("/")
            if root.endswith("/v1"):
                root = root[: -len("/v1")]
            response = httpx.get(f"{root}/api/tags", timeout=5.0)
            if response.status_code < 400:
                payload = response.json()
                for entry in payload.get("models") or []:
                    name = str(entry.get("name") or entry.get("model") or "").strip()
                    if name and name not in names:
                        names.append(name)
        except Exception:  # pragma: no cover - the provider may be down
            names = []
        self._installed_models = names
        return list(names)

    def resolve_model(self) -> str:
        """The model this window should ask for.

        An explicitly configured model (``AP_PI_MODEL``) wins even when it is
        not installed: that is the operator's decision, and a clear failure
        beats a silent substitution.  Without one, a model that is actually
        installed is used.
        """
        if self._configured_model:
            return self._configured_model
        installed = self.installed_models()
        if installed and self.model.removeprefix("ollama/") not in installed:
            # Prefer the largest local model: it is the one the operator pulled
            # on purpose.
            return f"ollama/{installed[0]}"
        return self.model

    def _ensure_attempt_config(self) -> Path:
        if self._attempt_dir is not None:
            return self._attempt_dir
        root = self.config_root or Path(tempfile.mkdtemp(prefix="agent-platform-pi-"))
        root.mkdir(parents=True, exist_ok=True)
        route = self.provider_route
        if route is not None:
            # The platform's quota pool: the provider points at the pool and the
            # model is the pool's model id.  The local Ollama is only the
            # fallback for a window that is **not** routed to the pool.
            provider_name = "pool"
            model_id = route.model
            base_url = route.base_url
            api_key = route.token
        else:
            provider_name = "ollama"
            model_id = self.resolve_model().removeprefix("ollama/")
            base_url = self.base_url
            api_key = "ollama"
        self.model = f"{provider_name}/{model_id}"
        models = {
            "providers": {
                provider_name: {
                    "baseUrl": base_url,
                    "api": "openai-completions",
                    "apiKey": api_key,
                    "models": [
                        {
                            "id": model_id,
                            "name": "local Ollama coding model",
                            "reasoning": False,
                            "contextWindow": 32768,
                            "maxTokens": 4096,
                            "input": ["text"],
                        }
                    ],
                }
            }
        }
        (root / "models.json").write_text(json.dumps(models, indent=2), encoding="utf-8")
        (root / "auth.json").write_text("{}", encoding="utf-8")
        (root / "models-store.json").write_text("{}", encoding="utf-8")
        self._attempt_dir = root
        return root

    def prepare_work_order(self, work_order: str, worktree: Path) -> str:
        root = self._ensure_attempt_config()
        self._work_order_file = root / "work-order.md"
        self._work_order_file.write_text(work_order, encoding="utf-8", newline="\n")
        return "Follow the attached approved work order. Use the available tools to implement it."

    def process_environment(self) -> dict[str, str]:
        root = self._ensure_attempt_config()
        env = os.environ.copy()
        env["PI_CODING_AGENT_DIR"] = str(root)
        env["PI_CODING_AGENT_SESSION_DIR"] = str(root / "sessions")
        env["PI_OFFLINE"] = "1"
        return env

    def command(self, work_order: str, worktree: Path) -> list[str]:
        executable = shutil.which(self.binary) or self.binary
        if self._work_order_file is None:
            raise RuntimeError("Pi work order was not prepared")
        route = self.provider_route
        if route is not None:
            # The pool's model id carries its own slash ("provider/model"), and
            # Pi's "provider/id" spelling would split it in the wrong place.
            # Pi takes the provider and the model separately, so give it so.
            model_args = ["--provider", "pool", "--model", route.model]
        else:
            # ``self.model`` is what the isolated config was written with: the
            # pool's model when this window is routed to the pool, otherwise a
            # model this machine has installed.
            model_args = ["--model", self.model]
        return [
            executable,
            "--approve",
            *model_args,
            "--tools",
            "read,edit,write",
            "--no-extensions",
            "--no-skills",
            "--no-context-files",
            "--no-session",
            "--print",
            "@" + str(self._work_order_file),
            work_order,
        ]

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """A review runs **without tools**: the reviewer has one job -- to say
        what is unclear, what cannot be done and how the acceptance should be
        checked.

        With the tools on, Pi treated the review as a work order and went off
        reading the repository: the prompt grew from 26 thousand tokens to 645
        thousand, the run took four minutes, and it ended without an answer at
        all.  A reviewer that may act is not a reviewer.
        """
        argv = self.command(prompt, workdir)
        if "--tools" in argv:
            index = argv.index("--tools")
            del argv[index : index + 2]
            argv.insert(index, "--no-tools")
        return argv

    def parse_output(self, stdout: str) -> dict[str, Any]:
        return {"model": self.model, "provider": "ollama", "final_message": stdout.strip()}

    def cleanup_work_order(self) -> None:
        root, self._attempt_dir = self._attempt_dir, None
        self._work_order_file = None
        if root is not None and self.config_root is None:
            shutil.rmtree(root, ignore_errors=True)
