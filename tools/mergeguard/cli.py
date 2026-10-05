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
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
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


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # encoding 必须显式指定：Windows 上 text=True 默认按 GBK 解码，而 git 输出
    # 的提交信息含 UTF-8 中文（本仓库的提交信息全是中文），不指定就会抛
    # UnicodeDecodeError —— 第一次真合并就会撞上。
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def _current_branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _porcelain() -> list[str]:
    return [
        ln for ln in _git("status", "--porcelain").stdout.splitlines() if ln.strip()
    ]


def _run_gate(name: str, args: list[str]) -> tuple[bool, str]:
    cmd = [sys.executable, *args]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    tail = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    return proc.returncode == 0, f"{name}: " + (tail[-1] if tail else "(无输出)")


def do_status() -> int:
    print(guard.describe_status(REPO_ROOT))
    return 0


def _detect_foreign_git_activity(repo: Path) -> str | None:
    """检测**别人**正在跑的 git 操作。

    互斥锁只拦走本入口的合并；别人裸跑 `git merge` / `git commit` 不会取锁。
    实测踩到过：锁取到了、门禁也绿，但 `git merge` 在写 ORIG_HEAD 时报
    `fatal: update_ref failed for ref 'ORIG_HEAD'`——别的 agent 正在合并。

    这两个信号能提前把它挡下来，而不是等git 自己撞锁。
    """
    common = guard.git_common_dir(repo)
    for name, why in (
        ("index.lock", "有未完成的 git 操作（index 被锁）"),
        ("MERGE_HEAD", "已存在进行中的合并（可能不是本工具发起）"),
        ("ORIG_HEAD.lock", "有未完成的 ref 更新"),
    ):
        if (common / name).exists():
            return f"{why}：{common / name}"
    return None


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

    foreign = _detect_foreign_git_activity(REPO_ROOT)
    if foreign is not None:
        print(f"拒绝：{foreign}", file=sys.stderr)
        print("另一个 agent 正在这个仓库跑 git。等它结束再试；", file=sys.stderr)
        print(
            "若确认无人操作，删掉该锁文件后再试（先确认没有 git 进程在跑）。",
            file=sys.stderr,
        )
        return 7

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
        proc = _git("merge", "--no-ff", "--no-commit", "--no-edit", branch, check=False)
        if proc.returncode != 0:
            # 瞬时 ref 锁争用要重试；真冲突不能重试。
            # 实测触发者不是别的 agent，而是 git 自己的后台维护：
            # `git maintenance run --auto --detach` 在写操作后自动运行、持有 ref 锁，
            # 与随后的 merge 撞出 `update_ref failed for ref 'ORIG_HEAD'`。
            # 预检抓不到它（锁是瞬时的），只能在撞上之后重试。
            for attempt in range(1, MERGE_RETRY_ATTEMPTS):
                text = (proc.stderr or "") + (proc.stdout or "")
                if "update_ref failed" not in text and "cannot lock ref" not in text:
                    break
                wait = MERGE_RETRY_BACKOFF_SECONDS * attempt
                print(
                    f"  ref 锁争用（多半是 git 自动维护），{wait}s 后重试"
                    f"（{attempt}/{MERGE_RETRY_ATTEMPTS}）…"
                )
                time.sleep(wait)
                _git("merge", "--abort", check=False)
                proc = _git(
                    "merge", "--no-ff", "--no-commit", "--no-edit", branch, check=False
                )
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

        if skip_gate:
            print(
                "已跳过门禁（--skip-gate）。请自行跑 ./scripts.sh check 后再提交 merge commit。"
            )
            print("当前改动已 staged 但**未提交**（用了 --no-commit）。")
            return 0

        print("\n合并无冲突，改动已 staged 但**未提交**（--no-commit）。开始跑门禁…")
        gates = [
            ("docstate", ["tools/docstate/docstate.py"]),
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
            print("修好门禁后重跑本命令，或 git merge --abort 回退。", file=sys.stderr)
            return 5

        print("\n门禁通过，提交 merge commit。")
        commit = _git("commit", "--no-edit", check=False)
        if commit.returncode != 0:
            print("提交失败：" + (commit.stderr or commit.stdout), file=sys.stderr)
            return 6
        print("HEAD: " + _git("log", "--oneline", "-1").stdout.strip())
        return 0
    finally:
        guard.release(REPO_ROOT)
        print("已释放合并锁。")


def main(argv: list[str] | None = None) -> int:
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
