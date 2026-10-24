"""The pool-direct model channel, pinned without touching a real upstream.

The chain prefers the free tier (CLI slots the pool serves through its own
declared CLI machinery); a dead or empty answer falls to the next link; when
nothing serves, the failure is raised, never silently rerouted into the
metered tier.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.assistant.model import FREE_MODELS, ChannelUnavailable, PoolModelChannel


class FakePool:
    def __init__(
        self,
        replies: dict[str, Any] | None = None,
        dead: set[str] | None = None,
        catalogue: Any = None,
        catalogue_raises: bool = False,
    ):
        self.replies = replies or {}
        self.dead = dead or set()
        self.catalogue = catalogue
        self.catalogue_raises = catalogue_raises
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.catalogue_reads = 0

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        self.catalogue_reads += 1
        if self.catalogue_raises:
            raise ConnectionError("catalogue down")
        return self.catalogue or {}

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((path, body))
        name = str(body.get("model"))
        if name in self.dead:
            raise ConnectionError(f"{name} unreachable")
        reply = self.replies.get(name)
        if reply is None:
            raise ConnectionError(f"{name} not registered in this fake")
        return reply


def _chat(content: str, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


def test_the_free_chain_holds_the_five_free_models():
    assert len(FREE_MODELS) == 5
    assert all("/" in m for m in FREE_MODELS)


def test_a_healthy_first_model_serves_and_its_receipt_is_kept():
    pool = FakePool({"qoder/Qwen3.8-Flash": _chat('{"thought": "好", "tool": "pool.status"}')})
    channel = PoolModelChannel(pool)
    out = channel.step("任务：…")
    assert json.loads(out)["tool"] == "pool.status"
    assert channel.last_model == "qoder/Qwen3.8-Flash"
    assert channel.last_usage == {}
    ((path, body),) = pool.calls
    assert path == "/pool/v1/chat/completions"
    assert body["stream"] is False
    assert body["messages"] == [{"role": "user", "content": "任务：…"}]


def test_a_dead_model_falls_to_the_next_link():
    pool = FakePool(
        {"qoder/DeepSeek-Flash": _chat('{"thought": "接住", "tool": "pool.status"}')},
        dead={
            "codebuddy/hy3",
            "codebuddy/minimax-m3.1-flash",
            "opencode-cli/minimax-m3.1-flash-free",
        },
    )
    channel = PoolModelChannel(pool)
    out = channel.step("继续")
    assert json.loads(out)["tool"] == "pool.status"
    assert channel.last_model == "qoder/DeepSeek-Flash"
    assert len(pool.calls) == 2, "回退按链序，一个一枪"


def test_all_models_down_raises_instead_of_burning_the_metered_tier():
    pool = FakePool(dead=set(FREE_MODELS))
    channel = PoolModelChannel(pool)
    with pytest.raises(ChannelUnavailable):
        channel.step("别编")


def test_an_empty_answer_is_not_served():
    pool = FakePool(
        {
            "qoder/Qwen3.8-Flash": _chat(""),
            "qoder/DeepSeek-Flash": _chat("补位"),
        }
    )
    channel = PoolModelChannel(pool)
    assert channel.step("问") == "补位"
    assert channel.last_model == "qoder/DeepSeek-Flash"


def test_a_real_usage_object_is_passed_through_untouched():
    usage = {"prompt_tokens": 5600, "completion_tokens": 2, "total_tokens": 5602}
    pool = FakePool({"qoder/Qwen3.8-Flash": _chat("答", usage=usage)})
    channel = PoolModelChannel(pool)
    channel.step("问")
    assert channel.last_usage == usage, "收据原样带回，不加工不估算"


# --------------------------------------------------- the live free catalogue


def _catalogue(*entries: tuple[str, str]) -> dict[str, Any]:
    return {"data": [{"id": name, "tier": tier} for name, tier in entries]}


def test_the_chain_comes_from_the_live_catalogue_not_from_the_pin(tmp_path):
    pool = FakePool(
        {"new/free-model": _chat('{"thought": "新档案在岗", "tool": "pool.status"}')},
        catalogue=_catalogue(("new/free-model", "free"), ("metered/x", "metered")),
    )
    channel = PoolModelChannel(pool)
    out = channel.step("问")
    assert json.loads(out)["tool"] == "pool.status"
    assert channel.last_model == "new/free-model"
    assert channel.last_chain_source == "catalogue"
    new_free = [m for m in channel._chain_now() if m == "new/free-model"]
    assert new_free, "免费档新成员进链"
    assert "metered/x" not in channel._chain_now(), "只认 free 档"


def test_an_unreadable_catalogue_falls_back_to_the_pin_and_says_so():
    pool = FakePool({"qoder/Qwen3.8-Flash": _chat("兜底照常")}, catalogue_raises=True)
    channel = PoolModelChannel(pool)
    assert channel.step("问") == "兜底照常"
    assert channel.last_chain_source == "pinned-fallback"
    assert channel.last_chain_source == channel._chain_source


def test_a_freshly_failed_free_model_is_cooled_not_polled():
    pool = FakePool(
        {"qoder/DeepSeek-Flash": _chat("接住")},
        dead={"qoder/Qwen3.8-Flash"},
        catalogue=_catalogue(("qoder/Qwen3.8-Flash", "free"), ("qoder/DeepSeek-Flash", "free")),
    )
    channel = PoolModelChannel(pool)
    assert channel.step("第一问") == "接住"
    assert len(pool.calls) == 2, "第一枪死了，第二枪接"  # Qwen dead → cool; DeepSeek serves
    assert channel.step("第二问") == "接住"
    assert len(pool.calls) == 3, "冷却中的不再试探，直接链上后面打"


def test_cool_down_only_claims_recent_failure_not_death():
    pool = FakePool(
        {"qoder/Qwen3.8-Flash": _chat("缓过来了")},
        dead=set(),
        catalogue=_catalogue(("qoder/Qwen3.8-Flash", "free")),
    )
    channel = PoolModelChannel(pool)
    channel._cool("qoder/Qwen3.8-Flash")  # pretend it failed a moment ago
    assert channel.step("再试") == "缓过来了", "全冷时最后一搏，好过不开工"


def test_the_catalogue_read_refreshes_on_the_ttl_not_per_step():
    pool = FakePool({"qoder/Qwen3.8-Flash": _chat("在")}, catalogue=_catalogue())
    channel = PoolModelChannel(pool, chain_ttl=3600.0)
    channel.step("一")
    channel.step("二")
    assert pool.catalogue_reads == 1, "目录带 TTL，不每步一读"
    channel = PoolModelChannel(pool, chain_ttl=0.0)
    channel.step("一")
    channel.step("二")
    assert pool.catalogue_reads == 3, "TTL 过期即重读"
