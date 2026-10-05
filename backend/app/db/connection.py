"""SQLite connection and transaction helpers.

The scheduler is a single-process MVP, but every operation still opens a
short-lived connection. Claim and other write paths can use ``transaction``
with ``immediate=True`` to acquire SQLite's write lock before reading state.
"""

from __future__ import annotations

import errno
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.db.migrations import apply_migrations

#: 跨进程锁的等待上限与轮询间隔。Windows 上 ``open()`` 与 ``msvcrt.locking``
#: 都不会阻塞等待（前者抛 PermissionError，后者需用 LK_NBLCK 配合重试），
#: 所以超时必须由调用方自己兜。
_LOCK_TIMEOUT_S = 10.0
_LOCK_POLL_S = 0.02


def _lock_file(handle, *, timeout_s: float = _LOCK_TIMEOUT_S, poll_s: float = _LOCK_POLL_S) -> None:
    """对文件句柄加跨进程排他锁，带有限超时重试。

    Windows 上有个坑：``msvcrt.locking`` 的``LK_LOCK`` 虽然是阻塞式的，但当
    另一个进程已持有该文件的锁时，本进程的 ``open()`` 会先抛
    ``PermissionError``，而不是阻塞等待。因此光靠 ``LK_LOCK`` 不够，
    必须在**调用侧**做有限重试。
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except (OSError, PermissionError) as exc:
            # Windows 上被独占时 open() 直接失败；POSIX 上 flock 也会报 EAGAIN
            retryable = isinstance(exc, PermissionError) or getattr(exc, "errno", None) in {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
            }
            if not retryable or time.monotonic() >= deadline:
                raise
            time.sleep(poll_s)


def _unlock_file(handle) -> None:
    """释放 :func:`_lock_file` 取到的锁。"""
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Database:
    """A small, explicit SQLite database facade."""

    def __init__(
        self, path: Path | str | None = None, *, busy_timeout_ms: int | None = None
    ) -> None:
        self.path = Path(path) if path is not None else settings.db_path
        self.busy_timeout_ms = (
            settings.sqlite_busy_timeout_ms if busy_timeout_ms is None else busy_timeout_ms
        )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a configured connection without starting a transaction.

        ``journal_mode`` 刻意不在这里设置：变更journal mode 需要排他锁，而
        SQLite 的 ``busy_timeout`` 对 ``PRAGMA journal_mode`` **不生效**
        （已知限制）。两个连接同时初始化时，后者的 journal_mode 变更会被
        直接拒绝并抛 ``database is locked``。该PRAGMA 由 :meth:`ensure_wal`
        在初始化路径上只执行一次。
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    def ensure_wal(self) -> None:
        """把 journal mode 切到 WAL。幂等，且可在并发初始化时安全调用。

        两个必须避开的坑：

        1. SQLite 的 ``busy_timeout`` 对 ``PRAGMA journal_mode`` **不生效**，
           所以不能靠超时重试，只能靠外部互斥；
        2. SQLite **禁止在事务内变更 journal mode**
           （``cannot change into wal mode from within a transaction``），
           因此用 ``BEGIN IMMEDIATE`` 包住反而会直接报错。

        解法：用 ``<db>-wal.lock`` 旁路文件做跨进程互斥（Windows 用
        ``msvcrt.locking``，POSIX 用 ``fcntl.flock``）。进锁后重新确认当前
        模式，已是 WAL 则立即返回，避免无谓的排他锁与写放大。

        Windows 上还有第三��坑：文件被独占锁定时``open()`` 本身就会抛
        ``PermissionError``，所以**打开锁文件也要重试**，不能只重试加锁。
        """
        with self.connect() as conn:
            row = conn.execute("PRAGMA journal_mode").fetchone()
            if row is not None and str(row[0]).lower() == "wal":
                return

        lock_path = self.path.with_name(self.path.name + "-wal.lock")
        deadline = time.monotonic() + _LOCK_TIMEOUT_S
        while True:
            try:
                lock_file = open(lock_path, "a+b")  # noqa: SIM115
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_LOCK_POLL_S)
        with lock_file:
            _lock_file(lock_file)
            try:
                # 拿到跨进程锁后必须重新确认：可能已被另一个进程设好
                conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
                try:
                    row = conn.execute("PRAGMA journal_mode").fetchone()
                    if row is not None and str(row[0]).lower() == "wal":
                        return
                    conn.execute("PRAGMA journal_mode=WAL")
                finally:
                    conn.close()
            finally:
                _unlock_file(lock_file)

    def initialize(self) -> int:
        """Apply all pending migrations and return the applied count.

        并发安全：``ensure_wal`` 先幂等地确保 WAL 已就位，之后多个调用方的
        ``apply_migrations`` 靠各自的 ``BEGIN IMMEDIATE`` 串行化。
        """
        self.ensure_wal()
        with self.connect() as conn:
            return apply_migrations(conn)

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        """Yield a connection inside an explicit transaction.

        ``BEGIN IMMEDIATE`` is the safe default for scheduler writes: it
        serializes competing writers before they inspect mutable state.
        """
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.rollback()
                raise
            else:
                conn.commit()

    def read(self, sql: str, parameters: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        """Run a read-only query and return rows as mapping-like objects."""
        with self.connect() as conn:
            conn.execute("PRAGMA query_only=ON")
            return list(conn.execute(sql, parameters).fetchall())
