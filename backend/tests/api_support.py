"""Test client helpers for the MVP-08 REST tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.db.connection import Database
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.tasks.service import SchedulerService
from tests.claim_support import make_database

#: The app rejects non-loopback Host headers (R10 §5 / prep D-6), and TestClient
#: defaults to ``testserver``, so every client must be built with a loopback URL.
LOOPBACK = "http://127.0.0.1"


def make_client(tmp_path: Path) -> tuple[TestClient, Database]:
    """Return a client plus the database it writes to.

    Deliberately not used as a context manager: skipping lifespan avoids the
    startup hook creating ``data/`` and ``wt/`` inside the repository.
    """
    database = make_database(tmp_path)
    app = create_app(database)
    return TestClient(app, base_url=LOOPBACK), database


def scheduler(database: Database) -> SchedulerService:
    return SchedulerService(database)


def bootstrap(client: TestClient, *, project_id: str = "P-1", group_id: str = "G-1") -> None:
    response = client.post("/api/v1/projects", json={"project_id": project_id, "name": "project"})
    assert response.status_code == 200, response.text
    response = client.post(
        f"/api/v1/projects/{project_id}/groups",
        json={"group_id": group_id, "name": "group"},
    )
    assert response.status_code == 200, response.text


def publish_plan(
    client: TestClient,
    *,
    plan_id: str = "PL-1",
    group_id: str = "G-1",
    tasks: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    body = {"plan_id": plan_id, "snapshot": {"tasks": tasks or [{"id": "T-1"}]}}
    response = client.post(f"/api/v1/groups/{group_id}/plans", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def worktree_manager(tmp_path: Path, database: Database) -> WorktreeManager:
    """A worktree manager rooted in the test's temp directory.

    ``SchedulerService.start_task`` provisions a real worktree, and its default
    manager would reach for the repository's own ``wt/`` and ``canonical.git``,
    which tests must not touch.
    """
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    return WorktreeManager(database, repo, root=tmp_path / "wt")
