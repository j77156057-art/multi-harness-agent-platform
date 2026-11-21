"""确定性执行人类下发的结构化操作指令（v1 只有 ``commit``）。

为什么不让模型做？（设计决定，别改回去）
--------------------------------------
管理员 worker 的模型通道只该做「决定」：细化目标、审阅、回答问题。仓库写
权限不该经过模型 —— 模型可能把 ``git add -A`` 当成 ``git add .``，也可能
自己发明一条 ``--amend``。所以「让管理员提交这几个文件」这件事被拆成两层：

* **人类授权**：人在群里发一条结构化 JSON（``refs`` 含 ``admin-command``），
  指定仓库、分支、提交消息和**显式的路径清单**；
* **确定性执行**：本模块逐条校验后调用 git。模型既不解析也不执行任何一条
  git 命令，全程没有模型调用。

为什么必须由能在**主工作区**跑的东西做：平台的任务模型是 worktree 隔离，
成员派单看不到主工作区的未提交改动，提交只能在这里做。

三条硬边界（都在 :func:`run_admin_command` 里强制，且都有测试）：
* 只在允许根内的 git 仓库上动作（默认项目根 + ``AP_ADMIN_COMMAND_ROOTS``）；
* 绝不在 ``main``/``master`` 上提交（rule #6，硬拒绝）；
* 只 ``git add`` 显式列出的仓库内相对路径 —— 不做 ``-A``/``-u``/通配符，
  也不容忍 ``..``/绝对路径/盘符逃逸。

v1 只做 ``add`` + ``commit`` + ``switch``：不 push、不 rebase、不 reset、
不 ``checkout --force``。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT
from app.git.repo import run_git

#: 消息 ``refs`` 里的标记：一条人类下发的结构化操作指令。
ADMIN_COMMAND_REF = "admin-command"
#: 本模块唯一支持的动作。别的 action 明确报「暂不支持」，不猜。
COMMIT_ACTION = "commit"
#: rule #6：集成分支/主分支不许直接写。大小写都拒（Windows 上 ``MAIN`` 也
#: 是同一件事）。
FORBIDDEN_BRANCHES = frozenset({"main", "master"})
#: 允许根的追加配置：``;`` 或 ``,`` 分隔的绝对路径。
ROOTS_ENV = "AP_ADMIN_COMMAND_ROOTS"
#: 路径里出现这些字符就是通配符 —— 拒绝。指令的语义是「只提交这几个文件」，
#: 通配符会把「我没看过的文件」也带进提交里。
_GLOB_CHARS = frozenset("*?[]{}")
#: 进行中的 git 操作标记（在 gitdir 下）。有就拒绝：在半截的 merge/rebase
#: 上提交，等于把别人的中间状态写成一条提交。
_UNFINISHED_MARKERS = (
    "MERGE_HEAD",
    "CHERRY_PICK_HEAD",
    "REVERT_HEAD",
    "rebase-merge",
    "rebase-apply",
)
#: 指令格式的示例，报错时贴给人看（人照着改一条再发就行）。
FORMAT_HINT = (
    '{"action":"commit","repo":"D:/path/to/repo","branch":"my-work",'
    '"commits":[{"message":"说明这次改了什么","paths":["a.py","b.py"]}],'
    '"dry_run":false,"command_id":"可选，幂等用"}'
)


class CommandError(ValueError):
    """A human-fixable problem with the command (or with the repository state).

    Everything raised as this class is safe to paste into the group chat: it
    names what was wrong and what was expected, never a stack trace.
    """


@dataclass
class CommitResult:
    """One entry of ``commits``: what happened to it, and why."""

    message: str
    paths: list[str] = field(default_factory=list)
    #: The commit hash, empty when the entry was skipped or never executed.
    commit: str = ""
    #: Files in the commit (or, in ``dry_run``, files that would be committed).
    files: int = 0
    #: No staged change after ``git add`` -- deliberately no empty commit.
    skipped: bool = False
    #: ``git show --stat --oneline`` of the commit, for the operator's record.
    detail: str = ""


@dataclass
class CommandReport:
    """The whole run: what was planned, what landed, and where it stopped."""

    repo: str = ""
    branch: str = ""
    dry_run: bool = False
    commits: list[CommitResult] = field(default_factory=list)
    #: Empty on success. On failure: the step and the reason.
    error: str = ""
    #: ``None`` = unknown (rejected before we looked); ``True``/``False`` =
    #: the branch existed / had to be created.
    branch_existed: bool | None = None
    #: Whether ``git switch`` actually ran. A refused command must be able to
    #: say "the workspace was not moved" and mean it.
    switched: bool = False

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def landed(self) -> list[CommitResult]:
        return [item for item in self.commits if item.commit]


def parse_admin_command(body: str) -> dict[str, Any]:
    """Turn the message body into a normalised command, or raise ``CommandError``.

    Only the *shape* is checked here. Whether the paths are inside the
    repository, whether the branch may be written -- those need the repository
    and are checked per commit by :func:`run_admin_command`, so a mistake in the
    second commit is reported after the first one landed (and named as such)
    instead of silently discarding every commit.
    """
    text = str(body or "").strip()
    if not text:
        raise CommandError(f"消息里没有内容，期望的是一条 JSON 指令：{FORMAT_HINT}")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CommandError(f"body 不是合法 JSON（{exc}）。期望的字段是：{FORMAT_HINT}") from exc
    if not isinstance(data, dict):
        raise CommandError(
            f"body 必须是一个 JSON 对象，收到的是 {type(data).__name__}。{FORMAT_HINT}"
        )

    action = str(data.get("action") or "").strip()
    if not action:
        raise CommandError(f'缺少 action 字段（v1 只支持 "{COMMIT_ACTION}"）：{FORMAT_HINT}')
    if action != COMMIT_ACTION:
        raise CommandError(f'暂不支持 action="{action}"，v1 只支持 "{COMMIT_ACTION}"')

    repo = str(data.get("repo") or "").strip()
    if not repo:
        raise CommandError(f"缺少 repo（仓库的绝对路径）：{FORMAT_HINT}")
    branch = data.get("branch")
    if branch is not None and not isinstance(branch, str):
        raise CommandError("branch 必须是字符串（工作分支名，不能是 main/master）")

    raw_commits = data.get("commits")
    if not isinstance(raw_commits, list) or not raw_commits:
        raise CommandError(f"commits 必须是非空数组：{FORMAT_HINT}")
    commits: list[dict[str, Any]] = []
    for index, item in enumerate(raw_commits, 1):
        if not isinstance(item, dict):
            raise CommandError(f"第 {index} 条 commit 不是对象（期望 message + paths）")
        message = str(item.get("message") or "").strip()
        if not message:
            raise CommandError(f"第 {index} 条 commit 缺 message")
        raw_paths = item.get("paths")
        if not isinstance(raw_paths, list) or not raw_paths:
            raise CommandError(f"第 {index} 条 commit 的 paths 必须是非空数组（显式文件路径）")
        for raw in raw_paths:
            if not isinstance(raw, str):
                # Coercing would turn ``null`` into the path "None" and ``123``
                # into "123" -- a typo silently becomes a real pathspec.
                raise CommandError(f"第 {index} 条 commit 的 paths 里有一项不是字符串：{raw!r}")
        paths = [path.strip() for path in raw_paths]
        if not all(paths):
            raise CommandError(f"第 {index} 条 commit 的 paths 里有空路径")
        commits.append({"message": message, "paths": paths})

    command_id = str(data.get("command_id") or "").strip()
    return {
        "action": action,
        "repo": repo,
        "branch": str(branch or "").strip(),
        "commits": commits,
        # ``dry_run`` is deliberately bool()-coerced: a missing field means
        # "really commit", so only an explicit true plans.
        "dry_run": bool(data.get("dry_run")),
        "command_id": command_id or None,
    }


def allowed_roots() -> list[Path]:
    """The roots a command may act in: the project root plus the env additions.

    ``AP_ADMIN_COMMAND_ROOTS`` is ``;``/``,`` separated. It exists so an operator
    can point the administrator at a clone outside the platform checkout (a
    smoke test, another working copy) without weakening the default.
    """
    roots: list[Path] = [PROJECT_ROOT]
    raw = os.environ.get(ROOTS_ENV, "")
    for chunk in raw.replace(";", ",").split(","):
        text = chunk.strip().strip('"').strip("'")
        if text:
            roots.append(Path(text).expanduser())
    resolved: list[Path] = []
    for root in roots:
        try:
            resolved.append(root.resolve())
        except OSError:  # pragma: no cover - unreadable drive
            continue
    return resolved


def _within(child: Path, parent: Path) -> bool:
    """Whether ``child`` is ``parent`` or below it (case-insensitive on Windows)."""
    child_text = os.path.normcase(str(child))
    parent_text = os.path.normcase(str(parent)).rstrip(os.sep)
    return child_text == parent_text or child_text.startswith(parent_text + os.sep)


def _git(repo: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run git in ``repo`` through the platform's sanitised runner.

    ``app.git.repo.run_git`` neutralises ``core.quotepath`` and sets
    ``GIT_CONFIG_NOSYSTEM=1``. That last one only covers **system** config:
    a rule in ``~/.gitconfig`` still applies, so ``commit.gpgsign`` is *not*
    neutralised here -- a developer who signs every commit will make this path
    ask for a signature too. ``core.autocrlf`` has the same gap and that one is
    silent: on Windows the working tree is CRLF while history is LF, so a commit
    that does not apply the normalisation stores CRLF blobs and turns a 40-line
    change into a whole-file rewrite. Files stay correct on disk, every test
    passes, and the only trace is a 17k-line diff nobody reads. That is why the
    pin is below rather than left to whichever ``~/.gitconfig`` the worker
    process happened to inherit -- do not remove it on the assumption that the
    shared runner already handles line endings. ``autocrlf=true`` converts CRLF
    to LF on add and leaves LF untouched, which is exactly what a developer's own
    commit does in this repository.

    It takes no stdin, which is why the commit message goes through a temporary
    file (``-F``) rather than ``-F -``: same immunity to Windows quoting, no
    change to a shared helper.
    """
    try:
        return run_git("-c", "core.autocrlf=true", *args, cwd=repo, check=check)
    except OSError as exc:  # git is not on PATH, or the drive is gone
        raise CommandError(f"无法运行 git：{exc}") from exc


