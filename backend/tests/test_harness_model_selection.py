"""人给窗口指定的模型来源：自带，还是额度池里的某个模型。

这一卡要证明四件事：

1. **存得下、读得回** —— 平台认这个选择（``harnesses.model_mode`` / ``model_id``），
   办公室读得到当前的选择。
2. **平台照办** —— 指定了池子里的某个模型，这一轮**直接**走它（不是等自己的
   provider 挂了才换池子）。
3. **不许越权** —— 人自己的 key 没点头就不花；池子里没有这个模型就回落自带并写明
   原因（绝不静默换一个）；刚挂掉的那个模型不用第二次。
4. **只有人能改** —— harness 令牌改自己的模型一律 403（自己给自己批额度）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.adapter import model_choice
from app.adapter.model_choice import ModelChoice
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth.tokens import TokenService
from app.config import settings
from app.pool.routing import plan_attempt
from tests.api_support import make_client

#: 池子里的两条额度：一条免费（qoder 的），一条用户自己的 key。
FREE_MODEL = {
    "id": "qoder/Qwen3.8-Flash",
    "upstream": "qoder:Qwen3.8-Flash",
    "protocol": "cli",
    "tier": "free",
}
OTHER_FREE_MODEL = {
    "id": "codebuddy/hy3",
    "upstream": "codebuddy:hy3",
    "protocol": "cli",
    "tier": "free",
}
USER_MODEL = {
    "id": "my-own-model",
    "upstream": "user:k-1",
    "protocol": "openai_chat",
    "tier": "user",
}


class _Pool:
    """A pool that answers with a fixed list of models."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries

    def models(self) -> list[dict[str, Any]]:
        return list(self._entries)


#: 一个**按量计费**的模型：选它要人明确点头。
PAID_MODEL = {
    "id": "codex/gpt-5.6",
    "upstream": "codex:geekspace",
    "protocol": "openai_responses",
    "tier": "metered",
}


def _free_pool_models() -> list[dict[str, Any]]:
    return [FREE_MODEL, OTHER_FREE_MODEL]


def _paid_pool_models() -> list[dict[str, Any]]:
    return [FREE_MODEL, OTHER_FREE_MODEL, PAID_MODEL]


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


def _post(client, database, body: dict[str, Any], *, subject_type: str = "human"):
    token = TokenService(database).issue(
        subject_type=subject_type, subject_id="h-A" if subject_type == "harness" else "operator"
    )
    return client.post(
        "/api/v1/adapter/model-selection",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )


def _availability(client, harness_id: str, token: str | None = None) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = client.get("/api/v1/adapter/availability", headers=headers)
    assert response.status_code == 200, response.text
    for item in response.json()["availability"]:
        if str(item["harness_id"]) == harness_id:
            return item
    raise AssertionError(f"{harness_id} 不在名单里")


# ------------------------------------------------------------------ 存取


