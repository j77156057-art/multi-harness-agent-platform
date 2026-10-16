"""CORE-RULES-001 (rules-tighten): F5 cross-provider, F7 recycle, F11 empty diff.

Each case targets the drill finding it closes; the fixtures use the same real
chains as the rest of the suite (claim -> start -> commit -> publish ->
submit), and the recycle sweep operates on the workspace rows the worktree
manager actually wrote.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.claim.service import ClaimRequest, ClaimService
from app.git.publish import PublishProxy
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import (
    WorktreeCapacityExceeded,
    WorktreeError,
    WorktreeManager,
    _force_rmtree,
)
from app.merge.worker import MergeWorker
from app.scheduler import sweep_worktree_recycle
from app.tasks.service import Conflict, SchedulerService
from app.verify.profile import VerifyPolicy
from app.verify.worker import MODE_NOT_APPLICABLE, VerifyWorker
from tests.claim_support import make_database, seed_project, seed_task
from tests.merge_support import HARNESS, PROFILE, approve, build_chain, register_harness
from tests.merge_support import policy as merge_policy

NOW = datetime.now(UTC)


# ------------------------------------------------------- F5: cross_provider


def test_same_provider_review_is_refused(tmp_path):
    chain = build_chain(tmp_path)
    register_harness(chain.database, "claude-1", provider="openai")  # same as harness-1

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id="T-1", attempt_id=chain.attempt_id, reviewer_harness_id="claude-1"
        )

    assert caught.value.code == "review.cross_provider_required"


def test_unregistered_reviewer_or_owner_is_refused(tmp_path):
    chain = build_chain(tmp_path)

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id="T-1", attempt_id=chain.attempt_id, reviewer_harness_id="ghost-1"
        )
    assert caught.value.code == "review.provider_unknown"

    # the same refusal on submit_review (the unleased path)
    with pytest.raises(Conflict) as caught:
        chain.service.submit_review(
            task_id="T-1",
            attempt_id=chain.attempt_id,
            reviewer_harness_id="ghost-1",
            verdict="approve",
            reviewed_commit=chain.head_commit,
            review_revision=1,
            findings=[],
        )
    assert caught.value.code == "review.provider_unknown"


def test_self_review_still_wins_over_the_provider_check(tmp_path):
    chain = build_chain(tmp_path)

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id="T-1", attempt_id=chain.attempt_id, reviewer_harness_id=HARNESS
        )

    assert caught.value.code == "review.self_review_forbidden"


def test_unknown_policy_is_refused_not_interpreted(tmp_path):
    chain = build_chain(tmp_path)
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET review_policy = 'vibes' WHERE task_id = 'T-1'")

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id="T-1", attempt_id=chain.attempt_id, reviewer_harness_id="reviewer-1"
        )

    assert caught.value.code == "review.policy_unknown"


def test_cross_provider_review_still_reaches_approved(tmp_path):
    """The positive path is untouched: reviewer-1 (anthropic) reviews harness-1's
    (openai) attempt, and the merge gate still sees it."""
    chain = approve(build_chain(tmp_path))

    assert (
        str(chain.database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])
        == "approved"
    )


# ------------------------------------------------------- F7: worktree recycle


def _archive_root(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path / "data")
    return tmp_path / "data" / "archives"


def _workspace_rows(database):
    return database.read("SELECT attempt_id, status, path FROM workspaces ORDER BY rowid")


def test_terminal_task_worktree_is_recycled(tmp_path, monkeypatch):
    archives = _archive_root(tmp_path, monkeypatch)
    chain = build_chain(tmp_path)
    approve(chain)
    MergeWorker(
        chain.database,
        chain.repo,
        policy=merge_policy(),
        overlay_root=tmp_path / "merge-ov",
    ).merge(
        task_id="T-1",
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )
    assert (
        str(chain.database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])
        == "merged"
    )

    report = sweep_worktree_recycle(chain.database, NOW, repo=chain.repo)

    assert report.detail["recycled"] == [chain.attempt_id]
    row = _workspace_rows(chain.database)[0]
    assert row["status"] == "archived"
    assert not chain.manager.workspace_for_attempt(chain.attempt_id)
    assert (archives / f"{chain.attempt_id}.bundle").exists(), "bundle archived"
    assert (archives / f"{chain.attempt_id}.patch").exists(), "patch archived"


def test_live_attempt_is_never_recycled_then_supersession_is(tmp_path, monkeypatch):
    _archive_root(tmp_path, monkeypatch)
    chain = build_chain(tmp_path)  # awaiting_review, this attempt is current

    assert sweep_worktree_recycle(chain.database, NOW, repo=chain.repo).actions == 0
    assert _workspace_rows(chain.database)[0]["status"] == "active", "restart window kept"

    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET current_attempt_id = 'T-1.a9' WHERE task_id = 'T-1'")

    report = sweep_worktree_recycle(chain.database, NOW, repo=chain.repo)

    assert report.detail["recycled"] == [chain.attempt_id]
    assert _workspace_rows(chain.database)[0]["status"] == "archived"


def test_changes_requested_freeze_recycles(tmp_path, monkeypatch):
    """R10 §14 L795 / §10 L513: the rework freeze archives the old worktree."""
    _archive_root(tmp_path, monkeypatch)
    chain = build_chain(tmp_path)
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'changes_requested' WHERE task_id = 'T-1'")

    report = sweep_worktree_recycle(chain.database, NOW, repo=chain.repo)

    assert report.detail["recycled"] == [chain.attempt_id]


def test_failed_archive_leaves_the_tree_and_reports_skipped(tmp_path, monkeypatch):
    _archive_root(tmp_path, monkeypatch)
    chain = build_chain(tmp_path)
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'cancelled' WHERE task_id = 'T-1'")

    def boom(self, *, attempt_id, archive_root=None):
        raise WorktreeError("bundle verify failed")

    monkeypatch.setattr(WorktreeManager, "archive_and_recycle", boom)

    report = sweep_worktree_recycle(chain.database, NOW, repo=chain.repo)

    assert report.actions == 0
    assert report.detail["recycled"] == []
    assert report.detail["skipped"][0]["attempt_id"] == chain.attempt_id
    assert _workspace_rows(chain.database)[0]["status"] == "active", "human gets the tree"
    assert chain.manager.workspace_for_attempt(chain.attempt_id) is not None


def test_missing_dead_worktree_is_reconciled_and_releases_capacity(tmp_path, monkeypatch):
    """A prior crash must not strand an active row after its tree is gone."""
    _archive_root(tmp_path, monkeypatch)
    chain = build_chain(tmp_path)
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'cancelled' WHERE task_id = 'T-1'")
    worktree = chain.manager.workspace_for_attempt(chain.attempt_id)
    assert worktree is not None
    _force_rmtree(worktree.path)
    _force_rmtree(worktree.gitdir)

    report = sweep_worktree_recycle(chain.database, NOW, repo=chain.repo)

    assert report.actions == 1
    assert report.detail["reconciled_missing"] == [chain.attempt_id]
    assert report.detail["skipped"] == []
    assert _workspace_rows(chain.database)[0]["status"] == "archived"


def test_capacity_comes_back_after_recycle(tmp_path, monkeypatch):
    """The drill's F7 pain: historical attempts eat the cap until start fails."""
    _archive_root(tmp_path, monkeypatch)
    monkeypatch.setattr("app.config.settings.concurrent_worktree", 1)
    chain = build_chain(tmp_path)
    # A genuine attempt row for T-2: workspaces.attempt_id is a FK, and the
    # real order is claim (mints the attempt) then start_task (provisions).
    seed_task(chain.database, task_id="T-2")
    item = (
        ClaimService(chain.database)
        .claim(ClaimRequest(claim_request_id="cr-cap", harness_id="harness-1", task_ids=("T-2",)))
        .items[0]
    )

    with pytest.raises(WorktreeCapacityExceeded):
        chain.manager.provision(
            task_id="T-2",
            attempt_id=str(item.attempt_id),
            group_short="g1",
            harness="harness-1",
            base_commit=chain.base_commit,
        )

    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status = 'merged' WHERE task_id = 'T-1'")
    sweep_worktree_recycle(chain.database, NOW, repo=chain.repo)

    worktree = chain.manager.provision(
        task_id="T-2",
        attempt_id=str(item.attempt_id),
        group_short="g1",
        harness="harness-1",
        base_commit=chain.base_commit,
    )
    assert worktree.path.exists()


