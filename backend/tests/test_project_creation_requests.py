from __future__ import annotations

from app.auth.tokens import TokenService
from app.config import settings
from tests.api_support import make_client
from tests.merge_support import register_harness

OPERATOR = "office-operator"
AGENT = "agent-window"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({AGENT}))
    client, database = make_client(tmp_path)
    register_harness(database, AGENT, provider="qoder")
    tokens = TokenService(database)
    human = tokens.issue(subject_type="human", subject_id=OPERATOR)
    agent = tokens.issue(subject_type="harness", subject_id=AGENT)
    assert client.post(
        "/api/v1/projects",
        json={"project_id": "P-source", "name": "来源项目"},
        headers=bearer(human),
    ).status_code == 200
    assert client.post(
        "/api/v1/projects/P-source/groups",
        json={"group_id": "G-1", "name": "来源群"},
        headers=bearer(human),
    ).status_code == 200
    return client, database, human, agent


def _application(project_id="P-approved", group_id="G-approved", **overrides):
    body = {
        "project_id": project_id,
        "project_name": "授权项目",
        "group_id": group_id,
        "group_name": "授权项目主群",
        "purpose": "隔离验证多窗口交付",
        "scope": "仅本项目任务、交付和验收记录",
        "expected_output": "一个可追踪的独立项目与主群",
        "requested_kind": "harness",
        "requested_by": AGENT,
    }
    body.update(overrides)
    return body


def _apply(client, token, body):
    return client.post(
        "/api/v1/groups/G-1/project-creation-requests", json=body, headers=bearer(token)
    )