def test_the_choice_is_stored_and_read_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """人指定池子里的模型 → 平台记下来 → 办公室读得到；改回自带也要能改回去。"""
    monkeypatch.setattr("app.api.routes._pool_models", _free_pool_models)
    client, database = make_client(tmp_path)
    _register(database, "h-A")

    chosen = _post(
        client,
        database,
        {"harness_id": "h-A", "model_mode": "pool", "model_id": FREE_MODEL["id"]},
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["selection"]["model_mode"] == "pool"

    entry = _availability(client, "h-A")
    assert entry["model_mode"] == "pool"
    assert entry["model_id"] == FREE_MODEL["id"]

    # 改回自带：池子的那个 id 不该留在登记里（不留半截的选择）。
    back = _post(client, database, {"harness_id": "h-A", "model_mode": "cli"})
    assert back.status_code == 200, back.text
    entry = _availability(client, "h-A")
    assert entry["model_mode"] == "cli"
    assert entry["model_id"] is None


def test_an_unknown_window_cannot_be_given_a_model(tmp_path: Path):
    """没在册的窗口谈不上选模型：它没有自己的 provider 可选。"""
    client, database = make_client(tmp_path)
    refused = _post(
        client,
        database,
        {"harness_id": "h-nobody", "model_mode": "pool", "model_id": FREE_MODEL["id"]},
    )
    assert refused.status_code == 400, refused.text


def test_a_model_the_pool_does_not_have_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """存下一个池子里没有的模型，只会让界面说「已保存」而下一轮悄悄回落。"""
    monkeypatch.setattr("app.api.routes._pool_models", lambda: [FREE_MODEL])
    client, database = make_client(tmp_path)
    _register(database, "h-A")
    refused = _post(
        client,
        database,
        {"harness_id": "h-A", "model_mode": "pool", "model_id": "not/in-the-pool"},
    )
    assert refused.status_code == 400, refused.text
    # 没存上：登记里还是自带。
    assert _availability(client, "h-A")["model_mode"] == "cli"


def test_only_a_human_may_change_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """harness 令牌改自己的模型 = 自己给自己批额度：403。"""
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    client, database = make_client(tmp_path)
    _register(database, "h-A")
    refused = _post(
        client,
        database,
        {"harness_id": "h-A", "model_mode": "pool", "model_id": FREE_MODEL["id"]},
        subject_type="harness",
    )
    assert refused.status_code == 403, refused.text
    human = TokenService(database).issue(subject_type="human", subject_id="operator")
    assert _availability(client, "h-A", human)["model_mode"] == "cli"


def test_a_pool_choice_must_name_a_model(tmp_path: Path):
    """「走额度池」但没说是哪个模型 = 没选。"""
    client, database = make_client(tmp_path)
    _register(database, "h-A")
    refused = _post(client, database, {"harness_id": "h-A", "model_mode": "pool"})
    assert refused.status_code == 400, refused.text


# ------------------------------------------------------------------ 平台照办


def test_the_attempt_runs_on_the_chosen_model():
    """指定了就照办：这一轮直接走那个池子模型（不用等自己的 provider 挂了）。"""
    plan = plan_attempt(
        "T-1",
        harness_id="h-A",
        pool=_Pool([FREE_MODEL]),
        selection=ModelChoice("h-A", "pool", FREE_MODEL["id"]),
    )
    assert plan["mode"] == "pool"
    assert plan["model"] == FREE_MODEL["id"]
    assert plan["upstream"] == "qoder:Qwen3.8-Flash"
    # 为什么这么走，要写清楚（事后能解释）。
    assert "chose" in str(plan["reason"])


def test_without_a_choice_the_window_keeps_its_own_provider():
    """没选就照旧：自己的 provider（池子只是失败后的救急路径）。"""
    plan = plan_attempt("T-1", harness_id="h-A", pool=_Pool([FREE_MODEL]))
    assert plan["mode"] == "direct"
    assert "model" not in plan


def test_the_users_own_key_still_needs_a_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """选「你自己的 key」不等于同意花钱：没点头就问，这一轮照旧走自带。"""
    monkeypatch.setattr(settings, "data_dir", str(tmp_path), raising=False)
    plan = plan_attempt(
        "T-1",
        harness_id="h-A",
        pool=_Pool([USER_MODEL]),
        selection=ModelChoice("h-A", "pool", USER_MODEL["id"]),
    )
    assert plan["mode"] == "direct"
    assert "not said yes" in str(plan["reason"])


def test_a_model_that_is_not_in_the_pool_falls_back_and_says_why():
    """池子里没有它了：**不静默换一个**，回落自带并写明原因。"""
    plan = plan_attempt(
        "T-1",
        harness_id="h-A",
        pool=_Pool([FREE_MODEL]),
        selection=ModelChoice("h-A", "pool", "gone/gone"),
    )
    assert plan["mode"] == "direct"
    assert "not in the pool" in str(plan["reason"])


def test_the_model_that_just_died_is_not_used_again(tmp_path: Path):
    """刚挂掉的模型不用第二次：这一轮按现状自动挑（自动路径本来就会跳过它）。"""
    evidence = tmp_path / "formal-runs"
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "T-1.failure.json").write_text(
        json.dumps(
            {
                "harness_id": "h-A",
                "error_type": "CliFailed",
                "error": "provider said no",
                "retryable": True,
                "model": FREE_MODEL["id"],
            }
        ),
        encoding="utf-8",
    )
    plan = plan_attempt(
        "T-1",
        harness_id="h-A",
        data_dir=tmp_path,
        pool=_Pool([FREE_MODEL, OTHER_FREE_MODEL]),
        selection=ModelChoice("h-A", "pool", FREE_MODEL["id"]),
    )
    assert plan["mode"] == "pool"
    assert plan["model"] == OTHER_FREE_MODEL["id"]


