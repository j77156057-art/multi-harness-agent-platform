"""Test client helpers for the MVP-08 REST tests."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.db.connection import Database
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeManager
from app.tasks.service import SchedulerService
from app.verify.profile import VerifyPolicy
from app.verify.worker import VerifyWorker
from tests.claim_support import make_database

#: The app rejects non-loopback Host headers (R10 §5 / prep D-6), and TestClient
#: defaults to ``testserver``, so every client must be built with a loopback URL.
LOOPBACK = "http://127.0.0.1"


def make_client(tmp_path: Path) -> tuple[TestClient, Database]:
    """Return a client plus the database it writes to.

    Deliberately not used as a context manager: skipping lifespan avoids the
    startup hook creating ``data/`` and ``wt/`` inside the repository.

    The canonical repository is injected too, so the publish route validates
    alternates against the temp repository rather than the one settings names.
    """
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    app = create_app(database, repo)
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


def verify_policy() -> VerifyPolicy:
    """A policy whose one profile always passes, for API-shape tests."""
    return VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                "always-pass": {
                    "command": ["python", "-c", "raise SystemExit(0)"],
                    "timeout_seconds": 30,
                }
            },
        }
    )


def advance_to_awaiting_review(
    client: TestClient,
    database: Database,
    tmp_path: Path,
    *,
    task_id: str,
    attempt_id: str,
    fencing_epoch: int,
    manager: WorktreeManager | None = None,
    filename: str = "work.txt",
) -> str:
    """Commit, publish, submit and verify — the real chain, no scaffolding.

    MVP-06 replaced the ``record_verify_passed`` stub, so reaching
    ``awaiting_review`` now means producing an actual receipt for an actual
    commit. Doing it through the HTTP surface keeps this file's tests honest
    about what an Adapter can really do.
    """
    manager = manager or worktree_manager(tmp_path, database)
    worktree = manager.workspace_for_attempt(attempt_id)
    assert worktree is not None, "start_task must have provisioned a worktree"

    target = worktree.path / filename
    target.write_text(f"{task_id}\n", encoding="utf-8")
    run_git("add", filename, cwd=worktree.path)
    run_git("commit", "-m", f"feat: {task_id}", cwd=worktree.path)
    commit = run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()

    published = client.post(
        "/api/v1/adapter/publish_task_branch",
        json={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "branch": worktree.branch,
            "commit": commit,
            "fencing_epoch": fencing_epoch,
            "idempotency_key": f"pub-{task_id}-{commit[:8]}",
        },
    )
    assert published.status_code == 200, published.text

    submitted = client.post(
        "/api/v1/adapter/submit_result",
        json={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "fencing_epoch": fencing_epoch,
            "completion": {"commit": commit, "task_id": task_id, "attempt_id": attempt_id},
        },
    )
    assert submitted.status_code == 200, submitted.text

    base = database.read("SELECT base_commit FROM tasks WHERE task_id = ?", (task_id,))[0][
        "base_commit"
    ]
    VerifyWorker(
        database,
        manager.repo,
        verify_policy(),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "evidence",
    ).run(
        task_id=task_id,
        attempt_id=attempt_id,
        commit=commit,
        base_commit=base,
        verify_profile="always-pass",
        fencing_epoch=fencing_epoch,
    )
    return commit
