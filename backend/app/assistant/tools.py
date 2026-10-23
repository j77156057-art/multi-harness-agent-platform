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

    def group_tasks(args: dict[str, Any]) -> dict[str, Any]:
        params: dict[str, Any] = {}
        status = str(args.get("status") or "").strip()
        if status:
            if not _STATUS.fullmatch(status):
                raise ToolRefused(f"status {status!r} is not a plain lowercase token")
            params["status"] = status
        return client.get(f"/groups/{group_id}/tasks", **params)

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
