"""The floor under every platform tool: what it may touch, and nothing else.

Three limits, each with a reason that has cost someone a run before:

**Path.**  Every path a tool accepts is resolved inside *this attempt's*
worktree, and every write must land inside the paths the task was authorized
for (``exclusive_paths``).  A harness gets a private worktree precisely so
that two members cannot edit one file; a platform tool that honoured
``../../other-member`` would hand that guarantee away.  Absolute paths and
``..`` escapes are refused outright rather than clamped: a refusal the model
can read is recoverable, a silently redirected write is not.

**Credentials.**  ``data/*.key`` holds this deployment's tokens and ``.env``
holds providers' keys.  A coding task never needs to read either, so both are
refused by *name* -- not by content inspection, because that would fail open
on the first file we did not think of.

**Commands.**  ``run_command`` is the one tool that is not bounded by the
path floor (a compiler writes wherever it likes), so it carries the same
structural lists the author's workbench uses: destructive verbs, network
exfiltration, and the interpreter escapes around them.  ``git`` *write*
subcommands are refused separately: the platform makes the commit, and a
harness that could ``push`` would move code off the machine.

Every refusal here is a sentence a model can act on -- "path is not yours,
write inside ``docs/``" -- never a bare code.  The whole point of routing a
refusal back into the transcript is that the next attempt is better.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.adapter.scope import path_in_scope


class FloorRefused(ValueError):
    """A tool call that must not happen; the message is the correction."""


#: Files by name that a coding task never needs and that leak this deployment.
_CREDENTIAL_NAMES = re.compile(
    r"(^|/)(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|id_rsa.*|id_ed25519.*|"
    r".*\.(key|pem|pfx|p12|keystore)|credentials(\.json)?|\.git-credentials)$",
    re.IGNORECASE,
)

#: Directories a tool must not read at all.  ``.git`` is here because the
#: platform owns the commit; a tool that could edit ``.git`` could forge one.
_DENIED_DIRS = (".git", ".hg", ".svn")

#: Destructive or exfiltrating command shapes (ported from the author's
#: workbench, which learned each line from a real incident).
_DANGEROUS_PATTERNS = (
    "rm -rf", "rm -r ", "rm -fr", "del /s", "del /q", "rd /s", "rmdir /s",
    "rmdir /q", "format ", "shutdown", "mkfs", "dd if=", "> /dev/sd", "> /dev/null",
    "git clean", "git reset --hard", "git push -f", "git push --force",
    "chmod -r", "chmod 777", "chmod 666", ":(){", "fork bomb",
    "remove-item", "ri -recurse", "del -recurse", "format-volume", "clear-disk",
    "invoke-expression", "iex ", "iex(", "downloadstring", "downloadfile",
)

#: First words that are always refused: mass deletion, network transfer,
#: persistence.  Interpreters are deliberately **not** here -- running
#: ``python x.py`` or ``npm run build`` is the work; the patterns above catch
#: the destructive thing such a script would have to spell out.
_BLOCKED_COMMANDS = {
    "rm", "del", "erase", "rd", "rmdir", "format", "shutdown", "restart", "reboot",
    "mkfs", "fdisk", "diskpart", "dd", "chkdsk", "chown", "chmod", "cacls", "icacls",
    "shred", "wipe", "cipher",
    "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm",
    "nc", "netcat", "ncat", "telnet", "ssh", "scp", "sftp", "ftp", "certutil",
    "reg", "wmic", "mshta", "rundll32", "schtasks", "wsl", "taskkill", "net",
}

#: ``git`` subcommands that write.  ``status``/``log``/``diff``/``show`` stay.
_GIT_WRITE_SUBCOMMANDS = {
    "commit", "push", "merge", "rebase", "revert", "reset", "clean",
    "checkout", "switch", "apply", "cherry-pick", "stash", "am",
    "update-ref", "worktree", "restore", "rm", "filter-branch", "replace",
}
_GIT_OPTIONS_WITH_VALUE = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix", "--list-submodules",
}


def _first_command_word(low: str) -> str:
    text = low.strip()
    text = re.sub(r"^(?:cmd(?:\.exe)?\s+/[ck]\s+)", "", text)
    text = re.sub(r"^(?:[a-z_][a-z0-9_]*=[^ ]*\s+)+", "", text)
    text = re.sub(r"^call\s+", "", text)
    match = re.match(r'"?([^"\s]+)"?', text)
    if not match:
        return ""
    word = os.path.basename(match.group(1)).lower()
    return word[:-4] if word.endswith(".exe") else word


def _git_subcommand(low: str) -> str:
    words = re.sub(r"\s+", " ", low).strip().split(" ")
    if not words or _first_command_word(low) != "git":
        return ""
    index = 1
    while index < len(words):
        word = words[index]
        if word in _GIT_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if word.startswith("-"):
            index += 1
            continue
        return word
    return ""


def command_refusal(command: str) -> str | None:
    """Why this command must not run, or ``None`` when it may."""
    low = re.sub(r"\s+", " ", str(command or "").lower()).strip()
    if not low:
        return "命令是空的。"
    for pattern in _DANGEROUS_PATTERNS:
        if pattern in low:
            return (
                f"命令含「{pattern.strip()}」，属于会毁数据或往外传东西的形状，平台不放行。"
                "改一条只动本任务工作树、只读或只写的命令。"
            )
    first = _first_command_word(low)
    if first == "git":
        sub = _git_subcommand(low)
        if sub in _GIT_WRITE_SUBCOMMANDS:
            return (
                f"`git {sub}` 会改仓库或把代码推走，改由平台在任务完成时提交。"
                "这里只能跑只读的 git（status / log / diff / show / ls-files / rev-parse）。"
            )
    if first in _BLOCKED_COMMANDS:
        return (
            f"`{first}` 在平台的拒绝名单里（批量删除、外网传输或系统操控）。"
            "用工具做文件操作，用构建/测试命令做验证。"
        )
    return None


def _normalize_relative(raw: str) -> str:
    """A relative, forward-slash, escape-free spelling of an input path."""
    text = str(raw or "").strip().strip("'\"")
    if not text:
        return ""
    text = text.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", text) or text.startswith("//") or text.startswith("/"):
        raise FloorRefused(
            f"路径「{text}」是绝对路径。工作树里的路径一律写相对路径，例如 `docs/note.md`。"
        )
    parts: list[str] = []
    for piece in text.split("/"):
        if piece in ("", "."):
            continue
        if piece == "..":
            if not parts:
                raise FloorRefused(
                    f"路径「{text}」试图走出工作树。只能在本任务的工作树里读写。"
                )
            parts.pop()
            continue
        parts.append(piece)
    return "/".join(parts)


@dataclass(frozen=True)
class Floor:
    """The bounds one attempt's tools live inside.

    ``root`` is the worktree the platform provisioned for *this* attempt;
    ``paths`` is what the task's plan authorized (``exclusive_paths``).
    """

    root: Path
    paths: tuple[str, ...] = ()
    #: A plan may authorize nothing in particular (an empty list means "no
    #: scope declared").  Then the worktree itself is the bound, which is
    #: still stronger than the CLI harnesses get.
    require_scope: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())
        object.__setattr__(self, "paths", tuple(str(p) for p in self.paths if str(p).strip()))

    # ------------------------------------------------------------------ paths

    def relative(self, raw: str) -> str:
        return _normalize_relative(raw)

    def resolve(self, raw: str, *, write: bool) -> Path:
        """The real path for ``raw``, or a refusal the model can act on."""
        relative = self.relative(raw)
        if not relative:
            if write:
                raise FloorRefused("没有给路径。写文件要给一个工作树里的相对路径。")
            return self.root
        lowered = relative.lower()
        head = lowered.split("/", 1)[0]
        if head in _DENIED_DIRS:
            raise FloorRefused(
                f"`{relative}` 在版本库元数据目录里，平台自己管提交，工具不进去。"
            )
        if _CREDENTIAL_NAMES.search("/" + lowered):
            raise FloorRefused(
                f"`{relative}` 是凭据类文件（密钥 / 环境变量），工具一律不读不写。"
            )
        candidate = (self.root / relative).resolve()
        if candidate != self.root and not _is_under(candidate, self.root):
            raise FloorRefused(
                f"`{relative}` 解析后落在工作树之外（软链接或联接点），工具不进去。"
            )
        if write:
            self.check_scope(relative)
        return candidate

    def check_scope(self, relative: str) -> None:
        """A write must be inside what the plan authorized for this task."""
        if not self.paths:
            if self.require_scope:
                raise FloorRefused(
                    "这条任务没有授权任何落点，平台不允许凭猜测写文件；"
                    "先做只读的确认，把结论写进交活说明。"
                )
            return
        if path_in_scope(relative, list(self.paths)):
            return
        allowed = "、".join(f"`{item}`" for item in self.paths[:6])
        raise FloorRefused(
            f"`{relative}` 不在本任务授权的落点里。授权范围：{allowed}。"
            "要改别处，请在交活说明里写清为什么，由人决定改方案。"
        )

    def listing(self) -> list[str]:
        """Every file the worktree currently has, as POSIX relative paths."""
        out: list[str] = []
        for current, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if d.lower() not in _DENIED_DIRS and d != "__pycache__"]
            base = Path(current)
            for name in files:
                relative = (base / name).relative_to(self.root).as_posix()
                if _CREDENTIAL_NAMES.search("/" + relative.lower()):
                    continue
                out.append(relative)
        return sorted(out)


def _is_under(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def describe(floor: Floor) -> Iterable[str]:
    """Lines a model reads to know where it may write (used in the prompt)."""
    yield f"工作树：{floor.root.as_posix()}"
    if floor.paths:
        yield "本任务授权的落点：" + "、".join(f"`{item}`" for item in floor.paths)
    else:
        yield "本任务没有声明落点清单：只读随便看，写之前先想清楚放在哪。"
