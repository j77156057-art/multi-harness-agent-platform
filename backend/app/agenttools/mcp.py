"""The platform's tools, spoken over MCP -- so the Harness keeps its own loop.

This is the other half of :mod:`app.agenttools`.  Same tool table, different
owner of the loop:

* ``platform`` harness (``app/agenttools/cli.py``) -- the platform runs the
  loop, because that channel cannot ask for a tool at all;
* **MCP** (this module) -- a normal CLI runs its own loop and calls these
  tools over the wire.  The platform only answers the calls.

Why MCP and not "just hand the CLI a prompt with the tool list": MCP is the
thing every CLI here already speaks (``claude --mcp-config``, ``opencode mcp``,
``pi mcp``), it carries JSON Schemas so the client can validate arguments, and
it keeps the tool definitions out of the prompt.

Three decisions worth naming:

**Scope comes from the database, never from the request.**  A call names its
``task_id``/``attempt_id``; the server looks up which worktree the platform
provisioned for that attempt (``workspaces.path``) and which paths the plan
authorized (``plan_snapshots`` -> task -> ``exclusive_paths``).  A client that
could name its own root would be able to wander into another member's worktree,
which is the one guarantee worktrees exist for.

**The caller must be that attempt's owner.**  The token is the harness's normal
platform token; the attempt row has to name the same harness.  So a window can
only reach its own worktree, and only while that attempt is the current one.

**A refusal is content, not a transport error.**  Tools that are told "no" (out
of scope, credential file, refused command) answer with the sentence, flagged
``isError`` when nothing ran.  That is what the model needs to correct itself;
an HTTP 500 would just look like the platform fell over.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agenttools.floor import Floor, FloorRefused
from app.agenttools.registry import Tool, ToolRefused, build_registry

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "agent-office-tools", "version": "1.0.0"}

#: JSON-RPC error codes we actually use.
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602


@dataclass(frozen=True)
class Attempt:
    """Everything one call is allowed to touch, resolved from the database."""

    task_id: str
    attempt_id: str
    harness_id: str
    worktree: Path
    paths: tuple[str, ...]


class McpRefused(Exception):
    """The call cannot be served at all (bad attempt, wrong owner, bad auth)."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def tools_payload(tools: dict[str, Tool]) -> list[dict[str, Any]]:
    """The ``tools/list`` answer, in the shape MCP clients expect."""
    return [
        {
            "name": tool.name,
            "description": f"{tool.summary}\n输入示例：{tool.usage}",
            "inputSchema": tool.input_schema(),
        }
        for tool in tools.values()
    ]


def handle(message: Any, tools: dict[str, Tool]) -> dict[str, Any] | None:
    """One JSON-RPC message in, one answer out (``None`` for a notification)."""
    if not isinstance(message, dict):
        return _error(None, ERR_INVALID_REQUEST, "JSON-RPC 消息必须是一个对象")
    method = str(message.get("method") or "")
    request_id = message.get("id")
    if not method:
        return _error(request_id, ERR_INVALID_REQUEST, "缺少 method")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    is_notification = "id" not in message

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": str(params.get("protocolVersion") or PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "平台工具：文件读写、搜索、命令、只读 git。"
                    "写入只能落在本任务授权的落点里；凭据类文件不读不写；"
                    "提交由平台完成，这里没有 git 写。"
                ),
            },
        )
    if method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": tools_payload(tools)})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments")
        return _result(request_id, _call(tools, name, arguments))
    if is_notification:
        return None
    return _error(request_id, ERR_METHOD_NOT_FOUND, f"不认识的方法：{method}")


def _call(tools: dict[str, Tool], name: str, arguments: Any) -> dict[str, Any]:
    tool = tools.get(name)
    if tool is None:
        available = "、".join(sorted(tools))
        return _text_result(f"没有叫 `{name}` 的工具。可用的是：{available}。", is_error=True)
    try:
        observation = tool.call(arguments)
    except (ToolRefused, FloorRefused) as exc:
        return _text_result(f"拒绝：{exc}", is_error=True)
    except Exception as exc:  # noqa: BLE001 - a tool bug must not kill the session
        return _text_result(
            f"工具 `{name}` 自己出错了（{type(exc).__name__}: {exc}）。换个做法，或换别的工具。",
            is_error=True,
        )
    return _text_result(observation, is_error=False)


def _text_result(text: str, *, is_error: bool) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def registry_for(attempt: Attempt) -> dict[str, Tool]:
    floor = Floor(root=attempt.worktree, paths=attempt.paths)
    return build_registry(floor)


def spec_paths(content_json: str, task_id: str) -> tuple[str, ...]:
    """The task's authorized paths out of its plan snapshot.

    Read straight from the snapshot rather than from the live plan: the
    snapshot is what this attempt was claimed against, and a later plan edit
    must not widen an attempt that is already running.
    """
    try:
        document = json.loads(content_json)
    except ValueError:
        return ()
    tasks = document.get("tasks") if isinstance(document, dict) else None
    for item in tasks or []:
        if isinstance(item, dict) and str(item.get("id")) == task_id:
            return tuple(str(path) for path in (item.get("exclusive_paths") or []) if str(path).strip())
    return ()
