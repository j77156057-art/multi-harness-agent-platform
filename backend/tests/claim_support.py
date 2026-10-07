"""Seeding helpers for the MVP-04 claim/lease tests.

Fixtures live here rather than in ``conftest.py`` so that MVP-04 does not
contend with other tasks over the shared conftest.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.db.connection import Database

DEFAULT_PROJECT = "P-1"
DEFAULT_GROUP = "G-1"


def make_database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "state.db")
    database.initialize()
    return database


def seed_project(
    database: Database,
    *,
    project_id: str = DEFAULT_PROJECT,
    group_id: str = DEFAULT_GROUP,
    integration_commit: str | None = "base-commit-0001",
) -> None:
    now = datetime.now(UTC).isoformat()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO projects(project_id, name, created_at) VALUES (?, ?, ?)",
            (project_id, "project", now),
        )
        conn.execute(
            "INSERT INTO groups(group_id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
            (group_id, project_id, "group", now),
        )
        conn.execute(
            "INSERT INTO integration_health(project_id, status, integration_commit)"
            " VALUES (?, 'green', ?)",
            (project_id, integration_commit),
        )


def seed_task(
    database: Database,
    *,
    task_id: str = "T-1",
    group_id: str = DEFAULT_GROUP,
    status: str = "ready",
    attempt_no: int = 0,
    max_attempts: int = 3,
    budget_override_by: str | None = None,
    exclusive_resources: list[str] | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    snapshot_id = None
    with database.transaction() as conn:
        if exclusive_resources is not None:
            snapshot_id = f"snap-{task_id}"
            conn.execute(
                "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
                " VALUES (?, ?, 'approved', ?, ?)",
                # same value as the snapshot below: create_plan writes one hash
                # into both rows, and the drift detector relies on that consistency.
                (f"plan-{task_id}", group_id, f"hash-{task_id}", now),
            )
            conn.execute(
                "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash,"
                " content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    f"plan-{task_id}",
                    f"hash-{task_id}",
                    json.dumps(
                        {
                            "plan_version": "v1",
                            "tasks": [
                                {
                                    "id": task_id,
                                    "exclusive_resources": exclusive_resources,
                                }
                            ],
                        }
                    ),
                    now,
                ),
            )
        conn.execute(
            "INSERT INTO tasks(task_id, group_id, plan_snapshot_id, status, attempt_no,"
            " max_attempts, budget_override_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                group_id,
                snapshot_id,
                status,
                attempt_no,
                max_attempts,
                budget_override_by,
                now,
                now,
            ),
        )


def hold_resource(
    database: Database, resource_id: str, *, attempt_id: str, lease_until: str
) -> None:
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO resource_leases(resource_lease_id, resource_id, attempt_id,"
            " lease_until, epoch, released_at, release_reason) VALUES (?, ?, ?, ?, 1, NULL, NULL)",
            (f"rl-holder-{resource_id}", resource_id, attempt_id, lease_until),
        )


def task_row(database: Database, task_id: str) -> dict[str, object]:
    rows = database.read("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    assert rows, f"task {task_id} not found"
    return dict(rows[0])


def count(database: Database, sql: str, parameters: tuple[object, ...] = ()) -> int:
    rows = database.read(sql, parameters)
    return int(rows[0][0])


def event_names(database: Database, *, aggregate_id: str) -> list[str]:
    rows = database.read(
        "SELECT event_name FROM task_events WHERE aggregate_id = ? ORDER BY seq",
        (aggregate_id,),
    )
    return [str(row["event_name"]) for row in rows]
