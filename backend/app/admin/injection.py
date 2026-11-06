"""Explicit human confirmation and auditable administrator-prompt injection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.admin.experience import ExperienceStore
from app.admin.injection_gate import decide
from app.db.connection import Database
from app.tasks.service import Conflict, InvalidInput, NotFound

LAYER_ADMIN_DISPATCH = "admin_dispatch"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def injection_enabled(database: Database) -> bool:
    rows = database.read("SELECT enabled FROM experience_injection_settings WHERE singleton = 1")
    return bool(rows and int(rows[0]["enabled"]) == 1)


def set_injection_enabled(database: Database, *, enabled: bool, decided_by: str) -> dict[str, Any]:
    moment = _now()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO experience_injection_settings(singleton, enabled, updated_by, updated_at)"
            " VALUES(1, ?, ?, ?) ON CONFLICT(singleton) DO UPDATE SET"
            " enabled = excluded.enabled, updated_by = excluded.updated_by,"
            " updated_at = excluded.updated_at",
            (1 if enabled else 0, decided_by, moment),
        )
    return injection_status(database)


def injection_status(database: Database) -> dict[str, Any]:
    rows = database.read(
        "SELECT enabled, updated_by, updated_at FROM experience_injection_settings WHERE singleton = 1"
    )
    if not rows:
        return {"enabled": False, "updated_by": "", "updated_at": ""}
    row = rows[0]
    return {
        "enabled": bool(int(row["enabled"])),
        "updated_by": str(row["updated_by"]),
        "updated_at": str(row["updated_at"]),
    }


def _confirmation(row: Any) -> dict[str, Any]:
    return {
        "confirmation_id": str(row["confirmation_id"]),
        "experience_id": str(row["experience_id"]),
        "task_id": str(row["task_id"]),
        "group_id": str(row["group_id"]),
        "project": str(row["project"]),
        "task_category": str(row["task_category"]),
        "scope": str(row["scope"]),
        "status": str(row["status"]),
        "decided_by": str(row["decided_by"]),
        "decided_at": str(row["decided_at"]),
        "revoked_by": row["revoked_by"],
        "revoked_at": row["revoked_at"],
    }


def confirmation_for(
    database: Database, *, task_id: str, experience_id: str
) -> dict[str, Any] | None:
    rows = database.read(
        "SELECT * FROM experience_confirmations WHERE task_id = ? AND experience_id = ?",
        (task_id, experience_id),
    )
    return _confirmation(rows[0]) if rows else None


def decide_experience(
    database: Database,
    store: ExperienceStore,
    *,
    task: dict[str, Any],
    project: str,
    task_category: str,
    experience_id: str,
    action: str,
    decided_by: str,
) -> dict[str, Any]:
    """Confirm, ignore, or revoke one candidate; repeated decisions are idempotent."""
    if action not in {"confirm", "ignore", "revoke"}:
        raise InvalidInput("action must be confirm, ignore or revoke")
    record = store.get(experience_id)
    if record is None:
        raise NotFound(f"unknown experience: {experience_id}")
    if record["task_category"] != task_category:
        raise Conflict(
            "experience category does not match the task", code="experience.scope_mismatch"
        )
    same_project = record["project"] == project
    if action == "confirm" and not same_project:
        if record["scope"] != "cross_project" or not record["cross_project_confirmed_by"]:
            raise Conflict(
                "cross-project experience requires explicit scope and confirmation",
                code="experience.cross_project_unconfirmed",
            )

    status = {"confirm": "confirmed", "ignore": "ignored", "revoke": "revoked"}[action]
    existing = confirmation_for(database, task_id=str(task["task_id"]), experience_id=experience_id)
    if existing is not None and existing["status"] == status:
        return existing
    moment = _now()
    confirmation_id = (
        existing["confirmation_id"] if existing is not None else "ec-" + uuid.uuid4().hex[:20]
    )
    revoked_by = decided_by if action == "revoke" else None
    revoked_at = moment if action == "revoke" else None
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO experience_confirmations("
            " confirmation_id, experience_id, task_id, group_id, project, task_category,"
            " scope, status, decided_by, decided_at, revoked_by, revoked_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(experience_id, task_id) DO UPDATE SET"
            " scope = excluded.scope, status = excluded.status,"
            " decided_by = excluded.decided_by, decided_at = excluded.decided_at,"
            " revoked_by = excluded.revoked_by, revoked_at = excluded.revoked_at",
            (
                confirmation_id,
                experience_id,
                str(task["task_id"]),
                str(task["group_id"]),
                project,
                task_category,
                record["scope"],
                status,
                decided_by,
                moment,
                revoked_by,
                revoked_at,
            ),
        )
    return confirmation_for(database, task_id=str(task["task_id"]), experience_id=experience_id)


def _confirmed_items(
    database: Database,
    store: ExperienceStore,
    *,
    group_id: str,
    project_name: str | None,
) -> list[dict[str, Any]]:
    """Every active confirmation, revalidating project, category and scope."""
    params: list[Any] = [group_id]
    project_clause = ""
    if project_name:
        project_clause = " AND project = ?"
        params.append(project_name)
    rows = database.read(
        "SELECT * FROM experience_confirmations WHERE group_id = ? AND status = 'confirmed'"
        + project_clause
        + " ORDER BY decided_at, confirmation_id LIMIT 20",
        tuple(params),
    )
    items: list[dict[str, Any]] = []
    for row in rows:
        record = store.get(str(row["experience_id"]))
        if record is None or record["task_category"] != row["task_category"]:
            continue
        same_project = record["project"] == row["project"]
        if not same_project and (
            record["scope"] != "cross_project" or not record["cross_project_confirmed_by"]
        ):
            continue
        if record["scope"] != row["scope"]:
            continue
        items.append(
            {
                "record": record,
                "selection": {
                    "confirmation_id": str(row["confirmation_id"]),
                    "experience_id": str(row["experience_id"]),
                    "confirmed_by": str(row["decided_by"]),
                    "confirmed_at": str(row["decided_at"]),
                },
            }
        )
    return items


def build_confirmed_prompt(
    database: Database,
    store: ExperienceStore,
    *,
    group_id: str,
    project_name: str | None,
    dispatch_text: str,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Bounded prompt from active confirmations, gated on gaps in this dispatch.

    Returns ``(text, selections, trace)``. ``text`` carries only fixed check-type
    sentences; ``trace`` records why the gate fired or stayed quiet. An empty
    ``dispatch_text`` is fail-closed: nothing is injected.
    """
    enabled = injection_enabled(database)
    items = (
        _confirmed_items(database, store, group_id=group_id, project_name=project_name)
        if enabled
        else []
    )
    decision = decide(dispatch_text=dispatch_text, items=items, enabled=enabled)
    selections = [
        {
            "confirmation_id": match["confirmation_id"],
            "experience_id": match["experience_id"],
            "confirmed_by": match["confirmed_by"],
            "confirmed_at": match["confirmed_at"],
        }
        for match in decision["matched"]
        if match["confirmation_id"]
    ]
    return str(decision["text"]), selections, decision


