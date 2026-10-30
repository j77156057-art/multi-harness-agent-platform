"""The platform's tools over MCP: the protocol, the scope, and the wire.

The point of that surface is that a normal CLI keeps its own loop and just calls
these tools, so the tests are about the two things a client can get wrong:
asking for a tool the table does not have, and reaching outside the worktree it
was given.  Both must come back as content the model can act on, not as a
transport failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.agenttools import mcp
from app.agenttools.floor import Floor
from app.agenttools.registry import build_registry
from app.auth.tokens import TokenService
from app.config import settings
from tests.api_support import bootstrap, make_client, publish_plan


def _tools(tmp_path: Path) -> dict:
    root = tmp_path / "wt"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("# 甲\n", encoding="utf-8")
    return build_registry(Floor(root=root, paths=("docs",)))


# ------------------------------------------------------------------ 协议本身


def test_initialize_names_the_protocol_and_the_tool_capability(tmp_path: Path) -> None:
    answer = mcp.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        _tools(tmp_path),
    )
    assert answer["id"] == 1
    assert answer["result"]["protocolVersion"] == "2025-06-18"
    assert "tools" in answer["result"]["capabilities"]
    assert answer["result"]["serverInfo"]["name"] == "agent-office-tools"


def test_tools_list_carries_json_schemas(tmp_path: Path) -> None:
    answer = mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _tools(tmp_path))
    tools = {item["name"]: item for item in answer["result"]["tools"]}
    assert {"create_file", "read_file", "run_command", "finish"} <= set(tools)
    schema = tools["create_file"]["inputSchema"]
    assert schema["required"] == ["path", "content"]
    assert schema["properties"]["path"]["type"] == "string"
    # 给客户端的说明不能只是一句"输入示例"，它要能自己校验参数。
    assert schema["type"] == "object"


def test_a_call_runs_the_tool_and_returns_text(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    answer = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "create_file", "arguments": {"path": "docs/b.md", "content": "内容"}},
        },
        tools,
    )
    result = answer["result"]
    assert result["isError"] is False
    assert "已写入" in result["content"][0]["text"]
    assert (tmp_path / "wt" / "docs" / "b.md").read_text(encoding="utf-8") == "内容"


def test_a_write_outside_the_authorized_paths_is_an_error_the_model_can_read(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    answer = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "create_file", "arguments": {"path": "elsewhere/x.md", "content": "x"}},
        },
        tools,
    )
    result = answer["result"]
    assert result["isError"] is True
    assert "不在本任务授权的落点里" in result["content"][0]["text"]
    assert not (tmp_path / "wt" / "elsewhere").exists()


def test_an_unknown_tool_lists_the_real_ones(tmp_path: Path) -> None:
    answer = mcp.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "write_file", "arguments": {}},
        },
        _tools(tmp_path),
    )
    assert answer["result"]["isError"] is True
    assert "create_file" in answer["result"]["content"][0]["text"]


def test_a_notification_gets_no_answer(tmp_path: Path) -> None:
    assert mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, _tools(tmp_path)) is None


def test_an_unknown_method_is_a_jsonrpc_error(tmp_path: Path) -> None:
    answer = mcp.handle({"jsonrpc": "2.0", "id": 6, "method": "resources/list"}, _tools(tmp_path))
    assert answer["error"]["code"] == mcp.ERR_METHOD_NOT_FOUND


def test_settled_paths_come_from_the_snapshot_not_the_request() -> None:
    snapshot = json.dumps(
        {
            "tasks": [
                {"id": "T-1", "exclusive_paths": ["docs/a.md"]},
                {"id": "T-2", "exclusive_paths": ["docs/b.md", " " ]},
            ]
        },
        ensure_ascii=False,
    )
    assert mcp.spec_paths(snapshot, "T-1") == ("docs/a.md",)
    assert mcp.spec_paths(snapshot, "T-2") == ("docs/b.md",)
    assert mcp.spec_paths(snapshot, "T-9") == ()
    assert mcp.spec_paths("not json", "T-1") == ()


# ------------------------------------------------------------------ HTTP 那一层


def _register(database, harness_id: str) -> None:
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=harness_id,
            name=harness_id,
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
            status="online",
        )
    )


def _ready_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A claimed attempt that owns a real worktree directory."""
    client, database = make_client(tmp_path)
    bootstrap(client)
    publish_plan(
        client,
        plan_id="PL-1",
        tasks=[{"id": "T-1", "exclusive_paths": ["docs/a.md"]}],
    )
    approved = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "h"})
    assert approved.status_code == 200, approved.text
    claimed = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-A", "task_ids": ["T-1"]},
    )
    assert claimed.status_code == 200, claimed.text
    attempt_id = claimed.json()["items"][0]["attempt_id"]

    worktree_root = tmp_path / "wt"
    worktree = worktree_root / attempt_id
    (worktree / "docs").mkdir(parents=True)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO workspaces(workspace_id, task_id, attempt_id, branch, path, status, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("ws-1", "T-1", attempt_id, "b", str(worktree), "in_use", "2026-01-01T00:00:00Z"),
        )
    monkeypatch.setattr(settings, "worktree_root", str(worktree_root), raising=False)
    _register(database, "h-A")
    token = TokenService(database).issue(subject_type="harness", subject_id="h-A")
    return client, database, attempt_id, token


