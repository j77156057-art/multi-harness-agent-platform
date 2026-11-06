from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.admin.experience import ExperienceStore
from app.admin.injection import confirmed_prompt, decide_experience, set_injection_enabled
from app.api.app import create_app
from app.api.routes import _experience_context
from app.auth.tokens import TokenService
from app.config import settings
from app.tasks.service import Conflict, SchedulerService
from tests.claim_support import DEFAULT_GROUP
from tests.merge_support import register_harness
from tests.test_admin_harness import (
    ADMIN_WINDOW,
    MEMBER,
    SPEC,
    ScriptedRunner,
    _admin_chain,
    _envelope,
    _refine_service,
)


def _candidate(store: ExperienceStore, task: dict, *, source: str = "backfill") -> dict:
    spec = task.get("spec") if isinstance(task.get("spec"), dict) else {}
    project, category, summary = _experience_context(task, spec)
    store.record_outcome(
        kind="request_changes",
        project=project,
        task_category=category,
        executor=MEMBER,
        spec_summary=summary,
        findings_summary="交付路径不明确，成员不知道该写到哪里",
        root_cause="产出物路径不明确，成员不知道该写到哪里",
        action="交付路径必须明确固定为 docs/startup.md",
        result="验收未通过",
        contract_id=str(task["task_id"]),
        recorded_at="",
        source=source,
        source_task_id=str(task["task_id"]),
        source_event_id=f"review:{task['task_id']}",
        source_snapshot={"task_id": task["task_id"], "status": task["status"]},
    )
    record = store.records()[0]
    return {"project": project, "category": category, "record": record}


def test_confirm_inject_revoke_and_audit_through_real_api(tmp_path, monkeypatch):
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({ADMIN_WINDOW}))
    store = ExperienceStore(tmp_path / "admin" / "experience.json")
    task = SchedulerService(chain.database).get_task("T-1")
    candidate = _candidate(store, task)
    experience_id = candidate["record"]["experience_id"]
    tokens = TokenService(chain.database)
    human_token = tokens.issue(subject_type="human", subject_id="human-1")
    register_harness(chain.database, ADMIN_WINDOW, provider="qoder")
    admin_token = tokens.issue(subject_type="harness", subject_id=ADMIN_WINDOW)
    api = TestClient(create_app(chain.database, chain.repo), base_url="http://127.0.0.1")

    def post(path, body, token=human_token):
        response = api.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        return response.json()

    def refine(runner):
        return _refine_service(chain, runner, experience=store).refine(
            requirement="帮我加一份启动说明",
            group_id=DEFAULT_GROUP,
            project_name=candidate["project"],
        )

    # 回填缺时间保留为空；默认未确认，即使总开关开启也不注入。
    assert candidate["record"]["recorded_at"] == ""
    post("/api/v1/experience-injection", {"enabled": True, "decided_by": "human-1"})
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    refine(runner)
    assert "check_type: path" not in runner.calls[0]["argv"][-1]

    decision_path = f"/api/v1/tasks/T-1/experiences/{experience_id}/decision"
    confirmed = post(decision_path, {"action": "confirm", "decided_by": "human-1"})
    assert confirmed["confirmation"]["status"] == "confirmed"
    assert confirmed["confirmation"]["decided_by"] == "human-1"
    assert confirmed["confirmation"]["decided_at"]

    runner = ScriptedRunner(stdout=_envelope(SPEC))
    result = refine(runner)
    prompt = runner.calls[0]["argv"][-1]
    # 缺项触发只注入“检查类型”，旧值本身绝不能进入提示词。
    assert "check_type: path" in prompt
    assert "docs/startup.md" not in prompt
    assert result["experience_injection"]["triggered"] is True
    assert result["experience_injection"]["check_types"] == ["path"]
    assert result["experience_injection_count"] == 1
    new_task_id = result["tasks"][0]["id"]

    # 经过真实 plan_draft 后，审计可以按新任务 ID 反查。
    post("/api/v1/adapter/admin/plan_draft", {
        "group_id": DEFAULT_GROUP,
        "title": "经验注入验证",
        "goal": "帮我加一份启动说明",
        "tasks": result["tasks"],
        "reviewer_harness_ids": [MEMBER],
        "opened_by": ADMIN_WINDOW,
    }, token=admin_token)
    audited = api.get(
        f"/api/v1/tasks/{new_task_id}/experience-injections",
        headers={"Authorization": f"Bearer {human_token}"},
    )
    assert audited.status_code == 200, audited.text
    (audit,) = audited.json()["injections"]
    assert audit["experience_id"] == experience_id
    assert audit["task_id"] == new_task_id
    assert audit["confirmed_by"] == "human-1"
    assert audit["layer"] == "admin_dispatch"
    assert audit["injected_at"]

    revoked = post(decision_path, {"action": "revoke", "decided_by": "human-1"})
    assert revoked["confirmation"]["status"] == "revoked"
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    refine(runner)
    assert "check_type: path" not in runner.calls[0]["argv"][-1]

    ignored = post(decision_path, {"action": "ignore", "decided_by": "human-1"})
    assert ignored["confirmation"]["status"] == "ignored"
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    refine(runner)
    assert "check_type: path" not in runner.calls[0]["argv"][-1]

    # 重新确认后，一键关闭总开关也立即阻断；历史审计保留用于追责。
    post(decision_path, {"action": "confirm", "decided_by": "human-1"})
    post("/api/v1/experience-injection", {"enabled": False, "decided_by": "human-1"})
    runner = ScriptedRunner(stdout=_envelope(SPEC))
    refine(runner)
    assert "check_type: path" not in runner.calls[0]["argv"][-1]
    assert len(api.get(
        f"/api/v1/tasks/{new_task_id}/experience-injections",
        headers={"Authorization": f"Bearer {human_token}"},
    ).json()["injections"]) == 1


