"""One decision point for conditional experience injection.

The earlier layer injected every confirmed slot verbatim. Paired runs showed the
cost of that default: an old value could be read as an instruction and copied
into the new dispatch. The gate fixes the trigger, not the wording: injection
happens only when the dispatch itself is missing a field (path, count, banned
word list, evidence sources) **and** a confirmed experience names that same
dimension. Only the *check type* is injected -- never the old value, the old
slot text or the source task id.

Everything is decided here so no call site can drift: ``decide`` is pure and
deterministic, and returns a trace saying why it fired or did not.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

#: The only dimensions this gate knows. Anything else is never injected.
DIMENSIONS = ("path", "count", "banned_words", "evidence_sources")

_PATH_TOKEN = re.compile(
    r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:md|txt|log|json|py|js|ts|tsx|css|html)(?![\w.-])",
    re.I,
)

_COUNT_TYPE = re.compile("映射表|表格|清单|检查项|检查|步骤|待补项|列名|列固定|条数")
_QUANTITY = re.compile(
    r"(?:恰好|只|仅|共)?\s*(?:\d+|两|二|三|四|五|六|七|八|九|十|多)\s*(?:个|条|列|项|种|份|步|行)"
)
_EXACTLY_ONE = ("不得创建其他文件", "只创建")

_BAN_CLAUSE = re.compile("不得出现|不能出现|不得使用|不能使用|不得写|不得包含|禁用词|禁词")
_PER_ITEM = re.compile("逐条|逐项|分别|每条|每项|单独|一一")

_SOURCE_NOUN = re.compile("来源|依据文件")
_VERIFICATION = re.compile(
    "实际存在|可核查|可核实|核实|章节|段落|不得泛指|不能泛指|具体路径|确证"
)

#: Topic is read from root_cause/action only. The list is ordered: the first
#: match wins, so a source-inventory lesson is not mistaken for a path lesson.
_TOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("source_inventory", re.compile("来源清单|来源文件|实际存在|具体路径|章节|段落|不能替代")),
    ("banned_words", re.compile("禁用词|禁词|不得出现|不能出现")),
    ("table_structure", re.compile("映射表|表格|列固定|列名")),
    ("diff_scope", re.compile("可读的实际 diff|实际 diff|readable patch|readable diff|diff")),
    ("delivery_path", re.compile("路径不明|路径不明确|交付路径|产出物路径|文件位置|required new file|exclusive path")),
    ("step_count", re.compile("步骤条数|启动步骤|恰好.{0,4}步")),
    ("log_path", re.compile("日志路径|日志")),
    ("entry_preserved", re.compile("既有入口|保留.{0,6}入口|入口可能被删除")),
    ("check_set", re.compile(r"F\d|检查项|待补项|清单")),
    ("failure_path", re.compile("失败路径|失败分支|异常路径|异常分支")),
    ("task_id_format", re.compile("任务 id|任务标识|task id")),
)

_TOPIC_DIMENSION: dict[str, str | None] = {
    "table_structure": "count",
    "check_set": "count",
    "step_count": "count",
    "banned_words": "banned_words",
    "source_inventory": "evidence_sources",
    "diff_scope": "path",
    "delivery_path": "path",
    "log_path": "path",
    "entry_preserved": "path",
    "failure_path": None,
    "task_id_format": None,
}

#: Fixed sentences. They name a check type only: no old value, no slot text, no
#: source task id. Adding one is the only way the prompt can change.
CHECK_TYPE_LINES: dict[str, str] = {
    "path": (
        "- check_type: path -- decide the exact deliverable file path(s) for this"
        " dispatch and put them in exclusive_paths; do not leave the path to be"
        " asked later."
    ),
    "count": (
        "- check_type: count -- this dispatch names a countable deliverable but no"
        " explicit quantity; state the exact number (and the items) in acceptance."
    ),
    "banned_words": (
        "- check_type: banned_words -- the dispatch lists forbidden words;"
        " acceptance must require them to be checked one by one"
        " (逐条/逐项/分别), not as one blanket phrase."
    ),
    "evidence_sources": (
        "- check_type: evidence_sources -- the dispatch names source files;"
        " acceptance must require each named source to actually exist"
        " (可核查), with its specific section or passage, and must forbid"
        " generic substitutes."
    ),
}


def topic_for(record: dict[str, Any]) -> str | None:
    """Classify a record's lesson topic from its cause/action text only."""
    if str(record.get("kind") or "") == "merged":
        return None
    text = f"{record.get('root_cause') or ''}\n{record.get('action') or ''}"
    if not text.strip():
        return None
    for topic, pattern in _TOPIC_PATTERNS:
        if pattern.search(text):
            return topic
    return None


def dimension_for(record: dict[str, Any]) -> str | None:
    topic = topic_for(record)
    return _TOPIC_DIMENSION.get(topic) if topic else None


