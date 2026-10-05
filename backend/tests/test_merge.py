"""MERGE-EXACT-001 / MERGE-REVERT-001 / MERGE-REVERT-002.

Most of these tests are about what does *not* reach the integration branch, and
each refusal also asserts the reference did not move. A gate that returned the
right answer but wrote anyway would pass a test that only checked its return
value.
"""

from __future__ import annotations

import pytest

from app.git.repo import run_git
from app.merge.gate import MergeGateRejected, evaluate_merge_gate
from app.merge.worker import MergeWorker
from app.verify.worker import FAILURE_CODE
from tests.merge_support import (
    INTEGRATION_EXPECTS_ABSENT,
    Chain,
    approve,
    build_chain,
    commit_on_integration,
    health_of,
    policy,
    status_of,
)

ALWAYS_FAIL = ["python", "-c", "import sys; sys.exit(1)"]


def worker_for(chain: Chain, *, command=None) -> MergeWorker:
    return MergeWorker(
        chain.database,
        chain.repo,
        policy=policy(command=command),
        overlay_root=chain.tmp_path / "merge-ov",
    )


def integration_head(chain: Chain) -> str | None:
    return chain.repo.ref_commit(chain.repo.integration_ref)


def gate(chain: Chain, *, commit: str | None = None, branch: str | None = None):
    return evaluate_merge_gate(
        chain.database,
        chain.repo,
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=commit or chain.head_commit,
        branch=branch or chain.branch,
    )


def approve_in_place(chain: Chain) -> None:
    """Take an already-built chain to ``approved`` without a review lease."""
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status='approved' WHERE task_id=?", (chain.task_id,))


def rename_in_worktree(chain: Chain, *, src: str, dst: str) -> str:
    worktree = chain.manager.workspace_for_attempt(chain.attempt_id)
    assert worktree is not None
    run_git("mv", src, dst, cwd=worktree.path)
    run_git("commit", "-m", f"refactor: rename {src}", cwd=worktree.path)
    return run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()


# ------------------------------------------------------------ gate refusals


def test_gate_passes_on_the_full_chain(tmp_path):
    chain = approve(build_chain(tmp_path))
    result = gate(chain)
    assert result.ok, result.failures


def test_gate_refuses_a_commit_with_no_passing_receipt(tmp_path):
    """Acceptance: code that has not been verified cannot reach integration.

    The base commit is a real commit that no receipt covers. A receipt cannot be
    edited away to build this case -- verification_receipts is append-only -- and
    that is the point: "this commit has no receipt" is the only shape an
    unverified commit has.
    """
    chain = approve(build_chain(tmp_path))
    result = gate(chain, commit=chain.base_commit)
    assert result.ok is False
    assert any("receipt" in failure for failure in result.failures)


def test_gate_refuses_without_an_approving_review(tmp_path):
    chain = build_chain(tmp_path)
    approve_in_place(chain)
    result = gate(chain)
    assert result.ok is False
    assert any("review" in failure for failure in result.failures)


def test_gate_refuses_a_review_for_a_different_commit(tmp_path):
    """MERGE-EXACT-001: a review of another commit says nothing about this one."""
    chain = approve(build_chain(tmp_path))
    with chain.database.transaction() as conn:
        conn.execute("UPDATE reviews SET reviewed_commit = ?", ("0" * 40,))
    result = gate(chain)
    assert result.ok is False
    assert any("review" in failure for failure in result.failures)


def test_gate_refuses_a_receipt_whose_epoch_is_no_longer_current(tmp_path):
    """A reclaim advances task.current_epoch, which must invalidate old receipts.

    The receipt is immutable by design (verification_receipts is append-only), so
    the mismatch is produced the way it happens in practice: the task moves on.
    """
    chain = approve(build_chain(tmp_path))
    with chain.database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET current_epoch = current_epoch + 1 WHERE task_id = ?",
            (chain.task_id,),
        )
    result = gate(chain)
    assert result.ok is False
    assert any("fencing_epoch" in failure for failure in result.failures)


