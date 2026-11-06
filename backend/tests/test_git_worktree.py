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

    path = worktree.gitdir / "objects" / "info" / "alternates"
    alternates = path.read_text(encoding="utf-8")
    assert alternates.strip() == str(fixture.repo.objects_dir)
    # LF-only is load-bearing: git does not strip a trailing CR from an
    # alternates entry, so a CRLF file makes every git command in the worktree
    # fail with `object directory ... does not exist` (observed in the R8
    # harness drill). The old `.strip()` assertion masked exactly this byte.
    assert b"\r" not in path.read_bytes()
    assert alternates.endswith("\n") and not alternates.endswith("\r\n")
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


def _attempt_suffix(attempt_id: str) -> str:
    return attempt_id.rsplit(".", 1)[-1] if "." in attempt_id else attempt_id


def test_recycle_archives_uncommitted_and_untracked_work(tmp_path):
    """07 现场：回收只打了 base..head，四条任务的 patch 全是 0 字节。

    这里在真实 git worktree 上造出「已提交 + 已跟踪未提交 + 未跟踪」三种改动，
    跑真实的 archive_and_recycle，断言脏工作被快照下来、敏感文件不进 tar。
    """
    import json
    import tarfile

    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    commit_in(worktree, filename="a.txt", content="hello\n", message="feat: a")
    # 已跟踪但没提交的改动：这是 dirty diff 要抓的部分。
    (worktree.path / "a.txt").write_text("hello changed\n", encoding="utf-8")
    # 未跟踪的交付物：这是 tar 要抓的部分。
    (worktree.path / "frontend").mkdir()
    for name in ("one.html", "two.js", "three.css"):
        (worktree.path / "frontend" / name).write_text(name, encoding="utf-8")
    # 敏感文件：必须不进 tar。
    (worktree.path / ".env.test").write_text("SECRET=do-not-archive\n", encoding="utf-8")

    archive_root = fixture.rest / "archives"
    result = fixture.manager.archive_and_recycle(
        attempt_id=item.attempt_id, archive_root=archive_root
    )
    assert result.bundle.exists() and result.bundle.stat().st_size > 0

    suffix = _attempt_suffix(item.attempt_id)
    checkpoint = archive_root / item.attempt_id
    dirty = checkpoint / f"checkpoint-{suffix}.dirty.diff"
    untracked = checkpoint / f"checkpoint-{suffix}.untracked.tar"
    manifest = json.loads((checkpoint / "manifest.json").read_text(encoding="utf-8"))

    assert dirty.stat().st_size > 0
    assert "hello changed" in dirty.read_text(encoding="utf-8")
    with tarfile.open(untracked) as archive:
        names = archive.getnames()
    assert {"frontend/one.html", "frontend/two.js", "frontend/three.css"} <= set(names)
    assert ".env.test" not in names, "secret files must be excluded at packaging time"

    assert manifest["deliverable"] is False
    assert manifest["reason"] == "uncommitted_worktree"
    assert manifest["excluded_secret_paths"] == [".env.test"]
    entries = {entry["kind"]: entry for entry in manifest["entries"]}
    assert entries["checkpoint_dirty"]["deliverable"] is False
    assert entries["checkpoint_untracked"]["reason"] == "uncommitted_worktree"

    with fixture.database.connect() as conn:
        kinds = {
            str(row["kind"])
            for row in conn.execute(
                "SELECT kind FROM artifacts WHERE attempt_id = ?", (item.attempt_id,)
            )
        }
    assert {
        "git_bundle",
        "patch",
        "checkpoint_dirty",
        "checkpoint_modified",
        "checkpoint_untracked",
    } == kinds
    assert not worktree.path.exists()


