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

CLI 端到端（把 cli.py 当子进程跑在临时仓库里）额外覆盖：
  7. 门禁全过 -> commit -> HEAD 前进 -> 锁与 MERGE_HEAD 都清干净。**唯一生产
     成功路径**；首版把 `test_cli_merges_and_releases_lock` 删掉且无替代，
     等于这条承诺零覆盖。
  8. 门禁红 -> 不提交 -> **重跑本命令自动续跑到提交**（不再是需要人肉的死路）
  9. 续跑时出现未暂存改动 -> 拒绝，不把别人的东西卷进 merge commit
  10. MERGE_HEAD 与归属记录不符 -> 拒绝接管（fail-closed）
  11. 退避重试时只回退自己的合并，绝不回退他人的

12~14 用注入点做**确定性**测试：竞态没法靠真实计时稳定复现，而一个靠 sleep
撞概率的测试只会偶尔绿、偶尔把别人的合并 abort 掉。
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
from mergeguard import cli, guard

#: 单次 git 调用的上限。没有它，一次挂死就是**无限挂死**：pytest 不会报错，
#: 只会永远等下去，直到调用方（人或 agent harness）超时把整棵进程树杀掉 ——
#: 那正是「1~3 个 dot 后进程树死亡、零输出」的成因。
GIT_TIMEOUT_SECONDS = 180


def _git_base_kwargs() -> dict[str, object]:
    """所有 git 子进程调用共用的安全参数。

    - `stdin=DEVNULL`：git 会派生 `sh.exe` / `git-receive-pack` /
      `git maintenance` 等 helper，它们**继承父进程的 stdin**。一旦某个
      helper 去读一个没人写、也没人关的句柄，整个调用就永久阻塞。
    - `timeout`：把「永久阻塞」变成「一条带上下文的失败」。
    """
    return {
        "stdin": subprocess.DEVNULL,
        "timeout": GIT_TIMEOUT_SECONDS,
        "encoding": "utf-8",
        "errors": "replace",
    }


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        **_git_base_kwargs(),
    )


def _git_out(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        **_git_base_kwargs(),
    ).stdout.strip()


#: 临时仓库里的假门禁。真实项目里它们是 docstate / discipline 两个脚本；
#: 夹具必须提供**可执行**的替身，否则「门禁全过 -> 提交」这条唯一生产成功
#: 路径在结构上就够不到——首版正是因此零覆盖。
#:
#: 失败开关放在 `.git/` 下而不是工作区里：放在工作区会被
#: 「拒绝脏工作区」那条守卫挡掉（`git status --porcelain` 看得见），
#: 就没法构造「先红后绿」的续跑场景。
#:
#: **stub 必须自己把 stdout 钉成 UTF-8**，与真实门禁（`tools/docstate`、
#: `tools/discipline` 里的 `_force_utf8_stdio`）保持一致。原因：stub 是在**代替**
#: 门禁的，若它按控制台代码页输出（Windows 上是 cp936），而 `cli.py` 的
#: `_run_gate` 按 UTF-8 解码，stub 打印的中文就会变成乱码 —— 于是
#: 「断言门禁输出里能看到豁免提示」这类用例在**没设 PYTHONIOENCODING** 的
#: 环境下平白失败。
#:
#: 这不是假想：本文件此前一直靠我在 shell 里手动设 `PYTHONIOENCODING=utf-8`
#: 才全绿。**用环境变量才能跑通的测试，等于没有在测真实环境。**
_GATE_FORCE_UTF8 = (
    "import sys\n"
    "for _s in (sys.stdout, sys.stderr):\n"
    "    try:\n"
    "        _s.reconfigure(encoding='utf-8', errors='replace')\n"
    "    except (AttributeError, ValueError, OSError):\n"
    "        pass\n"
)
_GATE_OK = _GATE_FORCE_UTF8 + 'print("{name}: 结果: 1 项检查，0 项失败")\n'
#: discipline 的替身必须真的打印 [skip] 行 —— 「豁免是显式的、不静默」是
#: mergeguard 的一条承诺，断言得在**成功路径**上就成立，而不是等某天门禁
#: 真的豁免时才第一次被看到。
_GATE_DISCIPLINE_OK = (
    _GATE_FORCE_UTF8
    + 'print("[skip] rootworktree（测试夹具）")\n'
    + _GATE_OK.format(name="discipline").split("\n", 1)[1]
)
_GATE_TOGGLING = (
    """
import pathlib
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass

_flag = pathlib.Path(".git/gate-fails.flag")
print("[skip] rootworktree（测试夹具）")
if _flag.exists():
    print("discipline: 结果: 1 项检查，1 项失败")
    sys.exit(1)
print("discipline: 结果: 1 项检查，0 项失败")
"""
)