def _resolve_repo(repo_text: str, roots: Sequence[Path] | None) -> Path:
    """Validate that ``repo_text`` is a git repository root inside an allowed root."""
    if not repo_text:
        raise CommandError("缺少 repo（仓库的绝对路径）")
    path = Path(repo_text).expanduser()
    if not path.is_absolute():
        raise CommandError(f"repo 必须是绝对路径：{repo_text}")
    try:
        repo = path.resolve()
    except OSError as exc:
        raise CommandError(f"repo 路径读不了：{exc}") from exc
    if not repo.is_dir():
        raise CommandError(f"repo 不是一个存在的目录：{repo}")

    proc = _git(repo, "rev-parse", "--show-toplevel")
    if proc.returncode != 0:
        detail = proc.stderr.strip()[:200] or "不是 git 仓库"
        raise CommandError(f"{repo} 不是一个 git 仓库：{detail}")
    try:
        toplevel = Path(proc.stdout.strip()).resolve()
    except OSError:  # pragma: no cover - unreadable toplevel
        toplevel = Path(proc.stdout.strip())
    if os.path.normcase(str(toplevel)) != os.path.normcase(str(repo)):
        raise CommandError(f"repo 必须是仓库根目录：{repo} 的根是 {toplevel}")

    allow = list(roots) if roots is not None else allowed_roots()
    allow_resolved: list[Path] = []
    for root in allow:
        try:
            allow_resolved.append(Path(root).resolve())
        except OSError:  # pragma: no cover - unreadable root
            continue
    if any(_within(repo, root) for root in allow_resolved):
        return repo
    shown = "；".join(str(root) for root in allow_resolved) or "（空）"
    raise CommandError(
        f"repo 不在允许根内：{repo}。允许根 = {shown}"
        f"（要加的话设环境变量 {ROOTS_ENV}，多个用 ; 或 , 分隔）"
    )


