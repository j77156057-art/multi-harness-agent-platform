"""合并互斥锁的机械验证。

不用 mock：`git init` 建真仓库、真的并发起线程抢锁、真的留下死 pid。
原因是这个工具的价值全在「跨进程、跨 worktree、原子性」上，mock 掉 git
就等于把要测的东西测没了——这与R10 复审总结的教训同构。

覆盖：
  1. 锁落在 git common dir，跨 worktree 可见（per-worktree 目录挡不住别人）
  2. 第二次 acquire 明确失败，且消息里带持有者信息
  3. release 后可以再取
  4. 持有者进程已死 + 超过 TTL -> 判定 stale；只死但没超时 -> 不 stale
  5. 真实并发：N 个线程同时抢锁，有且只有一个成功
  6. owner.json 损坏时不崩溃，报「残留」而不是假装空闲
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from mergeguard import guard


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """一个带一次提交、默认分支为 main 的真仓库。

    `-b main` 是必需的：cli 的第一条守卫是「当前分支必须是 main」，若 fixture
    建出来的是 master，所有 CLI 用例都会在分支检查处提前返回，测不到后面
    真正想测的拒绝路径。
    """
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("x", encoding="utf-8")
    _git(r, "add", "f.txt")
    _git(r, "commit", "-m", "init")
    return r


def test_lock_lives_in_git_common_dir(repo: Path):
    """per-worktree 的目录挡不住另一个 worktree，锁必须在 common dir。"""
    common = guard.git_common_dir(repo)
    guard.acquire(repo, task_id="T-1", branch="b1", worktree=repo)
    try:
        assert guard.lock_path(repo).parent == common
        assert guard.lock_path(repo).is_dir()
    finally:
        guard.release(repo)


def test_linked_worktree_resolves_to_same_common_dir(repo: Path):
    """真正的 linked worktree 里，锁必须和主工作区指向同一个目录。

    这是整个设计成立的前提：如果每个 worktree 各锁各的，跨 worktree 并发
    合并照样能撞车——而本仓库 7 个 worktree 正是这种布局。
    """
    linked = repo / "wt"
    _git(repo, "worktree", "add", "-b", "wtb", str(linked))
    try:
        assert guard.git_common_dir(linked) == guard.git_common_dir(repo)
        assert guard.lock_path(linked) == guard.lock_path(repo)
        guard.acquire(repo, task_id="T-1", branch="b1", worktree=repo)
        with pytest.raises(guard.LockError):
            guard.acquire(linked, task_id="T-2", branch="b2", worktree=linked)
        guard.release(repo)
    finally:
        _git(repo, "worktree", "remove", "--force", str(linked))


def test_git_dir_resolution_spawns_no_process(monkeypatch, repo: Path):
    """热路径不得 spawn 子进程。

    实测依据：pytest 的 fd 捕获下并发 `git rev-parse` 会随机返回
    0xC000013A（STATUS_CONTROL_C_EXIT），让抢锁随机失败。互斥原语不该
    依赖外部命令，更不该在并发下依赖它。
    """

    def boom(*_a, **_k):
        raise AssertionError("git_common_dir 不应 spawn git 子进程")

    monkeypatch.setattr(guard.subprocess, "run", boom)
    # 清掉缓存后重新解析，确保真的走了纯文件探测这条路
    guard._git_common_dir_cached.cache_clear()
    assert guard.git_common_dir(repo) == repo / ".git"


def test_second_acquire_fails_with_holder_info(repo: Path):
    """拿不到锁必须明确失败，并把「谁在合并」写进消息。"""
    guard.acquire(repo, task_id="T-1", branch="b1", worktree=repo)
    try:
        with pytest.raises(guard.LockError) as exc:
            guard.acquire(repo, task_id="T-2", branch="b2", worktree=repo)
        msg = str(exc.value)
        assert "T-1" in msg  # 持有者 task_id
        assert "b1" in msg  # 持有者分支
        assert "pid" in msg  # 可诊断信息
        assert "不要删除该目录" in msg  # 明确下一步，而不是让人自己去猜
    finally:
        guard.release(repo)


def test_release_allows_reacquire(repo: Path):
    guard.acquire(repo, task_id="T-1", branch="b1", worktree=repo)
    assert guard.release(repo) is True
    assert guard.release(repo) is False  # 幂等
    guard.acquire(repo, task_id="T-2", branch="b2", worktree=repo)
    guard.release(repo)


def test_stale_requires_both_dead_pid_and_expired_ttl(repo: Path):
    """只看 pid 会误判 pid 被回收；只看时间会在进程正忙时误判。"""
    holder = guard.LockInfo(
        pid=999999, task_id="T", branch="b", worktree="w", acquired_at=time.time()
    )
    assert not guard.is_stale(holder, ttl_seconds=900)  # 死了但没过 TTL
    fresh = guard.LockInfo(
        pid=os.getpid(), task_id="T", branch="b", worktree="w", acquired_at=time.time()
    )
    assert not guard.is_stale(fresh, ttl_seconds=900)  # 活着


def test_stale_lock_is_taken_over(repo: Path):
    """持有者已死且过 TTL -> 允许接管，否则一次崩溃就永久锁死合并。"""
    guard.acquire(repo, task_id="dead", branch="b1", worktree=repo)
    path = guard.lock_path(repo)
    data = json.loads((path / "owner.json").read_text(encoding="utf-8"))
    data["pid"] = 999999  # 假装持有者进程已死
    data["acquired_at"] = time.time() - 10_000  # 远超 TTL
    (path / "owner.json").write_text(json.dumps(data), encoding="utf-8")

    info = guard.acquire(repo, task_id="T-2", branch="b2", worktree=repo)
    assert info.task_id == "T-2"
    guard.release(repo)


def test_concurrent_acquire_has_exactly_one_winner(repo: Path):
    """真实并发：N 个线程同时抢，有且只有一个成功。

    线程里的异常**必须**显式收集：pytest 默认不会因线程抛异常而失败，
    那个线程会静默消失，于是「8 个线程」实际只跑了7 个——本测试第一版就
    栽在这里（losers 只有 6，缺的那个没有任何痕迹）。
    """
    winners: list[str] = []
    losers: list[str] = []
    errors: list[tuple[int, BaseException]] = []
    barrier = threading.Barrier(8)
    mutex = threading.Lock()

    def attempt(i: int) -> None:
        barrier.wait()
        try:
            guard.acquire(repo, task_id=f"T-{i}", branch=f"b{i}", worktree=repo)
        except guard.LockError:
            with mutex:
                losers.append(f"T-{i}")
        except BaseException as exc:  # noqa: BLE001 - 故意兜住一切以便暴露
            with mutex:
                errors.append((i, exc))
        else:
            with mutex:
                winners.append(f"T-{i}")

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"线程内出现非LockError 异常：{errors}"
    assert len(winners) == 1, f"应恰好一个赢家，实际 {winners} / losers={losers}"
    assert len(losers) == 7, f"其余 7 个都应收到LockError，实际 losers={losers}"
    guard.release(repo)


def test_corrupt_owner_file_reports_residue_not_idle(repo: Path):
    """锁目录存在但 owner.json 损坏：不能报「空闲」，那会让两个人同时合。"""
    path = guard.lock_path(repo)
    path.mkdir(parents=True)
    (path / "owner.json").write_text("{ not json", encoding="utf-8")

    assert guard.read_lock(repo) is None
    assert "残留" in guard.describe_status(repo)
    with pytest.raises(guard.LockError) as exc:
        guard.acquire(repo, task_id="T-1", branch="b1", worktree=repo)
    assert "内容不可读" in str(exc.value) or "残留" in str(exc.value)


# ---------------------------------------------------------------------------
# CLI 端到端：把 cli.py 当子进程跑在临时仓库里
#
# 为什么单测之外还要这一层：cli.py 是别人真正执行的东西，它有三条拒绝路径
# 是对事故的承诺（脏工作区、锁被占、非main），只有真跑一遍才算验证过。
# 这一层已经抓出过两个真 bug —— cli 曾把 REPO_ROOT 硬编码成脚本所在仓库，
# 以及 subprocess 用 text=True 在 Windows 上按 GBK 解码 git 的中文输出。
# ---------------------------------------------------------------------------

CLI = Path(__file__).resolve().parents[2] / "tools" / "mergeguard" / "cli.py"


def _cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_cli_rejects_dirty_worktree(repo: Path):
    """脏工作区直接拒绝，并点名文件。

    这条是第4 次事故（共享工作区丢提交）的直接防护：别人的未提交改动
    绝不能被卷进 merge commit。
    """
    (repo / "f.txt").write_text("dirty", encoding="utf-8")
    r = _cli(repo, "merge", "main", "--task-id", "T-0")
    assert r.returncode == 2, r.stderr
    assert "未提交改动" in r.stderr
    assert "f.txt" in r.stderr, "必须点名是哪个文件，不能只说「有改动」"


def test_cli_reports_lock_holder_verbatim(repo: Path):
    """锁被占时，消息必须含持有者的 task_id / 分支 / pid，并劝阻删锁。"""
    lock = guard.lock_path(repo)
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "task_id": "T-999",
                "branch": "b-x",
                "worktree": str(repo),
                "acquired_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    r = _cli(repo, "merge", "main", "--task-id", "T-0")
    assert r.returncode == 3, r.stderr
    assert "T-999" in r.stderr and "b-x" in r.stderr
    assert "不要删除该目录" in r.stderr

    s = _cli(repo, "status")
    assert s.returncode == 0 and "T-999" in s.stdout


def test_cli_refuses_when_not_on_main(repo: Path):
    _git(repo, "checkout", "-b", "side")
    r = _cli(repo, "merge", "main", "--task-id", "T-0")
    assert r.returncode == 2
    assert "只允许在 main 上运行" in r.stderr


def test_cli_releases_lock_when_gates_skipped(repo: Path):
    """跳过门禁的路径：不提交，但**必须释放锁**。

    锁泄漏会让整个仓库永久无法合并（除非人工去删目录），所以「任何退出路径都
    释放锁」比「合并成功」更要紧。
    """
    _git(repo, "checkout", "-b", "feature")
    (repo / "g.txt").write_text("y", encoding="utf-8")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-m", "feature work")
    _git(repo, "checkout", "main")
    before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()

    r = _cli(repo, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    assert before == after, "跳过门禁就不该提交"
    assert not guard.lock_path(repo).exists(), "任何退出路径都必须释放锁"
    assert "空闲" in _cli(repo, "status").stdout



def test_cli_keeps_conflict_and_does_not_abort(repo: Path):
    """冲突时保留现场并劝阻 abort——另一个人可能正在同一个工作区解冲突。

    必须让 main 也改同一个文件，否则 main 与 b 没有分叉，`--no-ff` 只会生成
    一个干净的 merge commit，测不到冲突路径。
    """
    _git(repo, "checkout", "-b", "b")
    (repo / "c.txt").write_text("b-side", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-m", "b")
    _git(repo, "checkout", "main")
    (repo / "c.txt").write_text("main-side", encoding="utf-8")  # 让 main 也分叉
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-m", "main moves on")

    r = _cli(repo, "merge", "b", "--task-id", "T-2", "--skip-gate")
    assert r.returncode == 4, r.stdout + r.stderr
    assert "不要 git merge --abort" in r.stderr
    assert (repo / ".git" / "MERGE_HEAD").exists(), "冲突现场必须保留"
    assert not guard.lock_path(repo).exists(), "退出前必须释放锁"


def test_cli_does_not_commit_before_gates(repo: Path):
    """核心保证：门禁跑在 merge commit **落地之前**。

    这条是 `--no-commit` 存在的唯一理由。首版漏了它：`git merge` 在无冲突时
    会自动提交，于是门禁实际跑在 merge commit 之后，「门禁红则不提交」的承诺
    是假的 —— 实测中工具最后一步 `git commit` 因「nothing to commit」返回 1，
    报成合并失败，而 main 其实已经带上了 merge commit。

    用 --skip-gate 走一遍：退出码 0，但main 的 HEAD 必须**没动**。
    """
    _git(repo, "checkout", "-b", "feature")
    (repo / "g.txt").write_text("y", encoding="utf-8")
    _git(repo, "add", "g.txt")
    _git(repo, "commit", "-m", "feature work")
    _git(repo, "checkout", "main")
    before = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace").stdout.strip()

    r = _cli(repo, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    after = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace").stdout.strip()
    assert before == after, "--skip-gate 也不该提交：门禁没跑，merge commit 不能落地"
    assert (repo / ".git" / "MERGE_HEAD").exists(), "应停在待提交的合并状态"