def test_gate_refuses_when_the_task_base_no_longer_matches_the_receipt(tmp_path):
    """The differential was run against one base; merging against another is not covered."""
    chain = approve(build_chain(tmp_path))
    with chain.database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET base_commit = 'deadbeef' WHERE task_id = ?", (chain.task_id,)
        )
    result = gate(chain)
    assert result.ok is False
    assert any("base_commit" in failure for failure in result.failures)


def test_gate_refuses_a_branch_that_is_not_the_attempts(tmp_path):
    chain = approve(build_chain(tmp_path))
    result = gate(chain, branch="agent/g1/harness-1/T-1/T-1.a9")
    assert result.ok is False
    assert any("does not exist" in failure for failure in result.failures)


def test_gate_refuses_while_integration_health_is_red(tmp_path):
    chain = approve(build_chain(tmp_path))
    with chain.database.transaction() as conn:
        conn.execute("UPDATE integration_health SET status='red'")
    result = gate(chain)
    assert result.ok is False
    assert any("integration_health" in failure for failure in result.failures)


def test_a_refused_merge_writes_nothing(tmp_path):
    """The gate runs before the first update-ref, so a refusal leaves no trace."""
    chain = build_chain(tmp_path, auto_verify=False)
    approve_in_place(chain)
    before = integration_head(chain)
    intents_before = len(chain.database.read("SELECT * FROM side_effect_intents"))

    with pytest.raises((MergeGateRejected, RuntimeError)):
        worker_for(chain).merge(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            commit=chain.head_commit,
            branch=chain.branch,
        )

    assert integration_head(chain) == before
    after = chain.database.read("SELECT type FROM side_effect_intents")
    assert len(after) == intents_before, "a refused merge records no new intent"
    assert "merge" not in {str(row["type"]) for row in after}
    assert status_of(chain) == "approved"


# --------------------------------------------------------------- the merge


def test_merge_writes_integration_and_marks_the_task_merged(tmp_path):
    chain = approve(build_chain(tmp_path))
    outcome = worker_for(chain).merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )

    assert outcome.status == "merged"
    assert status_of(chain) == "merged"
    merged = outcome.merge_commit
    assert merged is not None and integration_head(chain) == merged
    # The merge commit really carries both sides.
    assert (
        run_git("rev-parse", f"{merged}^2", cwd=chain.repo.path).stdout.strip() == chain.head_commit
    )
    assert (
        run_git("rev-parse", f"{merged}^1", cwd=chain.repo.path).stdout.strip() == chain.base_commit
    )
    assert run_git("show", f"{merged}:flag.txt", cwd=chain.repo.path).stdout.strip() == "ok"
    intents = {
        str(row["type"]) for row in chain.database.read("SELECT type FROM side_effect_intents")
    }
    assert "merge" in intents, "the merge is recorded as a side effect intent"
    assert "push" in intents, "the publish that put the branch in canonical is recorded too"


def test_conflict_in_the_tasks_own_files_needs_the_owner(tmp_path):
    chain = approve(build_chain(tmp_path, extra_files={"README.md": "from task\n"}))
    # integration changes the same file the task changed
    commit_on_integration(chain.repo, chain.tmp_path, edit=("README.md", "from integration\n"))

    outcome = worker_for(chain).merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )

    assert outcome.status == "conflict"
    assert outcome.event == "merge_conflict_needs_owner"
    assert status_of(chain) == "changes_requested"
    assert outcome.conflicts


