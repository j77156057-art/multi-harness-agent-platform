"""Official Harness CLI installation guidance and local binary probes.

Two jobs, both read-only:

* **guidance** -- for each Harness base the platform can drive, where its CLI
  comes from officially, what the install command is per OS, what to run to
  verify it, and what still has to happen before it can work (a CLI being on
  PATH is not the same as being logged in);
* **probe** -- on *this* machine, is the command there, and what version does it
  report.

The platform never downloads anything and never runs an installer: the command
text is shown to a human, who runs it.  Every address below was read from the
vendor's own documentation; a CLI whose official channel is not confirmed is
listed with ``official_url = ""`` and no command rather than a guess.

The probe runs exactly one thing per CLI: ``<command> --version``.  No
credential file is read, no environment is dumped, and the version line is cut
to a sane length before it leaves this module.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: How long a version probe may take before it is reported as unreadable.  A
#: CLI that needs longer than this to print a version is a CLI a worker cannot
#: start either, so this is diagnostic, not a timeout to tune.
PROBE_TIMEOUT_SECONDS = 8

#: A version line longer than this is not a version line; cut it instead of
#: shipping a wall of text into the office.
VERSION_MAX_CHARS = 200

PLATFORMS = ("windows", "macos", "linux")


@dataclass(frozen=True, slots=True)
class CliInstall:
    """One Harness CLI, and how a person gets it onto a computer."""

    harness_id: str
    display_name: str
    provider: str
    #: The command the platform's adapter actually launches.  This is the
    #: first thing probed, so "installed" means "the adapter would find it".
    command: str
    #: Other official names the same CLI answers to on some versions.
    aliases: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ("--version",)
    #: The vendor's own page for this CLI.  Empty means "not confirmed", and
    #: the office says so instead of inventing a link.
    official_url: str = ""
    #: Install command per OS.  Empty for an OS the vendor does not ship to.
    install: dict[str, str] = field(default_factory=dict)
    #: What the person still has to do after installing (login, provider).
    login_note: str = ""
    #: OSes where the vendor documents a build.  Absent key = not supported.
    supported: tuple[str, ...] = PLATFORMS
    #: (os, arch) pairs the vendor documents as unsupported.
    unsupported_arch: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Anything a person must know that the command text cannot say.
    notes: tuple[str, ...] = ()


#: The catalog.  One entry per built-in Harness base (the ones
#: ``app.adapter.formal_worker.ADAPTER_TYPES`` can drive), so the office can
#: answer "what do I install on a new computer" without a declaration file.
CATALOG: tuple[CliInstall, ...] = (
    CliInstall(
        harness_id="codex",
        display_name="Codex CLI",
        provider="OpenAI",
        command="codex",
        official_url="https://developers.openai.com/codex/cli/",
        install={
            "windows": 'powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"',
            "macos": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
            "linux": "curl -fsSL https://chatgpt.com/codex/install.sh | sh",
        },
        login_note="运行 codex 并选择 Sign in with ChatGPT；也可用 API key，但需要额外配置。",
        notes=("也可以用 npm 安装：npm install -g @openai/codex。",),
    ),
    CliInstall(
        harness_id="claude-code",
        display_name="Claude Code",
        provider="Anthropic",
        command="claude",
        official_url="https://code.claude.com/docs/en/setup",
        install={
            "windows": "irm https://claude.ai/install.ps1 | iex",
            "macos": "curl -fsSL https://claude.ai/install.sh | bash",
            "linux": "curl -fsSL https://claude.ai/install.sh | bash",
        },
        login_note="运行 claude 按浏览器提示登录；需要 Pro、Max、Team、Enterprise 或 Console 账户。",
        notes=(
            "Windows 上建议先装 Git for Windows。",
            "也可以用 npm 安装：npm install -g @anthropic-ai/claude-code（官方已标记为不推荐）。",
        ),
    ),
    CliInstall(
        harness_id="opencode",
        display_name="OpenCode",
        provider="OpenCode",
        command="opencode",
        official_url="https://opencode.ai/docs/",
        install={
            "windows": "npm install -g opencode-ai",
            "macos": "curl -fsSL https://opencode.ai/install | bash",
            "linux": "curl -fsSL https://opencode.ai/install | bash",
        },
        login_note="安装后按 OpenCode 文档配置 provider；平台只负责把它启动起来。",
        notes=("Windows 上平台通过 bash 转发启动（AP_OPENCODE_LAUNCH 可覆盖）。",),
    ),
    CliInstall(
        harness_id="pi",
        display_name="Pi CLI",
        provider="Pi",
        command="pi",
        official_url="https://github.com/badlogic/pi-mono/tree/main/packages/coding-agent",
        install={
            "windows": "npm install -g @mariozechner/pi-coding-agent",
            "macos": "npm install -g @mariozechner/pi-coding-agent",
            "linux": "npm install -g @mariozechner/pi-coding-agent",
        },
        login_note="Pi 需要模型 provider；平台适配器默认把它指向本机 Ollama（AP_PI_OLLAMA_BASE_URL）。",
        notes=("没有可用的 Ollama 时，Pi 窗口会启动但无法产出结果。",),
    ),
    CliInstall(
        harness_id="qoder",
        display_name="Qoder CLI CN",
        provider="Qoder",
        command="qoderclicn",
        aliases=("qodercn", "qoder"),
        official_url="https://docs.qoder.cn/cli/installation",
        install={
            "windows": "irm https://qoder.com.cn/install.ps1 | iex",
            "macos": "curl -fsSL https://qoder.com.cn/install | bash",
            "linux": "curl -fsSL https://qoder.com.cn/install | bash",
        },
        login_note="首次运行会提示登录；自动化可用 QODERCN_PERSONAL_ACCESS_TOKEN。",
        unsupported_arch={"windows": ("arm64",)},
        notes=(
            "国内版命令是 qoderclicn；国际版命令是 qoder，两者不是同一个安装包。",
            "平台默认按 qoderclicn 探测，找不到时依次尝试 qodercn、qoder。",
        ),
    ),
    CliInstall(
        harness_id="codebuddy",
        display_name="CodeBuddy",
        provider="Tencent",
        command="codebuddy",
        aliases=("cbc",),
        official_url="https://www.codebuddy.ai/docs/cli/installation",
        install={
            "windows": "npm install -g @tencent-ai/codebuddy-code",
            "macos": "npm install -g @tencent-ai/codebuddy-code",
            "linux": "npm install -g @tencent-ai/codebuddy-code",
        },
        login_note="首次运行会显示登录方式（国内站 / 国际站 / 企业域），需要 Node.js 18.20 以上。",
        notes=("原生二进制安装（Beta）：irm https://www.codebuddy.cn/cli/install.ps1 | iex。",),
    ),
)


def host_os() -> str:
    """This machine's OS in the catalog's vocabulary."""
    return {"nt": "windows", "darwin": "macos"}.get(os.name, "linux")


def host_arch() -> str:
    """This machine's CPU family, normalised to ``arm64`` / ``x86_64``.

    Windows reports ``PROCESSOR_ARCHITECTURE``; POSIX has ``uname``.  A machine
    that reports neither is assumed x86_64, which is the common case and the
    only assumption a probe of *this* host can safely make.
    """
    text = ""
    if os.name == "nt":
        text = os.environ.get("PROCESSOR_ARCHITECTURE", "")
    else:
        uname = getattr(os, "uname", None)
        if uname is not None:
            text = uname().machine
    lowered = str(text).lower()
    if "arm64" in lowered or "aarch64" in lowered:
        return "arm64"
    return "x86_64"


def _is_supported(entry: CliInstall, target_os: str, arch: str | None) -> tuple[bool, str]:
    """Whether the vendor ships this CLI for the target, and why not."""
    if target_os not in entry.supported:
        return False, f"官方没有发布 {target_os} 版本。"
    if arch and arch in entry.unsupported_arch.get(target_os, ()):
        return False, f"官方暂不支持 {target_os} {arch}。"
    return True, ""


def _installer_payload(entry: CliInstall, target_os: str, arch: str | None) -> dict[str, Any]:
    supported, why_not = _is_supported(entry, target_os, arch)
    command = entry.install.get(target_os, "") if supported else ""
    return {
        "harness_id": entry.harness_id,
        "display_name": entry.display_name,
        "provider": entry.provider,
        "command": entry.command,
        "aliases": list(entry.aliases),
        "official_url": entry.official_url,
        "platform": target_os,
        "supported": supported,
        "unsupported_reason": why_not,
        "install_command": command,
        "verify_command": f"{entry.command} --version",
        "login_note": entry.login_note,
        "notes": list(entry.notes),
        # Filled in by ``probe``; kept in the same shape so the office reads one
        # list instead of two it has to join by hand.
        "status": "unsupported" if not supported else "unknown",
        "found_command": None,
        "path": None,
        "version": None,
        "detail": why_not or "",
    }


def catalog(*, target_os: str | None = None, arch: str | None = None) -> list[dict[str, Any]]:
    """Every catalog entry, shaped for the request's target platform."""
    resolved = target_os or host_os()
    machine = arch if arch is not None else (host_arch() if resolved == host_os() else None)
    return [_installer_payload(entry, resolved, machine) for entry in CATALOG]


