"""受控合并入口：`merge <branch>`。

与裸 `git merge` 的区别，也即本工具存在的理由：

1. **先拿互斥锁**，拿不到就明确失败并打印持有者——裸 git 只会撞 ref 锁 fatal，
   或者更糟：被别人的并发操作静默 abort。
2. **脏工作区直接拒绝**。裸 `git merge` 会带着别人的未提交改动继续走，
   把它们卷进 merge commit。本项目第 4 次事故就是这个形状。
3. **冲突不自动 abort**。裸 git 也不会自动 abort，但人在压力下会敲
   `git merge --abort`，而另一个人可能正在同一个工作区解冲突。这里把
   「不要 abort」写进输出。
4. **门禁红则不提交 merge commit**。merge commit 一旦落地就进了 main，
   门禁再红也只能靠 revert 补救。

用法::

    python tools/mergeguard/cli.py status
    python tools/mergeguard/cli.py merge agent/local/x/T-1/a1 --task-id T-1

门禁红之后怎么恢复
------------------

`--no-commit` 意味着门禁红的时候仓库停在「已合并、未提交」的状态，
`MERGE_HEAD` 还在。**这正是问题 1 的死路**：早先版本在这里提示
「修好门禁后重跑本命令」，而入口预检见 `MERGE_HEAD` 一律 exit 7，
于是恢复指引指向一条自己走不通的路。

现在 merge 进入「待提交」状态时会写一份 `merge-pending.json` 记录**本次
merge 预期的 MERGE_HEAD**。入口据此区分两种 MERGE_HEAD：

- **与记录相符** → 是本工具自己留下的待提交合并，直接跳过合并步骤，
  续跑门禁并提交。门禁红不再需要人肉介入。
- **与记录不符 / 无记录** → 别人的合并，一律 exit 7，绝不接管。

判据是 MERGE_HEAD 的**内容**而不是它是否存在 —— 「存在即自己」在退避
窗口里是错的，那个窗口有 3~9 秒，足够别的 agent 裸跑 `git merge` 进来。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

if __package__ in (None, ""):  # 允许 `python tools/mergeguard/cli.py` 直接跑
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.mergeguard import guard  # type: ignore[no-redef]
else:
    from . import guard


@lru_cache(maxsize=1)
def _repo_root() -> Path:
    """当前工作区所在的仓库根。

    必须取**调用时的 cwd** 所属仓库，而不是本文件所在的仓库：合并的语义是
    「把你脚下这个仓库的 main 合进去」。若取脚本路径，跨仓库调用就会静默
    合错地方——这是演练时真踩到过的。
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        return Path(proc.stdout.strip())
    # 不在 git 仓库里（例如被打包分发），退回脚本所在目录。
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _repo_root()

#: ref 锁争用的重试次数与退避基数。git 的 `maintenance run --auto` 会在写操作
#: 后自动后台运行并持有 ref 锁，本仓库提交极其频繁（多 agent 并行），撞上的
#: 概率不低。退避递增，避免和自动维护互相追着抢。
MERGE_RETRY_ATTEMPTS = 4
MERGE_RETRY_BACKOFF_SECONDS = 3

#: 门禁红之后留在 git common dir 的「待提交合并」记录。文件名固定，便于人工
#: 检查与清理；内容用来证明 MERGE_HEAD 是本工具写的。
PENDING_FILENAME = "merge-pending.json"

#: git 报 ref 锁争用时的特征串。命中即认为可以原样重试。
_REF_CONTENTION = ("update_ref failed", "cannot lock ref")


#: 单次 git 调用的上限。没有它，一次挂死就是无限挂死，而本工具正在**持有
#: 互斥锁**——锁的 stale 判定要等持有进程已死且超过 TTL，一次挂死会把整个仓库
#: 的合并卡住直到 TTL（默认 900 秒）。
GIT_TIMEOUT_SECONDS = 300


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # encoding 必须显式指定：Windows 上 text=True 默认按 GBK 解码，而 git 输出
    # 的提交信息含 UTF-8 中文（本仓库的提交信息全是中文），不指定就会抛
    # UnicodeDecodeError —— 第一次真合并就会撞上。
    #
    # stdin=DEVNULL：git 会派生 helper（sh.exe / git-receive-pack /
    # git maintenance），它们继承父进程的 stdin。某个 helper 去读一个没人写
    # 也不关的句柄就会把这次调用永久挂住。
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=GIT_TIMEOUT_SECONDS,
        check=check,
    )