# -------------------------------------------------------- F11: empty diff


ALWAYS_PASS = ["python", "-c", "import sys; sys.exit(0)"]


def _empty_diff_chain(tmp_path):
    """verifying task whose head is an empty commit: tree == base tree."""
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    seed_project(database, integration_commit=base)
    seed_task(database, task_id="T-1")
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)

    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-e", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    service.start_task(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        group_short="g1",
        worktrees=manager,
    )
    worktree = manager.workspace_for_attempt(str(item.attempt_id))
    assert worktree is not None
    run_git("commit", "--allow-empty", "-m", "noop", cwd=worktree.path)
    head = run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()
    PublishProxy(database, repo).publish_task_branch(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        branch=worktree.branch,
        commit=head,
        fencing_epoch=int(item.fencing_epoch),
        idempotency_key="pub-e",
    )
    service.submit_result(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        completion={"commit": head},
    )
    return database, repo, item, head, base, tmp_path


def _worker_for(database, repo, tmp_path):
    return VerifyWorker(
        database,
        repo,
        VerifyPolicy.from_mapping(
            {
                "command_roots": ["python"],
                "profiles": {PROFILE: {"command": ALWAYS_PASS, "timeout_seconds": 60}},
            }
        ),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "evidence",
    )


def test_empty_diff_cannot_pass_verification(tmp_path):
    database, repo, item, head, base, tmp = _empty_diff_chain(tmp_path)

    outcome = _worker_for(database, repo, tmp).run(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        commit=head,
        base_commit=base,
        verify_profile=PROFILE,
        fencing_epoch=int(item.fencing_epoch),
    )

    assert outcome.status == "failed"
    assert outcome.failure_reason == "code_failed", "stays inside the frozen four"
    assert outcome.next_status.value == "changes_requested"
    receipt = database.read("SELECT receipt_json FROM verification_receipts WHERE task_id = 'T-1'")[
        0
    ]

    assert json.loads(str(receipt["receipt_json"]))["empty_diff"] is True