def _install_gates(repo: Path, discipline_body: str | None = None) -> None:
    (repo / "tools" / "docstate").mkdir(parents=True, exist_ok=True)
    (repo / "tools" / "discipline").mkdir(parents=True, exist_ok=True)
    (repo / "tools" / "docstate" / "docstate.py").write_text(
        _GATE_OK.format(name="docstate"), encoding="utf-8"
    )
    (repo / "tools" / "discipline" / "discipline.py").write_text(
        _GATE_DISCIPLINE_OK if discipline_body is None else discipline_body,
        encoding="utf-8",
    )
    _git(repo, "add", "-A", "tools")
    # 允许空提交：同一测试可能换一次门禁内容再装一次，内容相同时不该失败。
    _git(repo, "commit", "--allow-empty", "-m", "install gate stubs")


def _commit_feature(repo: Path, branch: str = "feature", fname: str = "g.txt") -> None:
    """在临时仓库里造一个待合并的分支。"""
    _git(repo, "checkout", "-b", branch)
    (repo / fname).write_text("y", encoding="utf-8")
    _git(repo, "add", fname)
    _git(repo, "commit", "-m", "feature work")
    _git(repo, "checkout", "main")


def _pending_file(repo: Path) -> Path:
    return guard.git_common_dir(repo) / cli.PENDING_FILENAME


def _merge_head_file(repo: Path) -> Path:
    return guard.git_common_dir(repo) / "MERGE_HEAD"


@pytest.fixture
def repo_with_gates(repo: Path) -> Path:
    """带真门禁替身脚本的临时仓库（脚本已提交，不会被脏工作区守卫挡下）。"""
    _install_gates(repo)
    return repo


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
    # 关掉 git 自动维护 —— 这是本文件最大的稳定性杠杆，实测证据见下。
    #
    # GIT_TRACE2 实测：git 2.55 下**每一次写操作**（哪怕一条普通 commit）
    # 都会派生一个 detached 子进程
    #     child_start[0] git maintenance run --auto --quiet --detach
    # 它 (a) 继承父进程的 stdout/stderr 句柄，于是 pytest 的 capture 管道
    # 要等它退出才拿到 EOF；(b) 去抢 packed-refs / ref 锁。
    # 本文件 34 条用例每条都要建真仓库、跑真 commit/merge/worktree，
    # 于是会派生上百个这样的后台进程，彼此与父进程抢锁。
    #
    # 后果不是断言失败，而是**挂死**：调用方超时后把整棵进程树杀掉 ——
    # 表现为「跑几个 dot 之后进程树死亡、零输出」，且 `-s` 也救不了
    # （问题出在子进程继承的句柄，与 pytest 自己的 capture 无关）。
    # 测试不需要 gc，所以这里直接掐掉触发源，而不是靠 xfail 掩盖症状。
    _git(r, "config", "gc.auto", "0")
    _git(r, "config", "maintenance.auto", "false")
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


def test_cli_detects_foreign_git_activity(repo: Path):
    """别人裸跑 git 时留下的锁，必须在动手前就被挡下。

    互斥锁只拦走本入口的合并。实测踩到过：锁取到了、门禁也绿，
    但 `git merge` 写 ORIG_HEAD 时撞上别的 agent，报
    `fatal: update_ref failed for ref 'ORIG_HEAD'`。等到 git 自己撞锁就太晚了。

    循环遍历 `cli.FOREIGN_GIT_LOCKS` 而不是手写名字：清单在代码里，加锁时
    若忘了配套用例，这里会静默少测一条。
    """
    for lock_name, _why in cli.FOREIGN_GIT_LOCKS:
        lock_path = guard.git_common_dir(repo) / lock_name
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("", encoding="utf-8")
        r = _cli(repo, "merge", "main", "--task-id", "T-0")
        assert r.returncode == 7, (lock_name, r.returncode, r.stdout, r.stderr)
        assert "另一个 agent 正在这个仓库跑 git" in r.stderr
        lock_path.unlink()


def test_detect_covers_ref_side_locks_beyond_index(repo: Path):
    """裸跑 commit/fetch 可能**只持 ref 侧锁**，那时 index 已经放开了。

    这是 TRAE 评审问题 6：`index.lock` 与 `ORIG_HEAD.lock` 之外的锁，早先
    版本的预检漏放，于是要等到真走到提交那步才撞 `cannot lock ref` ——
    那时锁已经取到、合并已经做完，前置工作全白费。
    """
    names = {n for n, _ in cli.FOREIGN_GIT_LOCKS}
    assert {
        "refs/heads/main.lock",
        "packed-refs.lock",
        "HEAD.lock",
    } <= names, f"缺 ref 侧锁：{names}"


