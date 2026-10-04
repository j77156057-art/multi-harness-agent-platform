"""SQLite connection and transaction helpers.

The scheduler is a single-process MVP, but every operation still opens a
short-lived connection. Claim and other write paths can use ``transaction``
with ``immediate=True`` to acquire SQLite's write lock before reading state.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.db.migrations import apply_migrations


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
        """Open a configured connection without starting a transaction."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=self.busy_timeout_ms / 1000)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_ms)}")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
        finally:
            conn.close()

    def initialize(self) -> int:
        """Apply all pending migrations and return the applied count."""
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