def _validate_branch(branch: str) -> str:
    """Refuse an empty branch, ``main``/``master``, and anything git rejects."""
    text = str(branch or "").strip()
    if not text:
        raise CommandError("缺少 branch（工作分支名，不能为空，也不能是 main/master）")
    if text.lower() in FORBIDDEN_BRANCHES:
        raise CommandError(
            f"拒绝在 {text} 上提交：rule #6 规定集成分支/主分支只能由合入流程写，"
            "不许直接提交。请给一个工作分支名（例如 office-ui-and-admin-fixes）。"
        )
    if text.startswith("-"):
        raise CommandError(f"branch 不能以 - 开头：{text}")
    if text.lower().startswith("refs/"):
        # ``git check-ref-format --branch refs/heads/main`` says OK, and
        # ``git switch -c refs/heads/main`` then creates
        # ``refs/heads/refs/heads/main`` -- main itself does not move, but the
        # human plainly meant "commit on main", so it must be refused (QA P2-2).
        raise CommandError(f"branch 只填分支名，不要带 refs/ 前缀：{text}")
    # ``check-ref-format`` needs no repository, so it runs without a cwd: the
    # branch must be valid before we know (or care) where it will be created.
    try:
        proc = run_git("check-ref-format", "--branch", text, check=False)
    except OSError as exc:  # pragma: no cover - git is not on PATH
        raise CommandError(f"无法运行 git：{exc}") from exc
    if proc.returncode != 0:
        raise CommandError(f"branch 不是合法的分支名：{text}")
    return text