def _current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _porcelain() -> list[str]:
    return [
        ln for ln in _git("status", "--porcelain").stdout.splitlines() if ln.strip()
    ]


def _index_tree() -> str:
    """当前暂存区的树对象哈希。

    续跑提交前唯一的精确判据：「暂存区里是不是**恰好**上次 merge 的结果」。
    比它更弱的信号都不够：

    - `git status --porcelain` 含未跟踪文件 —— 续跑时工作区因合并而脏是正常的，
      用它会把每一次续跑都拒掉；
    - `git diff --name-only` 只看未暂存 —— 未暂存与未跟踪的内容
      **`git commit` 本来就不会带进提交**，所以它们其实无害，拒了是假严格；
    - 而真正危险的是**别人往 index 里塞了东西**（手敲 `git add`），那会被
      `git commit --no-edit` 原样提交进 main。这只有比对树哈希能抓到。
    """
    return _git("write-tree", check=False).stdout.strip()


# --------------------------------------------------------------------------
# 待提交合并的归属判定
#
# 纯文件探测，不 spawn 子进程：这些读法都在入口热路径上。
# --------------------------------------------------------------------------


def _common() -> Path:
    return guard.git_common_dir(REPO_ROOT)


def _merge_head_path() -> Path:
    return _common() / "MERGE_HEAD"


def _read_merge_head() -> str | None:
    path = _merge_head_path()
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip() or None
    except OSError:
        return None


def _pending_path() -> Path:
    return _common() / PENDING_FILENAME


