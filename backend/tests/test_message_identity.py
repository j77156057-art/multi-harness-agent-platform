"""发消息的人必须就是令牌主体。

2026-11-01 实测撞到的：`POST /groups/{g}/messages` 原来既没有鉴权，`author_id` 也直接
取自请求体 —— 拿人的令牌以 `operator` 的名义发一条，结果**本人反而读不到自己那条**
（定向消息按观看人过滤）。往深里说，任何人都能以管理员的名义发消息，而"谁说的"是分发
与审批的依据：管理员 worker 会把那条当成自己的口吻继续处理。

这里钉住四条：
1. 作者与令牌不一致 → 403（不落库）；
2. 类型不一致（人冒充 harness、或反过来）→ 403；
3. 没有令牌 → 401；
4. 作者就是令牌主体时照常发出去，并能在读路径上看到。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.auth.tokens import TokenService
from app.config import settings
from tests.claim_support import DEFAULT_GROUP, make_database, seed_project

LOOPBACK = "http://127.0.0.1"
OPERATOR = "operator"


def _client(database, repo):
    return TestClient(create_app(database, repo), base_url=LOOPBACK)


def _human_token(database) -> str:
    return TokenService(database).issue(subject_type="human", subject_id=OPERATOR)


def _post(client: TestClient, token: str | None, payload: dict):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    return client.post(f"/api/v1/groups/{DEFAULT_GROUP}/messages", json=payload, headers=headers)


def _payload(**overrides) -> dict:
    body = {
        "author_kind": "human",
        "author_id": OPERATOR,
        "body": "这条消息是给自检用的",
        "mentions": [],
        "refs": [],
        "direct": False,
    }
    body.update(overrides)
    return body


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    database = make_database(tmp_path)
    seed_project(database)
    return _client(database, None), database


def test_a_message_cannot_be_sent_under_somebody_elses_name(tmp_path, monkeypatch):
    client, database = _setup(tmp_path, monkeypatch)
    response = _post(client, _human_token(database), _payload(author_id="office-admin"))
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["permission"] == "message:post"
    # 不落库：别人的名义不能被写成一条事实。
    assert database.read("SELECT * FROM task_messages") == []


def test_a_human_cannot_speak_as_a_harness(tmp_path, monkeypatch):
    client, database = _setup(tmp_path, monkeypatch)
    response = _post(client, _human_token(database), _payload(author_kind="harness"))
    assert response.status_code == 403, response.text
    assert database.read("SELECT * FROM task_messages") == []


def test_posting_without_a_token_is_refused_under_enforce(tmp_path, monkeypatch):
    client, _ = _setup(tmp_path, monkeypatch)
    response = _post(client, None, _payload())
    assert response.status_code == 401, response.text


def test_the_right_author_is_accepted_and_readable(tmp_path, monkeypatch):
    client, database = _setup(tmp_path, monkeypatch)
    token = _human_token(database)
    sent = _post(client, token, _payload())
    assert sent.status_code == 200, sent.text
    read = client.get(
        f"/api/v1/groups/{DEFAULT_GROUP}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert read.status_code == 200, read.text
    bodies = [row["body"] for row in read.json()["messages"]]
    assert "这条消息是给自检用的" in bodies


def test_auth_off_still_accepts_the_demo_path(tmp_path, monkeypatch):
    """认证关闭的单机演示没有主体可绑，这条路径不能被误伤。"""
    monkeypatch.setattr(settings, "auth_mode", "off")
    database = make_database(tmp_path)
    seed_project(database)
    client = _client(database, None)
    response = _post(client, None, _payload())
    assert response.status_code == 200, response.text
