"""成员窗口的启动前置条件（启动即校验，不满足立刻失败）。

07 现场：CodeBuddy 在 Windows 上没找到 Git Bash 时会回退到 PowerShell，
它的工具集里就没有 ``Bash``；模型第一步偏偏调用 ``Bash``，收到
``Tool "Bash" does not exist in the current tool set`` 后重试三次，然后进程
活着、CPU 不动、日志不再增长 —— 平台只能等 1800 秒超时才收口。

因此把「这台机器上该 harness 需要什么」变成可检查的前置条件：worker 启动时
先校验，缺了就带着明确原因退出，**不进 ready 池、不接任务**，而不是等到
一次 attempt 白烧半小时。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

#: 稳定的失败码，报告和日志里按它检索，不去猜自然语言。
CODEBUDDY_GIT_BASH = "codebuddy_git_bash"


@dataclass(frozen=True, slots=True)
class PrerequisiteFailure:
    code: str
    detail: str


def _probe_executable(path: str, *, timeout_seconds: float = 15.0) -> str | None:
    """跑一次 ``bash -c "exit 0"``；返回问题描述，正常则 ``None``。"""
    try:
        proc = subprocess.run(
            [path, "-c", "exit 0"],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        return f"{path} cannot be executed: {exc}"
    except subprocess.TimeoutExpired:
        return f"{path} did not exit within {timeout_seconds}s"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[:200]
        return f"{path} exited {proc.returncode}: {tail}"
    return None


def codebuddy_git_bash_failure(
    *,
    env: Mapping[str, str] | None = None,
    probe: Callable[[str], str | None] = _probe_executable,
) -> PrerequisiteFailure | None:
    """CodeBuddy 必须有可执行的 Git Bash，否则模型调用 Bash 会静默挂死。"""
    values = os.environ if env is None else env
    declared = (values.get("CODEBUDDY_CODE_GIT_BASH_PATH") or "").strip()
    if not declared:
        return PrerequisiteFailure(
            CODEBUDDY_GIT_BASH,
            "CODEBUDDY_CODE_GIT_BASH_PATH is not set; CodeBuddy falls back to PowerShell"
            ' and its model keeps calling the missing "Bash" tool',
        )
    path = Path(declared)
    if not path.is_file():
        return PrerequisiteFailure(
            CODEBUDDY_GIT_BASH,
            f"CODEBUDDY_CODE_GIT_BASH_PATH points at {declared}, which is not a file",
        )
    problem = probe(str(path))
    if problem is not None:
        return PrerequisiteFailure(CODEBUDDY_GIT_BASH, problem)
    return None


def check_harness_prerequisites(
    base_harness_id: str,
    *,
    env: Mapping[str, str] | None = None,
    probe: Callable[[str], str | None] = _probe_executable,
) -> list[PrerequisiteFailure]:
    """按 harness 返回缺失的启动前置条件；空列表表示可以进池。"""
    failures: list[PrerequisiteFailure] = []
    if base_harness_id == "codebuddy":
        failure = codebuddy_git_bash_failure(env=env, probe=probe)
        if failure is not None:
            failures.append(failure)
    return failures
