"""十条市场 CLI 契约：argv 逐字钉住，限制照实断言。

这些断言的意义在于它们都是**集成事实**：flag 写错、prompt 放错位置、或者把一个
没有只读保证的 CLI 当成审阅者，代价都是"跑完半小时才发现"。所以每条 CLI 都钉：

* argv 里二进制是第一个，``{prompt}`` 只出现一次且被换成工单正文；
* 有只读模式的五条带只读 flag，没有的五条**拒绝**审阅（不是退回可写模式）；
* 缺必需环境变量的（Amp）在启动前就拒绝，而不是挂着等登录；
* 输出解析认得各家真实形状，认不出来时留原文而不是返回空；
* 能与不能接平台额度池，各自说清。
"""

from __future__ import annotations

import inspect

import pytest

from app.adapter import installers
from app.adapter.harnesses import generic, market_cli
from app.adapter.harnesses.cli import CliHarness, CliUnavailable, ProviderRoute
from app.adapter.formal_worker import ADAPTER_TYPES
from app.adapter.protocol import HarnessRegistration
from tests.api_support import make_client
# R10 §7 的动词表在 test_adapter 里已经有一份解析（同一个来源），复用而不是再写一遍。
from tests.test_adapter import section_7_verbs

#: 有硬只读保证的五条（其余五条必须拒绝审阅）。
READ_ONLY = {"gemini", "qwen-code", "cursor-cli", "continue-cli", "aider"}

#: 真实 CLI 的输出形状（各条来自对应厂商的 json / stream-json 模式）。
OUTPUTS = {
    "gemini": ('{"response": "done\\n", "stats": {"tokens": 12}}', "done"),
    "qwen-code": ('[{"type":"system"},{"type":"result","result":"done"}]', "done"),
    "cursor-cli": ('{"type":"result","subtype":"success","result":"done"}', "done"),
    "copilot-cli": (
        '{"type":"session.start"}\n{"type":"assistant.message","data":{"content":"done"}}\n',
        "done",  # 字段名未核实：整段原样留下，也包含 done
    ),
    "amp": ('{"type":"system"}\n{"type":"result","result":"done"}\n', "done"),
    "mistral-vibe": (
        '{"messages":[{"role":"assistant","content":[{"type":"text","text":"done"}]}]}',
        "done",
    ),
}


def adapter_for(base: str, *, client=None, harness_id: str | None = None):
    """构造一个适配器。

    这些用例只考 argv / 解析 / 池子环境，不碰 §7 的 HTTP 那十六个动词，所以
    客户端用**占位对象**就够：真起一个 TestClient 会把每个断言拖成一次应用启动。
    """
    cls = market_cli.MARKET_ADAPTERS[base]
    if client is None:
        client = _StubClient()
    return cls(client, harness_id=harness_id) if harness_id else cls(client)


class _StubClient:
    """够 HttpAdapter 存着用；真调用会失败（这些用例不调用）。"""

    def post(self, *args, **kwargs):  # pragma: no cover - 不该被走到
        raise AssertionError("these tests must not reach the platform")

    def get(self, *args, **kwargs):  # pragma: no cover - 不该被走到
        raise AssertionError("these tests must not reach the platform")


@pytest.mark.parametrize("base", sorted(market_cli.MARKET_ADAPTERS))
def test_every_market_cli_is_registered_and_installable(base):
    """一个 CLI 要么能派活，要么不该出现在"平台可以驱动"那一栏。

    安装清单里的 ``driven`` 是从 ``ADAPTER_TYPES`` 推出来的，所以这两处必须同时
    成立：有适配器类，且清单里有它的 harness_id。
    """
    assert base in ADAPTER_TYPES, base
    assert base in generic.BUILTIN_BASES, base
    listed = {entry.harness_id for entry in installers.CATALOG}
    assert base in listed, base
    assert installers.driven_ids() <= set(ADAPTER_TYPES) | set(installers.SELF_HOSTED_BASES)


@pytest.mark.parametrize("base", sorted(market_cli.MARKET_ADAPTERS))
def test_the_work_order_lands_where_the_cli_documents_it(base, tmp_path, monkeypatch):
    monkeypatch.setenv("AMP_API_KEY", "test-key")
    monkeypatch.setattr(
        market_cli.MarketCliAdapter, "_binary", lambda self: self.spec.binary
    )
    adapter = adapter_for(base)
    order = "do the thing"

    argv = adapter.command(order, tmp_path)

    assert argv[0] == market_cli.MARKET_ADAPTERS[base].spec.binary
    assert argv.count(order) == 1, argv
    assert market_cli.PROMPT not in " ".join(argv)
    # 模板里的每个字面 flag 都在，且顺序不变。
    template = [part for part in adapter.spec.headless if part != market_cli.PROMPT]
    literal = [part for part in argv[1:] if part != order]
    assert literal[: len(template)] == template, argv


@pytest.mark.parametrize("base", sorted(market_cli.MARKET_ADAPTERS))
def test_only_a_real_read_only_mode_becomes_a_reviewer(base, tmp_path, monkeypatch):
    """审阅不许动文件：没有硬保证的 CLI 必须**拒绝**，不能退回可写模式。"""
    monkeypatch.setenv("AMP_API_KEY", "test-key")
    monkeypatch.setattr(
        market_cli.MarketCliAdapter, "_binary", lambda self: self.spec.binary
    )
    adapter = adapter_for(base)

    if base in READ_ONLY:
        argv = adapter.review_command("look at this", tmp_path)
        assert argv.count("look at this") == 1
        assert adapter.spec.review and adapter.spec.review != adapter.spec.headless
    else:
        with pytest.raises(CliUnavailable, match="只读"):
            adapter.review_command("look at this", tmp_path)