def _version_of(executable: str, args: tuple[str, ...]) -> tuple[str | None, str]:
    """Run one version command; return (version line, what went wrong)."""
    kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": PROBE_TIMEOUT_SECONDS,
        # A version probe must not create files where the caller happens to sit,
        # and it must not inherit an interactive stdin.
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        # ``.cmd`` shims are scripts: Windows cannot exec them directly.
        kwargs["shell"] = True
    try:
        completed = subprocess.run([executable, *args], **kwargs)
    except subprocess.TimeoutExpired:
        return None, f"版本命令超过 {PROBE_TIMEOUT_SECONDS} 秒没有返回。"
    except OSError as exc:
        return None, f"命令无法执行：{type(exc).__name__}。"
    output = (completed.stdout or "").strip() or (completed.stderr or "").strip()
    if completed.returncode != 0:
        return None, f"版本命令返回了退出码 {completed.returncode}。"
    if not output:
        return None, "版本命令没有输出。"
    return output.splitlines()[0][:VERSION_MAX_CHARS], ""


def _adapter_resolver(harness_id: str):
    """The adapter's own binary resolver, when it has one.

    Three adapters look further than ``PATH``: Codex falls back to its Windows
    install directory, Qoder to ``~/.qoder-cn/bin``, CodeBuddy to the ``.cmd``
    shim npm writes.  If the probe only asked ``PATH`` it would call a working
    CLI "missing" while the worker started it fine -- the office would then send
    a person to reinstall something that is already there.

    Imported inside the call: this module is read by routes that must stay
    importable without pulling the whole adapter stack in.
    """
    if harness_id == "codex":
        from app.adapter.harnesses.codex import _codex_binary

        return _codex_binary
    if harness_id == "qoder":
        from app.adapter.harnesses.qoder import _qoder_binary

        return _qoder_binary
    if harness_id == "codebuddy":
        from app.adapter.harnesses.codebuddy import _codebuddy_binary

        return _codebuddy_binary
    return None


