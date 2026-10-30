"""十个市面常见 CLI 的适配器：argv 契约来自各家自己的渠道，限制照实写。

为什么是**一张规格表**而不是十个各写一遍的类：这十个 CLI 的形状是同一件事
（一条命令行 + 一种输出），不同的只有几处事实。分十份写，改一处解析就得改十遍；
写在一处，每条 CLI 的证据、限制和 flag 放在一起，谁缺什么一眼看见。

## 无人值守的三个硬条件

1. **不会停下来问人**：必须有自动批准（``--yolo`` / ``--force`` / ``--yes-always``
   / ``--allow-all-tools`` / ``--auto-approve``）。没有这个，进程会一直等一个不会
   出现的 TTY 输入 —— 这是最贵的失败（占满整个尝试超时才算失败）。
2. **会自己结束**：``-p`` / ``-x`` / ``--message`` / ``run`` 这类一次性模式，
   不是交互式会话。
3. **不需要交互式配置**：Amp 缺 key 会转去设备登录（**实测挂起 >75 秒**），Crush
   没配 provider 会让人跑交互命令。这两种在这里都变成**启动前就拒绝**，带着
   具体原因，而不是挂半小时。

## 「只读审阅」只写有硬保证的

审阅是不许动文件的。只有 CLI 自己提供了硬保证的才给 ``review``：
Gemini / Qwen 的 ``--approval-mode plan``、Cursor 的 ``--mode=ask``、Continue 的
``--readonly``、Aider 的 ``--dry-run``。其余五个（Copilot / Vibe / Amp / Crush /
iFlow）**不给**：``review_command`` 直接拒绝。iFlow 有 ``--plan``，但它的官方说明只
写了「planning without execution」，没有说是写保护 —— 不拿它冒充只读。

## 额度池

平台可以把某次尝试的模型换成池子里的（``route_environment``）。只有**文档里明确
支持改端点**的才接：Qwen（``OPENAI_BASE_URL``）、Aider（``OPENAI_API_BASE``）、
iFlow（``baseUrl``）。其余七个返回空 —— 它们各自用自己的账号，平台不假装能改。

Gemini 有个特例：``GOOGLE_GEMINI_BASE_URL`` 只接受 HTTPS（localhost 除外），而平台
的池子是 loopback 上的 http，所以它也返回空，理由写在这条规格里。

## 证据强度（每条规格的 ``evidence``）

``measured`` = 这台机器上跑过 ``--help`` 或拒绝性命令（不给 key 的那种，不花额度）；
``documented`` = 厂商文档/包元数据里的原话。没有任何一条来自记忆。
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.adapter.harnesses.cli import CliHarness, CliUnavailable
from app.adapter.protocol import HarnessRegistration

#: argv 模板里的占位符：prompt 放在这一条 CLI 文档给出的那个位置上。
PROMPT = "{prompt}"


@dataclass(frozen=True, slots=True)
class MarketCli:
    """一条 CLI 的集成事实。"""

    #: 与安装清单（``app/adapter/installers.py``）里的 ``harness_id`` 一致。
    base: str
    name: str
    provider: str
    #: 回执里的 ``registration.adapter``：说清实际跑的是哪个可执行文件。
    adapter: str
    binary: str
    aliases: tuple[str, ...] = ()
    #: 覆盖二进制的环境变量（``AP_<X>_BIN``）。
    env_binary: str = ""
    #: 无人值守跑的 argv（``{prompt}`` 标出问题放在哪）。
    headless: tuple[str, ...] = ()
    #: 只读审阅的 argv；``None`` = 这条 CLI 没有可依赖的只读模式，拒绝审阅。
    review: tuple[str, ...] | None = None
    #: 走额度池时三件事各自的变量名（base_url, api_key, model）；空 = 不支持改端点。
    pool_env: tuple[str, str, str] = ("", "", "")
    #: 走池子时指定模型的 flag（空 = 只靠环境变量）。
    model_flag: str = ""
    #: 无人值守**必须**存在的环境变量；缺一个就拒绝启动（而不是挂着等登录）。
    requires: tuple[str, ...] = ()
    #: 需要对操作者说清的限制（每条都能追溯到上面的调查）。
    limits: tuple[str, ...] = field(default_factory=tuple)
    #: 这条事实的证据强度与出处。
    evidence: str = ""

    def __post_init__(self) -> None:
        for template in (self.headless, self.review or ()):
            if template.count(PROMPT) > 1:
                raise ValueError(f"{self.base}: argv has more than one {PROMPT}")


#: 安装位置不在 PATH 上时的补充查找（npm 全局 shim 的两种落点）。
NPM_SHIM_DIRS = (
    Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "npm",
    Path.home() / "AppData" / "Roaming" / "npm",
)


SPECS: tuple[MarketCli, ...] = (
    # ------------------------------------------------------------- 有硬保证的
    MarketCli(
        base="gemini",
        name="Gemini CLI",
        provider="google",
        adapter="gemini-cli",
        binary="gemini",
        env_binary="AP_GEMINI_BIN",
        # 实测（gemini 0.64.0 --help）：
        #   "-p, --prompt  Run in non-interactive (headless) mode"
        #   "--approval-mode ... [choices: default, auto_edit, yolo, plan]"
        #   "--skip-trust  Trust the current workspace for this session"
        headless=("--approval-mode", "yolo", "--skip-trust", "-p", PROMPT),
        review=("--approval-mode", "plan", "--skip-trust", "-p", PROMPT),
        model_flag="--model",
        # ``-o json`` 是一个对象，字段是 ``response``（官方 headless 文档）。
        limits=(
            "端点改写要 HTTPS（localhost 除外），所以不接平台额度池。",
            "无 TTY 时也会进入 headless，但仍需 --skip-trust，否则卡在信任目录确认上。",
        ),
        evidence="measured: gemini 0.64.0 --help；documented: headless 文档的 response 字段",
    ),
    MarketCli(
        base="qwen-code",
        name="Qwen Code",
        provider="alibaba",
        adapter="qwen-code",
        binary="qwen",
        env_binary="AP_QWEN_BIN",
        # 实测（qwen 0.26.4 --help）：prompt 用**位置参数**（-p 已标 deprecated），
        #   "--approval-mode ... [choices: plan, default, auto-edit, auto, yolo]"
        #   "-o, --output-format ... [choices: text, json, stream-json]"
        headless=("--approval-mode", "yolo", "-o", "json", PROMPT),
        review=("--approval-mode", "plan", "-o", "json", PROMPT),
        pool_env=("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"),
        model_flag="--model",
        limits=(
            "安装后重开终端让 PATH 生效，否则窗口起不来。",
            "-p/--prompt 在新版本里已标记弃用，这里用位置参数。",
            "变量名有文档，但把 OPENAI_BASE_URL 指到平台池子这条**实测没跑通**："
            "qwen 0.26.4 报 transport-error（池子里的免费档是 cli 协议槽位，不是 HTTP 端点）。",
            "yolo 模式会在 stderr 上打一条「没有沙箱」的警告（不致命，但会进日志）。",
        ),
        evidence="measured: qwen 0.26.4 --help；documented: 包内 headless / model-providers 文档",
    ),
    MarketCli(
        base="cursor-cli",
        name="Cursor CLI",
        provider="cursor",
        adapter="cursor-agent",
        binary="agent",
        aliases=("cursor-agent",),
        env_binary="AP_CURSOR_BIN",
        # 文档（cursor.com/docs/cli）：``agent -p --output-format json`` 只**提议**改动，
        # 要真的写文件必须加 ``--force``（= --yolo）。
        headless=("-p", "--force", "--output-format", "json", PROMPT),
        review=("-p", "--mode=ask", "--output-format", "json", PROMPT),
        model_flag="--model",
        limits=(
            "不加 --force 时它只提议、不落盘 —— 一次「成功」的尝试会交出空活。",
            "命令名是 agent（旧版本叫 cursor-agent），两者都认。",
        ),
        evidence="documented: cursor.com/docs/cli 与 /cli/headless（--force 才写入、json 的 .result）",
    ),
    MarketCli(
        base="continue-cli",
        name="Continue CLI",
        provider="continue",
        adapter="continue-cli",
        binary="cn",
        env_binary="AP_CONTINUE_BIN",
        # 实测（@continuedev/cli 1.5.47 --help）：-p/--print、--auto、--readonly、--format json
        headless=("-p", "--auto", "--format", "json", PROMPT),
        review=("-p", "--readonly", "--format", "json", PROMPT),
        limits=(
            "它的 --format json 不是包装层：CLI 往提示词里塞一句「必须回 JSON」，"
            "输出是模型自己写的 JSON，没有固定字段 —— 所以这里按纯文本处理。",
            "改端点只能靠 config.yaml 的 models[].apiBase，没有环境变量，不接池子。",
        ),
        evidence="measured: cn 1.5.47 --help；documented: 包内 format=json 的提示词注入",
    ),
    MarketCli(
        base="aider",
        name="Aider",
        provider="aider",
        adapter="aider-chat",
        binary="aider",
        env_binary="AP_AIDER_BIN",
        # 文档（aider 0.86.2 的 args.py）：
        #   "--message/--msg/-m  Specify a single message to send the LLM, process reply then exit"
        #   "--yes-always  Always say yes to every confirmation"
        #   "--openai-api-base  Specify the api base url" -> OPENAI_API_BASE
        headless=("--message", PROMPT, "--yes-always", "--no-stream"),
        # --dry-run 明确「不修改文件」，是目前能拿到的最近似只读的模式。
        review=("--message", PROMPT, "--dry-run", "--yes-always", "--no-stream"),
        pool_env=("OPENAI_API_BASE", "OPENAI_API_KEY", ""),
        model_flag="--model",
        limits=(
            "位置参数是**文件路径**不是提示词，问题只能走 --message。",
            "aider-chat 0.86.2 要求 Python <3.13；本机是 3.13，直接 pip install 会装到 0.16.0（flag 全不一样）。",
            "只输出纯文本，没有 JSON 模式。",
        ),
        evidence="documented: aider-chat 0.86.2 包内 aider/args.py 的 argparse 定义",
    ),
    # ------------------------------------------------- 能力有限、限制照实写的
    MarketCli(
        base="copilot-cli",
        name="GitHub Copilot CLI",
        provider="github",
        adapter="copilot-cli",
        binary="copilot",
        env_binary="AP_COPILOT_BIN",
        # 实测（copilot 1.0.105 --help）：
        #   "-p, --prompt <text>  Execute a prompt in non-interactive mode (exits after..."
        #   "--allow-all-tools  ... required for non-interactive mode"
        headless=("-p", PROMPT, "--allow-all-tools", "--output-format", "json"),
        review=None,
        limits=(
            "它的 json 是逐行事件流，最终答案的字段名在可达文档里查不到 —— 解析按容错处理，认不出就留原文。",
            "--mode plan 只被描述成「从 plan 模式开始」，不是写保护，所以不拿它做审阅。",
            "非交互模式必须加 --allow-all-tools，否则它停下来问人。",
        ),
        evidence="measured: copilot 1.0.105 --help（含 'required for non-interactive mode' 原话）",
    ),
    MarketCli(
        base="mistral-vibe",
        name="Mistral Vibe",
        provider="mistral",
        adapter="mistral-vibe",
        binary="vibe",
        env_binary="AP_VIBE_BIN",
        # 文档（mistral-vibe README）：
        #   "vibe --prompt \"...\" --auto-approve"
        #   "--auto-approve, --yolo: Approves all tool calls without prompting"
        #   "--output text|json|streaming"
        headless=("--prompt", PROMPT, "--auto-approve", "--output", "json"),
        review=None,
        limits=(
            "唯一的只读向模式是 --agent ask，而它「要审批」就等于无人值守时会一直等 —— 所以不做审阅。",
            "json 输出的最终消息字段没有文档，解析容错 + 认不出就留原文。",
            "官方只保证 UNIX 环境，Windows 上是次要平台。",
        ),
        evidence="documented: mistral-vibe 3.0.4 包内 README（--auto-approve / --output / UNIX 优先）",
    ),
    MarketCli(
        base="amp",
        name="Amp",
        provider="sourcegraph",
        adapter="amp-code",
        binary="amp",
        env_binary="AP_AMP_BIN",
        # 实测（amp --help）：
        #   "-x, --execute [message]  In execute mode, agent will execute provided prompt"
        #   "amp.dangerouslyAllowAll  Disable all command confirmation prompts"
        headless=("-x", PROMPT, "--stream-json"),
        review=None,
        # 没有自动批准 flag，只有设置项；缺 key 会转设备登录（实测挂起 >75 秒）。
        requires=("AMP_API_KEY",),
        limits=(
            "自动批准只有设置项 amp.dangerouslyAllowAll（或 amp permissions add），没有命令行 flag。",
            "没有 AMP_API_KEY 时会转去设备登录并一直等 —— 这里改为启动前直接拒绝。",
            "AMP_URL 指向的是 Amp 自己的服务，不是 OpenAI 兼容端点，所以不接池子。",
        ),
        evidence="measured: amp 0.0.1793363705 --help；实测无 key 时挂起 >75 秒后被杀",
    ),
    MarketCli(
        base="crush",
        name="Crush",
        provider="charm",
        adapter="crush-run",
        binary="crush",
        env_binary="AP_CRUSH_BIN",
        # 实测（crush v0.99.0）：
        #   "crush run [prompt...] [--quiet]  Run a single prompt in non-interactive mode and exit"
        #   `crush run -y` -> "Unknown shorthand flag: 'y' in -y."（-y 只属于交互式根命令）
        headless=("run", "--quiet", PROMPT),
        review=None,
        # 没有 provider 时它立刻退出并说 "No providers configured"，所以用一项配置做门槛。
        limits=(
            "run 子命令没有自动批准 flag（-y/--yolo 只属于交互式根命令），批准要写进配置的 permissions.allowed_tools。",
            "没配 provider 时会要求跑交互式命令去配 —— 这种机器上无人值守跑不了。",
            "只输出纯文本。",
        ),
        evidence="measured: crush v0.99.0 的 run --help 与 -y/--yolo 被拒的原始报错",
    ),
    MarketCli(
        base="iflow",
        name="iFlow CLI",
        provider="iflow",
        adapter="iflow-cli",
        binary="iflow",
        env_binary="AP_IFLOW_BIN",
        # 实测（iflow 0.5.19 --help）：
        #   "-p, --prompt  Prompt. Appended to input on stdin (if any)."
        #   "-y, --yolo  Automatically accept all actions"
        #   "--plan  Use plan mode (planning without execution)"
        headless=("-p", PROMPT, "-y"),
        review=None,
        pool_env=("baseUrl", "apiKey", "modelName"),
        limits=(
            "只输出纯文本，没有 JSON 模式。",
            "--plan 的官方说明只写「规划不执行」，没说是写保护，所以不做审阅。",
            "缺认证时它立刻退出并列出可用的环境变量名（不会挂），这一点比 Amp 好。",
        ),
        evidence="measured: iflow 0.5.19 --help 与缺认证时的原始报错（列出 apiKey/baseUrl/modelName）",
    ),
)


class MarketCliAdapter(CliHarness):
    """一条 ``MarketCli`` 规格变成一个 ``CliHarness``。

    子类只做一件事：把 ``spec`` 指过去。这样十份契约与它们各自的限制写在同一个
    文件里，而注册/回执/§7 那十六个动词全部走基类。
    """

    spec: MarketCli

    def __init__(
        self,
        client: Any,
        *,
        prefix: str = "/api/v1",
        harness_id: str | None = None,
    ) -> None:
        spec = type(self).spec
        identity = harness_id or spec.base
        registration = HarnessRegistration(
            harness_id=identity,
            name=spec.name if identity == spec.base else f"{spec.name} · {identity}",
            provider=spec.provider,
            adapter=spec.adapter,
            adapter_version="1.0.0",
            capabilities=("coding",),
            registration_version="reg-001",
        )
        super().__init__(client, registration, prefix=prefix)

    # ------------------------------------------------------------- 可执行文件

    def _binary(self) -> str:
        """PATH → 覆盖变量 → npm 全局 shim 目录 → 原名（让 runner 报 not found）。"""
        if self.spec.env_binary:
            override = os.environ.get(self.spec.env_binary, "").strip()
            if override:
                return override
        for name in (self.spec.binary, *self.spec.aliases):
            found = shutil.which(name)
            if found:
                return found
        if os.name == "nt":
            for name in (self.spec.binary, *self.spec.aliases):
                for directory in NPM_SHIM_DIRS:
                    for suffix in (".cmd", ".exe"):
                        candidate = directory / f"{name}{suffix}"
                        if candidate.exists():
                            return str(candidate)
        return self.spec.binary

    # ------------------------------------------------------------------ argv

    def _argv(self, template: tuple[str, ...], prompt: str) -> list[str]:
        argv = [self._binary()]
        for part in template:
            argv.append(prompt if part == PROMPT else part)
        route = self.provider_route
        if route is not None and self.spec.model_flag:
            model = str(route.model or "").strip()
            if model:
                argv.extend([self.spec.model_flag, model])
        return argv

    def command(self, work_order: str, worktree: Path) -> list[str]:
        missing = [name for name in self.spec.requires if not os.environ.get(name, "").strip()]
        if missing:
            # 拒绝而不是挂着：没有这些变量的 CLI 会转去交互式登录，
            # 一次尝试要占满整个超时才知道失败。
            raise CliUnavailable(
                f"{self.spec.binary}: 需要 {'、'.join(missing)}（无人值守无法完成交互式登录）"
            )
        return self._argv(self.spec.headless, work_order)

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        if self.spec.review is None:
            raise CliUnavailable(
                f"{self.spec.base}: 这条 CLI 没有可依赖的只读模式，不能当审阅者"
            )
        return self._argv(self.spec.review, prompt)

    # ------------------------------------------------------------------ 池子

    def route_environment(self) -> dict[str, str]:
        route = self.provider_route
        base_env, key_env, model_env = self.spec.pool_env
        if route is None or not base_env or not key_env:
            return {}
        env = {base_env: route.base_url, key_env: route.token}
        if model_env and route.model:
            env[model_env] = route.model
        return env

    # ------------------------------------------------------------------ 输出

    def parse_output(self, stdout: str) -> dict[str, Any]:
        """读出**最后一段人话**，容错到底。

        十个 CLI 的输出形状不统一：有的一个对象（``response``）、有的一个数组
        （最后一个元素的 ``result``）、有的逐行事件（``type":"result"``）、有的是
        纯文本。认不出来时**原样留下尾巴**，绝不返回空 —— 空会让上游把"它说了话"
        读成"它什么都没说"。
        """
        parsed: dict[str, Any] = {}
        text = stdout.strip()
        if not text:
            return parsed

        # 1) 整体是一个 JSON 值（Gemini 的对象 / Qwen 的数组）
        try:
            document = json.loads(text)
        except ValueError:
            document = None
        if isinstance(document, dict):
            _absorb(parsed, document)
        elif isinstance(document, list):
            for entry in reversed(document):
                if isinstance(entry, dict):
                    _absorb(parsed, entry)
                    if "final_message" in parsed:
                        break

        # 2) 逐行事件（Copilot / Amp / Qwen 的 stream-json / Gemini 的 stream-json）
        if "final_message" not in parsed:
            for line in stdout.splitlines():
                candidate = line.strip()
                if not candidate.startswith("{"):
                    continue
                try:
                    event = json.loads(candidate)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                for key in ("session_id", "sessionId", "conversation_id", "thread_id"):
                    value = event.get(key)
                    if isinstance(value, str) and value.strip():
                        parsed.setdefault("session_id", value)
                _absorb(parsed, event)
                nested = event.get("item")
                if isinstance(nested, dict):
                    _absorb(parsed, nested)

        # 3) 纯文本（Aider / Crush / iFlow / Continue 的模型自写 JSON 也会落到这里）
        if "final_message" not in parsed:
            parsed["final_message"] = text[-4000:]
        return parsed


def _block_text(blocks: Any) -> str:
    """``content: [{"type":"text","text":"..."}]`` —— 只取文本块。"""
    if not isinstance(blocks, list):
        return ""
    parts = [
        str(block.get("text") or "")
        for block in blocks
        if isinstance(block, dict) and str(block.get("type") or "") in {"text", "output_text"}
    ]
    return "".join(part for part in parts if part.strip())


def _absorb(parsed: dict[str, Any], event: dict[str, Any]) -> None:
    """从一个对象里尽力取出最终文本与会话 id。"""
    for key in ("session_id", "sessionId", "conversation_id", "thread_id"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            parsed.setdefault("session_id", value)
    for key in ("result", "response", "text", "content", "message", "output"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            parsed["final_message"] = value
        elif isinstance(value, dict):
            inner = value.get("content")
            text = _block_text(inner)
            if text:
                parsed["final_message"] = text
            elif isinstance(value.get("text"), str) and value["text"].strip():
                parsed["final_message"] = value["text"]
        elif isinstance(value, list):
            text = _block_text(value)
            if text:
                parsed["final_message"] = text
    # Vibe 的导出形状：messages: [{role, content: [blocks]}]
    messages = event.get("messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue
            if str(message.get("role") or "assistant") not in {"assistant", "model"}:
                continue
            text = _block_text(message.get("content")) or str(message.get("text") or "")
            if text.strip():
                parsed["final_message"] = text
                break


# --------------------------------------------------------------- 十个适配器


class GeminiCliAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "gemini")


class QwenCodeAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "qwen-code")


class CursorCliAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "cursor-cli")


class ContinueCliAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "continue-cli")


class AiderAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "aider")


class CopilotCliAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "copilot-cli")


class MistralVibeAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "mistral-vibe")


class AmpAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "amp")


class CrushAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "crush")


class IFlowAdapter(MarketCliAdapter):
    spec = next(spec for spec in SPECS if spec.base == "iflow")


#: ``base`` -> 适配器类。``formal_worker.ADAPTER_TYPES`` 直接合并这一份。
MARKET_ADAPTERS: dict[str, type[MarketCliAdapter]] = {
    "gemini": GeminiCliAdapter,
    "qwen-code": QwenCodeAdapter,
    "cursor-cli": CursorCliAdapter,
    "continue-cli": ContinueCliAdapter,
    "aider": AiderAdapter,
    "copilot-cli": CopilotCliAdapter,
    "mistral-vibe": MistralVibeAdapter,
    "amp": AmpAdapter,
    "crush": CrushAdapter,
    "iflow": IFlowAdapter,
}

__all__ = [
    "MARKET_ADAPTERS",
    "PROMPT",
    "SPECS",
    "AiderAdapter",
    "AmpAdapter",
    "ContinueCliAdapter",
    "CopilotCliAdapter",
    "CrushAdapter",
    "CursorCliAdapter",
    "GeminiCliAdapter",
    "IFlowAdapter",
    "MarketCli",
    "MarketCliAdapter",
    "MistralVibeAdapter",
    "QwenCodeAdapter",
]
