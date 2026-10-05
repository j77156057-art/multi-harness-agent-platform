"""Helpers for the MVP-05 git/worktree tests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.claim.service import ClaimRequest, ClaimService
from app.db.connection import Database
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import Worktree, WorktreeManager
from app.tasks.service import SchedulerService
from tests.claim_support import make_database, seed_project, seed_task

HARNESS = "harness-1"


@dataclass(frozen=True, slots=True)
class Fixture:
    database: Database
    repo: CanonicalRepo
    manager: WorktreeManager
    service: SchedulerService
    rest: Path


def make_fixture(tmp_path: Path, *, integration_commit: str | None = None) -> Fixture:
    database = make_database(tmp_path)
    seed_project(database, integration_commit=integration_commit)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    return Fixture(
        database=database,
        repo=repo,
        manager=WorktreeManager(database, repo, root=tmp_path / "wt"),
        service=SchedulerService(database),
        rest=tmp_path,
    )


def base_commit(fixture: Fixture) -> str:
    head = fixture.repo.ref_commit(fixture.repo.integration_ref)
    assert head is not None, "the canonical repository must have an integration HEAD"
    return head


def seed_and_claim(fixture: Fixture, *, task_id: str = "T-1"):
    seed_task(fixture.database, task_id=task_id)
    return (
        ClaimService(fixture.database)
        .claim(
            ClaimRequest(
                claim_request_id=f"cr-{task_id}",
                harness_id=HARNESS,
                task_ids=(task_id,),
            )
        )
        .items[0]
    )


def provision(fixture: Fixture, *, task_id: str, attempt_id: str, group_short: str = "g1"):
    return fixture.manager.provision(
        task_id=task_id,
        attempt_id=attempt_id,
        group_short=group_short,
        harness=HARNESS,
        base_commit=base_commit(fixture),
    )


def commit_in(worktree: Worktree, *, filename: str, content: str, message: str) -> str:
    (worktree.path / filename).write_text(content, encoding="utf-8")
    run_git("add", filename, cwd=worktree.path)
    run_git("commit", "-m", message, cwd=worktree.path)
    return run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()


def push(worktree: Worktree, repo: CanonicalRepo, refspec: str):
    """A client push, which is what the canonical hook guards against."""
    return run_git("push", str(repo.path), refspec, cwd=worktree.path, check=False)
