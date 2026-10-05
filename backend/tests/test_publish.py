"""GIT-ACL-002: canonical is writable only through the publish proxy.

The checklist's two acceptance criteria for MVP-05 are here: a direct write to
the integration branch fails, and a publish carrying a stale fencing epoch fails.
The first is enforced by the canonical pre-receive hook -- a real ``git push`` is
attempted and must be refused -- because a convention that no one can violate by
accident is not the same as a boundary.
"""

from __future__ import annotations

import pytest

from app.git.publish import PublishProxy, PublishRejected
from tests.git_support import base_commit, commit_in, make_fixture, provision, push, seed_and_claim

PROXY = "proxy"


def _prepared(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    head = commit_in(worktree, filename="a.txt", content="hello\n", message="feat: a")
    return fixture, item, worktree, head


def test_publish_imports_objects_and_writes_the_task_branch(tmp_path):
    fixture, item, worktree, head = _prepared(tmp_path)
    result = PublishProxy(fixture.database, fixture.repo).publish_task_branch(
        task_id="T-1",
        attempt_id=item.attempt_id,
        branch=worktree.branch,
        commit=head,
        fencing_epoch=item.fencing_epoch,
        idempotency_key="pub-1",
    )

    assert result.intent_status == "done"
    assert fixture.repo.ref_commit(f"refs/heads/{worktree.branch}") == head
    assert fixture.repo.has_object(head)
    # The canonical store receives the objects; the agent gitdir stays the only
    # place the branch exists as a local branch.
    assert fixture.repo.ref_commit(fixture.repo.integration_ref) == base_commit(fixture)


def test_publish_records_a_push_intent(tmp_path):
    fixture, item, worktree, head = _prepared(tmp_path)
    PublishProxy(fixture.database, fixture.repo).publish_task_branch(
        task_id="T-1",
        attempt_id=item.attempt_id,
        branch=worktree.branch,
        commit=head,
        fencing_epoch=item.fencing_epoch,
        idempotency_key="pub-intent",
    )
    intents = fixture.database.read("SELECT * FROM side_effect_intents")
    assert len(intents) == 1
    assert intents[0]["type"] == "push"
    assert intents[0]["status"] == "done"
    assert intents[0]["idempotency_key"] == "pub-intent"


def test_publish_is_idempotent_by_key(tmp_path):
    fixture, item, worktree, head = _prepared(tmp_path)
    proxy = PublishProxy(fixture.database, fixture.repo)
    kwargs = {
        "task_id": "T-1",
        "attempt_id": item.attempt_id,
        "branch": worktree.branch,
        "commit": head,
        "fencing_epoch": item.fencing_epoch,
        "idempotency_key": "pub-same",
    }
    first = proxy.publish_task_branch(**kwargs)
    second = proxy.publish_task_branch(**kwargs)

    assert second.replayed is True
    assert second.intent_id == first.intent_id
    assert len(fixture.database.read("SELECT * FROM side_effect_intents")) == 1


def test_old_fencing_epoch_is_rejected(tmp_path):
    """Acceptance criterion: a publish carrying a stale epoch fails."""
    fixture, item, worktree, head = _prepared(tmp_path)
    with pytest.raises(PublishRejected) as caught:
        PublishProxy(fixture.database, fixture.repo).publish_task_branch(
            task_id="T-1",
            attempt_id=item.attempt_id,
            branch=worktree.branch,
            commit=head,
            fencing_epoch=item.fencing_epoch - 1,
            idempotency_key="pub-stale",
        )
    assert caught.value.code == "fencing.epoch_stale"
    assert fixture.repo.ref_commit(f"refs/heads/{worktree.branch}") is None
    assert fixture.database.read("SELECT * FROM side_effect_intents") == []


def test_direct_push_to_integration_is_rejected_by_canonical(tmp_path):
    """Acceptance criterion: writing the integration branch directly fails."""
    fixture, _item, worktree, _head = _prepared(tmp_path)

    result = push(worktree, fixture.repo, "HEAD:refs/heads/integration")

    assert result.returncode != 0
    assert "may only be written by the Merge Worker" in result.stderr
    assert fixture.repo.ref_commit(fixture.repo.integration_ref) == base_commit(fixture)


def test_any_client_push_is_rejected(tmp_path):
    """ADR-059: the harness never pushes at all, so no ref is writable by push."""
    fixture, _item, worktree, _head = _prepared(tmp_path)

    result = push(worktree, fixture.repo, f"HEAD:refs/heads/{worktree.branch}")

    assert result.returncode != 0
    assert "client pushes are not accepted" in result.stderr
    assert fixture.repo.ref_commit(f"refs/heads/{worktree.branch}") is None


def test_publish_rejects_a_branch_that_is_not_the_attempts_branch(tmp_path):
    fixture, item, _worktree, head = _prepared(tmp_path)
    with pytest.raises(PublishRejected) as caught:
        PublishProxy(fixture.database, fixture.repo).publish_task_branch(
            task_id="T-1",
            attempt_id=item.attempt_id,
            branch=f"agent/g1/harness-1/T-1/{item.attempt_id}-sneaky",
            commit=head,
            fencing_epoch=item.fencing_epoch,
            idempotency_key="pub-wrong-branch",
        )
    assert caught.value.code == "publish.branch_not_allowed"


def test_publish_rejects_writing_the_integration_branch(tmp_path):
    fixture, item, _worktree, head = _prepared(tmp_path)
    with pytest.raises(PublishRejected) as caught:
        PublishProxy(fixture.database, fixture.repo).publish_task_branch(
            task_id="T-1",
            attempt_id=item.attempt_id,
            branch="integration",
            commit=head,
            fencing_epoch=item.fencing_epoch,
            idempotency_key="pub-integration",
        )
    assert caught.value.code == "publish.branch_not_allowed"
    assert caught.value.http_status == 403


def test_publish_rejects_alternates_pointing_outside_the_canonical_store(tmp_path):
    """GIT-ACL-002: the agent gitdir may only read this project's objects."""
    fixture, item, worktree, head = _prepared(tmp_path)
    outside = tmp_path / "elsewhere.git"
    outside.mkdir()
    (worktree.gitdir / "objects" / "info" / "alternates").write_text(
        f"{fixture.repo.objects_dir}\n{outside}\n", encoding="utf-8"
    )

    with pytest.raises(PublishRejected) as caught:
        PublishProxy(fixture.database, fixture.repo).publish_task_branch(
            task_id="T-1",
            attempt_id=item.attempt_id,
            branch=worktree.branch,
            commit=head,
            fencing_epoch=item.fencing_epoch,
            idempotency_key="pub-alternates",
        )
    assert caught.value.code == "publish.alternates_invalid"
    assert caught.value.http_status == 403


def test_publish_rejects_a_commit_the_agent_gitdir_does_not_have(tmp_path):
    fixture, item, worktree, _head = _prepared(tmp_path)
    with pytest.raises(PublishRejected) as caught:
        PublishProxy(fixture.database, fixture.repo).publish_task_branch(
            task_id="T-1",
            attempt_id=item.attempt_id,
            branch=worktree.branch,
            commit="0" * 40,
            fencing_epoch=item.fencing_epoch,
            idempotency_key="pub-unreachable",
        )
    assert caught.value.code == "publish.commit_unreachable"


def test_publish_records_the_head_commit_on_the_attempt(tmp_path):
    fixture, item, worktree, head = _prepared(tmp_path)
    PublishProxy(fixture.database, fixture.repo).publish_task_branch(
        task_id="T-1",
        attempt_id=item.attempt_id,
        branch=worktree.branch,
        commit=head,
        fencing_epoch=item.fencing_epoch,
        idempotency_key="pub-head",
    )
    row = fixture.database.read(
        "SELECT head_commit FROM task_attempts WHERE attempt_id = ?", (item.attempt_id,)
    )[0]
    assert row["head_commit"] == head
