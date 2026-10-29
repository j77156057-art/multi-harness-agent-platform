"""工作树的 git 配置坏了，平台要能修好它。

2026-10-29 的真实事故：某个成员 CLI 在工作树里跑了 ``git config core.autocrlf
--global``（``--global`` 本该在键名**前面**，这样才是「写全局配置」）—— 于是
``core.autocrlf = --global`` 被写进这个工作树自己的本地配置。git 读到一个非法
布尔值，就让**这个仓库的所有 git 命令**全废，连 ``git rev-parse --git-dir`` 都
报 "bad boolean config value"。

代价不是那条任务失败，而是平台的 sweeper 在回收工位时崩掉、**整个进程退出**：
这一群之后不再回收工位、不再救合入中的任务、不再发升级提醒，而群里一声不吭。
"""

from __future__ import annotations

from app.git.repo import run_git
from app.git.worktree import WorktreeManager
from tests.git_support import base_commit, make_fixture, seed_and_claim


def _poison(worktree_path) -> None:
    """把这个工作树的本地配置写坏（照抄那个 CLI 的错误写法）。"""
    gitdir = worktree_path / ".git"
    if gitdir.is_file():
        pointer = gitdir.read_text(encoding="utf-8").strip()
        gitdir = pointer.split(":", 1)[1].strip()
        config = gitdir + "/config"
    else:
        config = str(worktree_path.parent / f"{worktree_path.name}.git" / "config")
    with open(config, "a", encoding="utf-8") as handle:
        handle.write("\tautocrlf = --global\n")


def test_the_platform_repairs_a_worktree_config_a_member_broke(tmp_path):
    fixture = make_fixture(tmp_path)
    item = seed_and_claim(fixture, task_id="T-1")
    worktree = fixture.manager.provision(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        group_short="g1",
        harness="harness-1",
        base_commit=base_commit(fixture),
    )
    _poison(worktree.path)

    broken = run_git("rev-parse", "--git-dir", cwd=worktree.path, check=False)
    assert broken.returncode != 0, "the poisoned config breaks every git command"
    assert "bad boolean config value" in broken.stderr

    healed = WorktreeManager._heal_config(worktree.path)
    assert healed and "autocrlf" in healed

    fixed = run_git("rev-parse", "--git-dir", cwd=worktree.path, check=False)
    assert fixed.returncode == 0, "the worktree's git works again"
