"""把系统可确定的事实映射到回答校验；建议与未知事实不做机械改写。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.tasks.progress import next_action


@dataclass(frozen=True, slots=True)
class FactIssue:
    field: str
    source: str
    expected: str
    model_text: str
    action: str
    task_id: str = ""

    def as_log(self) -> dict[str, str]:
        return {
            "field": self.field,
            "source": self.source,
            "expected": self.expected,
            "model_text": self.model_text,
            "action": self.action,
            "task_id": self.task_id,
        }


class FactValidationError(ValueError):
    def __init__(self, issues: list[FactIssue]) -> None:
        super().__init__("model answer contradicts deterministic task facts")
        self.issues = issues


_STATUS_LABEL = {
    "draft": "方案草案",
    "ready": "待领取",
    "queued": "待领取",
    "claimed": "执行中",
    "in_progress": "执行中",
    "reclaiming": "执行中",
    "verifying": "交付验证",
    "awaiting_review": "待验收",
    "changes_requested": "验收返修",
    "awaiting_human": "等待人工",
    "escalated": "等待人工",
    "approved": "验收通过",
    "merging": "合并中",
    "merged": "已合并",
    "abandoned": "执行中断",
    "stale": "执行中断",
    "cancelled": "已取消",
}
_STATUS_WORDS = (
    "方案草案|验收返修|交付验证|等待人工|验收通过|执行中断|待领取|待验收|执行中|合并中|已合并|已取消|执行"
)
_CURRENT_RE = re.compile(rf"(?:当前|目前|现在)(?:正在处于|正在|处于|状态为|状态是|是|为|：|:)?\s*[「『]?(?:{_STATUS_WORDS})[」』]?")
_NEXT_RE = re.compile(r"下一步(?:是|为|：|:)?\s*([^。！？\n]+)")
_COUNT_RE = re.compile(r"((?:本群|本项目|当前项目)?共(?:有|计)?\s*)(\d+)(\s*条任务)")
_PATH_RE = re.compile(r"(允许修改(?:的)?(?:路径|文件)(?:为|是|：|:)?\s*)([^。！？\n]+)")
_FALSE_EXECUTOR_CLAIMS = (
    "尚无执行者",
    "没有执行者",
    "未有执行者",
    "无执行者",
    "尚无持有者",
    "没有持有者",
    "执行者（无人）",
    "执行者为空",
    "无人执行",
    "无人持有",
)
_NEXT_MARKERS = (
    ("审核", ("审核", "审阅", "裁决")),
    ("领取", ("领取",)),
    ("提交", ("提交", "交付")),
    ("验证", ("验证", "回执")),
    ("人工", ("人工", "群主", "裁决", "求助")),
    ("合并", ("合并",)),
)


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("task_id") or "")


def _executor(task: dict[str, Any]) -> str:
    return str(task.get("owner") or task.get("executor") or "").strip()


def _relevant_tasks(answer: str, question: str, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mentioned = [
        task
        for task in tasks
        if _task_id(task) and (_task_id(task) in question or _task_id(task) in answer)
    ]
    return mentioned if mentioned else (list(tasks) if len(tasks) == 1 else [])


def _paths(task: dict[str, Any]) -> list[str]:
    raw = task.get("exclusive_paths")
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item).strip()]
    return []


def _next_step(task: dict[str, Any]) -> str:
    text = next_action(task)
    return text.split("下一步：", 1)[1] if "下一步：" in text else ""


def _check_counts(answer: str, tasks: list[dict[str, Any]], issues: list[FactIssue]) -> str:
    expected = str(len(tasks))

    def replace(match: re.Match[str]) -> str:
        if match.group(2) == expected:
            return match.group(0)
        _record(issues, "task_count", "count(tasks)", expected, match.group(0), "corrected")
        return f"{match.group(1)}{expected}{match.group(3)}"

    return _COUNT_RE.sub(replace, answer)


def _record(
    issues: list[FactIssue],
    field: str,
    source: str,
    expected: str,
    model_text: str,
    action: str,
    task_id: str = "",
) -> None:
    issues.append(FactIssue(field, source, expected, model_text, action, task_id))


def _check_executor(answer: str, task: dict[str, Any], issues: list[FactIssue]) -> str:
    executor = _executor(task)
    if not executor:
        # 真没有执行者时，“尚无执行者”是允许的，不能误伤。
        return answer
    for claim in sorted(_FALSE_EXECUTOR_CLAIMS, key=len, reverse=True):
        if claim in answer:
            _record(
                issues,
                "executor",
                "task_attempts.owner",
                executor,
                claim,
                "corrected",
                _task_id(task),
            )
            answer = answer.replace(claim, f"执行者为 {executor}")
    return answer


def _check_status(answer: str, task: dict[str, Any], issues: list[FactIssue]) -> str:
    expected = _STATUS_LABEL.get(str(task.get("status") or ""), "")
    if not expected:
        return answer

    def replace(match: re.Match[str]) -> str:
        if expected in match.group(0):
            return match.group(0)
        _record(
            issues,
            "status",
            "tasks.status",
            expected,
            match.group(0),
            "corrected",
            _task_id(task),
        )
        return f"当前：{expected}"

    return _CURRENT_RE.sub(replace, answer)


def _check_next(answer: str, task: dict[str, Any], issues: list[FactIssue]) -> str:
    expected = _next_step(task)
    if not expected:
        return answer

    def replace(match: re.Match[str]) -> str:
        clause = match.group(1).strip()
        # 条件句、建议和未来计划不是对当前下一责任人的断言，不机械改写。
        if any(marker in clause for marker in ("建议", "可以", "如果", "或者", "待管理员", "待用户")):
            return match.group(0)
        expected_markers = next((words for marker, words in _NEXT_MARKERS if marker in expected), ())
        if expected_markers and any(word in clause for word in expected_markers):
            return match.group(0)
        if not expected_markers and expected in clause:
            return match.group(0)
        _record(
            issues,
            "next_action",
            "tasks.status + task_attempts.owner",
            expected,
            match.group(0),
            "corrected",
            _task_id(task),
        )
        return f"下一步：{expected}"

    return _NEXT_RE.sub(replace, answer)


def _check_paths(answer: str, task: dict[str, Any], issues: list[FactIssue]) -> str:
    expected_paths = _paths(task)
    if not expected_paths:
        return answer

    def replace(match: re.Match[str]) -> str:
        expected = "、".join(expected_paths)
        clause = match.group(2).strip()
        if all(path in clause for path in expected_paths):
            return match.group(0)
        _record(
            issues,
            "exclusive_paths",
            "plan_snapshots.exclusive_paths",
            expected,
            match.group(0),
            "corrected",
            _task_id(task),
        )
        return f"允许修改的路径：{expected}"

    return _PATH_RE.sub(replace, answer)


def validate_answer_facts(
    answer: str, *, question: str, tasks: list[dict[str, Any]]
) -> tuple[str, list[FactIssue]]:
    """修正能安全改写的矛盾；对象不清时拒绝，而不是猜一个答案。"""
    issues: list[FactIssue] = []
    result = _check_counts(answer, tasks, issues)
    relevant = _relevant_tasks(answer, question, tasks)
    if len(relevant) > 1:
        needs_check = any(
            _FALSE_EXECUTOR_CLAIMS and claim in result for claim in _FALSE_EXECUTOR_CLAIMS
        ) or _CURRENT_RE.search(result) or _NEXT_RE.search(result) or _PATH_RE.search(result)
        if needs_check:
            _record(
                issues,
                "task_reference",
                "task_id",
                "唯一任务对象",
                result[:400],
                "rejected",
            )
            raise FactValidationError(issues)
    for task in relevant[:1]:
        result = _check_executor(result, task, issues)
        result = _check_status(result, task, issues)
        result = _check_next(result, task, issues)
        result = _check_paths(result, task, issues)
    return result, issues


__all__ = ["FactIssue", "FactValidationError", "validate_answer_facts"]
