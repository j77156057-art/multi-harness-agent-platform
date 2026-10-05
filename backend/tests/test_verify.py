"""VERIFY-DIFF-001 / VERIFY-RECEIPT-001: the verify worker and its receipts.

The fixture walks the real chain rather than seeding a `verifying` task by hand:
claim (freezes the base), start (provisions a worktree), commit, publish (objects
reach canonical), submit (enters verifying). A worker tested against a hand-made
row would not prove that the base it diffs against is the frozen one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.claim.service import ClaimRequest, ClaimService
from app.db.connection import Database
from app.git.publish import PublishProxy
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.tasks.service import Conflict, InvalidInput, SchedulerService
from app.verify.profile import VerifyPolicy
from app.verify.receipts import merge_is_verifiable, passing_receipt_for, receipt_for
from app.verify.worker import (
    FAILURE_CODE,
    FAILURE_ENV,
    FAILURE_TIMEOUT,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_TIMEOUT,
    VerifyWorker,
)
from tests.claim_support import make_database, seed_project, seed_task

HARNESS = "harness-1"

#: argv, not a shell string: the policy's command root is what §16 gates on.
CHECK_FLAG = [
    "python",
    "-c",
    "import pathlib,sys; sys.exit(0 if pathlib.Path('flag.txt').exists() else 1)",
]
SLEEP = ["python", "-c", "import time; time.sleep(30)"]
MISSING = ["python", "-c", "raise SystemExit(3)"]


def policy(*, command=None, timeout_seconds: int = 60) -> VerifyPolicy:
    return VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                "flag-check": {
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


def build_chain(
    tmp_path: Path,
    *,
    task_id: str = "T-1",
    add_flag: bool = True,
    extra_files: dict[str, str] | None = None,
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
    assert worktree is not None, "start_task must have provisioned the worktree"
    files = {"flag.txt": "ok\n"} if add_flag else {"other.txt": "x\n"}
    files.update(extra_files or {})
    head = _commit_files(worktree, files)
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
    )


def worker_for(chain: Chain, *, command=None, timeout_seconds: int = 60) -> VerifyWorker:
    return VerifyWorker(
        chain.database,
        chain.repo,
        policy(command=command, timeout_seconds=timeout_seconds),
        overlay_root=chain.tmp_path / "ov",
        evidence_root=chain.tmp_path / "evidence",
    )


def run_verify(chain: Chain, *, command=None, differential_mode="required", timeout_seconds=60):
    return worker_for(chain, command=command, timeout_seconds=timeout_seconds).run(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        base_commit=chain.base_commit,
        verify_profile="flag-check",
        fencing_epoch=chain.fencing_epoch,
        differential_mode=differential_mode,
    )


def _commit_files(worktree, files: dict[str, str]) -> str:
    from app.git.repo import run_git

    for name, content in files.items():
        target = worktree.path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        run_git("add", name, cwd=worktree.path)
    run_git("commit", "-m", "feat: change", cwd=worktree.path)
    return run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()


def status_of(chain: Chain) -> str:
    return str(
        chain.database.read("SELECT status FROM tasks WHERE task_id=?", (chain.task_id,))[0][
            "status"
        ]
    )


# --------------------------------------------------------------- differential


def test_passing_head_moves_the_task_to_awaiting_review(tmp_path):
    chain = build_chain(tmp_path)
    outcome = run_verify(chain)

    assert outcome.status == STATUS_PASSED
    assert outcome.failure_reason is None
    assert outcome.baseline_exit_code == 1, "the base has no flag.txt, so the check fails there"
    assert outcome.head_exit_code == 0
    assert outcome.next_status.value == "awaiting_review"
    assert status_of(chain) == "awaiting_review"


def test_the_check_runs_on_the_exact_commit(tmp_path):
    chain = build_chain(tmp_path, add_flag=False)
    outcome = run_verify(chain)

    # Nothing changed the flag, so head fails exactly like the base does.
    assert outcome.head_exit_code == 1
    assert outcome.status == STATUS_FAILED
    assert outcome.failure_reason == FAILURE_CODE


def test_failing_head_cannot_reach_review(tmp_path):
    """Acceptance criterion: a failing check does not enter Review."""
    chain = build_chain(tmp_path, add_flag=False)
    run_verify(chain)

    assert status_of(chain) == "changes_requested"
    events = [
        str(row["event_name"])
        for row in chain.database.read(
            "SELECT event_name FROM task_events WHERE aggregate_id = ? ORDER BY seq",
            (chain.task_id,),
        )
    ]
    assert "verify_code_failed" in events
    assert "verify_passed" not in events
    assert (
        merge_is_verifiable(chain.database, task_id=chain.task_id, commit=chain.head_commit)[0]
        is False
    )


def test_new_test_files_are_recorded_from_the_diff(tmp_path):
    chain = build_chain(
        tmp_path,
        extra_files={"tests/test_flag.py": "def test_x():\n    assert True\n"},
    )
    outcome = run_verify(chain)
    assert outcome.new_test_files == ("tests/test_flag.py",)


def test_timeout_is_recorded_as_timeout_not_code_failure(tmp_path):
    """R10 §28: mixing these sends environment failures through futile rework."""
    chain = build_chain(tmp_path)
    outcome = run_verify(chain, command=SLEEP, timeout_seconds=1)

    assert outcome.status == STATUS_TIMEOUT
    assert outcome.failure_reason == FAILURE_TIMEOUT
    assert status_of(chain) == "escalated"


def test_a_disallowed_command_root_is_refused_by_the_policy():
    """R10 §16: the command root comes from a human-maintained allowlist, so a
    profile naming anything else cannot even be loaded."""
    from app.verify.profile import VerifyPolicyError

    with pytest.raises(VerifyPolicyError) as caught:
        policy(command=["definitely-not-a-program"])
    assert "allowlist" in str(caught.value)


def test_an_environment_failure_is_recorded_as_env_failed(tmp_path):
    """R10 §28: an environment failure must not be recorded as a code failure."""
    from app.verify.worker import RunResult

    chain = build_chain(tmp_path)
    worker = VerifyWorker(
        chain.database,
        chain.repo,
        policy(),
        overlay_root=chain.tmp_path / "ov",
        evidence_root=chain.tmp_path / "evidence",
        runner=lambda profile, cwd: RunResult(
            exit_code=None, output="", env_error="toolchain missing"
        ),
    )
    outcome = worker.run(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        base_commit=chain.base_commit,
        verify_profile="flag-check",
        fencing_epoch=chain.fencing_epoch,
    )

    assert outcome.failure_reason == FAILURE_ENV
    assert outcome.status == STATUS_FAILED
    assert status_of(chain) == "verifying", "env_failed is a self loop; the task stays put"


# ------------------------------------------------------------------ receipts


def test_receipt_carries_the_fields_r10_requires(tmp_path):
    chain = build_chain(tmp_path)
    outcome = run_verify(chain)
    row = receipt_for(
        chain.database,
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        verify_profile="flag-check",
    )
    assert row is not None
    assert row["commit"] == chain.head_commit
    assert row["base_commit"] == chain.base_commit
    assert row["fencing_epoch"] == chain.fencing_epoch
    assert row["head_exit_code"] == 0
    assert row["baseline_exit_code"] == 1
    assert row["failure_reason"] is None
    assert str(row["receipt_hash"]).startswith("sha256:")

    import json

    payload = json.loads(str(row["receipt_json"]))
    for field in (
        "new_test_files",
        "differential_mode",
        "diff_hash",
        "verify_profile",
        "verify_env_hash",
        "toolchain",
        "fencing_epoch",
        "input_decision_ids",
        "injected_secret_ids",
        "failure_reason",
        "failed_test_case_result_ids",
        "flaky_registry_ids",
        "diff_intersection",
        "log_hash",
    ):
        assert field in payload, field
    assert outcome.receipt_id == str(row["receipt_id"])


def test_a_repeat_returns_the_first_receipt(tmp_path):
    chain = build_chain(tmp_path)
    first = run_verify(chain)
    before = len(chain.database.read("SELECT * FROM verification_receipts"))

    replay = run_verify(chain)

    assert replay.replayed is True
    assert replay.receipt_id == first.receipt_id
    assert replay.receipt_hash == first.receipt_hash
    assert len(chain.database.read("SELECT * FROM verification_receipts")) == before


def test_a_different_commit_gets_its_own_receipt(tmp_path):
    """R10 §16: Merge Gate only accepts the receipt for the exact commit."""
    chain = build_chain(tmp_path)
    run_verify(chain)

    stale = passing_receipt_for(chain.database, task_id=chain.task_id, commit=chain.base_commit)
    assert stale is None, "the base commit has no receipt, so it cannot be merged"

    ok, reason = merge_is_verifiable(
        chain.database, task_id=chain.task_id, commit=chain.head_commit
    )
    assert ok is True and "matches" in reason
    bad, bad_reason = merge_is_verifiable(chain.database, task_id=chain.task_id, commit="0" * 40)
    assert bad is False and "no passing receipt" in bad_reason


# ------------------------------------------------- not_applicable: both edges


def test_differential_not_applicable_goes_to_a_human(tmp_path):
    chain = build_chain(tmp_path, add_flag=False)
    result = chain.service.declare_differential_not_applicable(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        fencing_epoch=chain.fencing_epoch,
        reason="pure rename, baseline differential is meaningless",
        static_evidence=["git diff --stat shows renames only"],
    )
    assert result["event"] == "differential_not_applicable"
    assert status_of(chain) == "awaiting_human"


def test_a_human_can_resume_verification_after_the_declaration(tmp_path):
    """Without the reverse edge the declaration is a dead end."""
    chain = build_chain(tmp_path, add_flag=False)
    chain.service.declare_differential_not_applicable(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        fencing_epoch=chain.fencing_epoch,
        reason="docs only",
        static_evidence=["only .md files changed"],
    )
    chain.service.resume_after_differential_review(task_id=chain.task_id, decided_by="human-1")

    assert status_of(chain) == "verifying"
    events = [
        str(row["event_name"])
        for row in chain.database.read(
            "SELECT event_name FROM task_events WHERE aggregate_id = ? ORDER BY seq",
            (chain.task_id,),
        )
    ]
    assert events[-2:] == [
        "differential_not_applicable",
        "human.resume_verify_not_applicable",
    ]


def test_the_declaration_requires_a_reason_and_evidence(tmp_path):
    chain = build_chain(tmp_path, add_flag=False)
    with pytest.raises((InvalidInput, Conflict)):
        chain.service.declare_differential_not_applicable(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            fencing_epoch=chain.fencing_epoch,
            reason="  ",
            static_evidence=["x"],
        )
    with pytest.raises((InvalidInput, Conflict)):
        chain.service.declare_differential_not_applicable(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            fencing_epoch=chain.fencing_epoch,
            reason="ok",
            static_evidence=[],
        )


def test_the_declaration_refuses_a_stale_epoch(tmp_path):
    chain = build_chain(tmp_path, add_flag=False)
    with pytest.raises((InvalidInput, Conflict)):
        chain.service.declare_differential_not_applicable(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            fencing_epoch=chain.fencing_epoch - 1,
            reason="docs only",
            static_evidence=["only .md changed"],
        )


# ------------------------------------------------------------- overlay hygiene


def test_a_passing_run_recycles_its_overlay(tmp_path):
    chain = build_chain(tmp_path)
    run_verify(chain)
    ov = chain.tmp_path / "ov"
    leftovers = list(ov.iterdir()) if ov.exists() else []
    assert leftovers == [], f"overlays left behind: {leftovers}"


def test_a_failing_run_keeps_the_overlay_for_inspection(tmp_path):
    """R10 §16 keeps failing overlays; deleting them destroys the evidence."""
    chain = build_chain(tmp_path, add_flag=False)
    run_verify(chain)
    kept = list((chain.tmp_path / "ov").iterdir()) if (chain.tmp_path / "ov").exists() else []
    assert kept, "the overlay a failure happened in must not be recycled"


def test_evidence_files_are_written(tmp_path):
    chain = build_chain(tmp_path)
    run_verify(chain)
    root = chain.tmp_path / "evidence" / chain.task_id / chain.attempt_id
    assert (root / "completion.md").exists()
    assert (root / "test-results.md").exists()
    assert (root / "verification" / "receipt.json").exists()
    kinds = {
        str(row["kind"])
        for row in chain.database.read(
            "SELECT kind FROM artifacts WHERE attempt_id = ?", (chain.attempt_id,)
        )
    }
    assert "evidence_receipt.json" in kinds