def test_the_plan_route_asks_the_database_for_the_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """worker 问平台「这一轮走哪儿」时，平台读的是**登记里的那个选择**。"""
    client, database = make_client(tmp_path)
    _register(database, "h-A")
    model_choice.save_choice(
        database, harness_id="h-A", model_mode="pool", model_id=FREE_MODEL["id"]
    )
    monkeypatch.setattr("app.pool.routes._service", lambda: _Pool([FREE_MODEL]))
    token = TokenService(database).issue(subject_type="harness", subject_id="h-A")
    response = client.get(
        "/api/v1/pool/plan",
        params={"task_id": "T-1", "harness_id": "h-A"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["mode"] == "pool"
    assert plan["model"] == FREE_MODEL["id"]


# ------------------------------------------------- OFFICE-UI-19：收费要点头、探针


def test_a_paid_model_needs_a_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """按量计费的模型：人没点头，平台就不给存（更不花）。"""
    monkeypatch.setattr("app.api.routes._pool_models", _paid_pool_models)
    client, database = make_client(tmp_path)
    _register(database, "h-A")

    refused = _post(
        client,
        database,
        {"harness_id": "h-A", "model_mode": "pool", "model_id": PAID_MODEL["id"]},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "model.paid_consent_required"
    assert _availability(client, "h-A")["model_mode"] == "cli", "没点头就没存上"

    agreed = _post(
        client,
        database,
        {
            "harness_id": "h-A",
            "model_mode": "pool",
            "model_id": PAID_MODEL["id"],
            "paid_consent": True,
        },
    )
    assert agreed.status_code == 200, agreed.text
    # 点头时那一档要记下来：它以后会变，平台要拿它跟现在这一档比。
    assert agreed.json()["selection"]["model_tier"] == "metered"


def test_a_free_quota_that_turned_paid_is_not_spent():
    """人点头时它是免费、现在它收费了 —— 平台不再花它。"""
    plan = plan_attempt(
        "T-1",
        harness_id="h-A",
        pool=_Pool([PAID_MODEL]),
        selection=ModelChoice("h-A", "pool", PAID_MODEL["id"], model_tier="free"),
    )
    assert plan["mode"] == "direct"
    assert "paid quota now" in str(plan["reason"])


def test_a_paid_quota_the_user_agreed_to_is_spent():
    """人已经点头过的付费额度：照办。"""
    plan = plan_attempt(
        "T-1",
        harness_id="h-A",
        pool=_Pool([PAID_MODEL]),
        selection=ModelChoice("h-A", "pool", PAID_MODEL["id"], model_tier="metered"),
    )
    assert plan["mode"] == "pool"
    assert plan["model"] == PAID_MODEL["id"]


class _Service:
    """A pool service that answers with a fixed list (and counts the calls)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def upstreams(self, refresh: bool = False) -> list[dict[str, Any]]:
        self.calls.append("upstreams")
        return []

    def models(self, refresh: bool = False) -> list[dict[str, Any]]:
        self.calls.append("models")
        return [dict(FREE_MODEL)]

    def usage_summary(self) -> dict[str, Any]:
        return {}

    def usage_timeline(self) -> dict[str, Any]:
        return {"bucket": "hour", "hours": 24, "buckets": []}

    def inventory(self) -> dict[str, Any]:
        return {"tiers": []}

    def rosters(self) -> dict[str, list[str]]:
        return {"codebuddy": ["hy3", "step-5-preview"]}


def test_the_probe_reads_the_pool_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """人点的「重新探一遍」：发现缓存被清掉，重读一遍，名册也回来。"""
    from app.pool import service as pool_service

    client, database = make_client(tmp_path)
    _register(database, "h-A")
    service = _Service()
    monkeypatch.setattr("app.pool.routes._service", lambda: service)
    # 缓存里先放一点东西：探针要把它清掉。
    pool_service._DISCOVERY[("probe-test",)] = (0.0, [])
    token = TokenService(database).issue(subject_type="harness", subject_id="h-A")

    response = client.post("/api/v1/pool/probe", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [item["id"] for item in body["models"]] == [FREE_MODEL["id"]]
    # 名册：CLI 自己说它能跑什么（哪些没人定档，办公室要报出来）。
    assert body["rosters"]["codebuddy"] == ["hy3", "step-5-preview"]
    assert ("probe-test",) not in pool_service._DISCOVERY, "探针要清掉发现缓存"
