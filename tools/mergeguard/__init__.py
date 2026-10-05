"""共享 main 的合并互斥锁（见 `guard.py` 的模块文档）。"""

from __future__ import annotations

from .guard import (
    DEFAULT_TTL_SECONDS,
    LockError,
    LockInfo,
    acquire,
    describe_status,
    is_stale,
    lock_path,
    read_lock,
    release,
)

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "LockError",
    "LockInfo",
    "acquire",
    "describe_status",
    "is_stale",
    "lock_path",
    "read_lock",
    "release",
]