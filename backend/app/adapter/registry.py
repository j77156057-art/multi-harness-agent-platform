"""Harness registration history and capability probes (R10 §6).

R10 §6 requires two things this module exists to provide:

* registration changes keep history, and a Claim pins the version it used -- so
  the pinned version has to be looked up later, which needs the history rows;
* a runtime self-report is only good for alarms and degradation, never on its own
  for claiming a high-risk task -- so every probe is kept, and the effective
  capability set is what the scheduler decided, not what the harness claimed.

``harnesses`` holds the current registration; ``harness_registrations`` holds
every version of it.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.adapter.protocol import HarnessRegistration
from app.db.connection import Database


class RegistrationConflict(ValueError):
    """A registration version cannot be reused for different data."""


class HarnessRegistry:
    def __init__(self, database: Database) -> None:
        self.database = database

    # ----------------------------------------------------------- registration

    def register(
        self, registration: HarnessRegistration, *, registered_by: str | None = None
    ) -> dict[str, Any]:
        """Record a registration, keeping the previous versions."""
        now = datetime.now(UTC).isoformat()
        if registration.max_parallel_tasks < 1 or registration.status not in {
            "online",
            "degraded",
            "offline",
        }:
            raise ValueError("invalid registration capacity or status")
        with self.database.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM harness_registrations WHERE harness_id = ? "
                "AND registration_version = ?",
                (registration.harness_id, registration.registration_version),
            ).fetchone()
            if existing is not None:
                current = conn.execute(
                    "SELECT registration_version FROM harnesses WHERE harness_id = ?",
                    (registration.harness_id,),
                ).fetchone()
                same = (
                    str(existing["provider"]) == registration.provider
                    and str(existing["adapter"]) == registration.adapter
                    and str(existing["adapter_version"]) == registration.adapter_version
                    and json.loads(str(existing["capabilities_json"]))
                    == list(registration.capabilities)
                    and int(existing["max_parallel_tasks"]) == registration.max_parallel_tasks
                    and str(existing["status"]) == registration.status
                    and existing["name"] == registration.name
                )
                if (
                    not same
                    or current is None
                    or str(current["registration_version"]) != registration.registration_version
                ):
                    raise RegistrationConflict(
                        f"registration version already exists for {registration.harness_id}"
                    )
                return {
                    "harness_id": registration.harness_id,
                    "registration_version": registration.registration_version,
                    "registration_id": str(existing["registration_id"]),
                    "replayed": True,
                }
            conn.execute(
                "INSERT INTO harnesses(harness_id, provider, adapter, status,"
                " registration_version, capabilities_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(harness_id) DO UPDATE SET provider = excluded.provider,"
                " adapter = excluded.adapter, status = excluded.status,"
                " registration_version = excluded.registration_version,"
                " capabilities_json = excluded.capabilities_json",
                (
                    registration.harness_id,
                    registration.provider,
                    registration.adapter,
                    registration.status,
                    registration.registration_version,
                    _json(list(registration.capabilities)),
                    now,
                ),
            )
            previous = conn.execute(
                "SELECT registration_id, registration_version FROM harness_registrations"
                " WHERE harness_id = ? AND superseded_by IS NULL"
                " ORDER BY rowid DESC LIMIT 1",
                (registration.harness_id,),
            ).fetchone()
            registration_id = f"reg-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT OR IGNORE INTO harness_registrations(registration_id, harness_id,"
                " registration_version, provider, adapter, adapter_version,"
                " capabilities_json, max_parallel_tasks, status, created_at, name, registered_by)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    registration_id,
                    registration.harness_id,
                    registration.registration_version,
                    registration.provider,
                    registration.adapter,
                    registration.adapter_version,
                    _json(list(registration.capabilities)),
                    registration.max_parallel_tasks,
                    registration.status,
                    now,
                    registration.name,
                    registered_by,
                ),
            )
            if previous is not None and str(previous["registration_version"]) != (
                registration.registration_version
            ):
                conn.execute(
                    "UPDATE harness_registrations SET superseded_by = ? WHERE registration_id = ?",
                    (registration.registration_version, str(previous["registration_id"])),
                )
        return {
            "harness_id": registration.harness_id,
            "registration_version": registration.registration_version,
            "registration_id": registration_id,
            "replayed": False,
        }

    def current(self, harness_id: str) -> dict[str, Any] | None:
        rows = self.database.read("SELECT * FROM harnesses WHERE harness_id = ?", (harness_id,))
        return dict(rows[0]) if rows else None

    def pinned(self, harness_id: str, registration_version: str) -> dict[str, Any] | None:
        """The registration a Claim pinned, recoverable because history is kept."""
        rows = self.database.read(
            "SELECT * FROM harness_registrations WHERE harness_id = ? AND registration_version = ?",
            (harness_id, registration_version),
        )
        return dict(rows[0]) if rows else None

    def listing(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.database.read(
                "SELECT h.harness_id, h.provider, h.adapter, h.status, h.registration_version,"
                " h.capabilities_json, h.model_mode, h.model_id, h.model_tier,"
                " r.name, r.adapter_version, r.max_parallel_tasks,"
                " (SELECT COUNT(*) FROM leases l WHERE l.owner = h.harness_id"
                " AND l.kind = 'development' AND l.released_at IS NULL"
                " AND l.lease_until > ?) AS running_tasks,"
                " (SELECT COUNT(*) FROM auth_tokens t WHERE t.subject_type = 'harness'"
                "  AND t.subject_id = h.harness_id AND t.revoked_at IS NULL)"
                "  AS credential_count"
                " FROM harnesses h LEFT JOIN harness_registrations r ON"
                " r.harness_id = h.harness_id AND r.registration_version = h.registration_version"
                " ORDER BY h.harness_id",
                (datetime.now(UTC).isoformat(),),
            )
        ]

    # ------------------------------------------------------------------ probe

    def record_probe(
        self,
        *,
        harness_id: str,
        reported_capabilities: list[str],
        effective_capabilities: list[str],
        adapter_version: str | None = None,
        host_mode: str | None = None,
        degraded: bool = False,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC).isoformat()
        probe_id = f"probe-{uuid.uuid4().hex}"
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT registration_version FROM harnesses WHERE harness_id = ?",
                (harness_id,),
            ).fetchone()
            registration_version = None if row is None else row["registration_version"]
            if row is None:
                conn.execute(
                    "INSERT INTO harnesses(harness_id, provider, adapter, status,"
                    " registration_version, capabilities_json, created_at)"
                    " VALUES (?, 'unknown', 'unknown', 'degraded', NULL, ?, ?)",
                    (harness_id, _json(reported_capabilities), now),
                )
            conn.execute(
                "INSERT INTO capability_probes(probe_id, harness_id, registration_version,"
                " reported_capabilities_json, effective_capabilities_json, host_mode,"
                " degraded, warnings_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    probe_id,
                    harness_id,
                    registration_version,
                    _json(reported_capabilities),
                    _json(effective_capabilities),
                    host_mode,
                    1 if degraded else 0,
                    _json(warnings or []),
                    now,
                ),
            )
        return {
            "probe_id": probe_id,
            "harness_id": harness_id,
            "registration_version": registration_version,
            "effective_capabilities": effective_capabilities,
            "degraded": degraded,
            "warnings": list(warnings or []),
        }

    def latest_probe(self, harness_id: str) -> dict[str, Any] | None:
        rows = self.database.read(
            "SELECT * FROM capability_probes WHERE harness_id = ? ORDER BY rowid DESC LIMIT 1",
            (harness_id,),
        )
        return dict(rows[0]) if rows else None


def _json(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def capabilities_from(registration_row: sqlite3.Row | dict) -> tuple[str, ...]:
    raw = registration_row["capabilities_json"]
    return tuple(str(item) for item in json.loads(str(raw)))