def _refuse_unfinished_operation(repo: Path) -> None:
    """Refuse to commit on top of a half-finished merge/rebase/cherry-pick.

    Committing there would write somebody else's intermediate state into a
    commit that claims to be a deliberate change.
    """
    proc = _git(repo, "rev-parse", "--git-dir")
    if proc.returncode != 0:  # pragma: no cover - validated by _resolve_repo
        raise CommandError(f"读不到 git 目录：{proc.stderr.strip()[:200]}")
    gitdir = Path(proc.stdout.strip())
    if not gitdir.is_absolute():
        gitdir = repo / gitdir
    for name in _UNFINISHED_MARKERS:
        if (gitdir / name).exists():
            raise CommandError(
                f"仓库里有进行中的 git 操作（{name}），先把它处理完（merge --abort / "
                "rebase --abort）再来提交"
            )


def _branch_exists(repo: Path, branch: str) -> bool:
    proc = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
    return proc.returncode == 0


def _index_has_changes(repo: Path, paths: Sequence[str]) -> bool:
    """Whether anything **in the declared paths** is staged.

    Scoped deliberately: ``git add -- <paths>`` only *adds to* the index, and
    ``git commit`` commits the whole index. Comparing the global index here
    would let a leftover staging area look like "my paths changed" (see
    :func:`_refuse_foreign_index` for the other half of that guard).
    """
    proc = _git(repo, "diff", "--cached", "--quiet", "--", *paths)
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    raise CommandError(f"判断暂存区失败：{proc.stderr.strip()[:300]}")


def _normalise_paths(paths: Sequence[str]) -> set[str]:
    """Path keys for set comparison; ``normcase`` keeps Windows' case folding honest."""
    return {os.path.normcase(Path(path).as_posix()) for path in paths}


def _staged_paths(repo: Path) -> dict[str, str]:
    """Everything currently staged, as ``{normalised key: name as git recorded it}``.

    The key is what comparisons use (``normcase`` keeps Windows' case folding
    honest); the value is what a human is shown -- telling somebody
    ``prestaged.txt`` when the file is ``PRESTAGED.txt`` is its own bug.
    """
    proc = _git(repo, "diff", "--cached", "--name-only", "-z")
    if proc.returncode != 0:  # pragma: no cover - the repository is validated
        raise CommandError(f"读暂存区失败：{proc.stderr.strip()[:300]}")
    staged: dict[str, str] = {}
    for name in proc.stdout.split("\0"):
        if not name.strip():
            continue
        shown = Path(name).as_posix()
        staged[os.path.normcase(shown)] = shown
    return staged


def _refuse_foreign_index(repo: Path, paths: Sequence[str], index: int) -> None:
    """Refuse when the index holds staged changes this commit did not declare.

    This is the hole that made "only add the listed paths" weaker than it
    looked (found by QA): ``git add -- a.py`` appends to the index, and
    ``git commit`` commits **the whole index**. A leftover ``git add`` in the
    main workspace -- the exact place this feature runs -- would therefore ride
    along into a commit whose message never mentioned it, and the "no change,
    skip" branch could turn somebody else's staging into a commit attributed to
    this instruction. Guarding here means ``-A`` is not needed to widen a
    commit, so the protection cannot be bypassed by accident.

    Refusing (rather than unstaging) is the only safe answer: the foreign
    entries are somebody's work in progress, and this worker must never touch
    them. The caller is told exactly what to fix.
    """
    staged = _staged_paths(repo)
    mine = _normalise_paths(paths)
    foreign = sorted(name for key, name in staged.items() if key not in mine)
    if foreign:
        shown = "、".join(foreign[:10])
        more = "" if len(foreign) <= 10 else f" 等 {len(foreign)} 项"
        raise CommandError(
            f"第 {index} 条 commit 只声明了 {list(paths)}，但暂存区里还有没声明的文件："
            f"{shown}{more}。git commit 会把整个暂存区一起提交 —— 先 `git restore --staged "
            f"<这些文件>` 清掉，或把它们也写进 paths，再发指令。"
        )