def test_checkpoint_captures_timeout_work_without_recycling(tmp_path):
    """超时分支必须保留 worktree，同时把半成品快照下来。"""
    import tarfile

    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    commit_in(worktree, filename="a.txt", content="hello\n", message="feat: a")
    (worktree.path / "a.txt").write_text("partial\n", encoding="utf-8")
    (worktree.path / "out").mkdir()
    for name in ("one.py", "two.py", "three.py"):
        (worktree.path / "out" / name).write_text("partial\n", encoding="utf-8")
    (worktree.path / ".env").write_text("TOKEN=do-not-archive\n", encoding="utf-8")

    snapshot = fixture.manager.checkpoint(
        attempt_id=item.attempt_id, archive_root=fixture.rest / "archives"
    )

    assert snapshot is not None
    assert snapshot["deliverable"] is False
    assert snapshot["untracked_files"] == ["out/one.py", "out/three.py", "out/two.py"]
    with tarfile.open(snapshot["untracked_archive"]) as archive:
        names = archive.getnames()
    assert {name for name in names} == {"out/one.py", "out/three.py", "out/two.py"}
    assert ".env" not in names
    assert worktree.path.exists(), "timeout keeps the worktree for replay"
    assert (worktree.path / ".env").exists()


def test_a_real_harness_timeout_still_archives_bundle_and_patch(tmp_path):
    """真的挂住一个子进程，走完「超时 → checkpoint → 清扫器归档」整条路。

    之前 checkpoint 与 archive 各自有测试，但没有一个用例证明**真实子进程
    被 deadline 杀掉之后**证据还能落盘。这里不模拟异常类型：用
    SubprocessRunner 起一个 sleep 的子进程，由其真实超时抛 HarnessTimeout，
    再按 worker 超时分支做 checkpoint、按 sweeper 做 archive，最后从磁盘
    核对 patch 里有已提交的改动、快照里有未提交的文件。
    """
    import sys
    import time

    from app.adapter.harnesses.cli import HarnessTimeout, SubprocessRunner

    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    commit_in(worktree, filename="a.txt", content="hello\n", message="feat: a")
    (worktree.path / "half-done.txt").write_text("partial\n", encoding="utf-8")

    runner = SubprocessRunner(poll_seconds=0.05)
    started = time.monotonic()
    with pytest.raises(HarnessTimeout) as caught:
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=worktree.path,
            timeout_seconds=0.5,
        )
    assert caught.value.duration_seconds >= 0.4
    assert time.monotonic() - started < 10, "deadline really kills the child"

    archive_root = fixture.rest / "archives"
    snapshot = fixture.manager.checkpoint(
        attempt_id=item.attempt_id, archive_root=archive_root
    )
    assert snapshot is not None and snapshot["deliverable"] is False
    assert worktree.path.exists(), "timeout keeps the worktree for replay"

    result = fixture.manager.archive_and_recycle(
        attempt_id=item.attempt_id, archive_root=archive_root
    )
    assert result.bundle.stat().st_size > 0
    assert "+hello" in result.patch.read_text(encoding="utf-8")
    assert not worktree.path.exists()

    kinds = {
        str(row["kind"])
        for row in fixture.database.read(
            "SELECT kind FROM artifacts WHERE attempt_id = ?", (item.attempt_id,)
        )
    }
    assert {"git_bundle", "patch", "checkpoint_untracked"} <= kinds


def test_archive_keeps_the_worktree_when_a_secret_is_the_only_dirty_file(tmp_path):
    """只有敏感文件脏时，不能因为「没有可归档内容」就丢掉 manifest 证据。"""
    import json

    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture)
    worktree = provision(fixture, task_id="T-1", attempt_id=item.attempt_id)
    (worktree.path / ".env").write_text("TOKEN=secret\n", encoding="utf-8")

    archive_root = fixture.rest / "archives"
    fixture.manager.archive_and_recycle(
        attempt_id=item.attempt_id, archive_root=archive_root
    )
    manifest = json.loads(
        (archive_root / item.attempt_id / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["deliverable"] is False
    assert manifest["excluded_secret_paths"] == [".env"]
    assert not (
        archive_root
        / item.attempt_id
        / f"checkpoint-{_attempt_suffix(item.attempt_id)}.untracked.tar"
    ).exists()