def confirmed_prompt(
    database: Database,
    store: ExperienceStore,
    *,
    group_id: str,
    project_name: str | None,
    dispatch_text: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Two-value wrapper for callers that only need the prompt and its audit rows."""
    text, selections, _ = build_confirmed_prompt(
        database,
        store,
        group_id=group_id,
        project_name=project_name,
        dispatch_text=dispatch_text,
    )
    return text, selections


def record_injections(
    database: Database,
    *,
    group_id: str,
    tasks: list[dict[str, Any]],
    selections: list[dict[str, Any]],
) -> None:
    """Persist exactly which confirmed entries reached each newly refined task."""
    moment = _now()
    with database.transaction() as conn:
        for task in tasks:
            task_id = str(task.get("id") or task.get("task_id") or "").strip()
            if not task_id:
                continue
            for selection in selections:
                conn.execute(
                    "INSERT INTO experience_injections("
                    " injection_id, experience_id, task_id, group_id, confirmation_id,"
                    " confirmed_by, confirmed_at, injected_at, layer)"
                    " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                    (
                        "ei-" + uuid.uuid4().hex[:20],
                        selection["experience_id"],
                        task_id,
                        group_id,
                        selection["confirmation_id"],
                        selection["confirmed_by"],
                        selection["confirmed_at"],
                        moment,
                        LAYER_ADMIN_DISPATCH,
                    ),
                )


def injections_for_task(database: Database, task_id: str) -> list[dict[str, Any]]:
    rows = database.read(
        "SELECT injection_id, experience_id, task_id, group_id, confirmation_id,"
        " confirmed_by, confirmed_at, injected_at, layer FROM experience_injections"
        " WHERE task_id = ? ORDER BY injected_at DESC, injection_id DESC",
        (task_id,),
    )
    return [dict(row) for row in rows]
