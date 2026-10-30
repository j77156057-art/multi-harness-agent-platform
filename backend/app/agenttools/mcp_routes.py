"""HTTP surface for the platform's MCP tools.

One endpoint, because MCP over HTTP is one endpoint: the client POSTs JSON-RPC
messages to it and gets JSON-RPC answers back.  Two query parameters say *which*
attempt the caller means; nothing about the workspace comes from the client.

    POST /api/v1/agenttools/mcp?task_id=PT-1&attempt_id=PT-1.a1
    Authorization: Bearer <该窗口自己的平台令牌>

Why the attempt is in the URL rather than in a session the platform hands out:
a session means a store, a TTL, and a second thing that can drift from the
database.  The attempt row already names the worktree and the owner, so the
call is authorised against facts the platform wrote itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.agenttools import mcp
from app.api.auth_deps import DatabaseDep, bearer_token
from app.api.errors import ApiError
from app.auth.tokens import TokenInvalid, TokenService
from app.config import settings
from app.db.connection import Database

router = APIRouter(tags=["agenttools"])


def _attempt(database: Database, *, task_id: str, attempt_id: str) -> mcp.Attempt:
    """Resolve the attempt to the worktree and the paths it may touch."""
    rows = database.read(
        "SELECT a.attempt_id, a.task_id, a.owner,"
        " (SELECT w.path FROM workspaces w WHERE w.attempt_id = a.attempt_id"
        "  AND w.task_id = a.task_id ORDER BY w.rowid DESC LIMIT 1) AS path"
        " FROM task_attempts a WHERE a.attempt_id = ? AND a.task_id = ?",
        (attempt_id, task_id),
    )
    if not rows:
        raise ApiError("agenttools.unknown_attempt", 404, "没有这次尝试的记录")
    row = rows[0]
    worktree = str(row["path"] or "").strip()
    if not worktree:
        raise ApiError("agenttools.no_workspace", 409, "这次尝试还没有工作树，工具暂时用不了")
    current = database.read(
        "SELECT current_attempt_id FROM tasks WHERE task_id = ?", (task_id,)
    )
    if not current or str(current[0]["current_attempt_id"] or "") != attempt_id:
        # A finished or superseded attempt must not keep editing the tree: the
        # platform may already have submitted, verified or recycled it.
        raise ApiError(
            "agenttools.stale_attempt", 409, "这次尝试已经不是这条任务的当前尝试，工具已关闭"
        )
    root = Path(worktree).resolve()
    allowed_root = Path(settings.worktree_root).resolve()
    if root != allowed_root and allowed_root not in root.parents:
        # The row is the platform's own fact, but a path outside the worktree
        # root would still mean "these tools are pointed at something else".
        raise ApiError("agenttools.bad_workspace", 409, "工作树不在平台的工位目录里，拒绝提供工具")
    # 授权落点从**这次尝试认领时的快照**里取，而不是当前的方案：方案后来改了，
    # 也不能把已经开跑的尝试的写入范围悄悄放大。
    snapshot = database.read(
        "SELECT s.content_json FROM tasks t JOIN plan_snapshots s"
        " ON s.snapshot_id = t.plan_snapshot_id WHERE t.task_id = ?",
        (task_id,),
    )
    paths = mcp.spec_paths(str(snapshot[0]["content_json"]), task_id) if snapshot else ()
    return mcp.Attempt(
        task_id=task_id,
        attempt_id=attempt_id,
        harness_id=str(row["owner"] or ""),
        worktree=root,
        paths=paths,
    )


def _principal(database: Database, request: Request) -> str:
    token = bearer_token(request)
    try:
        principal = TokenService(database).verify(token)
    except TokenInvalid as exc:
        raise ApiError("auth.token_invalid", 401, exc.reason) from exc
    if principal.subject_type != "harness":
        raise ApiError("auth.permission_denied", 403, "平台工具只接受窗口令牌")
    return principal.subject_id


@router.post("/agenttools/mcp")
async def mcp_endpoint(
    request: Request,
    database: DatabaseDep,
    task_id: str,
    attempt_id: str,
) -> Response:
    """Serve one JSON-RPC message (or a batch of them)."""
    caller = _principal(database, request)
    attempt = _attempt(database, task_id=task_id, attempt_id=attempt_id)
    if attempt.harness_id and attempt.harness_id != caller:
        # A window may only reach its own worktree -- this is the whole point of
        # per-attempt workspaces.
        raise ApiError(
            "agenttools.not_owner",
            403,
            "这次尝试属于别的窗口，不能替它用工具",
        )
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError("agenttools.bad_json", 400, "请求体不是 JSON") from exc

    tools = mcp.registry_for(attempt)
    messages = payload if isinstance(payload, list) else [payload]
    answers = [answer for answer in (mcp.handle(item, tools) for item in messages) if answer]
    if not answers:
        # Every message was a notification: JSON-RPC says answer with no body.
        return Response(status_code=202)
    body: Any = answers if isinstance(payload, list) else answers[0]
    return JSONResponse(body)


@router.get("/agenttools/mcp")
async def mcp_endpoint_get() -> Response:
    """MCP over HTTP is POST-only here; say so instead of pretending to stream."""
    return JSONResponse(
        {"error": {"code": -32600, "message": "这个端点只接受 POST（JSON-RPC）"}},
        status_code=405,
        headers={"Allow": "POST"},
    )
