"""人确认之后才合入主树（§17「merge/integration write：需二次确认」的 HTTP 面）。

此前平台没有给合并留 HTTP 面：审批一过，运行环自己就把活合了 —— 谁都没按这个键，
出问题时说不清是谁让它进的主树。这个口子把**决定权**交回给人，同时**不**把写权限
散出去：真正写集成分支的仍是 MergeWorker，闸门一条都不跳。

二次确认是手敲 commit，不是点按钮：合入不可撤销（只能 revert），而"点错一行"在列表
里太容易发生。
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api import routes as routes_module
from app.api.app import create_app
from app.auth.tokens import TokenService
from app.config import settings
from app.runtime.driver import RuntimePolicy
from app.verify.profile import VerifyPolicy
from tests.merge_support import REVIEWER, approve, build_chain, register_harness, status_of

LOOPBACK = "http://127.0.0.1"
OPERATOR = "operator"


def _passing_policy() -> RuntimePolicy:
    """合入后的集成检查：一条永远通过的命令。

    真实部署里这份 policy 来自仓库的 `verify-profiles.json`；测试里换成"必过"，
    因为要测的是**谁有权触发合并**，不是集成检查本身（那是 test_merge 的事）。
    """
    return RuntimePolicy(
        verify=VerifyPolicy.from_mapping(
            {
                "command_roots": ["python"],
                "profiles": {
                    "always-pass": {
                        "command": ["python", "-c", "import sys; sys.exit(0)"],
                        "timeout_seconds": 60,
                    }
                },
            }
        ),
        integration_profile="always-pass",
        source=None,
    )


@pytest.fixture(autouse=True)
def _policy(monkeypatch):
    monkeypatch.setattr(routes_module, "load_runtime_policy", lambda *_a, **_k: _passing_policy())


def _client(chain) -> TestClient:
    return TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)


def _human(chain) -> str:
    return TokenService(chain.database).issue(subject_type="human", subject_id=OPERATOR)


def _harness(chain, harness_id: str = "harness-1") -> str:
    return TokenService(chain.database).issue(subject_type="harness", subject_id=harness_id)


def _post_merge(client: TestClient, token: str, payload: dict, *, tries: int = 4):
    """点一次"确认合入"，对**这台机器的偶发 git 写失败**重试。

    这不是给被测逻辑兜底：拒绝分两种，`merge.confirm_mismatch`（我们的判定）和
    `merge.refused` + "could not write the merge ref"（这台机器上 git 偶发写不了
    ref，隔一下就好，mergeguard 对同一现象也是整轮重试）。只对后者重试，前者立刻
    失败 —— 否则权限或结构改坏了，测试会靠重试"蒙混过关"。
    """
    response = client.post("/api/v1/human/merge", json=payload,
                           headers={"Authorization": f"Bearer {token}"})
    for _ in range(max(0, tries - 1)):
        body = response.text or ""
        if response.status_code != 409 or "merge.refused" not in body:
            return response
        if "could not write the merge ref" not in body:
            return response
        time.sleep(2.0)
        response = client.post("/api/v1/human/merge", json=payload,
                               headers={"Authorization": f"Bearer {token}"})
    return response


def test_a_human_confirms_and_the_platform_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    chain = approve(build_chain(tmp_path))
    token = _human(chain)

    response = _post_merge(
        _client(chain),
        token,
        {
            "task_id": chain.task_id,
            "decided_by": OPERATOR,
            "confirm_commit": chain.head_commit,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "merged", body
    assert body["confirmed_by"] == OPERATOR
    assert body["merge_commit"]
    assert status_of(chain) == "merged"


def test_the_administrator_harness_may_merge_when_delegated(tmp_path, monkeypatch):
    """管理员 harness 经 `AP_ADMIN_SUBJECTS` 委托可以发起合入（仍需二次确认）。

    主人 2026-10-31 的要求：审核通过之后由管理员把改动送进主树。授权范围没有变宽 ——
    写集成分支的仍只有 MergeWorker，闸门一条都不跳；这里放行的只是"能不能发起这一下"。
    """
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({"admin-window"}))
    chain = approve(build_chain(tmp_path))
    register_harness(chain.database, "admin-window", provider="anthropic")
    token = _harness(chain, "admin-window")

    response = _post_merge(
        _client(chain),
        token,
        {
            "task_id": chain.task_id,
            "decided_by": "admin-window",
            "confirm_commit": chain.head_commit,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "merged"
    assert response.json()["confirmed_by"] == "admin-window"
    assert status_of(chain) == "merged"


def test_a_member_harness_still_cannot_merge(tmp_path, monkeypatch):
    """没被列入委托白名单的成员 harness，照样是 403。"""
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({"admin-window"}))
    chain = approve(build_chain(tmp_path))
    register_harness(chain.database, "harness-9", provider="anthropic")
    token = _harness(chain, "harness-9")

    response = _client(chain).post(
        "/api/v1/human/merge",
        json={
            "task_id": chain.task_id,
            "decided_by": "harness-9",
            "confirm_commit": chain.head_commit,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403, response.text
    assert status_of(chain) == "approved", "a refused merge must change nothing"


def test_the_confirmation_must_name_the_commit_being_merged(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    chain = approve(build_chain(tmp_path))
    token = _human(chain)

    response = _client(chain).post(
        "/api/v1/human/merge",
        json={
            "task_id": chain.task_id,
            "decided_by": OPERATOR,
            "confirm_commit": "0000000",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "merge.confirm_mismatch"
    # 拒绝时把"真正会被合的那一版"说出来，人不用再猜。
    assert error["details"]["commit"] == chain.head_commit
    assert status_of(chain) == "approved"


def test_a_task_that_has_not_passed_review_cannot_be_merged(tmp_path, monkeypatch):
    """闸门在前面：没过审的活没有 `approved`，人也不许硬合。"""
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    chain = build_chain(tmp_path)  # awaiting_review，没有 approve()
    token = _human(chain)

    response = _client(chain).post(
        "/api/v1/human/merge",
        json={
            "task_id": chain.task_id,
            "decided_by": OPERATOR,
            "confirm_commit": chain.head_commit,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "merge.not_approved"
    assert status_of(chain) == "awaiting_review"