def _resolve(entry: CliInstall) -> tuple[str | None, str | None]:
    """(command name, executable path) for the first candidate that exists.

    The adapter's resolver wins because it is the thing that will actually run;
    bare names are then looked up on ``PATH`` like the worker would.
    """
    resolver = _adapter_resolver(entry.harness_id)
    if resolver is not None:
        candidate = str(resolver() or "").strip()
        if candidate:
            if Path(candidate).is_absolute():
                if Path(candidate).exists():
                    return Path(candidate).name, candidate
            else:
                found = shutil.which(candidate)
                if found:
                    return candidate, found
    for name in (entry.command, *entry.aliases):
        found = shutil.which(name)
        if found:
            return name, found
    return None, None


def _probe_one(entry: CliInstall, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload["supported"]:
        return payload
    name, found = _resolve(entry)
    if found and name:
        version, problem = _version_of(found, entry.version_args)
        payload.update(
            {
                "found_command": name,
                "path": found,
                "version": version,
                "status": "ready" if version else "version_failed",
                "detail": "" if version else f"已找到 {name}，但{problem}",
            }
        )
        return payload
    tried = "、".join((entry.command, *entry.aliases))
    payload.update(
        {
            "status": "missing",
            "detail": f"PATH 与平台已知的安装目录里都没有 {tried}；安装后需要重开一个终端，让 PATH 生效。",
        }
    )
    return payload


def probe(*, harness_id: str | None = None, target_os: str | None = None) -> list[dict[str, Any]]:
    """Catalog entries for this machine, with the local command status filled in.

    Only the host can be probed: the question is "what is installed *here*".
    Asking about another platform returns that platform's guidance with every
    status left at ``unknown``, which is the honest answer.
    """
    resolved = target_os or host_os()
    on_host = resolved == host_os()
    rows = [
        _installer_payload(entry, resolved, host_arch() if on_host else None)
        for entry in CATALOG
        if harness_id in (None, entry.harness_id)
    ]
    if not on_host:
        return rows
    by_id = {entry.harness_id: entry for entry in CATALOG}
    return [_probe_one(by_id[row["harness_id"]], row) for row in rows]


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many are ready, missing, or installed-but-unreadable."""
    counts = {"ready": 0, "missing": 0, "version_failed": 0, "unsupported": 0, "unknown": 0}
    for row in rows:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return counts


__all__ = [
    "CATALOG",
    "PLATFORMS",
    "PROBE_TIMEOUT_SECONDS",
    "CliInstall",
    "catalog",
    "host_arch",
    "host_os",
    "probe",
    "summarize",
]
