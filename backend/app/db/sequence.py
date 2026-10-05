"""Single global sequence allocator (R10 §15 / §19).

``task_events`` and ``task_messages`` draw their seq from the same one-row
counter so that read_context can interleave both streams under one ``since_seq``
cursor. Allocation is an ``UPDATE ... RETURNING``-style bump performed **inside
the caller's write transaction**: a rolled-back domain transaction unwinds the
bump as well, so the global space develops no holes. ``BEGIN IMMEDIATE``
serializes competing allocators before the bump.
"""

from __future__ import annotations

import sqlite3

#: Single-row table created by migration 003.
_SQL_BUMP = "UPDATE global_seq SET seq = seq + 1 WHERE id = 1"
_SQL_READ = "SELECT seq FROM global_seq WHERE id = 1"


def next_global_seq(conn: sqlite3.Connection) -> int:
    """Bump and return the next global seq within ``conn``'s transaction.

    Must be called on a connection that already holds an open write
    transaction (every scheduler write goes through ``Database.transaction``,
    which issues ``BEGIN IMMEDIATE``).
    """
    if not conn.in_transaction:
        raise ValueError("global seq allocation requires an active transaction")
    conn.execute(_SQL_BUMP)
    row = conn.execute(_SQL_READ).fetchone()
    if row is None:  # pragma: no cover - migration 003 seeds the singleton row
        raise RuntimeError("global_seq is not initialised; run migrations")
    return int(row[0])
