"""Deterministic SQL migration runner for the MVP SQLite store."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path


class MigrationError(RuntimeError):
    """Raised when a migration is malformed or has changed after application."""


_VERSION_RE = re.compile(r"^(?P<version>\d{3,})_[^/]+\.sql$")
_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "migrations"


def _migration_files(directory: Path | str | None = None) -> list[tuple[int, Path]]:
    root = Path(directory) if directory is not None else _DEFAULT_DIR
    if not root.exists():
        return []
    result: list[tuple[int, Path]] = []
    for path in sorted(root.glob("*.sql")):
        match = _VERSION_RE.match(path.name)
        if not match:
            raise MigrationError(f"invalid migration filename: {path.name}")
        result.append((int(match.group("version")), path))
    versions = [version for version, _ in result]
    if len(versions) != len(set(versions)):
        raise MigrationError(f"duplicate migration version: {versions}")
    return result


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            filename TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def apply_migrations(conn: sqlite3.Connection, directory: Path | str | None = None) -> int:
    """Apply pending migrations atomically and return the number applied."""
    if conn.in_transaction:
        raise MigrationError("migrations require a connection without an active transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        _ensure_table(conn)
        applied = {
            int(row[0]): (str(row[1]), str(row[2]))
            for row in conn.execute("SELECT version, filename, checksum FROM schema_migrations")
        }
        count = 0
        for version, path in _migration_files(directory):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            previous = applied.get(version)
            if previous is not None:
                if previous != (path.name, checksum):
                    raise MigrationError(f"migration {version} changed: {path.name}")
                continue
            # execute() keeps the write lock; executescript() would commit it.
            # complete_statement() also handles semicolons inside triggers.
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations VALUES (?, ?, ?, ?)",
                (version, path.name, checksum, datetime.now(UTC).isoformat()),
            )
            count += 1
        conn.commit()
        return count
    except BaseException:
        conn.rollback()
        raise


def _statements(sql: str) -> Iterator[str]:
    buffer = ""
    for character in sql:
        buffer += character
        if character == ";" and sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    if buffer.strip():
        yield buffer
