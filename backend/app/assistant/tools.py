"""The assistant's tool table: a closed whitelist, fail-closed.

The assistant has exactly two kinds of ammunition -- read-only evidence
gathering and one ``say`` (a private message, the only write).  Anything not
in this table is refused, loudly, as an observation the model can course-
correct on; the loop never guesses what a missing tool "probably meant".
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

#: Identifiers handed to tools are validated: a task id or recipient is a
#: short token, never a path, never a shell word.
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
_STATUS = re.compile(r"^[a-z][a-z_-]{0,38}$")
_MAX_SAY_CHARS = 4000

#: 群任务表一次给多少条（摘要的窗口大小）与上限。
_GROUP_TASKS_DEFAULT_LIMIT = 20
_GROUP_TASKS_MAX_LIMIT = 100


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    """一个界内的整数；模型传了怪东西就当没传（用默认值），不因此失败。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _clip(text: str, limit: int) -> str:
    """太长的标题只留开头 —— 摘要里要的是「哪一条」，不是整段标题。"""
    value = str(text or "").strip()
    return value if len(value) <= limit else value[:limit] + "…"


class ToolRefused(ValueError):
    """A tool call that failed *before* doing anything: bad shape, bad arg."""


class Client(Protocol):
    """The worker's HTTP client shape (same duck type as admin's PlatformClient)."""

    def get(self, path: str, **params: Any) -> dict[str, Any]: ...

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class Tool:
    name: str
    kind: str  # "read" | "say"; "say" is the only write the engine allows
    description: str
    handler: Callable[[dict[str, Any]], dict[str, Any]]

    def call(self, args: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise ToolRefused(f"args must be an object, got {type(args).__name__}")
        return dict(self.handler(args))


def _required_id(args: dict[str, Any], field: str) -> str:
    value = str(args.get(field) or "").strip()
    if not _ID.fullmatch(value):
        raise ToolRefused(f"{field!r} must be a plain identifier, got {value!r}")
    return value


def _one_text(args: dict[str, Any], field: str, *, limit: int) -> str:
    value = str(args.get(field) or "").strip()
    if not value:
        raise ToolRefused(f"{field!r} must not be empty")
    if len(value) > limit:
        raise ToolRefused(f"{field!r} exceeds {limit} characters; shorter, not truncated")
    return value


def build_registry(
    client: Client,
    *,
    group_id: str,
    author_id: str,
    author_kind: str = "harness",
) -> dict[str, Tool]:
    """The whitelist, closed at construction time.

    ``group_id``/``author_id`` are the *worker's* facts, not tool arguments:
    the assistant cannot read another group's threads and cannot speak in
    someone else's name.
    """

    def pool_status(_args: dict[str, Any]) -> dict[str, Any]:
        return client.get("/pool/status")

    def task_get(args: dict[str, Any]) -> dict[str, Any]:
        return client.get(f"/tasks/{_required_id(args, 'task_id')}")

    def harness_bases(_args: dict[str, Any]) -> dict[str, Any]:
        return client.get("/harnesses/bases")

    def task_diff(args: dict[str, Any]) -> dict[str, Any]:
        task_id = _required_id(args, "task_id")
        include_patch = bool(args.get("include_patch") or False)
        return client.get(f"/tasks/{task_id}/diff", include_patch=include_patch)

    def task_verification(args: dict[str, Any]) -> dict[str, Any]:
        return client.get(f"/tasks/{_required_id(args, 'task_id')}/verification")

    def task_review_pack(args: dict[str, Any]) -> dict[str, Any]:
        """One read that lays the review-assist evidence side by side.

        The *narration* of a review is the assistant's job (what evidence
        backs which acceptance contract); the assembly here stays mechanical:
        the contracts as the plan pinned them, the diff reading with its
        honest ``available`` flag, and the latest verification receipt.  No
        verdict is computed anywhere -- approve/request_changes stays with
        the human reviewer.
        """
        task_id = _required_id(args, "task_id")
        include_patch = bool(args.get("include_patch") or False)
        task = client.get(f"/tasks/{task_id}")
        spec = task.get("spec") if isinstance(task.get("spec"), dict) else {}
        raw_contracts = spec.get("acceptance")
        contracts: list[dict[str, str]] = []
        if isinstance(raw_contracts, list):
            for item in raw_contracts:
                if not isinstance(item, dict):
                    continue
                contracts.append(
                    {
                        "id": str(item.get("id") or ""),
                        "check": str(item.get("check") or ""),
                        "description": str(item.get("description") or ""),
                    }
                )
        diff = client.get(f"/tasks/{task_id}/diff", include_patch=include_patch)
        verification = client.get(f"/tasks/{task_id}/verification")
        diff_keys = (
            "available",
            "reason",
            "branch",
            "base_commit",
            "head_commit",
            "files_changed",
            "insertions",
            "deletions",
            "files",
            "patch",
            "truncated",
        )
        if isinstance(diff, dict):
            diff_pack = {key: diff.get(key) for key in diff_keys}
        else:
            diff_pack = {"available": False, "reason": "diff read shape was not an object"}
        check_kinds: dict[str, int] = {}
        for contract in contracts:
            check_kinds[contract["check"]] = check_kinds.get(contract["check"], 0) + 1
        return {
            "task_id": task_id,
            "status": str(task.get("status") or ""),
            "title": str(task.get("title") or ""),
            "acceptance": contracts,
            "check_kinds": check_kinds,
            "diff": diff_pack,
            "verification": verification.get("verification"),
        }

    def task_diagnose(args: dict[str, Any]) -> dict[str, Any]:
        """One read that lays the failure evidence side by side.

        The *narration* of a root cause belongs to the assistant (which fact
        explains which symptom); the assembly here stays mechanical: the task
        state, the failure-shaped ledger events (abandon/release) kept to the
        newest few with their payloads untouched, the CLI's own output tail
        with its honest ``None`` when there is nothing, and the latest
        verification receipt.  Every fact comes from a read face that already
        exists -- nothing is invented to fill a gap.
        """
        task_id = _required_id(args, "task_id")
        task = client.get(f"/tasks/{task_id}")
        events = client.get(f"/tasks/{task_id}/events", limit=200)
        rows = events.get("events") if isinstance(events, dict) else []
        failure_events = [
            {
                key: event.get(key)
                for key in ("seq", "event_name", "created_at", "actor_id", "payload")
            }
            for event in rows
            if isinstance(event, dict)
            and str(event.get("event_name") or "") in ("abandon", "release")
        ]
        output = client.get(f"/tasks/{task_id}/output")
        output_pack = output.get("output") if isinstance(output, dict) else None
        verification = client.get(f"/tasks/{task_id}/verification")
        return {
            "task_id": task_id,
            "status": str(task.get("status") or ""),
            "title": str(task.get("title") or ""),
            "owner": task.get("owner"),
            "current_attempt_id": task.get("current_attempt_id"),
            "failure_events": failure_events[-3:],
            "output": output_pack,
            "verification": verification.get("verification"),
        }

    def task_forensics(args: dict[str, Any]) -> dict[str, Any]:
        """The deeper evidence: more of the CLI output, the stderr side, and where
        every byte came from.  The platform masks credential-looking lines
        before this point, so the raw value is not here -- the count of masked
        lines is, and the answer has to say so.
        """
        task_id = _required_id(args, "task_id")
        pack = client.get(f"/tasks/{task_id}/forensics")
        forensics = pack.get("forensics") if isinstance(pack, dict) else None
        if not forensics:
            return {"task_id": task_id, "forensics": None, "note": "no attempt transcript"}
        events = client.get(f"/tasks/{task_id}/events", limit=200)
        rows = events.get("events") if isinstance(events, dict) else []
        failure_events = [
            {
                key: event.get(key)
                for key in ("seq", "event_name", "created_at", "actor_id", "payload")
            }
            for event in rows
            if isinstance(event, dict)
            and str(event.get("event_name") or "") in ("abandon", "release", "submit_result")
        ]
        return {
            "task_id": task_id,
            "forensics": forensics,
            "failure_events": failure_events[-5:],
        }

    def group_tasks(args: dict[str, Any]) -> dict[str, Any]:
        """The group's task list -- as a **summary**, not the whole table.

        The whole table used to be one observation: dozens of tasks with every
        field each, tens of kilobytes.  The model only saw the first half of it
        and then honestly reported "the list is cut off at FD-76…" -- which
        reads like "the answer never finished".  So the tool now hands over a
        complete summary (counts, distribution, who holds what) and says how
        many rows it left out and how to page through them.
        """
        params: dict[str, Any] = {}
        status = str(args.get("status") or "").strip()
        if status:
            if not _STATUS.fullmatch(status):
                raise ToolRefused(f"status {status!r} is not a plain lowercase token")
            params["status"] = status
        limit = _bounded_int(
            args.get("limit"),
            default=_GROUP_TASKS_DEFAULT_LIMIT,
            low=1,
            high=_GROUP_TASKS_MAX_LIMIT,
        )
        offset = _bounded_int(args.get("offset"), default=0, low=0, high=_GROUP_TASKS_MAX_LIMIT)
        # Paging is the model's **explicit** request: without it the call stays
        # exactly what it was (one plain read), and the tool cuts the window
        # itself -- a whole table in one observation is what the model could not
        # finish reading in the first place.
        paged = args.get("limit") is not None or args.get("offset") is not None
        if paged:
            params["limit"] = limit
            params["offset"] = offset
        data = client.get(f"/groups/{group_id}/tasks", **params)
        tasks = [row for row in (data.get("tasks") or []) if isinstance(row, dict)]
        total = int(data.get("total") or len(tasks))
        if not paged:
            tasks = tasks[:limit]

        by_status: dict[str, int] = {}
        unclaimed: list[dict[str, Any]] = []
        claimed: list[dict[str, Any]] = []
        for row in tasks:
            state = str(row.get("status") or "unknown")
            by_status[state] = by_status.get(state, 0) + 1
            entry = {
                "task_id": row.get("task_id"),
                "title": _clip(str(row.get("title") or ""), 60),
                "status": state,
            }
            holder = str(row.get("owner") or "")
            if holder:
                entry["owner"] = holder
                claimed.append(entry)
            else:
                unclaimed.append(entry)

        # The distribution and the unclaimed count come from the **whole** list
        # when the platform says so: a window's own counts would read as the
        # list's counts ("21 未领取" instead of "73 未领取").
        whole_by_status = data.get("by_status")
        if isinstance(whole_by_status, dict) and whole_by_status:
            by_status = {str(key): int(value) for key, value in whole_by_status.items()}
        whole_unclaimed = data.get("unclaimed_total")

        shown = len(tasks)
        omitted = max(0, total - offset - shown)
        unclaimed_count = int(whole_unclaimed) if whole_unclaimed is not None else len(unclaimed)
        return {
            "total": total,
            "by_status": by_status,
            "unclaimed": {
                "count": unclaimed_count,
                "count_note": (
                    "全表里没有持有者的条数"
                    if whole_unclaimed is not None
                    else "本窗口内没有持有者的条数"
                ),
                "items": unclaimed,
            },
            "claimed": {"count": len(claimed), "items": claimed},
            "window": {"offset": offset, "limit": limit, "shown": shown},
            "omitted": omitted,
            "note": (
                f"共 {total} 条，本窗口（offset={offset}）给出 {shown} 条"
                + (f"，还有 {omitted} 条没列（要全量请用 offset/limit 翻页）" if omitted else "")
                + "。本工具给的是摘要：总数、状态分布、没被领的与已有人领的清单；"
                "要看某一条的细节，用 task.get 或 task.forensics。"
            ),
        }

    def say(args: dict[str, Any]) -> dict[str, Any]:
        to = _required_id(args, "to")
        text = _one_text(args, "text", limit=_MAX_SAY_CHARS)
        return client.post(
            f"/groups/{group_id}/messages",
            {
                "author_kind": author_kind,
                "author_id": author_id,
                "body": text,
                "mentions": [to],
                "direct": True,
            },
        )

    tools = [
        Tool("pool.status", "read", "额度池现状：各档逐家可用与否、余量", pool_status),
        Tool(
            "harness.bases",
            "read",
            "能当正式成员的基座：内置那几家 + 声明出来的，每个带一份缺口清单"
            "（缺什么、为什么缺）",
            harness_bases,
        ),
        Tool("task.get", "read", "一个任务的完整状态与账本", task_get),
        Tool(
            "task.diff",
            "read",
            "任务线程的 diff 读数（include_patch 才带补丁）",
            task_diff,
        ),
        Tool("task.verification", "read", "任务的验证读数", task_verification),
        Tool(
            "task.review_pack",
            "read",
            "审核助攻素材包：验收条款逐条 + diff 读数 + 验证回执（一次取证；"
            "include_patch 才带补丁文本）",
            task_review_pack,
        ),
        Tool(
            "task.diagnose",
            "read",
            "失败诊断证据束：任务状态 + 失败事件（最新至多 3 条，原样） + "
            "CLI 输出尾巴 + 验证回执（一次取证；不编原因）",
            task_diagnose,
        ),
        Tool(
            "task.forensics",
            "read",
            "深取证：更长的 CLI 输出、stderr 与出处（哪条任务/哪一轮/哪个日志文件/多大/何时）；"
            "像凭据的行平台已遮挡",
            task_forensics,
        ),
        Tool("group.tasks", "read", "本群任务表（可选 status 过滤）", group_tasks),
        Tool(
            "say",
            "say",
            "给人发一条私聊消息（text≤4000 字）——唯一的写工具",
            say,
        ),
    ]
    # The loop may read other groups' message threads through the worker's
    # poll; the *tool* table holds only what an assistant may act on alone.
    return {tool.name: tool for tool in tools}


__all__ = ["Client", "Tool", "ToolRefused", "build_registry"]
