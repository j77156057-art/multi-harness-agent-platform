"""用户自己的 API key：加、遮罩、点头才花。

钉住的是三条线：

1. **key 不会漏出去** —— 列表与接口都只给遮罩；
2. **不点头就不花** —— 未授权的 key 不进路由，被问到时反而是平台去问用户；
3. **问一次就够** —— 同一个 key 不会攒出一堆问题。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.api.app import create_app
from app.config import settings
from app.pool import user_keys


@pytest.fixture()
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user's own key store, on a temporary data dir."""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    monkeypatch.setattr("app.pool.sources.discover_upstreams", lambda *a, **k: [], raising=False)
    return tmp_path / "user-keys"


def _client() -> object:
    return create_app().test_client()


def _harness_token(database: object) -> str:
    """A token for a registered harness -- the pool serves harnesses."""
    from app.adapter.protocol import HarnessRegistration
    from app.adapter.registry import HarnessRegistry
    from app.auth.tokens import TokenService

    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id="h-A",
            name="h-A",
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )
    return TokenService(database).issue(subject_type="harness", subject_id="h-A")


def test_a_key_is_stored_and_never_returned(store: Path) -> None:
    key = user_keys.register(
        label="我的 OpenAI",
        protocol="openai_chat",
        base_url="https://api.example.com/v1/",
        key="sk-secret-value-1234",
        models=["gpt-x"],
    )
    assert key.id
    # The key itself is on disk, under the data dir, and only there.
    secret_file = store / f"{key.id}.key"
    assert secret_file.read_text(encoding="utf-8").strip() == "sk-secret-value-1234"
    # What the office may see: no key, only a mask.
    listed = user_keys.entries()[0].as_dict()
    assert "sk-secret-value-1234" not in json.dumps(listed, ensure_ascii=False)
    assert listed["label"] == "我的 OpenAI"
    assert user_keys.masked(key.id).endswith("1234")


def test_a_bad_key_is_refused_with_a_reason(store: Path) -> None:
    with pytest.raises(user_keys.UserKeyError):
        user_keys.register(label="", protocol="openai_chat", base_url="https://x/v1", key="k")
    with pytest.raises(user_keys.UserKeyError):
        user_keys.register(label="x", protocol="telepathy", base_url="https://x/v1", key="k")
    with pytest.raises(user_keys.UserKeyError):
        user_keys.register(label="x", protocol="openai_chat", base_url="ftp://x", key="k")
    with pytest.raises(user_keys.UserKeyError):
        user_keys.register(label="x", protocol="openai_chat", base_url="https://x/v1", key="  ")


def test_a_key_is_not_spendable_until_the_user_says_yes(store: Path) -> None:
    key = user_keys.register(
        label="我的", protocol="openai_chat", base_url="https://api.example.com/v1", key="k-1"
    )
    assert not user_keys.approved(key.id)
    assert user_keys.usable() == []
    user_keys.set_approval(key.id, user_keys.APPROVAL_ONCE)
    assert user_keys.approved(key.id)
    assert len(user_keys.usable()) == 1
    # "once" is spent by the request that used it.
    user_keys.spend_once(key.id)
    assert not user_keys.approved(key.id)


def test_always_stays_until_the_key_is_removed(store: Path) -> None:
    key = user_keys.register(
        label="我的", protocol="openai_chat", base_url="https://api.example.com/v1", key="k-1"
    )
    user_keys.set_approval(key.id, user_keys.APPROVAL_ALWAYS)
    user_keys.spend_once(key.id)
    assert user_keys.approved(key.id), "always 不该被一次请求用掉"
    assert user_keys.remove(key.id)
    assert user_keys.entries() == []


def test_the_question_is_raised_once_per_key(store: Path) -> None:
    key = user_keys.register(
        label="我的", protocol="openai_chat", base_url="https://api.example.com/v1", key="k-1"
    )
    user_keys.raise_request(key_id=key.id, reason="免费额度用完了", task_id="FD-1")
    first = user_keys.pending()
    assert first is not None and first.key_id == key.id
    user_keys.raise_request(key_id=key.id, reason="又来一次", task_id="FD-2")
    second = user_keys.pending()
    assert second is not None
    # 同���个 key 只留一个问题，不攒成一堆。
    assert len(user_keys._read().requests) == 1
    user_keys.clear_request(key.id)
    assert user_keys.pending() is None


