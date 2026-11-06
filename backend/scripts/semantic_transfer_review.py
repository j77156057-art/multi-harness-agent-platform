"""Deterministic regression judge for old-value pollution in dispatch outputs.

The literal rule ("does this string appear anywhere?") over-counted: negations,
exclusions and the new task's own wording were charged as copying. This module
judges the semantic question instead -- *does the output require keeping or
reusing an old item?* -- and keeps a location for every hit.

Classes:
    transferred   all required new values present, no old item required
    copied_wrong  at least one old item is required as delivered content
    missed        no old item, but a required new value is absent
    escalated     needs_owner, both old and new values, explicit conflict question
    unjudgeable   truncated / no complete output, so nothing can be judged

``unjudgeable`` is never folded into ``missed``: "cannot judge" is not
"did not copy".
"""

from __future__ import annotations

import json
import re
from typing import Any

CLASSES = ("transferred", "copied_wrong", "missed", "escalated", "unjudgeable")

_CLAUSE = re.compile("[，,。！？；;\n]")
#: A negation that excludes the following item. "不得删除 F3" is deliberately
#: *not* in this set: forbidding deletion asks to keep the old item.
_EXCLUDE_BEFORE = re.compile(
    "不得(?:出现|使用|包含|沿用|采用|保留|写|引用|存在)|不要(?:使用|出现|包含|沿用|保留|写|引用)"
    "|不能(?:使用|出现|包含|沿用|采用|保留|写|引用)|不存在"
    "|不(?:使用|出现|包含|沿用|保留|写|引用)"
    "|不再(?:使用|包含|沿用|保留)|无需|禁止|避免|不含|排除|而非"
)
_REMOVE_BEFORE = re.compile("删除|删掉|去掉|移除|替换|换成|改为")
_EXCLUDE_AFTER = re.compile(
    "^\\s*(?:不存在|不保留|不得保留|不得出现|不得沿用|不得使用|已删除|删除|去掉|移除|不属于|不是|已被删除)"
)
#: Clause-level exclusion: covers list forms ("F3/F4 不再检查") that a
#: value-adjacent check cannot see. Deletion-forbidding is checked first.
_EXCLUDE_ANY = re.compile(
    "不得|不要|不能|不含|不包含|排除|不再|无需|避免|不涉及|无关|删除|删掉|去掉|移除|替换|而非|不存在|不保留|禁止"
)
_EXCLUDE_KEEP = re.compile("不得保留|不要保留|不保留|无需保留|不再保留")
_KEEP = re.compile("不得删除|不要删除|不得移除|不要移除|保留|不删除")
_CONFLICT = re.compile("冲突|矛盾|不一致|无法同时")


def _clauses(text: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    start = 0
    for match in _CLAUSE.finditer(text):
        if match.start() > start:
            spans.append((start, match.start(), text[start : match.start()]))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text), text[start:]))
    return spans


def _context(text: str, index: int, value: str) -> tuple[str, str, str]:
    for start, end, clause in _clauses(text):
        if start <= index < end:
            local = index - start
            return clause[:local], clause[local + len(value) :], clause
    return "", "", ""


def _occurrences(text: str, value: str) -> list[int]:
    if not value:
        return []
    found: list[int] = []
    start = 0
    while True:
        hit = text.find(value, start)
        if hit == -1:
            return found
        found.append(hit)
        start = hit + len(value)


def _judge_value(
    text: str, value: str
) -> tuple[bool, str, dict[str, Any] | None]:
    """Return (requires_old_item, context_kind, evidence) for one old value."""
    positive: dict[str, Any] | None = None
    seen_negative = False
    for index in _occurrences(text, value):
        before, after, clause = _context(text, index, value)
        if _EXCLUDE_KEEP.search(clause):
            seen_negative = True
            continue
        if _KEEP.search(clause):
            positive = {"quote": clause, "offset": index, "context": "keep_required"}
            continue
        if (
            _EXCLUDE_BEFORE.search(before)
            or _REMOVE_BEFORE.search(before)
            or _EXCLUDE_AFTER.search(after)
            or _EXCLUDE_ANY.search(clause)
        ):
            seen_negative = True
            continue
        positive = {"quote": clause, "offset": index, "context": "delivered"}
    if positive is not None:
        return True, str(positive["context"]), positive
    return False, "excluded" if seen_negative else "absent", None


