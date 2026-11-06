"""Deterministic, evidence-backed conversion of experience records into prompt slots."""

from __future__ import annotations

import json
import re
from typing import Any

SLOT_NAMES = (
    "delivery_path",
    "task_id_format",
    "acceptance_check",
    "step_count",
    "log_path",
    "entry_preserved",
)
NO_LESSON = "no_lesson"
UNACTIONABLE = "unactionable"
_PATH = re.compile(r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:md|txt|log|json|py|js|ts|tsx|css|html)(?![\w.-])", re.I)
_LOG_PATH = re.compile(r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.log(?![\w.-])", re.I)
_PATH_PROBLEM = re.compile(
    r"路径不明|路径不明确|未明确.{0,8}路径|缺少.{0,8}路径|没有指定.{0,8}路径|"
    r"交付路径|产出物路径|文件位置|存放位置|输出位置|写到哪里|落在哪|落点"
)


def _root(record: dict[str, Any]) -> str:
    return str(record.get("root_cause") or "").strip()


def _action(record: dict[str, Any]) -> str:
    return str(record.get("action") or "").strip()


def _quote(root: str, pattern: str | re.Pattern[str]) -> str:
    """Keep the exact source sentence as the classification receipt."""
    parts = [part.strip() for part in re.split(r"(?<=。)|；", root) if part.strip()]
    for part in parts:
        matched = pattern.search(part) if isinstance(pattern, re.Pattern) else re.search(pattern, part, re.I)
        if matched:
            return part
    return ""


def _expected_path(text: str, *, log: bool = False) -> str:
    match = (_LOG_PATH if log else _PATH).search(text)
    return match.group(0) if match else ""


def _unactionable() -> dict[str, Any]:
    return {"category": UNACTIONABLE, "evidence_quote": "", "expected": None}


def classify_experience(record: dict[str, Any]) -> dict[str, Any]:
    """Classify only from explicit root-cause text; never infer from the spec alone."""
    root = _root(record)
    if record.get("kind") == "merged":
        return {"category": NO_LESSON, "evidence_quote": root, "expected": None}
    if not root or "未提供具体意见" in root:
        return _unactionable()
    action = _action(record)
    evidence_task = str(record.get("source_task_id") or record.get("contract_id") or "")

    quote = _quote(root, r"任务\s*(?:id|标识)")
    if quote and evidence_task:
        return {
            "category": "task_id_format",
            "target_field": "目标.任务标识",
            "expected": evidence_task,
            "rule": "派单目标必须显式包含该任务 ID",
            "evidence_task": evidence_task,
            "evidence_quote": quote,
        }

    quote = _quote(root, r"步骤条数|恰好\s*(?:三条|3\s*条).*步骤")
    if quote:
        expected = "恰好三条启动步骤" if re.search(r"恰好\s*(?:三条|3\s*条).*步骤", action) else ""
        if not expected:
            match = re.search(r"恰好\s*(?:三条|3\s*条)[^，。；]*步骤", root)
            expected = match.group(0) if match else ""
        if expected:
            return {
                "category": "step_count",
                "target_field": "验收.启动步骤条数",
                "expected": expected,
                "rule": action or expected,
                "evidence_task": evidence_task,
                "evidence_quote": quote,
            }

    quote = _quote(root, r"日志路径|日志.*\.log")
    if quote:
        expected = _expected_path(root, log=True) or _expected_path(action, log=True)
        if expected:
            return {
                "category": "log_path",
                "target_field": "验收.错误日志路径",
                "expected": expected,
                "rule": action,
                "evidence_task": evidence_task,
                "evidence_quote": quote,
            }

    quote = _quote(root, r"保留|删除|入口")
    if quote and "入口" in quote:
        expected = _expected_path(root) or _expected_path(action)
        if expected:
            return {
                "category": "entry_preserved",
                "target_field": "目标与验收.既有入口",
                "expected": expected,
                "rule": action,
                "evidence_task": evidence_task,
                "evidence_quote": quote,
            }

    quote = _quote(root, _PATH_PROBLEM)
    if quote:
        expected = _expected_path(root) or _expected_path(action)
        if expected:
            return {
                "category": "delivery_path",
                "target_field": "目标.交付路径",
                "expected": expected,
                "rule": "派单目标必须写明该固定路径，不得留待追问",
                "evidence_task": evidence_task,
                "evidence_quote": quote,
            }

    # Concrete review findings are acceptance checks, not delivery paths. Keep
    # the original instruction as expected instead of inventing a shorter value.
    quote = _quote(root, r"验收|必须|不得|请|检查|断言")
    if quote:
        expected = root.removeprefix("验收未通过（unknown）：").strip()
        return {
            "category": "acceptance_check",
            "target_field": "验收.必检项",
            "expected": action if action.startswith("验收必须") else expected,
            "rule": action if action.startswith("验收必须") else expected,
            "evidence_task": evidence_task,
            "evidence_quote": quote,
        }
    return _unactionable()


def slot_for_experience(record: dict[str, Any]) -> dict[str, str] | None:
    """Return an injectable slot, or None for no_lesson/unactionable."""
    classified = classify_experience(record)
    if classified["category"] not in SLOT_NAMES:
        return None
    return {
        "slot": classified["category"],
        "target_field": classified["target_field"],
        "expected": classified["expected"],
        "rule": classified["rule"],
        "evidence_task": classified["evidence_task"],
    }


def slot_prompt(slot: dict[str, str]) -> str:
    """One hard slot line; no narrative paraphrase."""
    return (
        f"- slot: {slot['slot']} | target_field: {slot['target_field']} | "
        f"expected: {slot['expected']} | rule: {slot['rule']} | "
        f"evidence_task: {slot['evidence_task'] or '未记录'}"
    )


__all__ = [
    "SLOT_NAMES",
    "NO_LESSON",
    "UNACTIONABLE",
    "classify_experience",
    "slot_for_experience",
    "slot_prompt",
]