def _read_pending() -> dict | None:
    path = _pending_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_pending(branch: str, task_id: str, merge_head: str) -> None:
    """登记「待提交合并」的归属与内容指纹。

    `index_tree` 是关键：只有 MERGE_HEAD 相符还不够——别人可能在这期间往
    暂存区里塞了他自己的东西。续跑时比对树哈希，才知道要提交的内容确实
    是本次 merge 的产物。
    """
    _pending_path().write_text(
        json.dumps(
            {
                "branch": branch,
                "task_id": task_id,
                "merge_head": merge_head,
                "index_tree": _index_tree(),
                "pid": os.getpid(),
                "created_at": time.time(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _clear_pending() -> None:
    _pending_path().unlink(missing_ok=True)


# --------------------------------------------------------------------------
# 瞬时 ref 锁争用的有界退避重试
# --------------------------------------------------------------------------


class _ForeignMerge(RuntimeError):
    """退避窗口内出现了不属于本次 merge 的 MERGE_HEAD。"""


def _is_ref_contention(proc: subprocess.CompletedProcess[str]) -> bool:
    text = (proc.stderr or "") + (proc.stdout or "")
    return any(s in text for s in _REF_CONTENTION)


def _merge_once(branch: str) -> subprocess.CompletedProcess[str]:
    return _git("merge", "--no-ff", "--no-commit", "--no-edit", branch, check=False)


def _abort_merge() -> subprocess.CompletedProcess[str]:
    return _git("merge", "--abort", check=False)


def _commit_once() -> subprocess.CompletedProcess[str]:
    return _git("commit", "--no-edit", check=False)


def _rev_parse(rev: str) -> str:
    proc = _git("rev-parse", rev, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _abort_only_our_merge(
    expected_head: str,
    *,
    read_merge_head: Callable[[], str | None],
    abort: Callable[[], object],
) -> bool:
    """只有能证明 MERGE_HEAD 是本次 merge 写的才回退。返回是否真的回退了。

    早先版本在这里无条件 `git merge --abort`。退避窗口有 3~9 秒，足够别的
    agent 裸跑 `git merge` 进来 —— 那个 abort 会静默杀掉别人的合并，与本
    文件 docstring 第 3 条、以及 TASKS.md「第 4 次事故」记录所描述的形态
    完全一致。入口预检是一次性快照，关不住这个窗口。

    三种情形：

    - MERGE_HEAD == 本次 merge 预期的那个 commit → 是我们的，回退。
    - MERGE_HEAD 不存在 → 本次 merge 连标记都没落盘（实测 git 先写
      ORIG_HEAD，ref 锁失败时现场没有「我的合并」标记）。没有属于我们的
      合并可回退，此时回退只会误伤，直接重试即可。
    - MERGE_HEAD 存在但指向别的 commit → 是别人的，抛 _ForeignMerge。
    """
    current = read_merge_head()
    if current is None:
        print("  未落 MERGE_HEAD，无本次合并可回退，直接重试（不会误伤他人合并）。")
        return False
    if current != expected_head:
        raise _ForeignMerge(current)
    abort()
    print("  已回退本次 merge（MERGE_HEAD 与预期一致）。")
    return True


def _merge_with_retry(
    branch: str,
    *,
    sleep: Callable[[float], object] = time.sleep,
    merge: Callable[[str], subprocess.CompletedProcess[str]] = _merge_once,
    read_merge_head: Callable[[], str | None] = _read_merge_head,
    abort: Callable[[], object] = _abort_merge,
    rev_parse: Callable[[str], str] = _rev_parse,
) -> tuple[subprocess.CompletedProcess[str], str]:
    """合并，并对瞬时 ref 锁争用做有界退避重试。

    返回 (最后一次 merge 的结果, 本次预期的 MERGE_HEAD)。

    实测触发者不是别的 agent，而是 git 自己的后台维护：
    `git maintenance run --auto --detach` 在写操作后自动运行、持有 ref 锁，
    与随后的 merge 撞出 `update_ref failed for ref 'ORIG_HEAD'`。
    预检抓不到它（锁是瞬时的），只能在撞上之后重试。
    """
    expected = rev_parse(branch)
    proc = merge(branch)
    for attempt in range(1, MERGE_RETRY_ATTEMPTS):
        if proc.returncode == 0 or not _is_ref_contention(proc):
            break
        wait = MERGE_RETRY_BACKOFF_SECONDS * attempt
        print(
            f"  merge 撞 ref 锁争用（多半是 git 自动维护），{wait}s 后重试"
            f"（{attempt}/{MERGE_RETRY_ATTEMPTS}）…"
        )
        sleep(wait)
        _abort_only_our_merge(
            expected, read_merge_head=read_merge_head, abort=abort
        )
        proc = merge(branch)
    return proc, expected


def _commit_landed(branch: str, *, git=None) -> tuple[bool, str]:
    """核实「`git commit` 报错了，但 merge commit 其实已经落地」。

    起因是 WorkBuddy 报的 bug（`docs/claims/52-workbuddy--report-mergeguard-commit-bug.md`，
    2026-10-06 09:00）：git 在这台机器上**间歇性**写 index 失败，报
    `fatal: unable to write new index file`，于是 `git commit` 退出码非零 ——
    但 commit 实际已经写入（写 index 与落 commit 是两步，前者报错时后者
    可能已完成）。工具据此报「提交失败」，而**调用方会以为合并没成功而重跑**，
    重跑时分支已经在 main 上，第二次 merge 要么是 no-op、要么造出重复提交。
    WorkBuddy 说他当天差点就这么干。

    首版实现只用 `check=True` 捕获异常；本版改用 `check=False` 比 returncode，
    误报**依然存在**（只是从 traceback 变成了一句「提交失败」）。所以必须
    在报失败之前核实一次。

    三条判据同时成立才算已落地：

    1. **工作区干净** —— 还有改动就说明确实没提交完。
    2. **HEAD 是 merge commit**（>= 2 个父）—— 防止把上一个 merge commit
       误当成本次的结果。
    3. **被合分支已是 HEAD 的祖先** —— 这条最关键：它直接证明「这次合并的
       内容确实进了 main」，比前两条都强。

    返回 (是否已落地, 说明)。

    `git` 参数是注入点：默认 `_git` 绑在本模块的 `REPO_ROOT` 上，而
    `REPO_ROOT` 是**导入时**按进程 cwd 定的。在 pytest 进程里它指向真实
    工作区，所以直接调这个函数会去查真实仓库 —— 既测不到临时仓库，又
    有误判风险。测试通过注入一个绑到临时仓库的 runner 来覆盖四条判据。
    """
    run = git if git is not None else _git

    dirty = run("status", "--porcelain", check=False).stdout.strip()
    if dirty:
        return False, "工作区仍不干净（%s）" % dirty.splitlines()[0][:80]

    parents = run("log", "-1", "--format=%P", check=False).stdout.split()
    if len(parents) < 2:
        return False, "HEAD 不是 merge commit（%d 个父）" % len(parents)

    anc = run("merge-base", "--is-ancestor", branch, "HEAD", check=False)
    if anc.returncode != 0:
        return False, "被合分支 %s 还不是 HEAD 的祖先" % branch

    return True, "HEAD=%s（%d 个父），且 %s 已是其祖先" % (
        run("log", "-1", "--format=%h", check=False).stdout.strip(),
        len(parents),
        branch,
    )


def _commit_with_retry(
    *,
    sleep: Callable[[float], object] = time.sleep,
    commit: Callable[[], subprocess.CompletedProcess[str]] = _commit_once,
) -> subprocess.CompletedProcess[str]:
    """提交 merge commit，同样对瞬时 ref 锁争用退避重试。

    早先版本只给 merge 加了退避，最后这步 `git commit` 裸奔——而它更新的
    正是 `refs/heads/main`，恰恰是最容易撞锁的一笔。撞上时 exit 6，而此时
    MERGE_HEAD 还在、工作区已是合并结果，人只能自己去猜该怎么办。

    重试不需要重新 merge，也不需要 abort：MERGE_HEAD 还在，重跑 commit
    就是把同一笔提交再写一次。
    """
    proc = commit()
    for attempt in range(1, MERGE_RETRY_ATTEMPTS):
        if proc.returncode == 0 or not _is_ref_contention(proc):
            break
        wait = MERGE_RETRY_BACKOFF_SECONDS * attempt
        print(
            f"  commit 撞 ref 锁争用，{wait}s 后重试"
            f"（{attempt}/{MERGE_RETRY_ATTEMPTS}）…"
        )
        sleep(wait)
        proc = commit()
    return proc


# --------------------------------------------------------------------------


def _run_gate(name: str, args: list[str]) -> tuple[bool, str]:
    """跑一道门禁。**不设上限**：门禁就是慢的活儿（pytest 动辄几分钟），
    这里宁可等，也不该因为超时把一次好合并误判成门禁红。

    超时的另一面是挂死保护：门禁脚本若自己卡死（比如它内部的 git 调用
    撞上自动维护），本工具会一直持有合并锁。所以上面 `_git` 的
    GIT_TIMEOUT_SECONDS 是给 git 用的，这里不给门禁用 —— 两者的
    「合理的慢」不是一个量级。

    返回的第二个值是**门禁的自述摘要**，也就是「绿灯的证据」。它不能是
    门禁碰巧的最后一行输出 —— 见下面 `_gate_summary`。
    """
    cmd = [sys.executable, *args]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    # 豁免行必须显示出来：门禁少跑一条断言这件事不能只存在于代码里。
    for ln in lines:
        if ln.startswith("[skip]"):
            print("  " + ln)
    return proc.returncode == 0, f"{name}: {_gate_summary(lines)}"


#: 门禁摘要行的标记。docstate 与 discipline 都用这一行收尾：
#:
#:     结果: 90 项检查，0 项失败
#:     结果: 44 项检查，0 项失败，1 项告警
#:
#: 之所以要**认这一行**而不是取最后一行输出：门禁在结果之后完全可能再打印
#: 东西 —— Python 3.13 对无效转义序列的 SyntaxWarning、DeprecationWarning、
#: `--json` 切换时的提示，都会插在结果行后面。
#:
#: 实测吃过：discipline 打印的 SyntaxWarning 落在结果之后，摘要变成那条警告，
#: **真正的检查结果被吃掉**，而退出码仍是 0 —— 门禁是绿的，绿灯的证据是错的。
#:
#: 用「包含」而不是「以…开头」：测试里的门禁替身打印的是
#: `print("{name}: 结果: 1 项检查，0 项失败")`，行首有门禁名；
#: 生产里两道门禁都是裸的 `结果:`。两种都是合法的门禁自述。
GATE_SUMMARY_MARK = "结果:"


def _gate_summary(lines: list[str]) -> str:
    """从门禁输出里挑出**门禁自己说出的结论**。

    取**最后一条**含 `结果:` 的行：门禁可能先打印一段旧摘要再打新的
    （重试、二次汇总），后面的才是最终结论。

    找不到时退到最后一行，但**必须标注**「未找到摘要行」。这不是啰嗦：
    两种情况的门禁自述强度完全不一样，混在一起显示等于把不可信的输出
    伪装成门禁的结论。
    """
    for ln in reversed(lines):
        if GATE_SUMMARY_MARK in ln:
            return ln
    if not lines:
        return "(无输出)"
    return "(未找到摘要行，最后一行: %s)" % lines[-1]


def do_status() -> int:
    print(guard.describe_status(REPO_ROOT))
    return 0


#: 别人裸跑 git 时会留下的锁文件 -> 为什么会挡住本工具。
#:
#: 一律相对 **git common dir**。这个布局是实测确认的，不是照抄文档：
#: - `index` / `HEAD` 是 **per-worktree**，linked worktree 的在
#:   `<common>/worktrees/<name>/` 下；
#: - `refs/` 与 `packed-refs` 是 **跨 worktree 共享**的。
#: 本工具只在根工作区（main）跑，那里 common dir 就是主工作区的 gitdir，
#: 两种布局路径重合，所以统一按 common dir 拼即可。
#:
#: 缺失 `refs/heads/main.lock` 与 `packed-refs.lock` 是 TRAE 评审指出的
#: 漏洞（问题 6）：裸跑 `git commit` / `git fetch` 可能**只持 ref 侧锁**，
#: index 那时已经放开，预检就漏放了；等真走到提交那步才撞
#: `cannot lock ref 'HEAD'`，那时已经做完所有前置工作。
FOREIGN_GIT_LOCKS: tuple[tuple[str, str], ...] = (
    ("index.lock", "有未完成的 git 操作（index 被锁）"),
    ("ORIG_HEAD.lock", "有未完成的 ref 更新"),
    # 下面三个是裸跑 commit / fetch / checkout 会留下、而 index 那时已放开的：
    ("refs/heads/main.lock", "有人正在提交 main（ref 被锁）"),
    ("packed-refs.lock", "有人在打包 refs（pack-refs / fetch 后台 gc）"),
    ("HEAD.lock", "有人在移动 HEAD（checkout / reset / commit 正在写）"),
)


def _detect_foreign_git_activity(repo: Path) -> str | None:
    """检测**别人**正在跑的 git 操作。

    互斥锁只拦走本入口的合并；别人裸跑 `git merge` / `git commit` 不会取锁。
    实测踩到过：锁取到了、门禁也绿，但 `git merge` 在写 ORIG_HEAD 时报
    `fatal: update_ref failed for ref 'ORIG_HEAD'`——别的 agent 正在合并。

    这些信号能提前把它挡下来，而不是等git 自己撞锁。

    `MERGE_HEAD` 不在这里判断：它需要和 `merge-pending.json` 对照才能分清
    「本工具的待提交合并」与「别人的合并」，由调用方单独处理。
    """
    common = guard.git_common_dir(repo)
    for name, why in FOREIGN_GIT_LOCKS:
        if (common / name).exists():
            return f"{why}：{common / name}"
    return None


def _classify_pending_merge(
    requested_branch: str, requested_task: str = ""
) -> tuple[bool, str | None]:
    """判断当前 MERGE_HEAD 是不是**本次请求**那次合并留下的待提交现场。

    返回 (是否可续跑, 拒绝理由)。**fail-closed**：任何无法证明归属的情形
    一律按「别人的合并」处理。

    **必须有两条独立证据，缺一不可：**

    1. `MERGE_HEAD` 的内容 == 记录里的 `merge_head`（同一个 commit）。
    2. 记录里的 `branch`（或 `task_id`）== 本次请求的分支（或任务）。

    为什么第 2 条不能省 —— 这是 2026-10-07 03:05 的实测事故：
    Qoder 合并 `sweeper-deps-resources` 后进程退出，现场悬着
    （`MERGE_HEAD` == `merge-pending.json` 的 `merge_head` == `eb7d8d36`）。
    随后我请求合并**另一条分支**，只凭第 1 条就判成「自己的遗留合并」，
    **跳过了 merge 步骤**，直接在别人的工作树上跑门禁 —— 我的分支根本没被
    合并，而门禁验的是他的树。

    最坏情况不是这一次：门禁若恰好是绿的，工具会报成功并提交，
    **把别人的合并当成我的合并提交掉** —— 正是本工具存在的目的所防的
    「接管别人的合并」。

    也就是说：第 1 条证明的是「**有一次**合并悬着」，第 2 条才证明
    「是**我请求的那次**合并」。只有前者就续跑，等于把别人的现场当成自己的。
    """
    pending = _read_pending()
    merge_head = _read_merge_head()

    if pending is None and merge_head is None:
        return False, None

    if pending is None:
        return False, (
            f"已存在进行中的合并（MERGE_HEAD 无本工具的归属记录，"
            f"可能不是本工具发起）：{_merge_head_path()}"
        )

    if merge_head is None:
        # 记录在、标记没了：上一次合并已被提交或回退，清掉陈迹继续。
        print("清理上次遗留的待提交合并记录（MERGE_HEAD 已不存在，合并已了结）。")
        _clear_pending()
        return False, None

    if pending.get("merge_head") != merge_head:
        return False, (
            f"存在待提交合并记录，但 MERGE_HEAD 与记录不符"
            f"（记录 {pending.get('merge_head')!r}，实际 {merge_head!r}）："
            f"说明合并已被他人接手或替换，不接管。"
        )

    # 第 2 条证据：**MERGE_HEAD 必须就是本次请求要合的那个分支的 tip**。
    #
    # 这里刻意**不**去比对记录里的 `branch` / `task_id` 标签 —— 那是上一次运行
    # 写下的字符串，是标签不是事实；标签可以被改、被抄错、被手工编辑。
    # 而 `MERGE_HEAD == rev-parse(请求的分支)` 是 git 自己的事实：它精确地
    # 证明「现在悬着的这次合并，合并的正是我要求合的那个分支的那个 commit」。
    # 那就等于「我请求的这次合并」，与它上一次是谁跑的无关。
    #
    # 顺带说明为什么「分支相同、task_id 不同」不该拒绝：那种情况下悬着的合并
    # 确实就是我要求的那一笔（MERGE_HEAD 就是那个分支的 tip），提交它就是
    # 完成我的请求。task_id 只是锁信息里的标签，不构成合并的身份。
    expected_tip = _rev_parse(requested_branch)
    if not expected_tip:
        return False, (
            f"无法解析要合并的分支 {requested_branch!r}（它还存在吗？），"
            f"不予接管任何现场。"
        )
    if merge_head != expected_tip:
        return False, (
            f"存在**他人遗留**的待提交合并（MERGE_HEAD={merge_head!r}，"
            f"记录分支 {pending.get('branch')!r}，记录任务 {pending.get('task_id')!r}），"
            f"而本次请求的 {requested_branch!r} 当前 tip 是 {expected_tip!r} —— "
            f"不是同一次合并，不接管。\n"
            f"  那次合并的现场仍在：{_merge_head_path()}\n"
            f"  请先让它的主人收尾，或确认无人操作后手工清理，**不要由本次请求代劳**。"
        )

    return True, None


def do_merge(branch: str, task_id: str, skip_gate: bool) -> int:
    current = _current_branch()
    if current != "main":
        print(
            f"拒绝：当前分支是 {current}，合并入口只允许在 main 上运行。",
            file=sys.stderr,
        )
        print(
            "（根工作区才是 main；请到根工作区执行，或先 git switch main）",
            file=sys.stderr,
        )
        return 2

    resuming, refusal = _classify_pending_merge(branch, task_id)
    if refusal is not None:
        print(f"拒绝：{refusal}", file=sys.stderr)
        print(
            "另一个 agent 正在这个仓库跑 git（或合并已被他人接手）。"
            "等它结束再试；确认无人操作后可手工清理现场，**不要盲删锁目录**。",
            file=sys.stderr,
        )
        return 7

    foreign = _detect_foreign_git_activity(REPO_ROOT)
    if foreign is not None:
        print(f"拒绝：{foreign}", file=sys.stderr)
        print("另一个 agent 正在这个仓库跑 git。等它结束再试；", file=sys.stderr)
        print(
            "若确认无人操作，删掉该锁文件后再试（先确认没有 git 进程在跑）。",
            file=sys.stderr,
        )
        return 7

    if resuming:
        pending = _read_pending() or {}
        print(
            f"检测到本工具上次遗留的待提交合并（分支 {pending.get('branch')}，"
            f"记录 {pending.get('merge_head')}）——跳过合并步骤，直接续跑。"
        )
        # 续跑要提交的是暂存区，所以唯一的危险是「暂存区不再是上次 merge 的
        # 结果」。未暂存与未跟踪的内容 git commit 不会带进去，无害，不拒。
        recorded = pending.get("index_tree")
        current = _index_tree()
        if recorded != current:
            print(
                "拒绝：待提交合并的暂存区内容已改变，不允许续跑提交。\n"
                f"  上次 merge 留下的暂存区 : {recorded}\n"
                f"  当前暂存区             : {current}",
                file=sys.stderr,
            )
            print(
                "有人在合并未提交期间改动了暂存区（手敲 git add、或解决冲突）。"
                "这些内容不属于本次合并，提交进去就等于把别人的活儿合进 main。",
                file=sys.stderr,
            )
            print(
                "请先处理掉暂存区的差异（git status 看清楚），再重跑本命令。",
                file=sys.stderr,
            )
            return 2
    else:
        dirty = _porcelain()
        if dirty:
            print("拒绝：工作区有未提交改动，不允许开始合并。", file=sys.stderr)
            print(
                "这些改动很可能属于另一个 agent，卷进 merge commit 就是本项目丢过提交的那次事故：",
                file=sys.stderr,
            )
            for line in dirty[:10]:
                print(f"  {line}", file=sys.stderr)
            if len(dirty) > 10:
                print(f"  ... 另有 {len(dirty) - 10} 行", file=sys.stderr)
            return 2

    try:
        info = guard.acquire(
            REPO_ROOT, task_id=task_id, branch=branch, worktree=REPO_ROOT
        )
    except guard.LockError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    print(f"已取合并锁：{info.task_id}（pid {info.pid}）")
    try:
        if not resuming:
            try:
                proc, expected_head = _merge_with_retry(branch)
            except _ForeignMerge as exc:
                print(
                    "拒绝：退避窗口内出现了他人的合并"
                    f"（MERGE_HEAD={exc.args[0]}，与本次预期的 "
                    f"{_rev_parse(branch)} 不符），不予回退。",
                    file=sys.stderr,
                )
                print(
                    "他人的合并不会被本工具 abort。请等它结束后重试。",
                    file=sys.stderr,
                )
                return 7
            if proc.returncode != 0:
                unmerged = _git("diff", "--name-only", "--diff-filter=U").stdout.split()
                if unmerged:
                    print(
                        "\n合并冲突，已保留现场**不要 git merge --abort**。",
                        file=sys.stderr,
                    )
                    print(
                        "另一个人可能正在同一个工作区解冲突；abort 会连他的工作一起丢掉。",
                        file=sys.stderr,
                    )
                    print("冲突文件：", file=sys.stderr)
                    for f in unmerged:
                        print(f"  {f}", file=sys.stderr)
                    print(
                        "\n解决后跑 ./scripts.sh check，全绿再 git commit。",
                        file=sys.stderr,
                    )
                else:
                    print(proc.stderr or proc.stdout, file=sys.stderr)
                return 4
            # 记下「待提交合并」的归属，供门禁红之后续跑，也供下一次入口
            # 把「自己的残留」与「别人的合并」分开。
            _write_pending(branch, task_id, expected_head)

        if skip_gate and not resuming:
            print("已跳过门禁（--skip-gate）。请自行跑 ./scripts.sh check 后再提交 merge commit。")
            print("当前改动已 staged 但**未提交**（用了 --no-commit）。")
            print(
                "恢复方式：手工 `git commit`（不再跑门禁）或 `git merge --abort` 回退；"
                "也可以去掉 --skip-gate 重跑本命令，改成跑门禁再提交。"
            )
            return 0

        if skip_gate:
            print("\n续跑：--skip-gate，跳过门禁直接提交。")
        else:
            print("\n合并无冲突，改动已 staged 但**未提交**（--no-commit）。开始跑门禁…")
            gates = [
                ("docstate", ["tools/docstate/docstate.py"]),
                # 不再对 discipline 传 `--skip-group`（该选项已整体回退）。
                # 合并期间根工作区是脏的，但 rootworktree 组的豁免判据已经
                # 能正确识别「受控合并进行中」——mergeguard 此刻持有合并锁，
                # 锁持有者 pid 存活且无未合并条目，所以那一组自己就放行。
                # 换句话说：**该修的是豁免判据，不是加一个能关掉检查的开关。**
                ("discipline", ["tools/discipline/discipline.py"]),
            ]
            failed = []
            for name, args in gates:
                ok, summary = _run_gate(name, args)
                print(f"  {summary}")
                if not ok:
                    failed.append(name)

            if failed:
                print(f"\n门禁未通过：{', '.join(failed)}", file=sys.stderr)
                print(
                    "merge commit **未提交**。main 尚未包含该分支的任何 commit。",
                    file=sys.stderr,
                )
                print(
                    "修好门禁后**重跑本命令即可**（会跳过合并、直接续跑门禁并提交）；"
                    "或 `git merge --abort` 回退。",
                    file=sys.stderr,
                )
                print(
                    f"续跑命令：python tools/mergeguard/cli.py merge {branch} "
                    f"--task-id {task_id}",
                    file=sys.stderr,
                )
                return 5

        print("\n门禁通过，提交 merge commit。")
        commit = _commit_with_retry()
        if commit.returncode != 0:
            landed, why = _commit_landed(branch)
            if landed:
                # 见 `_commit_landed` 的 docstring：git 间歇性写 index 失败会让
                # commit 退出码非零，而 commit 其实已经落地。此时**必须**报成功，
                # 否则调用方会重跑，重跑时分支已在 main 上。
                print(
                    "\n注意：`git commit` 返回非零，但核实后 merge commit "
                    "已经落地（%s）。" % why
                )
                print("git 的报错：" + (commit.stderr or commit.stdout).strip())
                print("按成功处理 —— 请**不要**重跑本命令。")
                _clear_pending()
                print("HEAD: " + _git("log", "--oneline", "-1").stdout.strip())
                return 0
            print("提交失败：" + (commit.stderr or commit.stdout), file=sys.stderr)
            print("核实结果：merge commit 未落地（%s）。" % why, file=sys.stderr)
            print(
                f"MERGE_HEAD 仍在、合并结果已暂存。修好后重跑本命令即可只重试提交：\n"
                f"  python tools/mergeguard/cli.py merge {branch} --task-id {task_id}",
                file=sys.stderr,
            )
            return 6
        _clear_pending()
        print("HEAD: " + _git("log", "--oneline", "-1").stdout.strip())
        return 0
    finally:
        guard.release(REPO_ROOT)
        print("已释放合并锁。")


def _force_utf8_stdio():
    """把 stdout/stderr 固定成 UTF-8，且**永不因编码而崩**。

    为什么必须做，而不是靠调用方设 `PYTHONIOENCODING`：

    实测 `tools/docstate/docstate.py --verbose` 在 Windows 上**直接崩溃**：

        UnicodeEncodeError: 'gbk' codec can't encode character '\u2286'

    `⊆`（U+2286）出现在一条断言消息里，而 `gbk` 与 `cp936` 都编不出它。
    CI 跑的是不带 `--verbose` 的 docstate，所以从没撞到；开发者一加
    `--verbose` 想看细节就崩。**门禁是给别人用的工具，要求每个人先设环境
    变量才能正确跑，本身就是缺陷。**

    顺带解决第二个问题：此前门禁输出跟着控制台代码页走，实测在
    `gbk` / `cp936` / 未设三种情况下输出**都不是合法 UTF-8**，
    被日志系统或 CI artifact 收走就是乱码（`gate.yml` 正是重定向收 `--json`）。

    - `encoding="utf-8"`：输出恒为合法 UTF-8，`⊆`、`§`、全角标点都能编码。
    - `errors="replace"`：万一还有别的字符编不出，**降级**而不是抛异常 ——
      门禁给出「结果不对」远好过「门禁自己崩了、什么结果都没有」。

    调用点放在 `main()` 的最前面，且用 try/except 兜住：某些宿主
    （Jupyter、部分测试夹具）会把 stdout 换成没有 `reconfigure` 的对象，
    那时保持原样即可，不该因此让门禁起不来。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(prog="mergeguard", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="打印当前合并锁状态")

    m = sub.add_parser("merge", help="受控地把分支合入 main")
    m.add_argument("branch")
    m.add_argument(
        "--task-id", default="(未指定)", help="写进锁信息，便于他人识别持有者"
    )
    m.add_argument("--skip-gate", action="store_true", help="跳过门禁（不推荐）")

    args = parser.parse_args(argv)
    if args.cmd == "status":
        return do_status()
    return do_merge(args.branch, args.task_id, args.skip_gate)


if __name__ == "__main__":
    raise SystemExit(main())