def test_agent_only_applies_and_human_approval_creates_with_audit(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    applied = _apply(client, agent, _application())
    assert applied.status_code == 200, applied.text
    request = applied.json()["request"]
    assert request["status"] == "pending"
    assert request["requested_by_type"] == "harness"
    assert request["requested_by_id"] == AGENT
    assert request["purpose"] == "隔离验证多窗口交付"
    assert request["scope"] == "仅本项目任务、交付和验收记录"
    assert request["expected_output"] == "一个可追踪的独立项目与主群"
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-approved'")
    assert not database.read("SELECT 1 FROM groups WHERE group_id = 'G-approved'")

    # 即使 agent 在管理员白名单里，也没有 project:create / group:create 委托。
    denied = client.post(
        f"/api/v1/project-creation-requests/{request['request_id']}/approve",
        json={"decided_by": AGENT, "reason": "自行批准"},
        headers=bearer(agent),
    )
    assert denied.status_code == 403, denied.text
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-approved'")

    approved = client.post(
        f"/api/v1/project-creation-requests/{request['request_id']}/approve",
        json={"decided_by": OPERATOR, "reason": "用途与范围明确，同意隔离验证"},
        headers=bearer(human),
    )
    assert approved.status_code == 200, approved.text
    row = approved.json()["request"]
    assert row["status"] == "approved"
    assert row["decided_by_type"] == "human"
    assert row["decided_by_id"] == OPERATOR
    assert row["decided_at"]
    assert row["decision_reason"] == "用途与范围明确，同意隔离验证"
    assert database.read("SELECT 1 FROM projects WHERE project_id = 'P-approved'")
    assert database.read("SELECT 1 FROM groups WHERE group_id = 'G-approved'")
    assert database.read(
        "SELECT status FROM integration_health WHERE project_id = 'P-approved'"
    )[0]["status"] == "green"


def test_rejection_requires_reason_and_never_creates(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    applied = _apply(client, agent, _application("P-rejected", "G-rejected"))
    request_id = applied.json()["request"]["request_id"]

    blank = client.post(
        f"/api/v1/project-creation-requests/{request_id}/reject",
        json={"decided_by": OPERATOR, "reason": " "},
        headers=bearer(human),
    )
    assert blank.status_code in {400, 422}, blank.text

    rejected = client.post(
        f"/api/v1/project-creation-requests/{request_id}/reject",
        json={"decided_by": OPERATOR, "reason": "当前预算不支持另建项目"},
        headers=bearer(human),
    )
    assert rejected.status_code == 200, rejected.text
    row = rejected.json()["request"]
    assert row["status"] == "rejected"
    assert row["decided_by_type"] == "human"
    assert row["decided_by_id"] == OPERATOR
    assert row["decided_at"]
    assert row["decision_reason"] == "当前预算不支持另建项目"
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-rejected'")
    assert not database.read("SELECT 1 FROM groups WHERE group_id = 'G-rejected'")


def test_requester_identity_is_bound_to_the_token(tmp_path, monkeypatch):
    client, database, _human, agent = _env(tmp_path, monkeypatch)

    response = _apply(client, agent, _application(requested_by="somebody-else"))

    assert response.status_code == 403, response.text
    assert not database.read("SELECT 1 FROM project_creation_requests")


def test_approval_conflict_rolls_back_project_group_and_decision(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    applied = _apply(client, agent, _application("P-atomic", "G-conflict"))
    request_id = applied.json()["request"]["request_id"]
    assert client.post(
        "/api/v1/projects", json={"project_id": "P-other", "name": "其他项目"}, headers=bearer(human)
    ).status_code == 200
    assert client.post(
        "/api/v1/projects/P-other/groups",
        json={"group_id": "G-conflict", "name": "已存在群"},
        headers=bearer(human),
    ).status_code == 200

    conflict = client.post(
        f"/api/v1/project-creation-requests/{request_id}/approve",
        json={"decided_by": OPERATOR, "reason": "批准"},
        headers=bearer(human),
    )

    assert conflict.status_code == 409, conflict.text
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-atomic'")
    row = database.read(
        "SELECT status, decided_by_id FROM project_creation_requests WHERE request_id = ?",
        (request_id,),
    )[0]
    assert row["status"] == "pending" and row["decided_by_id"] is None


def test_revised_approval_creates_final_ids_and_keeps_original_audit(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    request_id = _apply(client, agent, _application("P-original", "G-original")).json()["request"]["request_id"]

    approved = client.post(
        f"/api/v1/project-creation-requests/{request_id}/approve",
        json={
            "decided_by": OPERATOR,
            "reason": "修订 ID 以隔离既有命名空间",
            "project_id": "P-final", "project_name": "最终项目",
            "group_id": "G-final", "group_name": "最终主群",
        },
        headers=bearer(human),
    )

    assert approved.status_code == 200, approved.text
    row = approved.json()["request"]
    assert row["project_id"] == "P-final" and row["group_id"] == "G-final"
    assert row["original"] == {
        "project_id": "P-original", "project_name": "授权项目",
        "group_id": "G-original", "group_name": "授权项目主群",
    }
    assert row["revision"]["project_id"] == "P-final"
    assert row["revision"]["reason"] == "修订 ID 以隔离既有命名空间"
    assert row["revision"]["revised_by_id"] == OPERATOR
    assert database.read("SELECT 1 FROM projects WHERE project_id = 'P-final'")
    assert database.read("SELECT 1 FROM groups WHERE group_id = 'G-final'")
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-original'")
    audit = database.read(
        "SELECT original_project_id, final_project_id, reason, revised_by_id"
        " FROM project_creation_request_revisions WHERE request_id = ?", (request_id,)
    )[0]
    assert dict(audit) == {
        "original_project_id": "P-original", "final_project_id": "P-final",
        "reason": "修订 ID 以隔离既有命名空间", "revised_by_id": OPERATOR,
    }
    # 同一申请重复批准仍然冲突，不可能创建第二份项目。
    duplicate = client.post(
        f"/api/v1/project-creation-requests/{request_id}/approve",
        json={"decided_by": OPERATOR, "reason": "再次批准"},
        headers=bearer(human),
    )
    assert duplicate.status_code == 409
    assert len(database.read("SELECT project_id FROM projects WHERE project_id = 'P-final'")) == 1


def test_revised_rejection_keeps_revision_audit_but_creates_nothing(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    request_id = _apply(client, agent, _application("P-original", "G-original")).json()["request"]["request_id"]

    rejected = client.post(
        f"/api/v1/project-creation-requests/{request_id}/reject",
        json={
            "decided_by": OPERATOR, "reason": "修订后仍不符合预算",
            "project_id": "P-revised-rejected", "project_name": "修订后拒绝项目",
            "group_id": "G-revised-rejected", "group_name": "修订后拒绝群",
        },
        headers=bearer(human),
    )

    assert rejected.status_code == 200, rejected.text
    row = rejected.json()["request"]
    assert row["status"] == "rejected"
    assert row["project_id"] == "P-revised-rejected"
    assert row["original"]["project_id"] == "P-original"
    assert row["revision"]["group_id"] == "G-revised-rejected"
    assert not database.read("SELECT 1 FROM projects WHERE project_id IN ('P-original', 'P-revised-rejected')")
    assert not database.read("SELECT 1 FROM groups WHERE group_id IN ('G-original', 'G-revised-rejected')")
    assert database.read("SELECT 1 FROM project_creation_request_revisions WHERE request_id = ?", (request_id,))


def test_revision_requires_reason_and_does_not_partially_write(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    request_id = _apply(client, agent, _application("P-original", "G-original")).json()["request"]["request_id"]

    response = client.post(
        f"/api/v1/project-creation-requests/{request_id}/approve",
        json={"decided_by": OPERATOR, "reason": " ", "project_id": "P-final"},
        headers=bearer(human),
    )

    assert response.status_code in {400, 422}, response.text
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-final'")
    assert not database.read("SELECT 1 FROM project_creation_request_revisions")
    row = database.read("SELECT project_id, status FROM project_creation_requests WHERE request_id = ?", (request_id,))[0]
    assert row["project_id"] == "P-original" and row["status"] == "pending"

    blank = client.post(
        f"/api/v1/project-creation-requests/{request_id}/approve",
        json={"decided_by": OPERATOR, "reason": "清空名称", "project_name": "   "},
        headers=bearer(human),
    )
    assert blank.status_code in {400, 422}, blank.text
    row = database.read("SELECT project_name, status FROM project_creation_requests WHERE request_id = ?", (request_id,))[0]
    assert row["project_name"] == "授权项目" and row["status"] == "pending"


def test_revision_conflict_rolls_back_revision_and_creation(tmp_path, monkeypatch):
    client, database, human, agent = _env(tmp_path, monkeypatch)
    request_id = _apply(client, agent, _application("P-original", "G-original")).json()["request"]["request_id"]
    assert client.post(
        "/api/v1/projects", json={"project_id": "P-other", "name": "其他项目"}, headers=bearer(human)
    ).status_code == 200
    assert client.post(
        "/api/v1/projects/P-other/groups",
        json={"group_id": "G-final", "name": "已存在群"}, headers=bearer(human),
    ).status_code == 200

    response = client.post(
        f"/api/v1/project-creation-requests/{request_id}/approve",
        json={"decided_by": OPERATOR, "reason": "尝试修订", "project_id": "P-final", "group_id": "G-final"},
        headers=bearer(human),
    )

    assert response.status_code == 409, response.text
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'P-final'")
    assert not database.read("SELECT 1 FROM project_creation_request_revisions")
    row = database.read("SELECT project_id, group_id, status FROM project_creation_requests WHERE request_id = ?", (request_id,))[0]
    assert row["project_id"] == "P-original" and row["group_id"] == "G-original" and row["status"] == "pending"


def test_requests_are_readable_with_scope_and_decision_facts(tmp_path, monkeypatch):
    client, _database, human, agent = _env(tmp_path, monkeypatch)
    request_id = _apply(client, agent, _application()).json()["request"]["request_id"]

    listed = client.get(
        "/api/v1/project-creation-requests", params={"status": "pending"}, headers=bearer(human)
    )

    assert listed.status_code == 200, listed.text
    rows = listed.json()["requests"]
    assert len(rows) == 1
    assert rows[0]["request_id"] == request_id
    assert rows[0]["scope"] and rows[0]["expected_output"]
