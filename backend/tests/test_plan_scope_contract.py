"""计划范围契约：goal 要动的路径必须在 ``exclusive_paths`` 里。

2026-11-05 的 e16 是这条契约的反例：goal 写着 ``via conftest.py``，契约里
没有 ``tests/conftest.py``，成员照着 goal 做、提交时被范围闸门拒掉，整轮重跑。

现在这条矛盾在**批准层**就拒绝（``plan.scope_out_of_contract``），计划保持
draft；确需放行时 approve 带 ``allow_out_of_contract_paths=true``，放行清单
写进 ``plan.approve`` 事件的 ``scope_override``，事后查得到。
"""

from __future__ import annotations

import json

from tests.api_support import (
    bootstrap,
    make_client,
    publish_plan,
    seed_repository_profile,
)


def _plan_with(goal: str, exclusive_paths: list[str], *, plan_id: str = "PL-1") -> dict:
    return {
        "id": "T-1",
        "goal": goal,
        "exclusive_paths": exclusive_paths,
    }


def test_goal_paths_inside_the_contract_are_approved(tmp_path):
    """契约内放行：goal 说的文件和 exclusive_paths 对得上，批准照常。"""
    client, _database = make_client(tmp_path)
    bootstrap(client)
    # 这条用例只关心范围契约；合并检查用一个必然通过的空 profile 顶上。
    seed_repository_profile(client, tmp_path)
    publish_plan(
        client,
        tasks=[_plan_with("在 tests/unit 里新增 tests/unit/test_a.py", ["tests/unit/**"])],
    )

    response = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})

    assert response.status_code == 200, response.text
    assert response.json()["scope_warnings"] == []
    # 没有偏离就没有逃生口字段：老响应形状保持不变。
    assert "scope_override" not in response.json()


def test_out_of_contract_goal_path_is_rejected_at_approval(tmp_path):
    """契约外拒绝：409 + 明确错误码，消息与 details 都带违规路径。"""
    client, database = make_client(tmp_path)
    bootstrap(client)
    # 合并检查用一个必然通过的 profile 顶上，确保这条用例测的是范围契约，
    # 而不是先被 profile_unrunnable 拦下。
    seed_repository_profile(client, tmp_path)
    publish_plan(
        client,
        tasks=[_plan_with("修改 tests/conftest.py", ["backend/app/**"])],
    )

    response = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "plan.scope_out_of_contract"
    assert error["retryable"] is False
    assert "tests/conftest.py" in error["message"]
    assert error["details"]["denied_paths"] == ["tests/conftest.py"]
    assert error["details"]["violations"] == [
        {
            "task_id": "T-1",
            "path": "tests/conftest.py",
            "exclusive_paths": ["backend/app/**"],
        }
    ]
    assert error["details"]["plan_status"] == "draft"
    # 拒绝不改状态：补契约后可以直接再 approve，不需要重交方案。
    plans = database.read("SELECT status FROM plans WHERE plan_id = 'PL-1'")
    assert plans[0]["status"] == "draft"

    preflight = client.get("/api/v1/plans/PL-1/assignability")
    assert preflight.status_code == 200
    body = preflight.json()
    assert body["approvable"] is False
    assert body["scope"]["violations"][0]["path"] == "tests/conftest.py"


def test_escape_hatch_approves_and_records_the_override(tmp_path):
    """逃生口：显式放行时批准成功，且违规清单落进 plan.approve 事件。"""
    client, database = make_client(tmp_path)
    bootstrap(client)
    seed_repository_profile(client, tmp_path)
    publish_plan(
        client,
        tasks=[_plan_with("修改 tests/conftest.py", ["backend/app/**"])],
    )

    response = client.post(
        "/api/v1/plans/PL-1/approve",
        json={"decided_by": "human-1", "allow_out_of_contract_paths": True},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope_override"]["applied"] is True
    assert body["scope_override"]["paths"] == ["tests/conftest.py"]

    events = database.read(
        "SELECT payload_json FROM task_events"
        " WHERE event_name IN ('plan.approve', 'plan.approve_dependency_pending')"
        "   AND aggregate_id = 'T-1'"
        " ORDER BY seq DESC LIMIT 1"
    )
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload["scope_override"] == [
        {
            "task_id": "T-1",
            "path": "tests/conftest.py",
            "exclusive_paths": ["backend/app/**"],
        }
    ]
