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
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.adapter.protocol import HarnessRegistration
from app.config import settings
from app.db.connection import Database

#: A window the operator added for themselves: ``<base>-member-<slot>`` or
#: ``<base>-window-<slot>``.  Same shape as the worker's
#: ``base_harness_id`` accepts -- so "is this an instance?" has one answer
#: everywhere.
_INSTANCE_ID_RE = re.compile(
    r"^(?P<base>[a-z][a-z0-9]*(?:-[a-z0-9]+)*)-(?:member|window)"
    r"-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$"
)


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
            current = conn.execute(
                "SELECT registration_version, removed_at FROM harnesses WHERE harness_id = ?",
                (registration.harness_id,),
            ).fetchone()
            # 删过的窗口不算「还在册」：它被删过又被加回来，是**复活**，不是重放。
            removed = current is not None and current["removed_at"] is not None
            if existing is not None and not removed:
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
                " capabilities_json = excluded.capabilities_json,"
                " removed_at = NULL",
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
            # 复活：同一版本被删过又加回来时，把它重新算作「当前版本」。
            conn.execute(
                "UPDATE harness_registrations SET superseded_by = NULL"
                " WHERE harness_id = ? AND registration_version = ?"
                " AND superseded_by LIKE 'removed:%'",
                (registration.harness_id, registration.registration_version),
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
        """这个窗口**当前**的登记。删掉的窗口没有当前登记（行还在，只是过期了）。"""
        rows = self.database.read(
            "SELECT * FROM harnesses WHERE harness_id = ? AND removed_at IS NULL",
            (harness_id,),
        )
        return dict(rows[0]) if rows else None

    def pinned(self, harness_id: str, registration_version: str) -> dict[str, Any] | None:
        """The registration a Claim pinned, recoverable because history is kept."""
        rows = self.database.read(
            "SELECT * FROM harness_registrations WHERE harness_id = ? AND registration_version = ?",
            (harness_id, registration_version),
        )
        return dict(rows[0]) if rows else None

    def listing(self, *, include_removed: bool = False) -> list[dict[str, Any]]:
        """The registry, as the platform currently sees it.

        Removed windows are **not** in it by default: they are gone from the
        roster.  ``include_removed`` brings them back for the faces that must
        still be able to name them (a group roster that lists the work they
        did, for instance) -- they come back marked, not alive.
        """
        admin_subjects = set(getattr(settings, "admin_subjects", frozenset()))
        rows = [
            dict(row)
            for row in self.database.read(
                "SELECT h.harness_id, h.provider, h.adapter, h.status, h.registration_version,"
                " h.capabilities_json, h.model_mode, h.model_id, h.model_tier, h.removed_at,"
                " r.name, r.adapter_version, r.max_parallel_tasks,"
                " (SELECT COUNT(*) FROM leases l WHERE l.owner = h.harness_id"
                " AND l.kind = 'development' AND l.released_at IS NULL"
                " AND l.lease_until > ?) AS running_tasks,"
                " (SELECT COUNT(*) FROM auth_tokens t WHERE t.subject_type = 'harness'"
                "  AND t.subject_id = h.harness_id AND t.revoked_at IS NULL)"
                "  AS credential_count"
                " FROM harnesses h LEFT JOIN harness_registrations r ON"
                " r.harness_id = h.harness_id AND r.registration_version = h.registration_version"
                " WHERE (? OR h.removed_at IS NULL)"
                " ORDER BY h.harness_id",
                # 第一个 ? 是租约的时间界，第二个才是「要不要连删掉的窗口一起列出来」。
                # 写成参数而不是拼字符串，免得哪天真把某个值拼进 SQL。
                (datetime.now(UTC).isoformat(), 1 if include_removed else 0),
            )
        ]
        # 这个窗口是不是**平台承认的管理员**（`AP_ADMIN_SUBJECTS` 白名单）。
        # 界面据此在开工向导里提醒：挑一个不在白名单里的窗口当管理员，目标会被
        # 静默跳过（`goal_skipped_for_other_admin`）—— 2026-11-17 真机走查实测到
        # 的那种「什么都不发生」。
        for row in rows:
            row["is_admin"] = str(row.get("harness_id") or "") in admin_subjects
            # 删掉的窗口（只在 include_removed 时才在这里）带着这个标记回来：
            # 它**不在**名册里了，但它做过的事还在账上。
            row["removed"] = bool(row.get("removed_at"))
        return rows

    # -------------------------------------------------------------- removal

    def is_instance_window(self, harness_id: str) -> bool:
        """这个窗口是不是**后来自己加的实例**（而不是 CLI 基座本身）。

        基座是 CLI 本身（Claude Code、CodeBuddy……），删了它就没有这个 CLI
        了；实例是「同一个 CLI 再开一个身份」，是**自己加的**，加错了就该能删。
        形状与 ``app.adapter.formal_worker.base_harness_id`` 同一口径：
        ``<base>-member-<slot>`` 或 ``<base>-window-<slot>``。
        """
        match = _INSTANCE_ID_RE.fullmatch(str(harness_id or ""))
        return match is not None

    def unregister(self, harness_id: str) -> dict[str, Any]:
        """Remove a window the operator added themselves.

        Only **instance** windows can go: a base harness *is* the CLI, and
        removing it would silently take the CLI away.

        The removal is a **soft delete**: the row stays (the registration and
        probe history point at it with a foreign key), only ``removed_at`` is
        filled in.  Two different things stay apart this way -- "stop using
        this window" is recorded, "it never existed" is not -- and a task that
        pinned that registration version can still recover it.
        """
        harness_id = str(harness_id or "").strip()
        if not harness_id:
            raise ValueError("harness_id is required")
        if not self.is_instance_window(harness_id):
            raise ValueError(
                "only a window the operator added can be removed: "
                f"{harness_id} is a base harness (the CLI itself)",
            )
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as conn:
            current = conn.execute(
                "SELECT registration_version FROM harnesses WHERE harness_id = ?"
                " AND removed_at IS NULL",
                (harness_id,),
            ).fetchone()
            if current is None:
                raise KeyError(harness_id)
            version = str(current["registration_version"])
            # 登记历史留着，只标成被取代：领过活的任务 Claim 钉住的那个版本
            # 还得能查回来。
            conn.execute(
                "UPDATE harness_registrations SET superseded_by = ?"
                " WHERE harness_id = ? AND registration_version = ?"
                " AND superseded_by IS NULL",
                (f"removed:{now}", harness_id, version),
            )
            conn.execute(
                "UPDATE harnesses SET removed_at = ?, status = 'offline'" " WHERE harness_id = ?",
                (now, harness_id),
            )
            # 凭据也收回来：窗口都删了，它不该还能拿着令牌来调平台。
            conn.execute(
                "UPDATE auth_tokens SET revoked_at = ?, revoked_reason = 'harness_removed'"
                " WHERE subject_type = 'harness' AND subject_id = ? AND revoked_at IS NULL",
                (now, harness_id),
            )
        return {"harness_id": harness_id, "removed": True, "removed_at": now}

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
