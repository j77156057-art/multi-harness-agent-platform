"""Scripted tests for the slot roster expansion: the CLI lists its own free
family, the naming rule is the operator's declaration, a listing never runs a
model call.

The runner is injected, so no test spawns a real CLI.  The roster cache is
the module's shared one, so every test starts by clearing it.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.pool import sources

ROSTER = [
    "opencode/minimax-m3.1-flash-free",
    "opencode/beam-free",
    "opencode/deepseek-v4.1-flash-free",
    "opencode/glm-5.3-flash-free",
    "opencode/nemotron-3.5-lightning-free",
    "opencode/gpt-5.6",  # paid family: never a free slot
    "1/deepseek-v4.1-flash",  # an opencode.json provider: not the free family
    "free for a limited time!",  # prose line: not a model id
]


def _fake_runner(binary: str, args: list[str]) -> list[str]:
    assert args == ["models"], "枚举跑的是清单子命令"
    return ROSTER


def _discover(tmp_path: Path, listing_runner=_fake_runner):
    sources._ENUM_ROSTER.clear()
    return sources.discover_cli_slots(
        config_path=tmp_path / "missing-config.json", home=tmp_path, listing_runner=listing_runner
    )


def _free_family(upstreams, slot_prefix="opencode-cli:opencode/"):
    return sorted(u.name for u in upstreams if u.name.startswith(slot_prefix))


def test_the_declared_free_family_expands_from_the_clis_own_roster(tmp_path):
    upstreams = _discover(tmp_path)
    family = _free_family(upstreams)
    # the declared slot stays, the -free roster joins it
    assert "opencode-cli:opencode/minimax-m3.1-flash-free" in family
    assert "opencode-cli:opencode/beam-free" in family
    assert "opencode-cli:opencode/glm-5.3-flash-free" in family
    assert "opencode-cli:opencode/deepseek-v4.1-flash-free" in family
    assert "opencode-cli:opencode/nemotron-3.5-lightning-free" in family
    assert not any("gpt-5.6" in name for name in family), "要花钱的家族不冒充免费档"
    for name in family:
        upstream = next(u for u in upstreams if u.name == name)
        assert upstream.tier == "free", "命中的槽带的是声明的档位"
        assert upstream.cli is not None and upstream.cli.args, "同一份 argv 契约"
        assert upstream.models == (f"opencode-cli/{upstream.cli.model.split('/', 1)[-1]}",)


def test_the_pool_ids_stay_namespaced_and_never_merge_two_slots(tmp_path):
    upstreams = _discover(tmp_path)
    ids = [model for upstream in upstreams for model in upstream.models]
    assert "opencode-cli/beam-free" in ids
    assert "opencode-cli/minimax-m3.1-flash-free" in ids
    assert len(ids) == len(set(ids)), "一个槽一个名，不因为同名尾巴合并两家额度"


def test_a_listing_failure_expands_nothing_and_keeps_the_declared_slot(tmp_path):
    def broken(binary: str, args: list[str]) -> list[str]:
        raise RuntimeError("the CLI is not installed")

    upstreams = _discover(tmp_path, listing_runner=broken)
    family = _free_family(upstreams)
    assert family == [
        "opencode-cli:opencode/minimax-m3.1-flash-free"
    ], "清单取不到就只有声明过的那一个，不编造家族"
    assert all(u.tier == "free" for u in upstreams if u.name in family)


def test_the_roster_listing_is_cached_on_a_ttl(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def counting_runner(binary: str, args: list[str]) -> list[str]:
        calls.append(args)
        return ROSTER

    args = (tmp_path / "missing.json", tmp_path)
    real_monotonic = sources.time.monotonic
    sources._ENUM_ROSTER.clear()
    sources.discover_cli_slots(*args, listing_runner=counting_runner)
    sources.discover_cli_slots(*args, listing_runner=counting_runner)
    assert calls == [["models"]], "TTL 内不再 spawn 一个进程"

    monkeypatch.setattr(sources.time, "monotonic", lambda: real_monotonic() + 601.0)
    sources.discover_cli_slots(*args, listing_runner=counting_runner)
    assert len(calls) == 2, "TTL 过期即重列"


def test_a_slot_without_an_enumerate_rule_is_left_alone(tmp_path):
    upstreams = _discover(tmp_path)
    codebuddy = sorted(u.name for u in upstreams if u.name.startswith("codebuddy:"))
    # codebuddy 的 step-5 是**声明过**的免费额度（仓库主人说的），所以它在；
    # 别的没声明的模型仍然不进池子——不扩大也不缩小。
    assert codebuddy == [
        "codebuddy:hy3",
        "codebuddy:minimax-m3.1-flash",
        "codebuddy:step-5-preview",
    ], "没声明的就按声明来——不扩大也不缩小"


def test_pattern_filters_the_prose_and_the_other_providers(tmp_path):
    family = set(_free_family(_discover(tmp_path)))
    assert not any("limited" in name for name in family)
    assert not any(name.startswith("opencode-cli:1/") for name in family)


def test_a_broken_enumerate_spec_expands_nothing(tmp_path):
    slot = {
        "name": "toy",
        "model": "toy/f1",
        "tier": "free",
        "binary": "toy",
        "args": ["run"],
        "enumerate": {"args": ["models"], "pattern": "(unclosed"},
    }
    config = tmp_path / "cfg.json"
    config.write_text(json.dumps({"cli_slots": [slot]}), encoding="utf-8")

    def runner(binary: str, args: list[str]) -> list[str]:
        return ["toy/f2-free"]

    sources._ENUM_ROSTER.clear()
    upstreams = sources.discover_cli_slots(config_path=config, home=tmp_path, listing_runner=runner)
    names = [u.name for u in upstreams]
    assert names.count("toy:toy/f1") == 1, "声明的本体照在"
    assert not any("toy/f2" in name for name in names), "坏规则一个都不扩"
