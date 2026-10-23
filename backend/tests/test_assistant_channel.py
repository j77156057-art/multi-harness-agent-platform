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
    def __init__(self, replies: dict[str, Any] | None = None, dead: set[str] | None = None):
        self.replies = replies or {}
        self.dead = dead or set()
        self.calls: list[tuple[str, dict[str, Any]]] = []

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