def _call(client, *, attempt_id: str, token: str, body: dict):
    return client.post(
        "/api/v1/agenttools/mcp",
        params={"task_id": "T-1", "attempt_id": attempt_id},
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def test_a_window_can_use_its_own_attempt_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _database, attempt_id, token = _ready_attempt(tmp_path, monkeypatch)
    listed = _call(client, attempt_id=attempt_id, token=token, body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed.status_code == 200, listed.text
    names = {tool["name"] for tool in listed.json()["result"]["tools"]}
    assert "create_file" in names

    written = _call(
        client,
        attempt_id=attempt_id,
        token=token,
        body={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "create_file", "arguments": {"path": "docs/a.md", "content": "# 乙\n"}},
        },
    )
    assert written.status_code == 200, written.text
    assert "已写入" in written.json()["result"]["content"][0]["text"]
    assert (tmp_path / "wt" / attempt_id / "docs" / "a.md").read_text(encoding="utf-8") == "# 乙\n"


def test_another_window_cannot_borrow_the_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, database, attempt_id, _token = _ready_attempt(tmp_path, monkeypatch)
    _register(database, "h-B")
    other = TokenService(database).issue(subject_type="harness", subject_id="h-B")
    refused = _call(client, attempt_id=attempt_id, token=other, body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert refused.status_code == 403, refused.text
    assert "别的窗口" in refused.text


def test_a_human_token_is_not_a_window_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, database, attempt_id, _token = _ready_attempt(tmp_path, monkeypatch)
    human = TokenService(database).issue(subject_type="human", subject_id="operator")
    refused = _call(client, attempt_id=attempt_id, token=human, body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert refused.status_code == 403, refused.text


def test_no_token_is_unauthorised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _database, attempt_id, _token = _ready_attempt(tmp_path, monkeypatch)
    refused = client.post(
        "/api/v1/agenttools/mcp",
        params={"task_id": "T-1", "attempt_id": attempt_id},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert refused.status_code == 401, refused.text


def test_a_stale_attempt_is_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上一次尝试不该还能改树：平台可能已经提交、校验甚至回收了它。"""
    client, database, attempt_id, token = _ready_attempt(tmp_path, monkeypatch)
    with database.transaction() as conn:
        conn.execute("UPDATE tasks SET current_attempt_id = ? WHERE task_id = ?", ("T-1.a9", "T-1"))
    refused = _call(client, attempt_id=attempt_id, token=token, body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert refused.status_code == 409, refused.text
    assert "当前尝试" in refused.text


def test_a_worktree_outside_the_station_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库里的路径是平台自己写的，但指到工位目录外面就说明这几个工具被指向了别处。"""
    client, database, attempt_id, token = _ready_attempt(tmp_path, monkeypatch)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE workspaces SET path = ? WHERE attempt_id = ?",
            (str(tmp_path / "somewhere-else"), attempt_id),
        )
    refused = _call(client, attempt_id=attempt_id, token=token, body={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert refused.status_code == 409, refused.text
    assert "工位目录" in refused.text
