"""The platform's tool table: what a Harness can do when it has no tools.

One design decision drives everything here.  A tool call arrives as **text**,
not as a ``tool_calls`` array: the whole point of this package is to work with
a model that cannot ask for a tool.  So every tool accepts its arguments in
two shapes --

* an object (``{"path": "docs/a.md", "content": "..."}``), which any model
  that can write JSON produces;
* one string, either a JSON object spelled as a string, or the tool's own
  one-shot form (``create_file`` takes the path on the first line and the
  content after it).

That is the same "universal ``input``" convention the author's workbench uses,
and for the same measured reason: text-protocol models get the *shape* of the
call right long before they get a nested object right.

Every tool returns a **string a model can read**: what happened, what to do
next, and the exact reason when something was refused.  A refusal is data,
never an exception the loop cannot explain -- the model gets one more turn to
put it right.

Nothing here is creative work: the tools are the boring five (look, read,
search, write, edit) plus a bounded ``run_command`` and read-only ``git``.
A Harness that cannot call tools does not need a bigger toolbox, it needs a
way to touch its own worktree at all.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app.agenttools.floor import Floor, FloorRefused, command_refusal

#: Bounded observations: a prompt that grows without limit is how a 26k-token
#: work order became a 645k-token one in this project's own history.
MAX_READ_LINES = 400
MAX_READ_CHARS = 16000
MAX_GREP_HITS = 80
MAX_LIST = 400
MAX_COMMAND_CHARS = 6000
DEFAULT_COMMAND_TIMEOUT = 120


class ToolRefused(ValueError):
    """The call could not run; the message is what the model reads back."""


@dataclass(frozen=True)
class Tool:
    name: str
    summary: str
    usage: str
    run: Callable[[dict[str, Any]], str]
    writes: bool = False

    def call(self, raw: Any) -> str:
        """Run with arguments in either accepted shape (object, or text)."""
        return str(self.run(_coerce(raw)))


# --------------------------------------------------------------------- inputs


def _coerce(raw: Any) -> dict[str, Any]:
    """Read the two accepted argument shapes into one mapping."""
    if isinstance(raw, dict):
        return dict(raw)
    text = "" if raw is None else str(raw).strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolRefused(
                "输入看起来是 JSON 但读不开（"
                f"{exc.msg} 在第 {exc.lineno} 行）。给一个对象，或按工具说明的文本写法。"
            ) from exc
        if isinstance(parsed, dict):
            return parsed
        raise ToolRefused("输入的 JSON 必须是一个对象，不是数组或标量。")
    return {"_text": text}


def _text(args: dict[str, Any], *names: str) -> str:
    for name in names:
        value = args.get(name)
        if value is not None:
            return str(value)
    return ""


def _require(args: dict[str, Any], name: str) -> str:
    value = _text(args, name)
    if not value.strip():
        raise ToolRefused(f"缺少 `{name}`。")
    return value


def _clip_lines(text: str, *, limit: int, name: str) -> str:
    lines = text.splitlines()
    if len(lines) <= limit:
        return text
    kept = "\n".join(lines[:limit])
    return (
        f"{kept}\n...（共 {len(lines)} 行，这里只回到第 {limit} 行；"
        f"要看后面，用 read_file 带 start/end 参数分段读 `{name}`）"
    )


def _tail_head(text: str, limit: int) -> str:
    """Keep both ends: a compiler puts the reason in the last lines."""
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit - len(head)) :]
    return f"{head}\n...（中间 {len(text) - limit} 个字符略）...\n{tail}"


# ---------------------------------------------------------------------- tools


def build_registry(floor: Floor, *, read_only: bool = False) -> dict[str, Tool]:
    """The table for one attempt, closed over the floor it may not exceed.

    ``read_only`` is what a *review* gets: a reviewer looks and reports, it
    does not edit.  The write and run tools stay in the table on purpose --
    a model that cannot see them keeps inventing them -- but they answer with
    the reason instead of acting.
    """

    def list_files(args: dict[str, Any]) -> str:
        prefix = _text(args, "path", "dir", "_text").strip().strip("/")
        names = floor.listing()
        if prefix:
            names = [name for name in names if name.startswith(prefix)]
        if not names:
            where = f"`{prefix}` 下" if prefix else "工作树里"
            return f"{where}没有可读的文件（凭据类文件不计）。"
        shown = names[:MAX_LIST]
        more = "" if len(names) <= MAX_LIST else f"\n...（共 {len(names)} 个，只列前 {MAX_LIST} 个）"
        return "\n".join(shown) + more

    def read_file(args: dict[str, Any]) -> str:
        raw = _text(args, "path", "_text")
        if not raw.strip():
            raise ToolRefused("缺少 `path`。")
        path = floor.resolve(raw, write=False)
        if path.is_dir():
            return f"`{raw}` 是目录。用 list_files 看目录内容。"
        if not path.is_file():
            return (
                f"`{raw}` 不存在。先用 list_files 看真实有哪些文件，别猜路径。"
            )
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"读 `{raw}` 失败：{exc}"
        start = _int_or(args, "start", 1)
        end = _int_or(args, "end", 0)
        lines = text.splitlines()
        if start > 1 or end:
            stop = end if end else len(lines)
            lines = lines[max(start - 1, 0) : stop]
            text = "\n".join(lines)
            header = f"`{raw}` 第 {start}~{stop if end else len(text.splitlines())} 行：\n"
        else:
            header = f"`{raw}` （{len(lines)} 行）：\n"
        body = _clip_lines(text, limit=MAX_READ_LINES, name=raw)[:MAX_READ_CHARS]
        return header + body

    def grep(args: dict[str, Any]) -> str:
        pattern = _require(args, "pattern")
        scope = _text(args, "path", "glob").strip()
        try:
            regex = re.compile(pattern)
        except re.error as exc:
            raise ToolRefused(f"正则读不开：{exc}。给一个能编译的正则，或改用纯文本。") from exc
        hits: list[str] = []
        for name in floor.listing():
            if scope and not (name.startswith(scope) or Path(name).name == scope):
                continue
            try:
                text = (floor.root / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append(f"{name}:{number}: {line.strip()[:200]}")
                    if len(hits) >= MAX_GREP_HITS:
                        break
            if len(hits) >= MAX_GREP_HITS:
                break
        if not hits:
            return f"没有匹配 `{pattern}` 的行。"
        return "\n".join(hits) + (
            "" if len(hits) < MAX_GREP_HITS else f"\n...（到 {MAX_GREP_HITS} 条就停了）"
        )

    def create_file(args: dict[str, Any]) -> str:
        raw = _text(args, "path")
        content = args.get("content")
        if content is None:
            content = args.get("text")
        if content is None:
            body = _text(args, "_text")
            head, _, rest = body.partition("\n")
            raw = raw or head
            content = rest
        if not str(raw or "").strip():
            raise ToolRefused(
                "缺少 `path`。文本写法：第一行写路径，后面全是文件内容。"
            )
        path = floor.resolve(str(raw), write=True)
        text = "" if content is None else str(content)
        if path.exists() and path.read_text(encoding="utf-8", errors="replace") == text:
            return f"`{raw}` 已经是这个内容，没动它。"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return f"已写入 `{raw}`（{len(text.splitlines())} 行）。"

    def apply_edit(args: dict[str, Any]) -> str:
        raw = _text(args, "path")
        old = args.get("old")
        new = args.get("new")
        if old is None or new is None:
            body = _text(args, "_text")
            block = _text_block(body)
            raw = raw or block["path"]
            old, new = block["old"], block["new"]
        if not str(raw or "").strip():
            raise ToolRefused("缺少 `path`。")
        path = floor.resolve(str(raw), write=True)
        if not path.is_file():
            return f"`{raw}` 不存在。新建文件用 create_file。"
        text = path.read_text(encoding="utf-8")
        needle = str(old)
        if not needle:
            raise ToolRefused("`old` 是空的：替换要以一段真实存在的原文为锚。")
        count = text.count(needle)
        if count == 0:
            return (
                f"`{raw}` 里找不到要替换的那段原文。先把文件读一遍，"
                "连同缩进和标点一字不差地抄进 `old`。"
            )
        if count > 1:
            return (
                f"要替换的原文在 `{raw}` 里出现 {count} 次，改不准是哪一处。"
                "把 `old` 扩到只有一处唯一匹配（多带几行上下文）。"
            )
        path.write_text(text.replace(needle, str(new), 1), encoding="utf-8", newline="\n")
        return f"已改 `{raw}`（替换 1 处，{len(needle)} 字 → {len(str(new))} 字）。"

    def run_command(args: dict[str, Any]) -> str:
        command = _text(args, "cmd", "command", "_text").strip()
        if not command:
            raise ToolRefused("缺少 `cmd`。")
        refusal = command_refusal(command)
        if refusal:
            return f"拒绝执行：{refusal}"
        timeout = _int_or(args, "timeout", DEFAULT_COMMAND_TIMEOUT)
        timeout = max(5, min(timeout, 600))
        try:
            completed = subprocess.run(
                command,
                cwd=floor.root,
                shell=True,
                capture_output=True,
                timeout=timeout,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return (
                f"命令超过 {timeout} 秒被终止。要更长的预算就把 `timeout` 调大"
                "（上限 600 秒），或把活拆小。"
            )
        except OSError as exc:
            return f"命令没能启动：{exc}"
        output = (completed.stdout or "") + (completed.stderr or "")
        head = f"[exit {completed.returncode}]"
        if not output.strip():
            return head + "（命令已执行，没有输出）"
        return head + "\n" + _tail_head(output, MAX_COMMAND_CHARS)

    def git_read(args: dict[str, Any]) -> str:
        sub = _require(args, "sub")
        allowed = {"status", "log", "diff", "show", "ls-files", "rev-parse", "branch"}
        if sub not in allowed:
            return (
                f"`git {sub}` 不是这里能跑的只读子命令。可用："
                + "、".join(sorted(allowed))
            )
        rest = _text(args, "args", "_text")
        parts = [sub, *[piece for piece in rest.split() if piece != sub]]
        try:
            completed = subprocess.run(
                ["git", *parts],
                cwd=floor.root,
                capture_output=True,
                timeout=60,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"`git {sub}` 没能跑起来：{exc}"
        output = (completed.stdout or "") + (completed.stderr or "")
        return _tail_head(output.strip() or "（没有输出）", 4000)

    def finish(args: dict[str, Any]) -> str:
        return _text(args, "summary", "final", "_text")

    table = {
        "list_files": Tool(
            name="list_files",
            summary="列出工作树里的文件（可给目录前缀）",
            usage='{"path": "docs"} —— 不写参数就是全部',
            run=list_files,
        ),
        "read_file": Tool(
            name="read_file",
            summary="读一个文件的正文（可分段）",
            usage='{"path": "docs/a.md", "start": 1, "end": 80} —— start/end 可省',
            run=read_file,
        ),
        "grep": Tool(
            name="grep",
            summary="在文件里按正则搜索，返回 `文件:行号: 行`",
            usage='{"pattern": "def main"} —— 可选 "path" 限定目录前缀',
            run=grep,
        ),
        "create_file": Tool(
            name="create_file",
            summary="新建或整体覆盖一个文件",
            usage='{"path": "docs/a.md", "content": "全文"} ；文本写法：第一行路径，其后为内容',
            run=create_file,
            writes=True,
        ),
        "apply_edit": Tool(
            name="apply_edit",
            summary="按原文精确替换（原文必须唯一命中）",
            usage='{"path": "docs/a.md", "old": "原文", "new": "新文"}',
            run=apply_edit,
            writes=True,
        ),
        "run_command": Tool(
            name="run_command",
            summary="在工作树里跑一条命令（构建、测试、脚本）",
            usage='{"cmd": "python -m pytest -q", "timeout": 120}',
            run=run_command,
        ),
        "git_read": Tool(
            name="git_read",
            summary="只读的 git：status / log / diff / show / ls-files / rev-parse / branch",
            usage='{"sub": "status"}',
            run=git_read,
        ),
        "finish": Tool(
            name="finish",
            summary="交活：写下你做了什么、依据是什么",
            usage='{"summary": "改了 docs/a.md 的两处，因为…"}',
            run=finish,
        ),
    }
    if not read_only:
        return table
    return {name: _frozen(tool) for name, tool in table.items()}


REVIEW_REFUSAL = (
    "这次是**审阅**：只看不改。写文件、改文件、跑命令一律不执行。"
    "把你要说的写进 `finish` 的 summary：哪里不清楚、哪里做不到、验收怎么检查。"
)


def _frozen(tool: Tool) -> Tool:
    """The same tool, minus the ability to act."""
    if not (tool.writes or tool.name == "run_command"):
        return tool
    return Tool(
        name=tool.name,
        summary=tool.summary + "（本次审阅不可用）",
        usage=tool.usage,
        run=lambda _args: REVIEW_REFUSAL,
        writes=tool.writes,
    )


def tool_plans(tools: dict[str, Tool]) -> list[str]:
    """The table as prompt lines: name, usage, one-line summary."""
    lines: list[str] = []
    for tool in tools.values():
        lines.append(f"- `{tool.name}` {tool.summary}\n  输入：{tool.usage}")
    return lines


def _int_or(args: dict[str, Any], name: str, default: int) -> int:
    value = args.get(name)
    if value is None or value == "":
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


_OLD_MARK = re.compile(r"^<{3,}\s*OLD\s*$", re.MULTILINE)
_NEW_MARK = re.compile(r"^={3,}\s*$", re.MULTILINE)
_END_MARK = re.compile(r"^>{3,}\s*NEW\s*$", re.MULTILINE)


def _text_block(body: str) -> dict[str, str]:
    """The one-shot edit form, for a model that will not write nested JSON:

        path: docs/a.md
        <<<<<<< OLD
        old text
        =======
        new text
        >>>>>>> NEW
    """
    path = ""
    first, _, rest = body.partition("\n")
    match = re.match(r"^\s*path\s*[:：]\s*(.+)$", first, re.IGNORECASE)
    if match:
        path = match.group(1).strip()
    else:
        rest = body
    old_match = _OLD_MARK.search(rest)
    if not old_match:
        raise ToolRefused(
            "读不出要替换的原文。用对象写法 "
            '{"path": "...", "old": "...", "new": "..."}；'
            "或用文本写法：`path: 文件`，然后 `<<<<<<< OLD` / `=======` / `>>>>>>> NEW` 三段。"
        )
    tail = rest[old_match.end() :]
    new_match = _NEW_MARK.search(tail)
    if not new_match:
        raise ToolRefused("文本写法里缺 `=======` 这一行（它上面是原文，下面是新文）。")
    old = tail[: new_match.start()]
    after = tail[new_match.end() :]
    end_match = _END_MARK.search(after)
    if not end_match:
        raise ToolRefused("文本写法里缺 `>>>>>>> NEW` 这一行（新文到哪里为止）。")
    return {
        "path": path,
        "old": _strip_edges(old),
        "new": _strip_edges(after[: end_match.start()]),
    }


def _strip_edges(text: str) -> str:
    value = text
    if value.startswith("\n"):
        value = value[1:]
    if value.endswith("\n"):
        value = value[:-1]
    return value