def _scan(
    text: str, old_values: list[str], *, location: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect positive (polluting) hits and negated hits with their location."""
    positives: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for value in old_values:
        required, kind, evidence = _judge_value(text, value)
        if required and evidence is not None:
            positives.append(
                {
                    "value": value,
                    "json_path": location,
                    "quote": evidence["quote"],
                    "context": kind,
                }
            )
        elif kind == "excluded" and evidence is None:
            excluded.append({"value": value, "json_path": location, "context": "negated_or_removed"})
    return positives, excluded


def judge(
    *,
    old_values: list[str],
    required_groups: list[list[str]],
    result: dict[str, Any] | None,
    error: str | None = None,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    """Judge one output. Deterministic; no model call, no network."""
    truncated = str(finish_reason or "") == "length"
    tasks = (result or {}).get("tasks") or []
    questions = [str(item) for item in ((result or {}).get("questions") or [])]

    if truncated:
        return {
            "semantic": "unjudgeable",
            "reason": "finish_reason=length: output truncated, nothing to judge",
            "evidence": [],
            "excluded_values": [],
            "borderline": False,
            "borderline_reason": "",
        }
    if error or not tasks:
        if not tasks and questions:
            question_text = "\n".join(questions)
            old_hits = [value for value in old_values if value in question_text]
            new_hits = [value for group in required_groups for value in group if value in question_text]
            if old_hits and new_hits and _CONFLICT.search(question_text):
                return {
                    "semantic": "escalated",
                    "reason": "needs_owner with old and new values plus an explicit conflict question",
                    "evidence": [
                        {"value": value, "json_path": "questions[]", "quote": question_text[:240]}
                        for value in old_hits
                    ],
                    "excluded_values": [],
                    "borderline": False,
                    "borderline_reason": "",
                }
            if questions:
                return {
                    "semantic": "missed",
                    "reason": "needs_owner without a valid task specification",
                    "evidence": [],
                    "excluded_values": [],
                    "borderline": False,
                    "borderline_reason": "",
                }
        if not tasks:
            return {
                "semantic": "unjudgeable",
                "reason": "no complete output: " + (error or "empty task list"),
                "evidence": [],
                "excluded_values": [],
                "borderline": False,
                "borderline_reason": "",
            }

    task = tasks[0]
    positives: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for field in ("goal", "acceptance"):
        hits, skipped = _scan(str(task.get(field) or ""), old_values, location=f"tasks[0].{field}")
        positives.extend(hits)
        excluded.extend(skipped)
    for index, path in enumerate(task.get("exclusive_paths") or []):
        hits, skipped = _scan(
            str(path), old_values, location=f"tasks[0].exclusive_paths[{index}]"
        )
        positives.extend(hits)
        excluded.extend(skipped)

    text = (
        str(task.get("goal") or "")
        + "\n"
        + str(task.get("acceptance") or "")
        + "\n"
        + "\n".join(str(item) for item in task.get("exclusive_paths") or [])
    )
    missing = [group for group in required_groups if not any(value in text for value in group)]

    borderline = False
    borderline_reason = ""
    if positives:
        # A positive hit next to a negation verb in the same clause is rarely
        # clean: report it, but mark that a human should read the sentence.
        ambiguous = _CONFLICT.search(text) or "如果" in text or "若" in text
        if ambiguous:
            borderline = True
            borderline_reason = "a conditional/conflict sentence also mentions old and new values"
        return {
            "semantic": "copied_wrong",
            "reason": "an old item is required as delivered content",
            "evidence": positives,
            "excluded_values": excluded,
            "borderline": borderline,
            "borderline_reason": borderline_reason,
            "missing_required_groups": missing,
        }
    if missing:
        semantic = "missed"
        reason = "required new values absent: " + ", ".join("/".join(group) for group in missing)
    else:
        semantic = "transferred"
        reason = "all required new values present; old items only negated or absent"
    return {
        "semantic": semantic,
        "reason": reason,
        "evidence": [],
        "excluded_values": excluded,
        "borderline": False,
        "borderline_reason": "",
        "missing_required_groups": missing,
    }


def literal_judge(
    *, old_values: list[str], required_groups: list[list[str]], result: dict[str, Any] | None
) -> str:
    """The frozen v3/v4 rule, kept for side-by-side comparison only."""
    tasks = (result or {}).get("tasks") or []
    if not tasks:
        return "missed"
    task = tasks[0]
    text = (
        str(task.get("goal") or "")
        + "\n"
        + str(task.get("acceptance") or "")
        + "\n"
        + "\n".join(str(item) for item in task.get("exclusive_paths") or [])
    )
    if any(value in text for value in old_values):
        return "copied_wrong"
    if any(not any(value in text for value in group) for group in required_groups):
        return "missed"
    return "transferred"


def review_rows(rows: list[dict[str, Any]], cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map stored outcomes to the two columns the regression report expects."""
    by_case = {case["case_id"]: case for case in cases}
    output: list[dict[str, Any]] = []
    for row in rows:
        case = by_case[row["case_id"]]
        judged = judge(
            old_values=case["old_values"],
            required_groups=case["required_groups"],
            result=row.get("result"),
            error=row.get("error"),
            finish_reason=(row.get("receipt") or {}).get("finish_reason"),
        )
        output.append(
            {
                "case_id": row["case_id"],
                "condition": row.get("condition"),
                "literal": literal_judge(
                    old_values=case["old_values"],
                    required_groups=case["required_groups"],
                    result=row.get("result"),
                ),
                **judged,
            }
        )
    return output


def main() -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path, help="results.json with result/error/receipt")
    parser.add_argument("cases", type=Path, help="frozen protocol with a cases list")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    rows = json.loads(args.results.read_text(encoding="utf-8"))
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    reviewed = review_rows(rows, cases)
    document = json.dumps(reviewed, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(document, encoding="utf-8")
    print(document)


if __name__ == "__main__":
    main()