def test_a_real_diff_keeps_the_old_passing_behaviour(tmp_path):
    chain = build_chain(tmp_path, auto_verify=False)

    outcome = _worker_for(chain.database, chain.repo, tmp_path).run(
        task_id="T-1",
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        base_commit=chain.base_commit,
        verify_profile=PROFILE,
        fencing_epoch=chain.fencing_epoch,
    )

    assert outcome.status == "passed"
    assert outcome.next_status.value == "awaiting_review"
    receipt = chain.database.read(
        "SELECT receipt_json FROM verification_receipts WHERE task_id = 'T-1'"
    )[0]

    assert json.loads(str(receipt["receipt_json"]))["empty_diff"] is False


def test_not_applicable_mode_is_a_human_decision_not_a_blanket_pass(tmp_path):
    """The escape hatch is untouched by the tightening: no base comparison in
    not_applicable mode, so an empty tree does not fail here -- it is the
    awaiting_human path that judges it (R10 §16 step 5)."""
    database, repo, item, head, base, tmp = _empty_diff_chain(tmp_path)

    outcome = _worker_for(database, repo, tmp).run(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        commit=head,
        base_commit=base,
        verify_profile=PROFILE,
        fencing_epoch=int(item.fencing_epoch),
        differential_mode=MODE_NOT_APPLICABLE,
    )

    assert outcome.status == "passed"