def test_cross_project_confirmation_is_rejected_without_explicit_scope(tmp_path):
    chain = _admin_chain(tmp_path)
    store = ExperienceStore(tmp_path / "experience.json")
    task = SchedulerService(chain.database).get_task("T-1")
    candidate = _candidate(store, task, source="live")
    record = store.records()[0]
    other = store.record_outcome(
        kind="request_changes",
        project="P-other",
        task_category=candidate["category"],
        executor=MEMBER,
        spec_summary="其他项目规格",
        findings_summary="其他项目失败",
        root_cause="其他项目失败",
        action="返修",
        result="失败",
    )

    with pytest.raises(Conflict) as caught:
        decide_experience(
            chain.database,
            store,
            task=task,
            project=candidate["project"],
            task_category=candidate["category"],
            experience_id=other,
            action="confirm",
            decided_by="human-1",
        )
    assert caught.value.code == "experience.cross_project_unconfirmed"
    assert record["experience_id"] != other


def test_injection_decision_rejects_harness_identity(tmp_path, monkeypatch):
    chain = _admin_chain(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    store = ExperienceStore(tmp_path / "admin" / "experience.json")
    task = SchedulerService(chain.database).get_task("T-1")
    candidate = _candidate(store, task)
    token = TokenService(chain.database).issue(subject_type="harness", subject_id=MEMBER)
    api = TestClient(create_app(chain.database, chain.repo), base_url="http://127.0.0.1")

    response = api.post(
        f"/api/v1/tasks/T-1/experiences/{candidate['record']['experience_id']}/decision",
        json={"action": "confirm", "decided_by": MEMBER},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert json.loads(response.text)["error"]["code"] == "auth.permission_denied"


def test_no_lesson_never_injects_even_when_confirmed_and_enabled(tmp_path):
    chain = _admin_chain(tmp_path)
    store = ExperienceStore(tmp_path / "experience.json")
    task = SchedulerService(chain.database).get_task("T-1")
    spec = task.get("spec") if isinstance(task.get("spec"), dict) else {}
    project, category, summary = _experience_context(task, spec)
    store.record_merged(
        project=project,
        task_category=category,
        executor=MEMBER,
        spec_summary=summary,
    )
    experience_id = store.records()[0]["experience_id"]
    decide_experience(
        chain.database,
        store,
        task=task,
        project=project,
        task_category=category,
        experience_id=experience_id,
        action="confirm",
        decided_by="human-1",
    )
    set_injection_enabled(chain.database, enabled=True, decided_by="human-1")

    prompt, selections = confirmed_prompt(
        chain.database, store, group_id=DEFAULT_GROUP, project_name=project
    )
    assert prompt == "" and selections == []
