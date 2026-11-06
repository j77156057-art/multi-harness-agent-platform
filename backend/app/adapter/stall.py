"""静默挂死检测（三项同时不动才判 stalled）。

07 现场：CodeBuddy 缺 Git Bash 时，模型调 ``Bash`` 收到 "does not exist"，
重试三次后**进程活着、CPU 不动、日志不再增长**；平台只能等 1800 秒超时。

这里用三个互相独立的进展信号：

* ``bytes``  —— CLI 输出（stdout+stderr）的累计字节数；
* ``cpu``    —— 子进程累计 CPU 秒；
* ``tokens`` —— 最近输出里 ``output_tokens`` 的（出现次数, 最大值）签名。

任何一项变化都算"还在干活"，只有**三项都不变**且持续到窗口结束才返回 True。
``tokens`` 拿不到（某些 CLI 不输出用量）时一律不判——宁可漏判，不做没把握
的误杀。
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

_TOKEN_RE = re.compile(r'"output_tokens"\s*:\s*(\d+)')


@dataclass(frozen=True, slots=True)
class ProgressSample:
    bytes_total: int
    cpu_seconds: float | None
    tokens: tuple[int, int] | None

    def complete(self) -> bool:
        return self.cpu_seconds is not None and self.tokens is not None


class StallDetector:
    """记住最后一次进展，超过窗口内三项都没动就报告 stalled。"""

    def __init__(self, *, window_seconds: float, clock=time.monotonic) -> None:
        self.window_seconds = float(window_seconds)
        self._clock = clock
        self._baseline: ProgressSample | None = None
        self._baseline_at: float | None = None

    def observe(self, sample: ProgressSample, *, now: float | None = None) -> bool:
        moment = self._clock() if now is None else now
        if self._baseline is None or not sample.complete():
            self._baseline = sample
            self._baseline_at = moment
            return False
        if (
            sample.bytes_total != self._baseline.bytes_total
            or sample.cpu_seconds != self._baseline.cpu_seconds
            or sample.tokens != self._baseline.tokens
        ):
            self._baseline = sample
            self._baseline_at = moment
            return False
        return (moment - self._baseline_at) >= self.window_seconds

    def stalled_seconds(self, *, now: float | None = None) -> float:
        if self._baseline_at is None:
            return 0.0
        moment = self._clock() if now is None else now
        return max(0.0, moment - self._baseline_at)


def output_tokens_in_tail(path: Path | None, *, tail_bytes: int = 8192) -> tuple[int, int] | None:
    """从日志尾部取 ``output_tokens`` 的（次数, 最大值）；没有就 None。"""
    if path is None:
        return None
    try:
        data = path.read_bytes()[-tail_bytes:]
    except OSError:
        return None
    values = [int(match) for match in _TOKEN_RE.findall(data.decode("utf-8", "replace"))]
    if not values:
        return None
    return (len(values), max(values))


def child_cpu_seconds(pid: int) -> float | None:
    """子进程累计 CPU 秒；Windows 走 GetProcessTimes，POSIX 用 RUSAGE_CHILDREN。

    拿不到就返回 None —— 调用方据此放弃判定，而不是把"未知"当成"没进展"。
    """
    if os.name == "nt":
        return _windows_cpu_seconds(pid)
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        return float(usage.ru_utime + usage.ru_stime)
    except Exception:  # pragma: no cover - platform dependent
        return None


def _windows_cpu_seconds(pid: int) -> float | None:
    try:
        import ctypes
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            creation = FILETIME()
            exit_time = FILETIME()
            kernel = FILETIME()
            user = FILETIME()
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            hundred_ns = (kernel.dwLowDateTime + kernel.dwHighDateTime * 2**32) + (
                user.dwLowDateTime + user.dwHighDateTime * 2**32
            )
            return hundred_ns / 10_000_000.0
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # pragma: no cover - platform dependent
        return None