def test_the_pool_asks_instead_of_spending(store: Path) -> None:
    """No free and no metered quota: the platform asks, it does not spend."""
    from app.pool.routing import plan_attempt

    key = user_keys.register(
        label="我的", protocol="openai_chat", base_url="https://api.example.com/v1", key="k-1"
    )
    # 一条"这个窗口自己的 provider 挂了"的记录：路由只在这种时候才动池子。
    evidence = store.parent / "formal-runs" / "FD-1.failure.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps(
            {
                "error_type": "provider_quota_exhausted",
                "error": "quota",
                "retryable": True,
            }
        ),
        encoding="utf-8",
    )

    class _Pool:
        def models(self) -> list[dict[str, object]]:
            return [
                {
                    "id": "my-model",
                    "upstream": f"user:{key.id}",
                    "protocol": "openai_chat",
                    "tier": "user",
                }
            ]

    plan = plan_attempt("FD-1", harness_id="codex-window", pool=_Pool(), data_dir=store.parent)
    assert plan["mode"] == "direct", "没点头就不能用用户的 key"
    pending = user_keys.pending()
    assert pending is not None and pending.key_id == key.id

    # 点了头：这一次就用自己的 key。
    user_keys.set_approval(key.id, user_keys.APPROVAL_ONCE)
    plan = plan_attempt("FD-1", harness_id="codex-window", pool=_Pool(), data_dir=store.parent)
    assert plan["mode"] == "pool"
    assert plan["upstream"] == f"user:{key.id}"
    assert "user's own key" in plan["reason"]


def test_the_pool_refuses_to_serve_an_unapproved_key(store: Path) -> None:
    """Even a direct request for that model is refused while unapproved."""
    from app.pool.service import PoolError, PoolService

    key = user_keys.register(
        label="我的",
        protocol="openai_chat",
        base_url="https://api.example.com/v1",
        key="k-1",
        models=["my-model"],
    )
    service = PoolService()
    with pytest.raises(PoolError) as error:
        service.route("my-model")
    assert error.value.code == "pool_user_key_pending"
    pending = user_keys.pending()
    assert pending is not None and pending.key_id == key.id


def test_an_once_approval_is_spent_by_the_request_that_used_it(store: Path) -> None:
    """「用一次」就是用一次：这一笔记完账，授权就没了。

    少了这一步，用户点一次「同意用一次」，平台就能一直花 —— 那不叫"用一次"。
    """
    from app.pool.service import PoolService, Usage

    key = user_keys.register(
        label="我的",
        protocol="openai_chat",
        base_url="https://api.example.com/v1",
        key="k-1",
        models=["my-model"],
    )
    user_keys.set_approval(key.id, user_keys.APPROVAL_ONCE)
    service = PoolService()
    service._account(
        Usage(
            at="2026-10-23T00:00:00",
            model="my-model",
            upstream=f"user:{key.id}",
            protocol="openai_chat",
            status=200,
            duration_ms=1,
        )
    )
    assert not user_keys.approved(key.id), "用完之后就不再能花"
    assert user_keys.find(key.id).requests == 1, "用了几次要记下来"

    # 「以后都用它」不受影响。
    user_keys.set_approval(key.id, user_keys.APPROVAL_ALWAYS)
    service._account(
        Usage(
            at="2026-10-23T00:00:01",
            model="my-model",
            upstream=f"user:{key.id}",
            protocol="openai_chat",
            status=200,
            duration_ms=1,
        )
    )
    assert user_keys.approved(key.id), "「以后都用它」不该被一次请求用掉"


def test_the_refusal_reaches_the_caller_as_a_status(store: Path) -> None:
    """The worker must be able to read *why* the pool refused.

    A 500 says "internal error" and invites a retry loop; the worker needs a
    409 with the code, so it knows to wait for the user instead of trying
    again.
    """
    from tests.api_support import make_client

    user_keys.register(
        label="我的",
        protocol="openai_chat",
        base_url="https://api.example.com/v1",
        key="k-1",
        models=["my-model"],
    )
    client, database = make_client(store.parent)
    harness = _harness_token(database)
    response = client.post(
        "/api/v1/pool/v1/chat/completions",
        json={"model": "my-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {harness}"},
    )
    assert response.status_code == 409, response.text
    body = response.json()
    assert body["error"]["code"] == "pool_user_key_pending"
    # The refusal is a *reason*, not a leak: the key is not in the answer.
    assert "k-1" not in response.text


def test_a_streaming_refusal_is_a_status_too(store: Path) -> None:
    """A broken stream halfway through is not something a client can read."""
    from tests.api_support import make_client

    user_keys.register(
        label="我的",
        protocol="openai_chat",
        base_url="https://api.example.com/v1",
        key="k-1",
        models=["my-model"],
    )
    client, database = make_client(store.parent)
    harness = _harness_token(database)
    response = client.post(
        "/api/v1/pool/v1/chat/completions",
        json={
            "model": "my-model",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"Authorization": f"Bearer {harness}"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "pool_user_key_pending"
