"""Scripted tests for the slot roster expansion: the CLI lists its own free
family, the naming rule is the operator's declaration, a listing never runs a
model call.

The runner is injected, so no test spawns a real CLI.  The roster cache is
the module's shared one, so every test starts by clearing it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_one_failed_spawn_is_retried_right_away(tmp_path, monkeypatch):
    """一次 spawn 失败不等于「这个 CLI 没有模型」。

    这台机器上同时跑着 opencode 窗口，`opencode models` 偶发失败一次就把 41 个
    模型从选择器里抹掉（界面只剩已定档的那几个）。所以空答案要先问第二次。
    """
    monkeypatch.setattr(sources, "time", type("Clock", (), {"monotonic": staticmethod(lambda: 0.0), "sleep": staticmethod(lambda _seconds: None)}))
    calls = {"count": 0}

    def fails_once(binary: str, args: list[str]) -> list[str]:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("the CLI was busy this time")
        return ROSTER

    sources._ENUM_ROSTER.clear()
    family = _free_family(
        sources.discover_cli_slots(
            config_path=tmp_path / "missing.json", home=tmp_path, listing_runner=fails_once
        )
    )
    assert "opencode-cli:opencode/beam-free" in family, "失败一次要立刻重问"
    assert calls["count"] == 2


def test_an_empty_listing_is_retried_sooner_than_a_good_one(tmp_path, monkeypatch):
    """一次失败的名册读取不能把整份清单藏十分钟。

    `opencode --list-models` 失败一次时，缓存里会留下一个**空名册**并按长 TTL
    生效：界面于是只看到 19 个模型，另外 42 个凭空消失 —— 而那份清单正是人挑
    模型的唯一入口。空的结果只信半分钟，好的仍然信十分钟（不再多 spawn 进程）。
    """

    class Clock:
        now = 1000.0

        @classmethod
        def monotonic(cls) -> float:
            return cls.now

        @staticmethod
        def sleep(_seconds: float) -> None:
            return None

    calls = {"count": 0}

    def always_empty(binary: str, args: list[str]) -> list[str]:
        calls["count"] += 1
        return []

    monkeypatch.setattr(sources, "time", Clock)
    args = (tmp_path / "missing.json", tmp_path)
    sources._ENUM_ROSTER.clear()

    assert _free_family(sources.discover_cli_slots(*args, listing_runner=always_empty)) == [
        "opencode-cli:opencode/minimax-m3.1-flash-free"
    ], "清单空了就只剩声明过的那一个"
    # 空答案连续两次才算数（立刻重试过一次），之后按短 TTL 缓存。
    assert calls["count"] == 2

    def good(binary: str, args: list[str]) -> list[str]:
        calls["count"] += 1
        return ROSTER

    Clock.now += sources.EMPTY_ROSTER_TTL_SECONDS + 1
    family = _free_family(sources.discover_cli_slots(*args, listing_runner=good))
    assert "opencode-cli:opencode/beam-free" in family, "空名册半分钟后就该重试"

    # 读到了就按长 TTL 缓存：再多等一会儿仍然不再 spawn。
    before = calls["count"]
    Clock.now += 60.0
    sources.discover_cli_slots(*args, listing_runner=good)
    assert calls["count"] == before, "好名册不该被当成空的重列"


def test_a_declared_tier_beats_the_family_rule(tmp_path):
    """人给家族里的某一条定过档，就按人的那条来。

    免费家族按 free 先加进来，声明那条晚一步、同名；`models()` 去重时先到先得，
    于是**声明被悄悄丢掉** —— "改成收费"看着成功，池子里它还是免费。这是真机上
    撞出来的：改档回成功、档位没变。
    """
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "cli_slots": [
                    {
                        "name": "opencode-cli",
                        "model": "opencode/beam-free",
                        "tier": "metered",
                        "binary": "opencode",
                        "args": ["run"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    sources._ENUM_ROSTER.clear()
    upstreams = sources.discover_cli_slots(
        config_path=config, home=tmp_path, listing_runner=_fake_runner
    )
    hits = [u for u in upstreams if u.name == "opencode-cli:opencode/beam-free"]
    assert len(hits) == 1, "同一个模型只能有一条上游"
    assert hits[0].tier == "metered", "人的档位要盖过家族规则"


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


def test_a_model_the_cli_lists_can_be_given_a_tier(tmp_path):
    """人给名册里的模型定档：写进声明，argv 契约照抄那家现有的槽。"""
    from app.pool.sources import declare_cli_model

    config = tmp_path / "config.json"
    slot = declare_cli_model(
        "codebuddy",
        "hy3-x",
        "free",
        config_path=config,
        roster=["hy3", "hy3-x", "step-5-preview"],
    )
    assert slot["name"] == "codebuddy"
    assert slot["model"] == "hy3-x"
    assert slot["tier"] == "free"
    # 同一家的调用约定照抄：模型不同，程序契约不该不同。
    assert slot["binary"] == "codebuddy"
    assert slot["args"], "argv 契约要跟着一起写进去"
    written = json.loads(config.read_text(encoding="utf-8"))
    declared = {entry["model"]: entry for entry in written["cli_slots"]}
    assert declared["hy3-x"]["tier"] == "free"


def test_a_namespaced_roster_name_can_be_declared(tmp_path):
    """名册里的名字带命名空间（`opencode/beam-free`），模型 id 用短名。

    两边口径不同时，拿全名去比名册的短名永远对不上：opencode 那 42 个模型因此
    一次都没定档成功过（codebuddy 的名册不带斜杠，所以它一直正常）。定档要把人
    给的名字归一成 **CLI 自己列的那一个** —— argv 直接拿它去跑。
    """
    from app.pool.sources import declare_cli_model

    roster = ["opencode/beam-free", "opencode/big-pickle"]
    config = tmp_path / "config.json"

    # 界面传的是名册里的原样写法
    namespaced = declare_cli_model(
        "opencode-cli", "opencode/big-pickle", "free", config_path=config, roster=roster
    )
    assert namespaced["model"] == "opencode/big-pickle"

    # 人也可能只说短名：一样认，但仍然存成 CLI 那份写法
    bare = declare_cli_model(
        "opencode-cli", "beam-free", "metered", config_path=config, roster=roster
    )
    assert bare["model"] == "opencode/beam-free"


def test_a_non_cli_upstream_can_be_retiered_and_withdrawn(tmp_path):
    """非 CLI 上游（写在各家 CLI 自己配置里那几条）也能改档、能撤档。

    它们的档位本来是**代码里的默认值**（`claude-provider` / `opencode:1` 按付费，
    `minimax` 按兜底），池子里没有可改的东西 —— 人若知道"这条其实是免费额度"，
    以前只能去改代码。现在写成一份覆盖，撤掉就回到默认。
    """
    from app.pool import sources

    config = tmp_path / "config.json"
    sources.declare_upstream_tier("opencode:1", "free", config_path=config)
    assert sources.declared_upstream_tiers(config) == [{"name": "opencode:1", "tier": "free"}]

    upstreams = sources.discover_upstreams(include_cli=False, config_path=config)
    hit = [u for u in upstreams if u.name == "opencode:1"]
    assert hit and hit[0].tier == "free", "人的说法要盖过代码默认"

    assert sources.withdraw_upstream_tier("opencode:1", config_path=config) is True
    back = [u for u in sources.discover_upstreams(include_cli=False, config_path=config) if u.name == "opencode:1"]
    assert back and back[0].tier == "metered", "撤掉就回到代码默认"
    assert sources.withdraw_upstream_tier("opencode:1", config_path=config) is False


def test_a_non_cli_upstream_tier_is_validated(tmp_path):
    """档位只有三档、名字不能空：平台不猜"这条到底花不花钱"。"""
    from app.pool.sources import PoolDeclarationError, declare_upstream_tier

    config = tmp_path / "config.json"
    with pytest.raises(PoolDeclarationError):
        declare_upstream_tier("", "free", config_path=config)
    with pytest.raises(PoolDeclarationError):
        declare_upstream_tier("opencode:1", "maybe", config_path=config)


def test_the_users_own_key_tier_is_never_overridden(tmp_path):
    """用户自己的 key 那一档不是"价格"，是"这笔钱是用户的"，覆盖不能动它。"""
    from app.pool import sources

    config = tmp_path / "config.json"
    sources.declare_upstream_tier("my-key", "metered", config_path=config)
    user = [
        sources.Upstream(name="my-key", protocol="openai_chat", base_url="https://x/v1", tier="user")
    ]
    assert sources._apply_upstream_tiers(user, config)[0].tier == "user"


def test_a_declaration_can_be_taken_back(tmp_path):
    """撤档：撤的只是**人写的那条**，内置档位撤不掉、也不该被撤没。

    档位是人的一句话，而人会点错：没有撤档时，一条错标成"免费"的收费额度会一直
    按免费花下去（平台以为不花钱，就真花了）。
    """
    from app.pool.sources import declare_cli_model, withdraw_cli_model

    config = tmp_path / "config.json"
    declare_cli_model(
        "opencode-cli", "opencode/beam-free", "metered", config_path=config, roster=["opencode/beam-free"]
    )
    assert withdraw_cli_model("opencode-cli", "beam-free", config_path=config) is True
    assert json.loads(config.read_text(encoding="utf-8"))["cli_slots"] == []
    # 再撤一次什么都没得撤
    assert withdraw_cli_model("opencode-cli", "beam-free", config_path=config) is False
    # 内置档位不在声明文件里 —— 撤不到它
    assert withdraw_cli_model("opencode-cli", "minimax-m3.1-flash-free", config_path=config) is False


def test_withdraw_does_not_touch_other_declarations(tmp_path):
    from app.pool.sources import declare_cli_model, withdraw_cli_model

    config = tmp_path / "config.json"
    roster = ["opencode/beam-free", "opencode/big-pickle"]
    declare_cli_model("opencode-cli", "opencode/beam-free", "free", config_path=config, roster=roster)
    declare_cli_model("opencode-cli", "opencode/big-pickle", "free", config_path=config, roster=roster)

    assert withdraw_cli_model("opencode-cli", "opencode/beam-free", config_path=config) is True
    left = json.loads(config.read_text(encoding="utf-8"))["cli_slots"]
    assert [slot["model"] for slot in left] == ["opencode/big-pickle"]


def test_a_model_the_cli_does_not_list_is_refused(tmp_path):
    """平台不替 CLI 发明模型：它没列出来的，定档就 refused。"""
    from app.pool.sources import PoolDeclarationError, declare_cli_model

    with pytest.raises(PoolDeclarationError):
        declare_cli_model(
            "codebuddy",
            "not-a-model",
            "free",
            config_path=tmp_path / "config.json",
            roster=["hy3", "hy3-x"],
        )


def test_a_tier_the_platform_cannot_guess_is_refused(tmp_path):
    """档位只有三个：平台不猜「这个到底花不花钱」。"""
    from app.pool.sources import PoolDeclarationError, declare_cli_model

    with pytest.raises(PoolDeclarationError):
        declare_cli_model(
            "codebuddy",
            "hy3",
            "maybe",
            config_path=tmp_path / "config.json",
            roster=["hy3"],
        )


def test_a_declaration_lands_in_the_data_directory(tmp_path, monkeypatch):
    """写声明要写进**数据目录**。

    读的时候有历史相对路径的兜底（老检出还能用），但**写**不能落在那儿 ——
    平台是在 backend 目录下跑的，写进去就落进了仓库，而不是平台被指到的数据目录。
    """
    from app.pool import sources

    monkeypatch.setattr(sources.settings, "data_dir", str(tmp_path), raising=False)
    slot = sources.declare_cli_model("codebuddy", "hy3-x", "free", roster=["hy3-x"])
    written = tmp_path / "pool" / "config.json"
    assert written.exists(), "声明要落在数据目录里"
    assert slot["model"] == "hy3-x"