def test_rename_rename_conflict_belongs_to_the_merge_worker(tmp_path):
    """A conflict on a path the task never changed is not the owner's to fix.

    rename/rename is the case that makes the two edges distinguishable: the task
    renamed README.md to TASK.md, integration renamed it elsewhere, and the
    conflicted path is README.md -- absent from the task's changed set, because
    ``git diff --name-only`` reports a rename under its new name.
    """
    chain = build_chain(tmp_path, rename=("README.md", "TASK.md"))
    commit_on_integration(chain.repo, chain.tmp_path, rename=("README.md", "INTEGRATION.md"))
    chain = approve(chain)

    outcome = worker_for(chain).merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )

    assert outcome.status == "conflict"
    assert outcome.event == "merge_conflict"
    assert status_of(chain) == "approved", "the merge worker re-queues it"


# ---------------------------------------------------------------- revert


def test_post_merge_failure_is_reverted_with_the_tree_assertion(tmp_path):
    """MERGE-REVERT-001: tree(revert) == tree(first_parent(failed_merge)).

    The integration profile passes only while flag.txt is absent, so the merge
    turns it red and the revert turns it green again -- which is what makes the
    re-test after the revert meaningful rather than a second guaranteed failure.
    """
    chain = approve(build_chain(tmp_path))
    before = integration_head(chain)

    outcome = worker_for(chain, command=INTEGRATION_EXPECTS_ABSENT).merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )

    assert outcome.status == "reverted"
    assert outcome.event == "merge_post_commit_failure"
    merge_commit, revert_commit = outcome.merge_commit, outcome.revert_commit
    assert merge_commit is not None and revert_commit is not None
    assert integration_head(chain) == revert_commit

    revert_tree = run_git(
        "rev-parse", f"{revert_commit}^{{tree}}", cwd=chain.repo.path
    ).stdout.strip()
    parent_tree = run_git(
        "rev-parse", f"{merge_commit}^1^{{tree}}", cwd=chain.repo.path
    ).stdout.strip()
    assert revert_tree == parent_tree, "the revert must restore the first parent's tree"
    assert (
        run_git("rev-parse", f"{revert_commit}^", cwd=chain.repo.path).stdout.strip()
        == merge_commit
    )
    assert (
        run_git(
            "rev-list", "--count", f"{merge_commit}..{revert_commit}", cwd=chain.repo.path
        ).stdout.strip()
        == "1"
    )
    assert before is not None and before != revert_commit

    assert status_of(chain) == "approved"
    events = [
        str(row["event_name"])
        for row in chain.database.read(
            "SELECT event_name FROM task_events WHERE aggregate_id=? ORDER BY seq", (chain.task_id,)
        )
    ]
    assert "merge_reverted" in events
    intents = {
        str(row["type"]) for row in chain.database.read("SELECT type FROM side_effect_intents")
    }
    assert {"merge", "revert_merge"} <= intents


def test_a_successful_revert_returns_health_to_green(tmp_path):
    chain = approve(build_chain(tmp_path))
    worker_for(chain, command=INTEGRATION_EXPECTS_ABSENT).merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )
    # The revert restored the previous tree, so the re-run passes.
    assert health_of(chain) == "green"


def test_verification_failure_never_reaches_the_gate(tmp_path):
    """A failing check leaves the task in changes_requested, so merging is moot."""
    chain = build_chain(tmp_path, verify_command=ALWAYS_FAIL)
    assert status_of(chain) == "changes_requested"
    receipt = chain.database.read("SELECT failure_reason FROM verification_receipts")[0]
    assert receipt["failure_reason"] == FAILURE_CODE

    with pytest.raises((MergeGateRejected, RuntimeError)):
        worker_for(chain).merge(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            commit=chain.head_commit,
            branch=chain.branch,
        )


def test_a_revert_that_cannot_restore_green_escalates(tmp_path):
    """MERGE-REVERT-002: a recovery that does not recover must not look like one."""
    chain = approve(build_chain(tmp_path))
    outcome = worker_for(chain, command=ALWAYS_FAIL).merge(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )
    assert outcome.status == "revert_red"
    assert status_of(chain) == "escalated"
    assert health_of(chain) == "escalated"