def test_the_read_only_flags_are_the_documented_ones(tmp_path, monkeypatch):
    """五条只读 flag 逐条钉住：改错一个，审阅者就能写文件了。"""
    monkeypatch.setattr(
        market_cli.MarketCliAdapter, "_binary", lambda self: self.spec.binary
    )
    expected = {
        "gemini": ("--approval-mode", "plan"),
        "qwen-code": ("--approval-mode", "plan"),
        "cursor-cli": ("--mode=ask",),
        "continue-cli": ("--readonly",),
        "aider": ("--dry-run",),
    }
    for base, flags in expected.items():
        argv = adapter_for(base).review_command("p", tmp_path)
        for flag in flags:
            assert flag in argv, (base, flag, argv)


def test_amp_refuses_before_it_would_hang_on_a_login_flow(tmp_path, monkeypatch):
    """Amp 没有 key 会转设备登录并一直等（实测 >75 秒）——这里必须直接拒绝。"""
    monkeypatch.delenv("AMP_API_KEY", raising=False)
    monkeypatch.setattr(
        market_cli.MarketCliAdapter, "_binary", lambda self: self.spec.binary
    )

    with pytest.raises(CliUnavailable, match="AMP_API_KEY"):
        adapter_for("amp").command("x", tmp_path)

    monkeypatch.setenv("AMP_API_KEY", "test-key")
    assert adapter_for("amp").command("x", tmp_path)[0] == "amp"


@pytest.mark.parametrize("base", sorted(OUTPUTS))
def test_parse_output_reads_the_shape_the_cli_really_prints(base):
    stdout, expected = OUTPUTS[base]
    parsed = adapter_for(base).parse_output(stdout)

    assert expected in parsed["final_message"], parsed


@pytest.mark.parametrize("base", ["aider", "crush", "iflow", "continue-cli"])
def test_a_plain_text_cli_keeps_its_tail_instead_of_returning_nothing(base):
    """认不出来就留原文：空会让上游把「说了话」读成「什么都没说」。"""
    parsed = adapter_for(base).parse_output("all done here\n")

    assert parsed["final_message"] == "all done here"


def test_an_unknown_json_field_falls_back_to_the_raw_tail():
    """Copilot / Vibe 的最终消息字段没有核实过，就不能假装认得。"""
    stdout = '{"type":"whatever","data":{"content":"done"}}\n'
    parsed = adapter_for("copilot-cli").parse_output(stdout)

    assert "done" in parsed["final_message"]


def test_pool_routing_is_only_offered_where_the_vendor_documents_it():
    """能改端点的三条给环境变量，其余七条返回空（各自用自己的账号）。"""
    expected = {
        "qwen-code": {"OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"},
        "aider": {"OPENAI_API_BASE", "OPENAI_API_KEY"},
        "iflow": {"baseUrl", "apiKey", "modelName"},
    }
    for base in sorted(market_cli.MARKET_ADAPTERS):
        adapter = adapter_for(base)
        adapter.provider_route = ProviderRoute(
            base_url="http://127.0.0.1:8848/api/v1/pool/v1",
            token="apt_x",
            model="some/model",
            protocol="openai",
        )
        env = adapter.route_environment()
        if base in expected:
            assert set(env) == expected[base], (base, env)
            assert "http://127.0.0.1:8848" in env[next(iter(env))]
        else:
            assert env == {}, (base, env)


def test_the_model_flag_only_appears_when_the_pool_serves_the_model():
    adapter = adapter_for("qwen-code")
    assert "--model" not in adapter.command("x", None)
    adapter.provider_route = ProviderRoute(
        base_url="http://127.0.0.1:8848/api/v1/pool/v1",
        token="apt_x",
        model="pool-model",
    )
    assert adapter.command("x", None)[-2:] == ["--model", "pool-model"]


@pytest.mark.parametrize("base", sorted(market_cli.MARKET_ADAPTERS))
def test_the_registration_names_the_executable_that_really_runs(base):
    """回执里的 adapter 名是**这个可执行文件**，不能被认成别的 CLI。"""
    adapter = adapter_for(base, harness_id=f"{base}-member-1")
    registration = adapter.registration

    assert isinstance(registration, HarnessRegistration)
    assert registration.harness_id == f"{base}-member-1"
    assert registration.adapter == market_cli.MARKET_ADAPTERS[base].spec.adapter
    assert registration.provider == market_cli.MARKET_ADAPTERS[base].spec.provider


def test_a_market_adapter_still_answers_the_whole_section_7_surface(project_root):
    """共享基类不能少 §7 那十六个动词里的任何一个。"""
    verbs = section_7_verbs(project_root / "docs" / "spec" / "0.3-R10.md")
    assert len(verbs) == 16
    missing = [verb for verb in verbs if not hasattr(market_cli.GeminiCliAdapter, verb)]
    assert not missing, missing
    unwired = [
        verb
        for verb in verbs
        if not {"_post", "_get"} & set(getattr(market_cli.GeminiCliAdapter, verb).__code__.co_names)
    ]
    assert not unwired, unwired
    assert issubclass(market_cli.GeminiCliAdapter, CliHarness)