def _validate_paths(repo: Path, paths: Sequence[str], index: int) -> list[str]:
    """Only explicit, repository-relative paths survive this function.

    ``..`` escapes, absolute paths and drive letters are the interesting cases;
    a glob would make the commit contain files the human did not list. The
    resolved path is checked again because ``a/../../b`` can look relative and
    still leave the repository.
    """
    cleaned: list[str] = []
    for raw in paths:
        text = str(raw).strip()
        if not text:
            raise CommandError(f"第 {index} 条 commit 的 paths 里有空路径")
        if text.startswith("-"):
            raise CommandError(f"第 {index} 条 commit 的路径不能以 - 开头：{text}")
        if _GLOB_CHARS & set(text):
            raise CommandError(
                f"第 {index} 条 commit 的路径不能用通配符：{text}"
                "（指令的语义是只提交列出的文件，不做 git add -A）"
            )
        candidate = Path(text)
        if candidate.is_absolute() or candidate.drive:
            raise CommandError(f"第 {index} 条 commit 的路径必须是仓库内相对路径：{text}")
        if ".." in candidate.parts:
            raise CommandError(f"第 {index} 条 commit 的路径不能跳出仓库根：{text}")
        try:
            resolved = (repo / candidate).resolve()
        except OSError as exc:  # pragma: no cover - unreadable path
            raise CommandError(f"第 {index} 条 commit 的路径读不了：{text}（{exc}）") from exc
        if resolved == repo or not _within(resolved, repo):
            raise CommandError(f"第 {index} 条 commit 的路径跑出了仓库根：{text}")
        relative = resolved.relative_to(repo)
        if ".git" in relative.parts:
            raise CommandError(f"第 {index} 条 commit 的路径指到了 .git 里：{text}")
        if resolved.is_dir():
            # ``git add office`` sweeps the whole directory, including files the
            # human never looked at (untracked ones too). The instruction is
            # "these files", so a directory is a mistake, not a shorthand.
            raise CommandError(
                f"第 {index} 条 commit 的路径是一个目录：{text}"
                "（会把目录里所有文件都带走，包括没看过的）。请逐个列出文件。"
            )
        cleaned.append(candidate.as_posix())
    return cleaned


def _pending_files(repo: Path, paths: Sequence[str]) -> int:
    """How many files ``git add -- <paths>`` would stage (dry-run accounting)."""
    proc = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", *paths)
    return len([line for line in proc.stdout.splitlines() if line.strip()])


def _switch_branch(repo: Path, branch: str, exists: bool) -> None:
    args = (branch,) if exists else ("-c", branch)
    proc = _git(repo, "switch", *args)
    if proc.returncode != 0:
        raise CommandError(
            f"切分支 {branch} 失败：{proc.stderr.strip()[:300] or 'git switch 退出非 0'}"
        )


def _commit_with_message(repo: Path, message: str) -> subprocess.CompletedProcess[str]:
    """``git commit -F <tempfile>``: the message never passes through a shell.

    A message written on the command line breaks on Windows quoting, and a
    heredoc is not available through ``subprocess``. A temporary file keeps the
    bytes exactly as the human wrote them (UTF-8) and leaves hooks alone.
    """
    handle, name = tempfile.mkstemp(prefix="admin-command-", suffix=".txt")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(message.rstrip() + "\n")
        return _git(repo, "commit", "-F", name)
    finally:
        with contextlib.suppress(OSError):
            os.unlink(name)


