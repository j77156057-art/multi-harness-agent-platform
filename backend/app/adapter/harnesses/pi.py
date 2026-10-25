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
        self.model = model or os.environ.get("AP_PI_MODEL", "ollama/qwen3:8b")
        self.base_url = base_url or os.environ.get(
            "AP_PI_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"
        )
        self.config_root = Path(config_root) if config_root is not None else None
        self._attempt_dir: Path | None = None
        self._work_order_file: Path | None = None

    def _ensure_attempt_config(self) -> Path:
        if self._attempt_dir is not None:
            return self._attempt_dir
        root = self.config_root or Path(tempfile.mkdtemp(prefix="agent-platform-pi-"))
        root.mkdir(parents=True, exist_ok=True)
        models = {
            "providers": {
                "ollama": {
                    "baseUrl": self.base_url,
                    "api": "openai-completions",
                    "apiKey": "ollama",
                    "models": [
                        {
                            "id": self.model.removeprefix("ollama/"),
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
        return [
            executable,
            "--approve",
            "--model",
            self.model,
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

    def parse_output(self, stdout: str) -> dict[str, Any]:
        return {"model": self.model, "provider": "ollama", "final_message": stdout.strip()}

    def cleanup_work_order(self) -> None:
        root, self._attempt_dir = self._attempt_dir, None
        self._work_order_file = None
        if root is not None and self.config_root is None:
            shutil.rmtree(root, ignore_errors=True)
