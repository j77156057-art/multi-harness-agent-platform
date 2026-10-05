"""Build a task that has passed verification and review, for the MVP-07 tests.

The chain is walked for real -- claim, start, commit, publish, submit, verify,
review -- because the Merge Gate's whole job is to check the artefacts those
steps produce. A hand-made `approved` row would test the gate against fixtures
that no real path could have produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.claim.service import ClaimRequest, ClaimService
from app.db.connection import Database
from app.git.publish import PublishProxy
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeManager
from app.tasks.service import SchedulerService
from app.verify.profile import VerifyPolicy
from app.verify.worker import VerifyWorker
from tests.claim_support import make_database, seed_project, seed_task

HARNESS = "harness-1"
REVIEWER = "reviewer-1"
PROFILE = "flag-check"

#: Integration check: green while flag.txt is absent, red once the merge adds it.
INTEGRATION_EXPECTS_ABSENT = [
    "python",
    "-c",
    "import pathlib,sys; sys.exit(1 if pathlib.Path('flag.txt').exists() else 0)",
]

CHECK_FLAG = [
    "python",
    "-c",
    "import pathlib,sys; sys.exit(0 if pathlib.Path('flag.txt').exists() else 1)",
]


def policy(*, command=None, timeout_seconds: int = 60) -> VerifyPolicy:
    return VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                PROFILE: {
                    "command": command or CHECK_FLAG,
                    "timeout_seconds": timeout_seconds,
                }
            },
        }
    )


@dataclass(frozen=True, slots=True)
class Chain:
    database: Database
    repo: CanonicalRepo
    service: SchedulerService
    manager: WorktreeManager
    tmp_path: Path
    task_id: str
    attempt_id: str
    fencing_epoch: int
    base_commit: str
    head_commit: str
    branch: str
    review_id: str


def _commit_files(worktree, files: dict[str, str], message: str = "feat: change") -> str:
    for name, content in files.items():
        target = worktree.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        run_git("add", name, cwd=worktree.path)
    run_git("commit", "-m", message, cwd=worktree.path)
    return run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()


def commit_on_integration(
    repo: CanonicalRepo,
    tmp_path: Path,
    *,
    rename: tuple[str, str] | None = None,
    edit: tuple[str, str] | None = None,
    tag: str = "divergence",
) -> str:
    """Move the integration branch on, to create divergence for the merge to hit.

    ``rename`` moves a file that exists in canonical; ``edit`` rewrites one. Both
    are done in a throwaway worktree whose commit is then imported and pointed at
    by the integration ref, the same way the scheduler writes it.
    """
    work = tmp_path / f"integration-{tag}"
    work.mkdir(parents=True, exist_ok=True)
    run_git("init", "--initial-branch", "integration", str(work))
    head = repo.ref_commit(repo.integration_ref)
    assert head is not None
    run_git("fetch", "--no-tags", str(repo.path), head, cwd=work)
    run_git("checkout", "-b", "integration", "FETCH_HEAD", cwd=work)
    if rename is not None:
        run_git("mv", rename[0], rename[1], cwd=work)
        message = f"chore: rename {rename[0]}"
    elif edit is not None:
        (work / edit[0]).write_text(edit[1], encoding="utf-8")
        run_git("add", edit[0], cwd=work)
        message = f"chore: change {edit[0]}"
    else:  # pragma: no cover - a caller has to pick one
        raise ValueError("commit_on_integration needs rename= or edit=")
    run_git("commit", "-m", message, cwd=work)
    commit = run_git("rev-parse", "HEAD", cwd=work).stdout.strip()
    repo.import_objects(work, commit)
    repo.update_ref(repo.integration_ref, commit)
    return commit


def build_chain(
    tmp_path: Path,
    *,
    task_id: str = "T-1",
    extra_files: dict[str, str] | None = None,
    verify_command=None,
    auto_verify: bool = True,
    rename: tuple[str, str] | None = None,
) -> Chain:
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    seed_project(database, integration_commit=base)
    seed_task(database, task_id=task_id)
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)

    item = (
        ClaimService(database)
        .claim(
            ClaimRequest(claim_request_id=f"cr-{task_id}", harness_id=HARNESS, task_ids=(task_id,))
        )
        .items[0]
    )
    service.start_task(
        task_id=task_id,
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=manager,
    )
    worktree = manager.workspace_for_attempt(item.attempt_id)
    assert worktree is not None
    files = {"flag.txt": "ok\n"}
    files.update(extra_files or {})
    head = _commit_files(worktree, files)
    if rename is not None:
        run_git("mv", rename[0], rename[1], cwd=worktree.path)
        run_git("commit", "-m", f"refactor: rename {rename[0]}", cwd=worktree.path)
        head = run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()
    PublishProxy(database, repo).publish_task_branch(
        task_id=task_id,
        attempt_id=item.attempt_id,
        branch=worktree.branch,
        commit=head,
        fencing_epoch=item.fencing_epoch,
        idempotency_key=f"pub-{task_id}",
    )
    service.submit_result(
        task_id=task_id,
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        completion={"commit": head},
    )
    if auto_verify:
        VerifyWorker(
            database,
            repo,
            policy(command=verify_command),
            overlay_root=tmp_path / "ov",
            evidence_root=tmp_path / "evidence",
        ).run(
            task_id=task_id,
            attempt_id=item.attempt_id,
            commit=head,
            base_commit=base,
            verify_profile=PROFILE,
            fencing_epoch=item.fencing_epoch,
        )
    return Chain(
        database=database,
        repo=repo,
        service=service,
        manager=manager,
        tmp_path=tmp_path,
        task_id=task_id,
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        base_commit=base,
        head_commit=head,
        branch=worktree.branch,
        review_id="",
    )


def approve(chain: Chain, *, reviewer: str = REVIEWER, commit: str | None = None) -> Chain:
    """Record an approving review, taking the task to ``approved``."""
    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=reviewer,
    )
    chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=reviewer,
        verdict="approve",
        reviewed_commit=commit or chain.head_commit,
        review_revision=1,
        findings=[],
        review_id=str(claimed["review_id"]),
    )
    return Chain(
        database=chain.database,
        repo=chain.repo,
        service=chain.service,
        manager=chain.manager,
        tmp_path=chain.tmp_path,
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        fencing_epoch=chain.fencing_epoch,
        base_commit=chain.base_commit,
        head_commit=commit or chain.head_commit,
        branch=chain.branch,
        review_id=str(claimed["review_id"]),
    )


def status_of(chain: Chain) -> str:
    return str(
        chain.database.read("SELECT status FROM tasks WHERE task_id=?", (chain.task_id,))[0][
            "status"
        ]
    )


def health_of(chain: Chain) -> str:
    return str(
        chain.database.read("SELECT status FROM integration_health WHERE project_id='P-1'")[0][
            "status"
        ]
    )
