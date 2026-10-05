"""共享 `main` 的合并互斥锁。

为什么需要它
------------
本仓库有 7 个 git worktree 共享同一个 `.git`。其中只有 `HEAD` 与 index 是
per-worktree 的，**`refs/`、`ORIG_HEAD` 与锁文件全部共享**。而 AGENTS.md 规定
仓库根目录是「协调者专用合并区」—— 所有合并都挤在那里，却没有任何互斥。

这不是理论风险。本项目实际发生过两次：

1. 一次 `git merge` 解完冲突后，`MERGE_HEAD` 凭空消失、工作区恢复干净——
   被并发的 git 操作静默 abort，冲突解决成果零提交丢失。git 不会告知任何一方。
2. 立即重试则 `fatal: update_ref failed for ref 'ORIG_HEAD': couldn't set
   'ORIG_HEAD'`——共享 ref 锁竞争。

dev-convention.md 的「靠自觉的」清单里已列「禁止在 main 上直接提交 ——
没有 hook，纯自觉」。合并这一环连自觉都没有。

设计取舍
--------
- **原子性靠 `mkdir`**：POSIX 与 Windows 上 `mkdir` 在目录已存在时都会失败，
  这是跨平台可用的唯一原子「占位」原语，不需要额外的进程间通信库。
- **锁放在 git common dir**：per-worktree 的目录挡不住另一个 worktree。
- **不静默等待**：拿不到锁就明确失败并打印持有者。排队等待会让人以为卡住，
  而本项目的事故恰恰是「静默」——一个静默的等待只会换一种方式重现它。
- **stale 可接管**：持有者进程已死且超过 TTL 时允许接管，否则一次崩溃就会
  永久锁死合并。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

#: 锁目录名，放在 git common dir 下以便跨 worktree 生效。
LOCK_DIRNAME = "merge-lock"

#: 持有者进程确认已死后，仍需等待这么久才允许接管（防止 pid 被系统回收复用）。
DEFAULT_TTL_SECONDS = 900


class LockError(RuntimeError):
    """锁相关失败。消息面向人，必须能直接读懂下一步做什么。"""


@dataclass(frozen=True)
class LockInfo:
    pid: int
    task_id: str
    branch: str
    worktree: str
    acquired_at: float

    def age_seconds(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.acquired_at


def _resolve_git_dir_no_subprocess(repo: Path) -> Path | None:
    """纯文件探测 git 目录，不 spawn 任何进程。

    - 主工作区：`<repo>/.git` 是目录，它就是 common dir。
    - linked worktree：`<repo>/.git` 是文件，内容 `gitdir: <common>/worktrees/<name>`，
      往上两级就是 common dir。

    互斥原语的热路径上不能有子进程。这不是洁癖 —— 实测在 pytest 的 fd捕获
    下，并发 spawn `git rev-parse` 会随机返回 `0xC000013A`
    （STATUS_CONTROL_C_EXIT），让抢锁随机失败。锁必须在 git 不可用时也能工作。
    """
    dot_git = repo / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            text = dot_git.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            return None
        if not text.lower().startswith("gitdir:"):
            return None
        target = Path(text.split(":", 1)[1].strip())
        if not target.is_absolute():
            target = (repo / target).resolve()
        # <common>/worktrees/<name> -> <common>
        if target.parent.name == "worktrees" and target.parent.parent.is_dir():
            return target.parent.parent
        return target
    return None


@lru_cache(maxsize=32)
def _git_common_dir_cached(key: str) -> str:
    repo = Path(key)
    resolved = _resolve_git_dir_no_subprocess(repo)
    if resolved is not None:
        return str(resolved)
    # 兜底：.git 探测失败（BARE=false 的裸仓库、submodule 等）才问 git。
    # encoding 必须显式指定：Windows 上 text=True 默认按 GBK 解码，而 git 输出
    # 的路径/提交信息含 UTF-8 中文，会直接抛 UnicodeDecodeError。
    out = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--path-format=absolute",
            "--git-common-dir",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    return out


def git_common_dir(repo: Path) -> Path:
    """返回共享的 git 目录（所有 worktree 共用同一个）。

    注意不能用 `.git` 直接拼路径：linked worktree 的 gitdir 在
    `.git/worktrees/<name>` 下，那里只存 per-worktree 状态。
    按 repo 路径缓存——同一仓库的 common dir 在进程生命周期内不会变。
    """
    return Path(_git_common_dir_cached(str(Path(repo).resolve())))


def lock_path(repo: Path) -> Path:
    return git_common_dir(repo) / LOCK_DIRNAME


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但不属于当前用户，仍算活着。
        return True
    except OSError:
        return False
    return True


def read_lock(repo: Path) -> LockInfo | None:
    """读当前锁；不存在返回 None。内容损坏时也返回 None（视作无锁）。"""
    path = lock_path(repo) / "owner.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return LockInfo(
            pid=int(data["pid"]),
            task_id=str(data["task_id"]),
            branch=str(data["branch"]),
            worktree=str(data["worktree"]),
            acquired_at=float(data["acquired_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def is_stale(info: LockInfo, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> bool:
    """持有者进程已死 **且** 超过 TTL 才算 stale。

    两个条件都要满足：只看 pid 可能误判 pid 被回收；只看时间会在进程正忙时
    误判，导致两个人同时以为自己持有锁。
    """
    return (not _pid_alive(info.pid)) and info.age_seconds() > ttl_seconds


def acquire(
    repo: Path,
    task_id: str,
    branch: str,
    worktree: str | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> LockInfo:
    """取锁。已被占用时抛 LockError，并把持有者信息写进消息。"""
    path = lock_path(repo)
    info = LockInfo(
        pid=os.getpid(),
        task_id=task_id,
        branch=branch,
        worktree=str(worktree or repo),
        acquired_at=time.time(),
    )
    try:
        path.mkdir()
    except FileExistsError:
        holder = read_lock(repo)
        if holder is not None and is_stale(holder, ttl_seconds):
            # 持有者已死且超过 TTL：接管，但要留下痕迹而不是静默覆盖。
            release(repo)
            return _acquire_now(repo, info)
        raise _describe_holder(repo, holder) from None
    (path / "owner.json").write_text(
        json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def _acquire_now(repo: Path, info: LockInfo) -> LockInfo:
    path = lock_path(repo)
    path.mkdir()  # 若再次 FileExistsError，说明有人同时接管，让调用方看到原始错误
    (path / "owner.json").write_text(
        json.dumps(asdict(info), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return info


def _describe_holder(repo: Path, holder: LockInfo | None) -> LockError:
    path = lock_path(repo)
    if holder is None:
        return LockError(
            f"合并锁目录已存在但内容不可读：{path}\n"
            f"确认没有 agent 正在合并后手动删除该目录再重试。"
        )
    return LockError(
        f"已有 agent 持有合并锁，本次不合并：\n"
        f"  持有者 : {holder.task_id}（分支 {holder.branch}）\n"
        f"  工作区 : {holder.worktree}\n"
        f"  pid    : {holder.pid}（{'存活' if _pid_alive(holder.pid) else '已退出'}）\n"
        f"  已持有 : {holder.age_seconds():.0f} 秒\n"
        f"  锁路径 : {path}\n"
        f"等对方合并结束后再试。不要删除该目录——那正是本项目丢过提交的原因。"
    )


def release(repo: Path) -> bool:
    """释放锁。返回是否真的释放了。"""
    path = lock_path(repo)
    if not path.is_dir():
        return False
    try:
        (path / "owner.json").unlink(missing_ok=True)
        path.rmdir()
    except OSError:
        return False
    return True


def describe_status(repo: Path, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """给人看的锁状态。"""
    holder = read_lock(repo)
    if holder is None:
        if lock_path(repo).is_dir():
            return f"锁目录存在但无有效 owner.json：{lock_path(repo)}（疑似残留）"
        return "合并锁：空闲"
    stale = "（stale，可接管）" if is_stale(holder, ttl_seconds) else ""
    return (
        f"合并锁：被占用{stale}\n"
        f"  持有者 : {holder.task_id}（分支 {holder.branch}）\n"
        f"  工作区 : {holder.worktree}\n"
        f"  pid    : {holder.pid}（{'存活' if _pid_alive(holder.pid) else '已退出'}）\n"
        f"  已持有 : {holder.age_seconds():.0f} 秒"
    )