def _path_is_known(repo: Path, path: str) -> bool:
    """Whether ``path`` exists in the index or in ``HEAD``'s tree.

    Used only to tell "the content did not change" from "this path did not
    match anything" -- two very different messages for the human. It cannot run
    before ``git add``: a brand-new untracked file is legitimately unknown at
    that point. That is exactly why the N1 guard lives at execution time.
    """
    if _git(repo, "ls-files", "--error-unmatch", "--", path).returncode == 0:
        return True
    tree = _git(repo, "ls-tree", "--name-only", "HEAD", "--", path)
    return tree.returncode == 0 and bool(tree.stdout.strip())


def _commit_one(repo: Path, message: str, paths: Sequence[str], index: int) -> CommitResult:
    """Add exactly ``paths`` and commit. No staged change means no empty commit."""
    # Before ``git add``: adding can only touch declared paths, so checking the
    # index first is both complete and non-destructive.
    _refuse_foreign_index(repo, paths, index)
    proc = _git(repo, "add", "--", *paths)
    if proc.returncode != 0:
        raise CommandError(f"第 {index} 条 commit 的 git add 失败：{proc.stderr.strip()[:300]}")
    if not _index_has_changes(repo, paths):
        # N1 (QA): on Windows a path with the wrong case is a *silent no-op* --
        # ``git add -- new.txt`` exits 0 while the file on disk is ``New.txt``,
        # nothing is staged, and the old code reported "提交完成 · 无变化跳过"
        # for a commit that never happened. Reporting success for a no-op is the
        # one lie this channel must never tell, so an unknown path is an error
        # instead of a skip. Checked after ``git add`` because that is the only
        # moment "nothing staged" and "nothing matched" can be distinguished.
        unknown = [path for path in paths if not _path_is_known(repo, path)]
        if unknown:
            raise CommandError(
                f"第 {index} 条 commit 的路径在仓库里找不到（注意大小写）：{'、'.join(unknown)}。"
                "git add 对大小写不符的路径会静默什么都不做，照盘上的实际名字重发一次。"
            )
        return CommitResult(message=message, paths=list(paths), skipped=True)

    proc = _commit_with_message(repo, message)
    if proc.returncode != 0:
        raise CommandError(
            f"第 {index} 条 commit 的 git commit 失败：{proc.stderr.strip()[:300] or proc.stdout.strip()[:300]}"
        )

    head = _git(repo, "rev-parse", "HEAD")
    if head.returncode != 0:  # pragma: no cover - a commit just succeeded
        raise CommandError(f"第 {index} 条 commit 读不到 HEAD：{head.stderr.strip()[:200]}")
    commit_hash = head.stdout.strip()
    names = _git(repo, "show", "--pretty=format:", "--name-only", commit_hash)
    files = len([line for line in names.stdout.splitlines() if line.strip()])
    stat = _git(repo, "show", "--stat", "--oneline", commit_hash)
    return CommitResult(
        message=message,
        paths=list(paths),
        commit=commit_hash,
        files=files,
        detail=stat.stdout.strip()[:400],
    )