def _path_tokens(text: str) -> list[str]:
    return list(dict.fromkeys(match.group(0) for match in _PATH_TOKEN.finditer(text)))


def _missing_dimensions(text: str) -> tuple[list[str], dict[str, bool], dict[str, str]]:
    """Return missing dimensions, the raw signals and one reason per dimension."""
    paths = _path_tokens(text)
    count_type = bool(_COUNT_TYPE.search(text))
    quantity = bool(_QUANTITY.search(text))
    exactly_one = any(marker in text for marker in _EXACTLY_ONE)
    banned_clause = bool(_BAN_CLAUSE.search(text))
    per_item = bool(_PER_ITEM.search(text))
    source_named = len(paths) >= 2 or bool(_SOURCE_NOUN.search(text))
    verification = bool(_VERIFICATION.search(text))

    reasons: dict[str, str] = {}
    missing: list[str] = []

    if not paths:
        missing.append("path")
        reasons["path"] = "dispatch carries no explicit deliverable path"
    if count_type and not quantity and not exactly_one:
        missing.append("count")
        reasons["count"] = "dispatch names a countable deliverable but no explicit quantity"
    if banned_clause and not per_item:
        missing.append("banned_words")
        reasons["banned_words"] = "dispatch lists forbidden words but no per-item check wording"
    if source_named and not verification:
        missing.append("evidence_sources")
        reasons["evidence_sources"] = (
            "dispatch names source files but no verification-of-existence wording"
        )

    signals = {
        "path_token_count": len(paths),
        "has_count_type": count_type,
        "has_quantity": quantity,
        "exactly_one_file": exactly_one,
        "banned_clause": banned_clause,
        "per_item_check": per_item,
        "source_named": source_named,
        "verification_wording": verification,
    }
    return missing, signals, reasons


def decide(*, dispatch_text: str, items: list[dict[str, Any]], enabled: bool = True) -> dict[str, Any]:
    """Decide which check types, if any, enter the administrator prompt.

    ``items`` are ``(record, selection)`` pairs already revalidated by the caller.
    ``dispatch_text`` empty means fail-closed: no evidence, so no injection.
    """
    trace: dict[str, Any] = {
        "enabled": bool(enabled),
        "triggered": False,
        "reason": "",
        "dispatch_sha256": hashlib.sha256((dispatch_text or "").encode("utf-8")).hexdigest(),
        "signals": {},
        "missing_dimensions": [],
        "uncovered_dimensions": [],
        "reasons": {},
        "matched": [],
        "skipped": [],
        "check_types": [],
        "line_count": 0,
        "text": "",
    }
    if not enabled:
        trace["reason"] = "injection switch is off"
        return trace

    text = (dispatch_text or "").strip()
    if not text:
        trace["reason"] = "no dispatch text to judge (fail-closed)"
        return trace

    missing, signals, reasons = _missing_dimensions(text)
    trace["signals"] = signals
    trace["missing_dimensions"] = missing
    trace["reasons"] = reasons
    if not missing:
        trace["reason"] = "dispatch already states the fields this gate checks"
        return trace

    covered: list[str] = []
    skipped: list[dict[str, Any]] = []
    matched: list[dict[str, Any]] = []
    for item in items:
        record = item.get("record") or item
        selection = item.get("selection") or {}
        topic = topic_for(record)
        dimension = _TOPIC_DIMENSION.get(topic) if topic else None
        if dimension is None or dimension not in missing:
            skipped.append(
                {
                    "experience_id": str(record.get("experience_id") or ""),
                    "topic": topic,
                    "dimension": dimension,
                    "reason": (
                        "topic has no injectable dimension"
                        if dimension is None
                        else "dispatch already states this dimension"
                    ),
                }
            )
            continue
        covered.append(dimension)
        matched.append(
            {
                "experience_id": str(record.get("experience_id") or ""),
                "topic": topic,
                "dimension": dimension,
                "confirmation_id": str(selection.get("confirmation_id") or ""),
                "confirmed_by": str(selection.get("confirmed_by") or ""),
                "confirmed_at": str(selection.get("confirmed_at") or ""),
            }
        )

    check_types = [name for name in DIMENSIONS if name in set(covered)]
    trace["uncovered_dimensions"] = [name for name in missing if name not in set(covered)]
    trace["matched"] = matched
    trace["skipped"] = skipped
    trace["check_types"] = check_types
    if not check_types:
        trace["reason"] = "dispatch has gaps but no confirmed experience covers them"
        return trace

    lines = [CHECK_TYPE_LINES[name] for name in check_types]
    trace["triggered"] = True
    trace["line_count"] = len(lines)
    trace["text"] = "\n".join(lines)
    trace["reason"] = "injected check types for: " + ", ".join(check_types)
    return trace


__all__ = [
    "DIMENSIONS",
    "CHECK_TYPE_LINES",
    "decide",
    "dimension_for",
    "topic_for",
]
