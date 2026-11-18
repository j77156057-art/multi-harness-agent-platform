"""「自己加的窗口」能不能删 —— 登记表的删除面。

平台上登记的窗口分两类。**基座**是 CLI 本身（``claude-code``、
``codebuddy``……），删了它就没有这个 CLI 了；**实例**是同一个 CLI 再开一个身份
（``codebuddy-member-arch``、``架构 · Arch``），那是**人自己加的**，加错了
就该能删。注册表此前只有登记、没有删除，于是加错的窗口永远摆在那，选成员时它
还在候选里（2026-11-18）。

这些测试钉住四件事：

1. 「是不是实例」只有一个答案 —— 后端与办公室前端按同一个形状判
   （``<base>-member-<slot>`` / ``<base>-window-<slot>``）。
2. 只有实例窗口能删；基座被**明确拒绝并说清原因**，不是静默无效。
3. 删掉实例窗口**不动登记历史**（``harness_registrations`` 行留着）—— 领过活
   的任务 Claim 钉住的那个版本还得能查回来。
4. 权限面沿用 ``harness:register``：只有人能删，窗口令牌改不了（403）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapter.registry import HarnessRegistry
from app.api.app import create_app
from app.auth.tokens import TokenService
from app.config import settings
from tests.claim_support import make_database
from tests.merge_support import register_harness

LOOPBACK = "http://127.0.0.1"
BASE = "codebuddy"
INSTANCE = "codebuddy-member-arch"


def _registry(tmp_path) -> HarnessRegistry:
    return HarnessRegistry(make_database(tmp_path))


@pytest.mark.parametrize(
    ("harness_id", "expected"),
    [
        ("codebuddy-member-arch", True),
        ("codebuddy-window-1", True),
        ("claude-code-member-1", True),
        ("qoder-window-42", True),
        ("claude-code", False),
        ("qoder-cli-cn", False),
        ("office-assistant", False),
        ("codebuddy-admin", False),
        ("codebuddy-member", False),
        ("", False),
    ],
)
def test_is_instance_window_uses_one_shape_everywhere(tmp_path, harness_id, expected):
    """办公室前端（``isInstanceWindow``）与后端必须是同一个答案。

    两边判得不一样时，人在界面上看得到「删除」按钮，点下去平台却说
    「这不是一个能删的窗口」—— 前后端对「这是不是一个实例」得是同一件事。
    """
    assert _registry(tmp_path).is_instance_window(harness_id) is expected


def test_an_instance_window_can_be_removed(tmp_path):
    """自己加的窗口删掉之后，它不再是登记表里的人。"""
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    register_harness(database, INSTANCE, provider="tencent")

    result = registry.unregister(INSTANCE)

    assert result["removed"] is True
    assert result["harness_id"] == INSTANCE
    assert registry.current(INSTANCE) is None
    # 名册里也没有它了 —— 界面上「选谁当成员」不该再把它列出来。
    assert INSTANCE not in [row["harness_id"] for row in registry.listing()]


def test_removal_is_a_soft_delete_so_the_row_stays(tmp_path):
    """删的是「不再用它」，不是「它存在过」。

    登记历史与探测历史都以外键指着这一行，硬删会把「它领过什么活、当时的登记
    版本是什么」一起抹掉 —— 那是**过程证据**。所以行留着，只标上删除时间。
    """
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    register_harness(database, INSTANCE, provider="tencent")

    registry.unregister(INSTANCE)

    row = database.read(
        "SELECT removed_at, status FROM harnesses WHERE harness_id = ?", (INSTANCE,)
    )
    assert len(row) == 1
    assert str(row[0]["removed_at"] or "") != ""
    assert str(row[0]["status"]) == "offline"


def test_removing_a_window_takes_its_credential_back(tmp_path):
    """窗口都删了，它不该还能拿着令牌来调平台。"""
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    register_harness(database, INSTANCE, provider="tencent")
    TokenService(database).issue(
        subject_type="harness", subject_id=INSTANCE, label="test credential"
    )

    registry.unregister(INSTANCE)

    rows = database.read(
        "SELECT revoked_at, revoked_reason FROM auth_tokens WHERE subject_id = ?",
        (INSTANCE,),
    )
    assert len(rows) == 1
    assert str(rows[0]["revoked_at"] or "") != ""
    assert str(rows[0]["revoked_reason"]) == "harness_removed"


def test_a_window_added_again_comes_back(tmp_path):
    """删掉之后又加回来同一个 id：是复活，不是重放 —— 它重新在名册里。"""
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    register_harness(database, INSTANCE, provider="tencent")
    registry.unregister(INSTANCE)

    register_harness(database, INSTANCE, provider="tencent")

    assert registry.current(INSTANCE) is not None
    assert INSTANCE in [row["harness_id"] for row in registry.listing()]


def test_removing_an_instance_keeps_the_registration_history(tmp_path):
    """登记历史是**过程证据**：Claim 钉住的那个版本还得能查回来。

    删窗口是为了「不再用它」，不是为了「抹掉它存在过」—— 所以
    ``harness_registrations`` 的行留着，只标成被取代。
    """
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    register_harness(database, INSTANCE, provider="tencent")

    registry.unregister(INSTANCE)

    history = database.read(
        "SELECT registration_version, superseded_by FROM harness_registrations"
        " WHERE harness_id = ?",
        (INSTANCE,),
    )
    assert len(history) == 1
    assert str(history[0]["registration_version"]) == "reg-001"
    assert str(history[0]["superseded_by"] or "").startswith("removed:")


def test_a_base_harness_cannot_be_removed(tmp_path):
    """基座是 CLI 本身：删了它就没有这个 CLI 了，要说清原因而不是静默无效。"""
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    register_harness(database, BASE, provider="tencent")

    with pytest.raises(ValueError) as excinfo:
        registry.unregister(BASE)

    assert "base harness" in str(excinfo.value)
    assert registry.current(BASE) is not None


def test_removing_an_unknown_window_is_a_key_error(tmp_path):
    registry = _registry(tmp_path)
    with pytest.raises(KeyError):
        registry.unregister("codebuddy-member-ghost")


# ------------------------------------------------------------------ API face


def _client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    database = make_database(tmp_path)
    register_harness(database, BASE, provider="tencent")
    register_harness(database, INSTANCE, provider="tencent")
    tokens = TokenService(database)
    human = tokens.issue(subject_type="human", subject_id="office-operator")
    window = tokens.issue(subject_type="harness", subject_id=INSTANCE)
    client = TestClient(create_app(database), base_url=LOOPBACK)
    return client, database, human, window


def test_the_api_removes_an_instance_window(tmp_path, monkeypatch):
    client, database, human, _ = _client(tmp_path, monkeypatch)

    response = client.delete(
        f"/api/v1/harnesses/{INSTANCE}",
        headers={"Authorization": f"Bearer {human}"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["removed"] is True
    assert HarnessRegistry(database).current(INSTANCE) is None


def test_the_api_refuses_a_base_harness_with_a_reason(tmp_path, monkeypatch):
    client, database, human, _ = _client(tmp_path, monkeypatch)

    response = client.delete(
        f"/api/v1/harnesses/{BASE}",
        headers={"Authorization": f"Bearer {human}"},
    )

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "harness.not_removable"
    assert HarnessRegistry(database).current(BASE) is not None


def test_the_api_reports_an_unknown_window_as_not_found(tmp_path, monkeypatch):
    client, _, human, _ = _client(tmp_path, monkeypatch)

    response = client.delete(
        "/api/v1/harnesses/codebuddy-member-ghost",
        headers={"Authorization": f"Bearer {human}"},
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "harness.not_found"


def test_a_window_token_cannot_remove_any_window(tmp_path, monkeypatch):
    """删注册表是**人**的决定：一个窗口不能自己把同类的窗口删掉。"""
    client, database, _, window = _client(tmp_path, monkeypatch)

    response = client.delete(
        f"/api/v1/harnesses/{INSTANCE}",
        headers={"Authorization": f"Bearer {window}"},
    )

    assert response.status_code == 403, response.text
    assert HarnessRegistry(database).current(INSTANCE) is not None
