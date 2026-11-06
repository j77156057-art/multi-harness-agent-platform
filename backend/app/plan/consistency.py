"""方案批准前的一致性检查：goal 提到了什么，契约写了什么。

2026-11-05 的 e16 不是"成员乱写文件"那么简单：goal 原文写着
``e.g., via conftest.py or autouse fixture``，而 ``exclusive_paths`` 里没有
``tests/conftest.py``。成员照着 goal 做，提交时被范围闸门拒掉，整轮重跑。
契约自己自相矛盾，代价却由执行者承担。

这里只做一件事：把 goal 里**带动作语境**的路径样 token 抽出来，跟这条任务
的 ``exclusive_paths`` 对一遍，把"提到了但没写进契约"的路径摆给批准人看。

两条纪律：

* **宁可漏报，不要误报。** 批准人一旦被训练成"看到提示就点确定"，提示就
  等于没有。因此 token 只有在它前面（同一行、约 70 字符内）出现明确动作词时
  才算数：``via X``、``在 X 里``、``修改 X``、``create X`` …
* **只提示，不硬拒。** 自然语言引用天然有歧义（"像 index.html 那样"并不是
  要改它），硬拒会把一次误判变成一次无法继续的批准。真正能确定"跑不起来"
  的检查（合并 profile 缺入口文件）才在 approve 阶段硬拒，见
  ``app/api/routes.py::_plan_profile_block``。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from app.adapter.scope import is_runtime_artifact, path_in_scope

#: 同一个动作词到 token 之间最多隔多少字符。CJK 一句话的信息密度高，70 个
#: 字符足够覆盖 ``在 tests/ 目录下的 conftest.py 里`` 这类长句。
_WINDOW = 70

#: 明确"要动这个路径"的动作词。英文按词边界匹配，中文直接子串。
_ACTION_EN = (
    "via",
    "in",
    "into",
    "under",
    "inside",
    "using",
    "use",
    "create",
    "add",
    "write",
    "modify",
    "edit",
    "update",
    "change",
    "replace",
    "touch",
    "put",
    "place",
    "save",
    "generate",
    "produce",
    "cover",
    "include",
    "through",
)
_ACTION_ZH = (
    "通过",
    "修改",
    "更新",
    "新增",
    "创建",
    "写入",
    "放到",
    "放进",
    "覆盖",
    "生成",
    "输出",
)
#: ``在 X 里`` / ``在 X 中``：单看一个「在」会把「存在」「现在」「所在」全算进来，
#: 所以必须同时满足"前面有 在"且"后面有方位词"。这是噪声与覆盖之间唯一
#: 站得住的折中（要求里明确点了「在 X 里」这个句式）。
_LOCATIVE_BEFORE = "在"
_LOCATIVE_AFTER = ("里", "中", "下", "内", "目录")
#: 出现这些词就整句跳过：否定/禁止语境里出现的路径多半不是要动它。
_NEGATION = (
    "不要",
    "不用",
    "无需",
    "禁止",
    "不得",
    "别",
    "未",
    "没有",
    "不属于",
    "not",
    "don't",
    "avoid",
    "never",
    "without",
)

_FILE_SUFFIXES = (
    "py",
    "pyi",
    "js",
    "jsx",
    "mjs",
    "cjs",
    "ts",
    "tsx",
    "html",
    "htm",
    "css",
    "scss",
    "json",
    "yaml",
    "yml",
    "md",
    "rst",
    "txt",
    "toml",
    "ini",
    "cfg",
    "sh",
    "ps1",
    "bat",
    "go",
    "rs",
    "java",
    "kt",
    "rb",
    "php",
    "sql",
    "vue",
    "svelte",
    "xml",
    "svg",
)

_FILE_RE = re.compile(
    r"(?<![\w./~-])"
    r"((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]*[A-Za-z0-9_-]"
    r"\.(?:" + "|".join(_FILE_SUFFIXES) + r"))"
    r"(?![\w/])"
)

_DIR_RE = re.compile(r"(?<![\w./~-])((?:[A-Za-z0-9_.-]+/)+)(?=[\s，。、；;:）)]|$)")


def _has_action(before: str, after: str = "") -> bool:
    lowered = before.lower()
    for marker in _NEGATION:
        if marker in lowered:
            return False
    if any(marker in before for marker in _ACTION_ZH):
        return True
    if _LOCATIVE_BEFORE in before and any(
        marker in after[:10] for marker in _LOCATIVE_AFTER
    ):
        return True
    return any(
        re.search(rf"(?<![a-z]){re.escape(marker)}(?![a-z])", lowered)
        for marker in _ACTION_EN
    )


def extract_mentioned_paths(text: str) -> list[str]:
    """Paths the text asks for, in the order they appear (deduplicated)."""
    found: list[str] = []
    for line in str(text or "").splitlines():
        for match in _FILE_RE.finditer(line):
            token = match.group(1).replace("\\", "/").strip("./")
            if not token or is_runtime_artifact(token):
                continue
            start = max(0, match.start() - _WINDOW)
            if not _has_action(line[start : match.start()], line[match.end() : match.end() + 10]):
                continue
            if token not in found:
                found.append(token)
        for match in _DIR_RE.finditer(line):
            token = match.group(1).replace("\\", "/").strip("./").rstrip("/")
            if not token or is_runtime_artifact(token):
                continue
            start = max(0, match.start() - _WINDOW)
            if not _has_action(line[start : match.start()], line[match.end() : match.end() + 10]):
                continue
            if token not in found:
                found.append(token)
    return found


def _in_contract(path: str, patterns: Sequence[str]) -> bool:
    if path_in_scope(path, list(patterns)):
        return True
    # goal 里写的是裸文件名（``via conftest.py``），契约写的是完整落点
    # （``tests/conftest.py``）：同名即视为覆盖，避免把同一件事报两遍。
    if "/" not in path:
        return any(str(pattern).replace("\\", "/").rsplit("/", 1)[-1] == path for pattern in patterns)
    # goal 里写的是目录（``在 tests/ 里``），契约覆盖了它下面的文件即可。
    return any(str(pattern).replace("\\", "/").startswith(path + "/") for pattern in patterns)


def goal_scope_warnings(tasks: Sequence[Mapping[str, object]]) -> list[str]:
    """Paths mentioned by a task goal but absent from its ``exclusive_paths``.

    Returns human-readable lines. Empty list means "nothing to review", not
    "the goal was parsed perfectly" -- an unrecognized phrasing is dropped on
    purpose (see the module docstring).
    """
    warnings: list[str] = []
    for index, task in enumerate(tasks, start=1):
        raw_paths = task.get("exclusive_paths")
        if not isinstance(raw_paths, list | tuple) or not raw_paths:
            continue
        patterns = [str(item) for item in raw_paths]
        task_id = str(task.get("id") or task.get("task_id") or f"#{index}")
        goal = str(task.get("goal") or "")
        for path in extract_mentioned_paths(goal):
            if _in_contract(path, patterns):
                continue
            line = (
                f"task {task_id}: goal mentions {path!r}, which is not in its"
                f" exclusive_paths ({', '.join(patterns)}); if the work really"
                " touches it, add it to the contract -- the scope gate will"
                " otherwise refuse the submission"
            )
            if line not in warnings:
                warnings.append(line)
    return warnings
