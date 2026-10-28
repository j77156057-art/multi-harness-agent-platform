"""窗口的凭据：看得到哪条在用，收得回多余的那条。

平台只存 ``auth_tokens.token_hash``，凭据明文只在本机 ``data/<窗口>.key`` 里。
于是一个被重复签发的窗口会攒下多条**仍然有效**的凭据，谁也说不出哪条是哪条 ——
这一面就是补上"看得到、收得回"。四条边界在这里钉住：

* 列表给的是**指纹**，凭据明文一个字都不出现；
* 在用与不在用分得清（在用的那条按窗口自己的 key 文件认出来）；
* 吊销不在用的那条，窗口照常能用；
* 吊销**在用**的那条要先说清楚（``force``），因为它会让窗口下线；
* 这一面只有人能碰，窗口自己的凭据拿不到。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth.tokens import TokenInvalid, TokenService, hash_token
from app.config import settings
from tests.api_support import make_client

OPERATOR = "operator"
HARNESS = "codex-member-9"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def fingerprint_of(raw: str) -> str:
    return hash_token(raw)[:19]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=HARNESS,
            name=HARNESS,
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "data_dir", data_dir)

    service = TokenService(database)
    human = service.issue(subject_type="human", subject_id=OPERATOR)
    stale = service.issue(subject_type="harness", subject_id=HARNESS, label="上一轮签发")
    current = service.issue(subject_type="harness", subject_id=HARNESS, label="这一轮签发")
    # 窗口此刻真正在用的那条，就写在它自己的 key 文件里。
    (data_dir / f"{HARNESS}.key").write_text(f"{current}\n", encoding="utf-8")

    return SimpleNamespace(
        client=client,
        database=database,
        data_dir=data_dir,
        human=human,
        stale=stale,
        current=current,
    )


def credentials(env) -> dict[str, object]:
    response = env.client.get(
        f"/api/v1/harnesses/{HARNESS}/credentials", headers=bearer(env.human)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_list_marks_the_one_the_window_is_using(env):
    payload = credentials(env)

    rows = {str(row["label"]): row for row in payload["credentials"]}
    assert set(rows) == {"上一轮签发", "这一轮签发"}
    assert rows["这一轮签发"]["in_use"] is True
    assert rows["这一轮签发"]["status"] == "in_use"
    assert rows["上一轮签发"]["in_use"] is False
    assert rows["上一轮签发"]["status"] == "valid"
    assert payload["live"] == 2
    assert payload["key_files"] == [f"{HARNESS}.key"]
    assert rows["这一轮签发"]["key_file"] == f"{HARNESS}.key"
    assert rows["上一轮签发"]["key_file"] is None

    # 只有指纹，没有任何一条凭据的明文。
    printed = json.dumps(payload, ensure_ascii=False)
    assert env.stale not in printed
    assert env.current not in printed
    assert rows["上一轮签发"]["fingerprint"] == fingerprint_of(env.stale)


def test_the_in_use_row_is_found_by_hash_not_by_file_name(tmp_path, monkeypatch):
    """文件名是前人留下的约定，不是事实。

    真实平台里 ``pi`` 这个窗口的凭据在 ``data/game-pi.key`` 里。只按
    ``<窗口>.key`` 找会把"哪条在用"全部落空 —— 正在用的那条会被摆上吊销按钮，
    一点就把窗口弄下线。所以按哈希在**本机所有 key 文件**里认。
    """
    client, database = make_client(tmp_path)
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id="pi",
            name="Pi CLI",
            provider="ollama",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "data_dir", data_dir)

    service = TokenService(database)
    human = service.issue(subject_type="human", subject_id=OPERATOR)
    current = service.issue(subject_type="harness", subject_id="pi", label="这一轮签发")
    stale = service.issue(subject_type="harness", subject_id="pi", label="上一轮签发")
    (data_dir / "game-pi.key").write_text(f"{current}\n", encoding="utf-8")
    (data_dir / "notes.txt").write_text("不该被当成凭据\n", encoding="utf-8")

    payload = client.get("/api/v1/harnesses/pi/credentials", headers=bearer(human)).json()

    rows = {str(row["label"]): row for row in payload["credentials"]}
    assert rows["这一轮签发"]["in_use"] is True
    assert rows["这一轮签发"]["key_file"] == "game-pi.key"
    assert rows["上一轮签发"]["in_use"] is False
    assert rows["上一轮签发"]["key_file"] is None
    assert payload["key_files"] == ["game-pi.key"]


def test_revoking_the_unused_one_leaves_the_window_working(env):
    response = env.client.post(
        f"/api/v1/harnesses/{HARNESS}/credentials/revoke",
        json={
            "fingerprint": fingerprint_of(env.stale),
            "decided_by": OPERATOR,
            "reason": "旧的那条早就不用了",
        },
        headers=bearer(env.human),
    )

    assert response.status_code == 200, response.text
    assert response.json()["revoked"] is True
    assert response.json()["was_in_use"] is False

    service = TokenService(env.database)
    with pytest.raises(TokenInvalid, match="token_revoked"):
        service.verify(env.stale)
    assert service.verify(env.current).subject_id == HARNESS

    payload = credentials(env)
    rows = {str(row["label"]): row for row in payload["credentials"]}
    assert rows["上一轮签发"]["status"] == "revoked"
    assert rows["上一轮签发"]["revoked_reason"] == "旧的那条早就不用了"
    assert payload["live"] == 1


def test_revoking_the_one_in_use_needs_to_be_asked_for(env):
    body = {"fingerprint": fingerprint_of(env.current), "decided_by": OPERATOR}

    refused = env.client.post(
        f"/api/v1/harnesses/{HARNESS}/credentials/revoke", json=body, headers=bearer(env.human)
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "credential.in_use"
    # 拒绝了就是什么都没发生：窗口还能用它那条凭据。
    assert TokenService(env.database).verify(env.current).subject_id == HARNESS

    forced = env.client.post(
        f"/api/v1/harnesses/{HARNESS}/credentials/revoke",
        json={**body, "force": True},
        headers=bearer(env.human),
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["was_in_use"] is True
    with pytest.raises(TokenInvalid, match="token_revoked"):
        TokenService(env.database).verify(env.current)


def test_an_unknown_fingerprint_reports_not_found(env):
    response = env.client.post(
        f"/api/v1/harnesses/{HARNESS}/credentials/revoke",
        json={"fingerprint": "sha256:000000000000", "decided_by": OPERATOR},
        headers=bearer(env.human),
    )

    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "credential.not_found"


def test_the_declared_identity_must_be_the_token_holder(env):
    response = env.client.post(
        f"/api/v1/harnesses/{HARNESS}/credentials/revoke",
        json={"fingerprint": fingerprint_of(env.stale), "decided_by": "somebody-else"},
        headers=bearer(env.human),
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.permission_denied"
    # 身份对不上的那次没有吊销任何东西。
    assert TokenService(env.database).verify(env.stale).subject_id == HARNESS


def test_a_window_cannot_read_its_own_credentials(env):
    """凭据归人管：窗口拿着自己的 token 也看不到、收不回同类的凭据。"""
    response = env.client.get(
        f"/api/v1/harnesses/{HARNESS}/credentials", headers=bearer(env.current)
    )

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "auth.permission_denied"