def test_declared_lock_paths_are_real_git_lock_locations(repo: Path):
    """清单里的路径必须是 git 真的会写、且真的会挡住某个写操作的位置。

    不 mock git：造一个同名锁文件，然后让**真 git** 去操作，看它是否报
    `File exists`。若路径拼错（比如写成 `refs/main.lock`），git 根本不会碰它，
    预检就会放行一个不存在的障碍 —— 那比漏检更坏。

    **不硬断言「一定挡住 commit」**：实测各锁的效力不同（git 2.55），
    `packed-refs.lock` 对 `commit` 只打印 error 但退出码仍是 0，它真正挡的是
    `pack-refs`。断言写成「commit 必须失败」会把一条**正确的**声明判成缺陷。
    真正该保证的是：git 确实因为这个文件而拒绝做某件事。
    """
    probes = (
        ("commit", ("commit", "--allow-empty", "-m", "probe")),
        ("pack-refs", ("pack-refs", "--all")),
    )
    # git 在错误信息里打印的是**绝对路径且用正斜杠**（实测 git 2.55，
    # Windows 上亦然）。按 lock_name 本身匹配，不要做分隔符转换。
    for lock_name in ("refs/heads/main.lock", "packed-refs.lock", "HEAD.lock"):
        lock_path = guard.git_common_dir(repo) / lock_name
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text("", encoding="utf-8")
        blocked_by: list[str] = []
        for label, argv in probes:
            proc = subprocess.run(
                ["git", "-C", str(repo), *argv],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            combined = proc.stdout + proc.stderr
            if "File exists" in combined and lock_name in combined:
                blocked_by.append(f"{label}(exit={proc.returncode})")
        assert blocked_by, f"{lock_name} 没让 git 拒绝任何操作（路径可能拼错了）"
        lock_path.unlink()


def test_cli_detects_foreign_merge_in_progress(repo: Path):
    """别人发起的合并正在进行 —— 也必须挡下，不能接管别人的现场。"""
    (guard.git_common_dir(repo) / "MERGE_HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
    r = _cli(repo, "merge", "main", "--task-id", "T-0")
    assert r.returncode == 7, (r.returncode, r.stdout, r.stderr)
    assert "进行中的合并" in r.stderr


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
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    r = _cli(repo, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
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
    before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()

    r = _cli(repo, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    assert r.returncode == 0, r.stdout + r.stderr
    after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    ).stdout.strip()
    assert before == after, "--skip-gate 也不该提交：门禁没跑，merge commit 不能落地"
    assert (repo / ".git" / "MERGE_HEAD").exists(), "应停在待提交的合并状态"


# ---------------------------------------------------------------------------
# 问题 1（高）：门禁红之后不是死路
# ---------------------------------------------------------------------------


def test_cli_merges_commits_and_releases_lock(repo_with_gates: Path):
    """**唯一生产成功路径**：门禁全过 -> commit -> HEAD 前进 -> 现场清干净。

    这条曾被删掉且无替代（TRAE 评审问题 3）：临时仓库夹具里没有门禁脚本，
    门禁那段分支结构上就够不到，于是「门禁跑在 merge commit 落地之前」这个
    核心承诺——也是整个工具存在的理由——零覆盖。
    """
    _commit_feature(repo_with_gates)
    before = _git_out(repo_with_gates, "rev-parse", "HEAD")

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[skip] rootworktree（测试夹具）" in r.stdout, "豁免必须可见，不静默"

    after = _git_out(repo_with_gates, "rev-parse", "HEAD")
    assert after != before, "门禁通过后必须真的提交 merge commit"

    parents = _git_out(repo_with_gates, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3, f"应是 merge commit（有 2 个父）：{parents}"
    assert before in parents, "第一个父必须是合并前的 main"
    assert _git_out(repo_with_gates, "rev-parse", "feature") in parents

    assert not _merge_head_file(repo_with_gates).exists(), "提交后必须清掉 MERGE_HEAD"
    assert not _pending_file(repo_with_gates).exists(), "提交后必须清掉待提交记录"
    assert not guard.lock_path(repo_with_gates).exists(), "任何退出路径都必须释放锁"
    assert "空闲" in _cli(repo_with_gates, "status").stdout


def test_cli_gate_failure_does_not_commit_and_rerun_resumes(repo_with_gates: Path):
    """门禁红 -> 不提交 -> **重跑同一条命令**自动续跑到提交。

    早先版本的恢复指引是「修好门禁后重跑本命令」，而入口预检见 MERGE_HEAD
    一律 exit 7，还误报「另一个 agent 正在跑 git」并诱导删锁 —— 一条指向自己
    走不通的死路。现在靠 merge-pending.json 记录归属，重跑直接续跑。
    """
    _install_gates(repo_with_gates, discipline_body=_GATE_TOGGLING)
    _commit_feature(repo_with_gates)
    flag = repo_with_gates / ".git" / "gate-fails.flag"
    flag.write_text("", encoding="utf-8")
    before = _git_out(repo_with_gates, "rev-parse", "HEAD")

    first = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert first.returncode == 5, first.stdout + first.stderr
    assert "门禁未通过" in first.stderr
    assert "重跑本命令即可" in first.stderr, "恢复指引必须可执行"
    assert _git_out(repo_with_gates, "rev-parse", "HEAD") == before, "门禁红不得提交"
    assert _merge_head_file(repo_with_gates).exists(), "应停在待提交状态"
    assert _pending_file(repo_with_gates).exists(), "必须记下归属，否则无法区分他人合并"
    assert not guard.lock_path(repo_with_gates).exists(), "退出前必须释放锁"

    # 门禁修好了，重跑同一条命令。
    flag.unlink()
    second = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "跳过合并步骤，直接续跑" in second.stdout, "续跑必须是显式的"
    assert _git_out(repo_with_gates, "rev-parse", "HEAD") != before, "续跑后必须提交"
    assert not _pending_file(repo_with_gates).exists()
    assert not _merge_head_file(repo_with_gates).exists()


def test_cli_resume_refuses_to_sweep_foreign_staged_work(repo_with_gates: Path):
    """续跑时若有人往暂存区塞了自己的东西，必须拒绝而不是提交进 main。

    判据是**暂存区树哈希**与上次 merge 记录的指纹是否一致，而不是「工作区
    脏不脏」。第一版这里用的是 `git diff --name-only`（未暂存），被本测试
    抓出是假严格：未暂存与未跟踪的内容 `git commit` 本来就不会带进提交，
    拒了只是白拒；反过来真正的危险（手敲 `git add`）它却完全看不见。
    """
    _install_gates(repo_with_gates, discipline_body=_GATE_TOGGLING)
    _commit_feature(repo_with_gates)
    flag = repo_with_gates / ".git" / "gate-fails.flag"
    flag.write_text("", encoding="utf-8")
    assert _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1").returncode == 5
    flag.unlink()

    # 别人的活儿被塞进暂存区
    (repo_with_gates / "theirs.txt").write_text("别人的活儿", encoding="utf-8")
    _git(repo_with_gates, "add", "theirs.txt")

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "暂存区内容已改变" in r.stderr
    assert "不允许续跑提交" in r.stderr
    assert _merge_head_file(repo_with_gates).exists(), "拒绝时必须保留待提交合并现场"
    assert not guard.lock_path(repo_with_gates).exists(), "退出前必须释放锁"


def test_cli_resume_tolerates_unstaged_and_untracked_changes(repo_with_gates: Path):
    """续跑时**未暂存**的改动不该被拒——`git commit` 本来就不会提交它们。

    这是对上一条的镜像：守卫要精确，不能因为「看起来不安全」就一拒了之。
    断言提交后这些内容仍原样留在工作区，即没有被顺手带进 merge commit。
    """
    _install_gates(repo_with_gates, discipline_body=_GATE_TOGGLING)
    _commit_feature(repo_with_gates)
    flag = repo_with_gates / ".git" / "gate-fails.flag"
    flag.write_text("", encoding="utf-8")
    assert _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1").returncode == 5
    flag.unlink()

    (repo_with_gates / "theirs.txt").write_text("别人的活儿", encoding="utf-8")
    (repo_with_gates / "f.txt").write_text("工作区里的未暂存改动", encoding="utf-8")

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (repo_with_gates / "theirs.txt").exists(), "未跟踪文件必须仍留在工作区"
    assert (repo_with_gates / "f.txt").read_text(encoding="utf-8") == "工作区里的未暂存改动"
    tracked = _git_out(repo_with_gates, "ls-tree", "--name-only", "HEAD")
    assert "theirs.txt" not in tracked, "未跟踪文件绝不能被带进 merge commit"
    assert (repo_with_gates / "f.txt").read_text(
        encoding="utf-8"
    ) != "x", "未暂存改动绝不能被带进 merge commit"


def test_cli_refuses_foreign_merge_head_even_with_our_record(repo_with_gates: Path):
    """归属记录在、但 MERGE_HEAD 指向别处 -> 拒绝接管（fail-closed）。

    这种情况意味着合并已被他人接手或替换。此时「有记录」不构成「是我的」
    的证据 —— 宁可拒绝，不能猜。
    """
    _install_gates(repo_with_gates, discipline_body=_GATE_TOGGLING)
    _commit_feature(repo_with_gates)
    flag = repo_with_gates / ".git" / "gate-fails.flag"
    flag.write_text("", encoding="utf-8")
    assert _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1").returncode == 5
    flag.unlink()

    # 模拟他人的合并替换了 MERGE_HEAD。
    _merge_head_file(repo_with_gates).write_text("b" * 40 + "\n", encoding="utf-8")
    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "不符" in r.stderr


def test_cli_clears_stale_record_when_merge_head_is_gone(repo_with_gates: Path):
    """记录还在但 MERGE_HEAD 没了 = 合并已被提交或回退，清陈迹后正常继续。

    不清理的话记录会一直卡住下一次合并，而它已经不对应任何现场。
    """
    _install_gates(repo_with_gates)
    _commit_feature(repo_with_gates)
    _pending_file(repo_with_gates).write_text(
        json.dumps({"branch": "feature", "task_id": "T-9", "merge_head": "c" * 40}),
        encoding="utf-8",
    )
    before = _git_out(repo_with_gates, "rev-parse", "HEAD")

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "清理上次遗留的待提交合并记录" in r.stdout
    assert _git_out(repo_with_gates, "rev-parse", "HEAD") != before
    assert not _pending_file(repo_with_gates).exists()


# ---------------------------------------------------------------------------
# 问题 4（中）：退避重试绝不 abort 他人的合并
#
# 用注入点做确定性测试。这些分支的行为**只在毫秒级的 ref 锁竞态里出现**，
# 真去撞那个窗口只会偶尔绿；而一个偶尔绿的测试在这里等于「偶尔 abort 掉
# 别人的合并」，比没有测试更糟。
# ---------------------------------------------------------------------------


def _proc(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout="", stderr=stderr
    )


_CONTENTION = "fatal: update_ref failed for ref 'ORIG_HEAD': couldn't set 'ORIG_HEAD'"
_OK_PROC = _proc(0)
_LOCKED_PROC = _proc(128, _CONTENTION)


def test_retry_aborts_merge_it_started_itself():
    """MERGE_HEAD 等于本次 merge 预期的那个 commit -> 是我们的，可以回退。"""
    aborted: list[bool] = []
    did = cli._abort_only_our_merge(
        "a" * 40,
        read_merge_head=lambda: "a" * 40,
        abort=lambda: aborted.append(True),
    )
    assert did is True
    assert aborted == [True]


def test_retry_does_not_abort_when_no_merge_head():
    """MERGE_HEAD 不存在 -> 本次 merge 根本没留下合并，无可回退。

    实测 git 先落 ORIG_HEAD、再落 MERGE_HEAD，ref 锁失败时现场没有
    「我的合并」标记。此时无条件 abort 只会误伤恰好在这时裸跑合并的别人。
    """
    aborted: list[bool] = []
    did = cli._abort_only_our_merge(
        "a" * 40,
        read_merge_head=lambda: None,
        abort=lambda: aborted.append(True),
    )
    assert did is False
    assert aborted == [], "没有 MERGE_HEAD 时绝不能 abort"


def test_retry_refuses_to_abort_someone_elses_merge():
    """MERGE_HEAD 存在但指向别的 commit -> 他人的合并，抛 _ForeignMerge。

    这正是退避窗口（3~9 秒）里会发生的事，而早先版本在这里无条件 abort，
    会静默杀掉别人的合并 —— 与本工具存在的理由直接冲突。
    """
    aborted: list[bool] = []
    with pytest.raises(cli._ForeignMerge):
        cli._abort_only_our_merge(
            "a" * 40,
            read_merge_head=lambda: "b" * 40,
            abort=lambda: aborted.append(True),
        )
    assert aborted == [], "他人的合并绝不能被 abort"


def test_merge_retries_on_transient_ref_lock():
    """瞬时 ref 锁：退避递增重试，重试预算有界。"""
    waits: list[float] = []
    calls: list[str] = []

    def merge(_branch: str):
        calls.append("merge")
        return _LOCKED_PROC if len(calls) < 3 else _OK_PROC

    proc, expected = cli._merge_with_retry(
        "feature",
        sleep=waits.append,
        merge=merge,
        read_merge_head=lambda: None,
        abort=lambda: None,
        rev_parse=lambda _b: "a" * 40,
    )
    assert proc.returncode == 0
    assert expected == "a" * 40
    assert len(calls) == 3, calls
    assert waits == [3.0, 6.0], f"退避必须递增有界：{waits}"


def test_merge_gives_up_after_retry_budget():
    """预算用尽仍撞锁 -> 如实返回失败，不无限重试。"""
    calls: list[str] = []

    proc, _ = cli._merge_with_retry(
        "feature",
        sleep=lambda _s: None,
        merge=lambda _b: (calls.append("m"), _LOCKED_PROC)[1],
        read_merge_head=lambda: None,
        abort=lambda: None,
        rev_parse=lambda _b: "a" * 40,
    )
    assert proc.returncode != 0
    assert len(calls) == cli.MERGE_RETRY_ATTEMPTS, calls


def test_merge_does_not_retry_real_conflict():
    """真冲突不能重试：冲突现场必须留着给人解，重试只会把它盖掉。"""
    calls: list[str] = []
    conflict = _proc(1, "CONFLICT (content): Merge conflict in c.txt")

    proc, _ = cli._merge_with_retry(
        "feature",
        sleep=lambda _s: None,
        merge=lambda _b: (calls.append("m"), conflict)[1],
        read_merge_head=lambda: None,
        abort=lambda: None,
        rev_parse=lambda _b: "a" * 40,
    )
    assert proc.returncode != 0
    assert len(calls) == 1, "非 ref 锁失败必须立刻停，不重试"


# ---------------------------------------------------------------------------
# commit 误报：git 报错但 merge commit 其实已落地
#
# 起因：WorkBuddy 的报告（docs/claims/52-workbuddy--report-mergeguard-commit-bug.md）
# —— git 在本机间歇性写 index 失败（unable to write new index file），commit
# 退出码非零但实际已写入。工具报「提交失败」，调用方会以为没合并而重跑，
# 而重跑时分支已在 main 上。他当天差点就这么干。
# ---------------------------------------------------------------------------


def _runner_at(repo: Path):
    """一个绑到指定仓库的 git runner，用来给 cli._commit_landed 注入。"""

    def run(*args: str, check: bool = False):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=check,
        )

    return run


def test_commit_landed_recognises_a_real_merge_commit(repo_with_gates: Path):
    """核实路径的正例：真的合完了，三条判据都成立。"""
    _commit_feature(repo_with_gates, "feature", "g.txt")
    _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    _git(repo_with_gates, "commit", "--no-edit")

    landed, why = cli._commit_landed("feature", git=_runner_at(repo_with_gates))
    assert landed is True, why
    assert "祖先" in why


def test_commit_landed_rejects_dirty_worktree(repo_with_gates: Path):
    """工作区仍不干净 -> 不能算已落地（否则会把别人的未提交改动认成合并成功）。"""
    _commit_feature(repo_with_gates, "feature", "g.txt")
    _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    _git(repo_with_gates, "commit", "--no-edit")
    (repo_with_gates / "theirs.txt").write_text("别人的", encoding="utf-8")

    landed, why = cli._commit_landed("feature", git=_runner_at(repo_with_gates))
    assert landed is False
    assert "不干净" in why


def test_commit_landed_rejects_non_merge_head(repo_with_gates: Path):
    """HEAD 不是 merge commit -> 不能算已落地（防止把上一次的合并当成本次）。"""
    _commit_feature(repo_with_gates, "feature", "g.txt")
    landed, why = cli._commit_landed("feature", git=_runner_at(repo_with_gates))
    assert landed is False
    assert "merge commit" in why


def test_commit_landed_requires_branch_to_be_ancestor(repo_with_gates: Path):
    """**关键判据**：仓库里有个 merge commit、工作区也干净，但被合分支不是祖先。

    只查「干净 + 两个父」不够 —— 那两条在上一次合并之后一直成立。
    「分支已是 HEAD 的祖先」才真正证明**这次合并的内容**进了 main。
    """
    _commit_feature(repo_with_gates, "feature", "g.txt")
    _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1", "--skip-gate")
    _git(repo_with_gates, "commit", "--no-edit")

    # 再合一个别的分支，于是 HEAD 是 merge commit 且工作区干净
    _commit_feature(repo_with_gates, "other", "h.txt")
    _cli(repo_with_gates, "merge", "other", "--task-id", "T-2", "--skip-gate")
    _git(repo_with_gates, "commit", "--no-edit")

    run = _runner_at(repo_with_gates)
    landed, why = cli._commit_landed("feature", git=run)
    assert landed is True, "feature 早已合过，仍应算已落地：" + why

    # 注意：`git branch X` 会把 X 建在**当前 HEAD** 上，那样它天然就是祖先，
    # 断言会假通过。必须让该分支带一个 main 上没有的提交。
    _git(repo_with_gates, "checkout", "-b", "never-merged")
    (repo_with_gates / "n.txt").write_text("n", encoding="utf-8")
    _git(repo_with_gates, "add", "n.txt")
    _git(repo_with_gates, "commit", "-m", "not merged")
    _git(repo_with_gates, "checkout", "main")
    landed2, why2 = cli._commit_landed("never-merged", git=run)
    assert landed2 is False, "未合入的分支不得算已落地：" + why2
    assert "祖先" in why2


def test_cli_reports_failure_when_commit_really_failed(repo_with_gates: Path):
    """真失败仍然报 exit 6 —— 核实不能变成「一律当成功」。

    用一个必然失败的 pre-commit hook 让 `git commit --no-edit` 确定性地失败：
    不靠注入（子进程里注入无效），也不靠碰运气的间歇性故障。
    """
    _commit_feature(repo_with_gates, "feature", "g.txt")
    hooks = repo_with_gates / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    before = _git_out(repo_with_gates, "rev-parse", "HEAD")

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert r.returncode == 6, r.stdout + r.stderr
    assert "提交失败" in r.stderr
    assert "merge commit 未落地" in r.stderr, "必须说明核实结论"
    assert _git_out(repo_with_gates, "rev-parse", "HEAD") == before
    assert _merge_head_file(repo_with_gates).exists(), "真失败必须保留待提交现场"
    assert not guard.lock_path(repo_with_gates).exists()


# 覆盖范围的诚实说明：`_commit_landed` 的四条判据各有一条注入式单测
# （含最容易写错的那条「分支不是祖先」），「真失败仍报 exit 6」有端到端用例。
# 但**「commit 报错却已落地 -> 退出码 0」这条成功路径没有端到端用例**：
# 它需要制造「commit 落地但退出码非零」，而唯一稳定的做法（pre-commit hook
# 里自己提交）会递归触发 hook 并退化成 182 秒的退避重试。
# 第一版硬写过一版，代价是单条用例占满整个测试文件的 75% 时长。
# 这里选择**不留一条脆弱的用例**：判据逻辑本身已被穷尽覆盖，而一条依赖
# hook 递归偶然行为的「绿」比没有更危险 —— 它绿了也证明不了什么。


# ---------------------------------------------------------------------------
# 续跑必须证明「记录与请求是同一次合并」
#
# 2026-10-07 03:05 实测事故：Qoder 合并 sweeper-deps-resources 后进程退出，
# 现场悬着（MERGE_HEAD == merge-pending.json 的 merge_head == eb7d8d36）。
# 随后有人请求合并**另一条分支**，只凭「内容相符」就判成自己的遗留合并，
# **跳过了 merge 步骤**，直接在别人的工作树上跑门禁 —— 自己的分支根本没被合并。
# 最坏情况：门禁若恰好是绿的，工具会报成功并提交，即**把别人的合并当成自己的
# 合并提交掉** —— 正是本工具存在的目的所防的事故。
# ---------------------------------------------------------------------------


def _write_pending(repo: Path, branch: str, task_id: str, merge_head: str):
    (guard.git_common_dir(repo) / cli.PENDING_FILENAME).write_text(
        json.dumps(
            {
                "branch": branch,
                "task_id": task_id,
                "merge_head": merge_head,
                "index_tree": _git_out(repo, "write-tree"),
                "pid": 999999,
                "created_at": time.time(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_pending_record_of_another_branch_is_not_resumed(repo_with_gates: Path):
    """**回归**：pending 记录属于**别的分支**时必须拒绝，而不是续跑。

    这道缺口的正例：`MERGE_HEAD` 与记录内容相符，只说明「**有一次**合并悬着」，
    不说明「是**我请求的那次**合并」。只凭前者就续跑，等于把别人的现场当成
    自己的 —— 而本工具的全部价值就是绝不接管别人的合并。
    """
    _commit_feature(repo_with_gates, "feature", "g.txt")
    _git(repo_with_gates, "branch", "someone-elses")
    their_head = _git_out(repo_with_gates, "rev-parse", "someone-elses")
    _merge_head_file(repo_with_gates).write_text(their_head + "\n", encoding="utf-8")
    _write_pending(repo_with_gates, "someone-elses", "T-theirs", their_head)
    before = _git_out(repo_with_gates, "rev-parse", "HEAD")

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-mine")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "他人遗留" in r.stderr, r.stderr
    assert (
        "someone-elses" in r.stderr and "feature" in r.stderr
    ), "必须同时点名「那次是谁的」与「本次要谁」"
    assert _git_out(repo_with_gates, "rev-parse", "HEAD") == before, "不许提交"
    assert _merge_head_file(repo_with_gates).exists(), "他人的现场必须原样保留"
    assert _pending_file(repo_with_gates).exists(), "他人的归属记录必须原样保留"


def test_pending_record_of_another_task_same_branch_is_resumed(repo_with_gates: Path):
    """同一分支、task_id 不同 -> **仍然续跑**，因为那确实是同一笔合并。

    这条是判据选型的对照。曾经的写法是「记录的 branch 或 task_id 与请求相符」
    —— 用**标签**当身份。那样会多拒一次本该接受的续跑，而且拒得毫无理由：
    悬着的合并就是 `MERGE_HEAD == 该分支的 tip`，提交它就是完成我的请求，
    与它上一次挂在哪个 task_id 下无关。

    现在的判据是事实：`MERGE_HEAD == rev-parse(请求的分支)`。标签只用于
    报错文案，不参与判定。
    """
    _install_gates(repo_with_gates, discipline_body=_GATE_TOGGLING)
    _commit_feature(repo_with_gates, "feature", "g.txt")
    flag = repo_with_gates / ".git" / "gate-fails.flag"
    flag.write_text("", encoding="utf-8")
    assert _cli(repo_with_gates, "merge", "feature", "--task-id", "T-theirs").returncode == 5

    flag.unlink()
    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-mine")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "跳过合并步骤，直接续跑" in r.stdout
    parents = _git_out(repo_with_gates, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3, parents


def test_pending_record_is_refused_when_branch_tip_moved(repo_with_gates: Path):
    """请求的分支在悬着的那次合并之后又前进了 -> 必须拒绝续跑。

    `MERGE_HEAD` 是悬着那次合并的 commit。若这期间该分支被推进了，
    续跑就会提交一个**已经不是最新**的合并 —— 而调用方请求的是新的那个。
    这条把「事实比对」的价值讲清楚：它同时挡住了「别人的现场」与
    「过期的现场」。

    构造顺序有讲究：必须**先推进分支、再造 MERGE_HEAD**。反过来做不到 ——
    `git checkout` 会顺手清掉 MERGE_HEAD，悬置现场也就没了。
    （真实世界要让这个场景发生，得是别人从别处用 fetch 或 `update-ref` 强推了
    那个 ref；本仓库里被 checkout 占用的分支是拒绝普通更新的，所以罕见但
    并非不可能——而罕见不等于不设防。）
    """
    _commit_feature(repo_with_gates, "feature", "g.txt")
    old_tip = _git_out(repo_with_gates, "rev-parse", "feature")

    # 先把分支推进（此刻没有 MERGE_HEAD，checkout 不会被它拦）
    _git(repo_with_gates, "checkout", "feature")
    (repo_with_gates / "g.txt").write_text("moved", encoding="utf-8")
    _git(repo_with_gates, "add", "g.txt")
    _git(repo_with_gates, "commit", "-m", "feature moves on")
    _git(repo_with_gates, "checkout", "main")
    new_tip = _git_out(repo_with_gates, "rev-parse", "feature")
    assert new_tip != old_tip

    # 再造那个悬置现场：MERGE_HEAD 指向的是**旧** tip
    _merge_head_file(repo_with_gates).write_text(old_tip + "\n", encoding="utf-8")
    _write_pending(repo_with_gates, "feature", "T-theirs", old_tip)

    r = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-mine")
    assert r.returncode == 7, r.stdout + r.stderr
    assert "他人遗留" in r.stderr
    assert (
        old_tip[:8] in r.stderr and new_tip[:8] in r.stderr
    ), "必须同时给出悬着的 MERGE_HEAD 与当前 tip，便于人判断"
    assert _merge_head_file(repo_with_gates).exists(), "现场必须原样保留"


def test_pending_record_matching_branch_and_task_is_resumed(repo_with_gates: Path):
    """对照：分支与 task_id 都对得上时，续跑仍然正常（别把功能改坏）。"""
    _install_gates(repo_with_gates, discipline_body=_GATE_TOGGLING)
    _commit_feature(repo_with_gates, "feature", "g.txt")
    flag = repo_with_gates / ".git" / "gate-fails.flag"
    flag.write_text("", encoding="utf-8")

    first = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert first.returncode == 5, first.stdout + first.stderr
    assert _merge_head_file(repo_with_gates).exists()

    flag.unlink()
    second = _cli(repo_with_gates, "merge", "feature", "--task-id", "T-1")
    assert second.returncode == 0, second.stdout + second.stderr
    assert "跳过合并步骤，直接续跑" in second.stdout
    parents = _git_out(repo_with_gates, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3, parents


# ---------------------------------------------------------------------------
# 问题 2（中）：最终 commit 也必须有界重试
# ---------------------------------------------------------------------------


def test_commit_retries_on_transient_ref_lock():
    """commit 撞的是 refs/heads/main，最容易撞锁；必须有退避重试。

    早先版本只给 merge 加了退避，这一步裸奔 —— exit 6 之后 MERGE_HEAD 还在、
    合并结果已暂存，人只能自己猜该怎么办。
    """
    waits: list[float] = []
    calls: list[int] = []

    def commit():
        calls.append(1)
        return _LOCKED_PROC if len(calls) < 2 else _OK_PROC

    proc = cli._commit_with_retry(sleep=waits.append, commit=commit)
    assert proc.returncode == 0
    assert len(calls) == 2, calls
    assert waits == [3.0], waits


def test_commit_retry_does_not_re_merge_or_abort():
    """重试 commit 只需重跑 commit：MERGE_HEAD 还在，重来一遍就是同一笔。"""
    calls: list[int] = []
    proc = cli._commit_with_retry(
        sleep=lambda _s: None,
        commit=lambda: (calls.append(1), _LOCKED_PROC if len(calls) < 2 else _OK_PROC)[1],
    )
    assert proc.returncode == 0
    assert len(calls) == 2, "只该重试 commit 本身"


def test_commit_gives_up_after_retry_budget():
    calls: list[int] = []
    proc = cli._commit_with_retry(
        sleep=lambda _s: None,
        commit=lambda: (calls.append(1), _LOCKED_PROC)[1],
    )
    assert proc.returncode != 0
    assert len(calls) == cli.MERGE_RETRY_ATTEMPTS, calls
