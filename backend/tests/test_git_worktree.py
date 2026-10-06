"""GIT-ACL-001: one attempt, one writable worktree.

R10 §14 ("两个任务不得共享可写 worktree") and its lifecycle rules: short-id
directories, capacity as a queue reason, and archive-before-recycle.
"""

from __future__ import annotations

import pytest

from app.git.branch import parse_branch
from app.git.worktree import WorktreeCapacityExceeded, WorktreeError, WorktreeManager
from tests.git_support import commit_in, make_fixture, provision, seed_and_claim


def test_two_attempts_get_separate_writable_directories(tmp_path):
    fixture = make_fixture(tmp_path)
    first = seed_and_claim(fixture, task_id="T-1")
    second = seed_and_claim(fixture, task_id="T-2")

    one = provision(fixture, task_id="T-1", attempt_id=first.attempt_id)
    two = provision(fixture, task_id="T-2", attempt_id=second.attempt_id)

    assert one.path != two.path
    assert one.gitdir != two.gitdir
    assert one.branch != two.branch
    with fixture.database.transaction() as conn:
        WorktreeManager.assert_isolated(conn)


def test_the_directory_id_is_short_and_the_branch_carries_the_identity(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)

    assert worktree.path.parent == fixture.rest / "wt"
    assert len(worktree.path.name) == 4, "R10 §14 asks for a short physical id"
    assert worktree.branch == f"agent/g1/harness-1/T-1/{item.attempt_id}"
    parsed = parse_branch(worktree.branch)
    assert parsed.name == worktree.branch
    assert not parsed.is_help


def test_provisioning_the_same_attempt_twice_is_a_no_op(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    first = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    second = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)

    assert first.workspace_id == second.workspace_id
    assert first.path == second.path
    rows = fixture.database.read("SELECT * FROM workspaces")
    assert len(rows) == 1


def test_capacity_raises_the_queue_reason_condition(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    with pytest.raises(WorktreeCapacityExceeded):
        fixture.manager.provision(
            task_id="T-2",
            attempt_id="T-2.a1",
            group_short="g1",
            harness="harness-1",
            base_commit="deadbeef",
            max_concurrent=1,
        )


def test_worktree_uses_canonical_objects_through_alternates(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)

    alternates = (worktree.gitdir / "objects" / "info" / "alternates").read_text(encoding="utf-8")
    assert alternates.strip() == str(fixture.repo.objects_dir)
    # The tree really is a separate writable directory, not the bare repository.
    assert not str(worktree.gitdir).startswith(str(fixture.repo.path))


def test_archive_verifies_the_bundle_before_recycling(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    head = commit_in(worktree, filename="a.txt", content="hello\n", message="feat: a")

    result = fixture.manager.archive_and_recycle(
        attempt_id=item.attempt_id, archive_root=fixture.rest / "archives"
    )

    assert result.bundle.exists() and result.bundle.stat().st_size > 0
    assert result.patch.exists()
    patch = result.patch.read_text(encoding="utf-8")
    assert "+hello" in patch, "the patch is the base..head diff, not a commit listing"
    assert "a.txt" in patch
    assert head  # the commit exists; a diff carries no hash to assert on
    assert not worktree.path.exists(), "R10 §14 recycles only after the archive verifies"
    assert not worktree.gitdir.exists()

    with fixture.database.connect() as conn:
        row = conn.execute(
            "SELECT status FROM workspaces WHERE attempt_id = ?", (item.attempt_id,)
        ).fetchone()
    assert row["status"] == "archived"
    kinds = {
        str(r["kind"])
        for r in fixture.database.read(
            "SELECT kind FROM artifacts WHERE attempt_id = ?", (item.attempt_id,)
        )
    }
    assert kinds == {"git_bundle", "patch"}


def test_archiving_an_unknown_attempt_is_refused(tmp_path):
    fixture = make_fixture(tmp_path)
    with pytest.raises(WorktreeError):
        fixture.manager.archive_and_recycle(attempt_id="T-9.a9")


def test_start_task_provisions_the_worktree(tmp_path):
    """The MVP-08 card called `start` scaffolding; MVP-05 makes it real."""
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)

    outcome = fixture.service.start_task(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=fixture.manager,
    )

    assert outcome["branch"] == f"agent/g1/harness-1/T-1/{item.attempt_id}"
    assert (
        fixture.database.read("SELECT status FROM tasks WHERE task_id='T-1'")[0]["status"]
        == "in_progress"
    )
    rows = fixture.database.read(
        "SELECT * FROM workspaces WHERE attempt_id = ?", (item.attempt_id,)
    )
    assert len(rows) == 1 and rows[0]["status"] == "active"


def test_long_task_ids_do_not_lengthen_the_physical_path(tmp_path):
    """R10 §14: the physical directory carries a short id; the identity lives
    in the branch name. That choice is what keeps deep roots + long task ids
    under Windows' classic 260-char limit -- proven here end to end
    (provision -> commit -> archive) instead of asserted in prose.
    """
    deep = tmp_path / ("d" * 100)
    deep.mkdir()
    fixture = make_fixture(deep)
    long_task = "T-" + "a" * 90
    item = seed_and_claim(fixture, task_id=long_task)

    worktree = provision(fixture, task_id=long_task, attempt_id=item.attempt_id)
    assert long_task in worktree.branch, "the branch still carries the long identity"
    assert len(str(worktree.path)) < 260
    assert len(str(worktree.gitdir)) < 260

    commit_in(worktree, filename="a.txt", content="hello\n", message="feat: long path")
    result = fixture.manager.archive_and_recycle(
        attempt_id=item.attempt_id, archive_root=deep / "archives"
    )
    assert result.bundle.exists() and result.bundle.stat().st_size > 0
    assert not worktree.path.exists(), "cleanup still works under a deep root"