def run_admin_command(
    payload: dict[str, Any],
    *,
    roots: Sequence[Path] | None = None,
    dry_run: bool | None = None,
) -> CommandReport:
    """Execute a parsed command and report every step.

    ``roots`` overrides the allow-list (tests do this); ``dry_run`` overrides
    the payload, which is how the worker's own ``--dry-run`` keeps a real
    request from touching the repository. In ``dry_run`` **nothing changes**:
    no branch switch, no add, no commit -- only the plan and the pending-file
    counts.
    """
    repo_text = str(payload.get("repo") or "").strip()
    plan_only = bool(payload.get("dry_run")) if dry_run is None else bool(dry_run)
    report = CommandReport(
        repo=repo_text,
        branch=str(payload.get("branch") or "").strip(),
        dry_run=plan_only,
    )
    try:
        if str(payload.get("action") or "") != COMMIT_ACTION:
            raise CommandError(
                f'暂不支持 action="{payload.get("action")}"，v1 只支持 "{COMMIT_ACTION}"'
            )
        repo = _resolve_repo(repo_text, roots)
        report.branch = _validate_branch(report.branch)
        _refuse_unfinished_operation(repo)
    except CommandError as exc:
        report.error = str(exc)
        return report

    report.branch_existed = _branch_exists(repo, report.branch)
    raw_commits = [item for item in payload.get("commits") or [] if isinstance(item, dict)]
    if not raw_commits:  # pragma: no cover - parse_admin_command rejects this
        report.error = "commits 必须是非空数组"
        return report

    # Static validation for **every** commit before the branch moves: a rejected
    # path must not leave the workspace switched onto a new branch (QA P2-1 --
    # "拒绝" that still ran ``git switch`` is a side effect the human did not
    # authorise, even with zero commits).
    validated: list[list[str]] = []
    for index, item in enumerate(raw_commits, 1):
        try:
            validated.append(_validate_paths(repo, item.get("paths") or [], index))
        except CommandError as exc:
            report.error = str(exc)
            return report

    # Same reasoning for the index guard: the first commit's requirement can be
    # judged here, before any side effect. Later commits are checked again right
    # before their own commit (the index is clean once a commit lands, and only
    # a hook could dirty it again).
    try:
        _refuse_foreign_index(repo, validated[0], 1)
    except CommandError as exc:
        report.error = str(exc)
        return report

    if plan_only:
        # A plan must not move HEAD either: switching branches is a change.
        for item, paths in zip(raw_commits, validated, strict=True):
            report.commits.append(
                CommitResult(
                    message=str(item.get("message") or "").strip(),
                    paths=paths,
                    files=_pending_files(repo, paths),
                )
            )
        return report

    try:
        _switch_branch(repo, report.branch, report.branch_existed)
    except CommandError as exc:
        report.error = str(exc)
        return report
    report.switched = True

    for index, (item, paths) in enumerate(zip(raw_commits, validated, strict=True), 1):
        message = str(item.get("message") or "").strip()
        try:
            report.commits.append(_commit_one(repo, message, paths, index))
        except CommandError as exc:
            # Stop here: a later commit built on a rejected path would hide the
            # reason. The already-landed commits stay in ``report.commits`` so
            # the operator can see exactly how far it got.
            report.error = str(exc)
            return report
    return report


def render_report(report: CommandReport) -> str:
    """The group message a human reads: hashes, messages, and what was skipped."""
    heading = "提交计划" if report.dry_run else "提交"
    lines: list[str] = []
    if report.error:
        lines.append(f"{heading}失败：{report.error}")
    elif report.dry_run:
        lines.append(
            f"{heading}（dry_run：没有切分支、没有 add、没有 commit，也没有记下 command_id "
            "—— 去掉 dry_run 用同一个 id 可以真跑）"
        )
    else:
        lines.append(f"{heading}完成。")

    if report.branch:
        note = ""
        if report.branch_existed is True:
            note = "（已存在）"
        elif report.branch_existed is False:
            # "had to be created" is not "was created": a refused command must
            # not claim it did anything (P3-4 -- the same inaccuracy as saying
            # "no half-finished commit" after the branch was already switched).
            if report.dry_run:
                note = "（将新建）"
            elif report.switched:
                note = "（已新建）"
            else:
                note = "（尚未新建）"
        lines.append(f"仓库：{report.repo}")
        lines.append(f"分支：{report.branch}{note}")

    for index, item in enumerate(report.commits, 1):
        if item.skipped:
            lines.append(f"{index}. 无变化跳过 · {item.message}")
        elif item.commit:
            lines.append(f"{index}. {item.commit[:8]} · {item.message} · {item.files} 个文件")
        elif report.dry_run:
            lines.append(f"{index}. {item.files} 个待提交文件 · {item.message}")
        else:
            lines.append(f"{index}. 未执行 · {item.message}")

    if report.error:
        landed = [f"{item.commit[:8]} · {item.message}" for item in report.landed]
        if landed:
            lines.append("已完成的提交：" + "；".join(landed))
        elif report.switched:
            lines.append(
                f"没有提交被创建；分支已经切到 {report.branch}（这一步已经发生，需要你处理）。"
            )
            # An execution-time failure leaves the workspace on a branch the
            # human did not ask to sit on -- the same accident class P2-1
            # closed, through another door: the next ad-hoc ``git add/commit``
            # lands there. Say how to get back. Deliberately no "rollback" /
            # "undo" wording: nothing was undone, a switch is all that
            # happened. Order matters -- deleting the checked-out branch fails,
            # so "switch away" comes first.
            if report.branch_existed is False:
                lines.append(
                    f"要回去：先 `git switch -`，再 `git branch -D {report.branch}`"
                    "（这条分支是刚建的、没有提交）。"
                )
            else:
                lines.append("要回去：`git switch -`。")
        else:
            lines.append("没有任何提交被创建，分支也没有切换。")
    return "\n".join(lines)
