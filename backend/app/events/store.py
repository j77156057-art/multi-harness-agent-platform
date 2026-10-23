"""Append-only SQLite event store.

Events are the durable fact source. Higher-level services may build projections
from ``iter_since`` or ``replay``; this module never edits a previously
written event and never changes task status on its own.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.config import settings
from app.db.connection import Database
from app.db.sequence import next_global_seq


@dataclass(frozen=True, slots=True)
class EventRecord:
    seq: int
    event_name: str
    aggregate_type: str
    aggregate_id: str
    actor_type: str
    actor_id: str
    payload: dict[str, Any]
    payload_hash: str
    schema_version: int
    created_at: str


def _payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


class EventStore:
    """Write and read append-only task events."""

    def __init__(self, database: Database) -> None:
        self.database = database
        blocks = re.findall(
            r"(?:迁移|观测|恢复|分配)事件（[MORA]，\d+ 项）：\s*~~~text\n(.*?)\n~~~",
            settings.spec_path.read_text(encoding="utf-8"),
            re.S,
        )
        if len(blocks) != 4:
            raise ValueError("cannot load the R10 M/O/R/A event allowlist")
        self.allowed_events = frozenset(word for block in blocks for word in block.split())

    def append(
        self,
        *,
        event_name: str,
        aggregate_type: str,
        aggregate_id: str,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any] | None = None,
        schema_version: int = 1,
        connection: sqlite3.Connection | None = None,
    ) -> EventRecord:
        """Append one event, optionally inside a caller-owned transaction."""
        if event_name not in self.allowed_events:
            raise ValueError(f"event is not registered in R10: {event_name}")
        if any(not value.strip() for value in (aggregate_type, aggregate_id, actor_type, actor_id)):
            raise ValueError("aggregate and actor identities must not be empty")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        if connection is not None and not connection.in_transaction:
            raise ValueError("caller-owned connection must have an active transaction")
        event_payload = {} if payload is None else payload
        serialized = _payload_json(event_payload)
        payload_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        created_at = datetime.now(UTC).isoformat()
        sql = """
            INSERT INTO task_events(
                seq, event_name, aggregate_type, aggregate_id, actor_type, actor_id,
                payload_json, payload_hash, schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        arguments = (
            event_name,
            aggregate_type,
            aggregate_id,
            actor_type,
            actor_id,
            serialized,
            payload_hash,
            schema_version,
            created_at,
        )

        if connection is not None:
            seq = next_global_seq(connection)
            connection.execute(sql, (seq, *arguments))
        else:
            with self.database.transaction() as conn:
                seq = next_global_seq(conn)
                conn.execute(sql, (seq, *arguments))
        return EventRecord(
            seq=seq,
            event_name=event_name,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=json.loads(serialized),
            payload_hash=payload_hash,
            schema_version=schema_version,
            created_at=created_at,
        )

    def iter_since(self, since_seq: int = 0) -> Iterable[EventRecord]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM task_events WHERE seq > ? ORDER BY seq ASC", (since_seq,)
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def latest_seq(self) -> int:
        rows = self.database.read("SELECT COALESCE(MAX(seq), 0) AS seq FROM task_events")
        return int(rows[0]["seq"])

    def replay(
        self,
        reducer: Callable[[Any, EventRecord], Any],
        initial: Any = None,
        *,
        since_seq: int = 0,
    ) -> Any:
        state = initial
        for event in self.iter_since(since_seq):
            state = reducer(state, event)
        return state

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EventRecord:
        serialized = str(row["payload_json"])
        expected_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if expected_hash != row["payload_hash"]:
            raise ValueError(f"event payload hash mismatch at seq={row['seq']}")
        return EventRecord(
            seq=int(row["seq"]),
            event_name=str(row["event_name"]),
            aggregate_type=str(row["aggregate_type"]),
            aggregate_id=str(row["aggregate_id"]),
            actor_type=str(row["actor_type"]),
            actor_id=str(row["actor_id"]),
            payload=json.loads(serialized),
            payload_hash=str(row["payload_hash"]),
            schema_version=int(row["schema_version"]),
            created_at=str(row["created_at"]),
        )
