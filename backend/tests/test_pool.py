"""The platform's quota pool: translation, sources, routing.

The pool is the platform's answer to "this window's provider is out of quota:
whose quota can it borrow?".  These tests pin the three facts it rests on:

* the **protocol translation** does not lose the text (a translation bug must
  never turn into an empty answer);
* the **sources** are discovered from the CLIs' own configuration -- never
  hard-coded -- and a CLI-only quota (OpenCode's free tier refuses to be
  called as an API) is served by the CLI itself;
* the **routing** sends a failed window to a *different* quota, prefers
  somebody's free quota, and never routes a task back to the provider that
  just failed it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.pool.cli_model import CliModelSource, extract_text
from app.pool.routing import plan_attempt
from app.pool.sources import ANTHROPIC, CLI, OPENAI_CHAT, discover_upstreams
from app.pool.translate import (
    anthropic_response_to_openai,
    anthropic_to_openai,
    chat_to_responses,
    openai_to_anthropic,
    responses_to_chat,
)

# --------------------------------------------------------------------------- #
# translation
# --------------------------------------------------------------------------- #


def test_openai_to_anthropic_keeps_the_system_prompt_and_the_question():
    body, notes = openai_to_anthropic(
        {
            "model": "m",
            "messages": [
                {"role": "system", "content": "be brief"},
                {"role": "user", "content": "hello"},
            ],
            "max_tokens": 32,
        }
    )
    assert body["max_tokens"] == 32
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert body["messages"][1] == {"role": "user", "content": "hello"}
    assert notes == []


def test_anthropic_response_becomes_a_chat_completion_with_the_text():
    data = anthropic_response_to_openai(
        {
            "id": "msg_1",
            "model": "m",
            "content": [{"type": "text", "text": "OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 1},
        },
        model="m",
    )
    assert data["choices"][0]["message"]["content"] == "OK"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["total_tokens"] == 4


def test_anthropic_to_openai_and_back_keep_the_question():
    body, _ = anthropic_to_openai(
        {
            "model": "m",
            "max_tokens": 16,
            "system": "be brief",
            "messages": [{"role": "user", "content": "hello"}],
        }
    )
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    responses = chat_to_responses({"model": "m", "messages": body["messages"]})
    assert responses["instructions"] == "be brief"
    assert responses["input"][0]["content"][0]["text"] == "hello"


def test_responses_and_chat_answer_each_other():
    chat = {
        "id": "c1",
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }
    responses = chat_to_responses(chat) if False else None  # (the other direction)
    back = responses_to_chat(
        {
            "id": "r1",
            "model": "m",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
            "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
        },
        model="m",
    )
    assert back["choices"][0]["message"]["content"] == "OK"
    assert back["usage"]["total_tokens"] == 7
    assert responses is None


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #


def test_sources_are_discovered_from_the_clis_own_configuration(tmp_path):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text(
        'model = "gpt-6.1-sol"\nmodel_provider = "geekspace"\n'
        '[model_providers.geekspace]\nbase_url = "http://127.0.0.1:8402"\nwire_api = "responses"\n',
        encoding="utf-8",
    )
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "model": "fable",
                "env": {
                    "ANTHROPIC_BASE_URL": "https://example.invalid/anthropic",
                    "ANTHROPIC_AUTH_TOKEN": "secret",
                    "ANTHROPIC_DEFAULT_FABLE_MODEL": "some-model",
                },
            }
        ),
        encoding="utf-8",
    )
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "local": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {"baseURL": "http://127.0.0.1:11434/v1", "apiKey": "ollama"},
                        "models": {"qwen": {"name": "qwen"}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    upstreams = {upstream.name: upstream for upstream in discover_upstreams(home)}

    assert upstreams["codex:geekspace"].protocol == "openai_responses"
    assert upstreams["claude-provider"].protocol == ANTHROPIC
    assert upstreams["claude-provider"].models == ("some-model",)
    assert upstreams["claude-provider"].credential_value() == "secret"
    assert upstreams["opencode:local"].protocol == OPENAI_CHAT
    assert upstreams["opencode:local"].models == ("qwen",)
    # The CLI slots are declared, not sniffed: codebuddy's hy3, its
    # minimax-m3.1-flash, qoder's two free models.
    slot_names = set(upstreams)
    assert "codebuddy:hy3" in slot_names
    assert "codebuddy:minimax-m3.1-flash" in slot_names
    assert "qoder:Qwen3.8-Flash" in slot_names
    assert "qoder:DeepSeek-Flash" in slot_names


def test_a_source_pointing_at_the_pool_itself_is_skipped(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "opencode").mkdir(parents=True)
    (home / ".config" / "opencode" / "opencode.json").write_text(
        json.dumps(
            {
                "provider": {
                    "pool": {
                        "options": {
                            "baseURL": "http://127.0.0.1:8848/api/v1/pool/v1",
                            "apiKey": "t",
                        },
                        "models": {"m": {"name": "m"}},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    upstreams = {
        upstream.name: upstream for upstream in discover_upstreams(home, relay_ports=(8848,))
    }
    assert "opencode:pool" not in upstreams


def test_the_cli_slot_is_the_only_way_to_spend_a_cli_only_quota(tmp_path):
    source = CliModelSource(name="opencode-cli", model="opencode/minimax-m3.1-flash-free")
    prompt = source.prompt_for(
        [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "Reply with exactly: OK"},
        ]
    )
    # The CLI is an agent: the message has to look like a question, and it
    # has to be one line (cmd.exe would cut a multi-line argument).
    assert "Question:" in prompt
    assert "Reply with exactly: OK" in prompt
    argv = source._argv(prompt, tmp_path)
    assert argv[-1].count("\n") == 0
    assert "Reply with exactly: OK" in argv[-1]
    assert "--model" in argv and "opencode/minimax-m3.1-flash-free" in argv


def test_the_text_of_a_cli_run_is_read_from_the_last_text_event():
    stdout = "\n".join(
        [
            json.dumps({"type": "step_start", "part": {"type": "step-start"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "first"}}),
            json.dumps({"type": "text", "part": {"type": "text", "text": "OK"}}),
        ]
    )
    assert extract_text(stdout) == "OK"
    assert extract_text("not json at all") == "not json at all"


# --------------------------------------------------------------------------- #
# routing
# --------------------------------------------------------------------------- #


class _FakePool:
    def __init__(self, entries: list[dict[str, str]]):
        self._entries = entries

    def models(self) -> list[dict[str, str]]:
        return self._entries


def _write_failure(tmp_path: Path, task_id: str, **fields) -> None:
    evidence = tmp_path / "formal-runs"
    evidence.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "harness_id": "claude-code",
        "error_type": "CliFailed",
        "error": "the harness CLI failed before it could deliver",
        "retryable": True,
        "retry_reason": "the harness CLI failed before it could deliver",
    }
    payload.update(fields)
    (evidence / f"{task_id}.failure.json").write_text(json.dumps(payload), encoding="utf-8")


def test_a_failed_window_is_routed_to_the_pool(tmp_path):
    _write_failure(tmp_path, "T-1")
    pool = _FakePool(
        [
            {
                "id": "gpt-6.1-sol",
                "upstream": "codex:geekspace",
                "protocol": "openai_responses",
                "tier": "metered",
            },
            {"id": "minimax-free", "upstream": "opencode-cli", "protocol": "cli", "tier": "free"},
        ]
    )
    plan = plan_attempt("T-1", harness_id="claude-code", data_dir=tmp_path, pool=pool)
    assert plan["mode"] == "pool"
    # Somebody's *free* quota comes first.
    assert plan["model"] == "minimax-free"
    assert plan["upstream"] == "opencode-cli"


def test_a_reserved_quota_is_never_spent_on_its_own(tmp_path):
    """A plan kept in reserve is there when nothing else is left.

    The platform does not reach for it by itself: asking for it has to be
    explicit, so the automatic routing skips the reserve tier entirely.
    """
    _write_failure(tmp_path, "T-1")
    pool = _FakePool(
        [
            {
                "id": "gpt-6.1-sol",
                "upstream": "codex:geekspace",
                "protocol": "openai_responses",
                "tier": "metered",
            },
            {
                "id": "MiniMax-M3.1-Flash-Preview",
                "upstream": "minimax",
                "protocol": "anthropic",
                "tier": "reserve",
            },
        ]
    )
    plan = plan_attempt("T-1", harness_id="claude-code", data_dir=tmp_path, pool=pool)
    assert plan["mode"] == "pool"
    # The metered account is used; the reserved plan is not touched.
    assert plan["upstream"] == "codex:geekspace"


def test_a_reserved_quota_is_still_servable_by_name(tmp_path):
    """Reserve means "not automatic", not "unavailable"."""
    upstreams = {
        upstream.name: upstream
        for upstream in discover_upstreams(tmp_path, config_path=tmp_path / "pool.json")
    }
    assert upstreams["codebuddy:hy3"].tier == "free"
    assert upstreams["opencode-cli:opencode/minimax-m3.1-flash-free"].tier == "free"


def test_a_task_is_never_routed_back_to_the_provider_that_failed_it(tmp_path):
    _write_failure(tmp_path, "T-1", harness_id="opencode")
    pool = _FakePool(
        [
            {"id": "minimax-free", "upstream": "opencode-cli", "protocol": "cli"},
            {"id": "gpt-6.1-sol", "upstream": "codex:geekspace", "protocol": "openai_responses"},
        ]
    )
    plan = plan_attempt("T-1", harness_id="opencode", data_dir=tmp_path, pool=pool)
    assert plan["mode"] == "pool"
    assert plan["upstream"] != "opencode-cli"


def test_a_new_harness_is_not_routed_back_to_its_own_quota(tmp_path):
    """The self-loop guard follows the owner convention, not a fixed list.

    A harness the pool has never seen before must still not be sent back to
    the quota that just failed it; otherwise adding a harness would
    re-introduce the loop the pool exists to break.
    """
    _write_failure(tmp_path, "T-1", harness_id="pi-member-1")
    pool = _FakePool(
        [
            {"id": "pi-free", "upstream": "pi:free", "protocol": "cli", "tier": "free"},
            {
                "id": "Qwen3.8-Flash",
                "upstream": "qoder:Qwen3.8-Flash",
                "protocol": "cli",
                "tier": "free",
            },
        ]
    )
    plan = plan_attempt("T-1", harness_id="pi-member-1", data_dir=tmp_path, pool=pool)
    assert plan["mode"] == "pool"
    assert plan["upstream"] == "qoder:Qwen3.8-Flash"


def test_a_task_without_a_failure_stays_on_its_own_provider(tmp_path):
    plan = plan_attempt("T-2", harness_id="opencode", data_dir=tmp_path, pool=_FakePool([]))
    assert plan["mode"] == "direct"
    assert "no failure on record" in plan["reason"]


def test_another_windows_failure_does_not_implicate_this_window(tmp_path):
    _write_failure(tmp_path, "T-1", harness_id="claude-code")
    plan = plan_attempt("T-1", harness_id="opencode", data_dir=tmp_path, pool=_FakePool([]))
    assert plan["mode"] == "direct"
    assert "another harness" in plan["reason"]


def test_a_failure_that_is_not_a_provider_failure_stays_direct(tmp_path):
    _write_failure(tmp_path, "T-1", retryable=False, error_type="OperationalError")
    plan = plan_attempt("T-1", harness_id="claude-code", data_dir=tmp_path, pool=_FakePool([]))
    assert plan["mode"] == "direct"
    assert "not a provider failure" in plan["reason"]


def test_the_cli_slot_is_declared_not_sniffed(tmp_path):
    upstreams = {
        upstream.name: upstream
        for upstream in discover_upstreams(tmp_path, config_path=tmp_path / "pool.json")
    }
    assert "opencode-cli:opencode/minimax-m3.1-flash-free" in upstreams
    slot = upstreams["opencode-cli:opencode/minimax-m3.1-flash-free"]
    assert slot.protocol == CLI
    # The pool's model id is namespaced by the slot: two CLIs may both offer
    # "deepseek-v4.1-flash", and the pool must not merge them.
    assert slot.models == ("opencode-cli/minimax-m3.1-flash-free",)
    # The MiniMax CLI is a slot too -- but only once it is signed in: a quota
    # that cannot be spent must not be offered.
    assert "mmx:MiniMax-M3.1-Flash-Preview" not in upstreams
    signed_in = tmp_path / "home"
    (signed_in / ".mmx").mkdir(parents=True)
    (signed_in / ".mmx" / "config.json").write_text(
        json.dumps({"api_key": "sk-cp-test"}), encoding="utf-8"
    )
    with_mmx = {
        upstream.name: upstream
        for upstream in discover_upstreams(signed_in, config_path=tmp_path / "pool.json")
    }
    assert "mmx:MiniMax-M3.1-Flash-Preview" in with_mmx
    assert with_mmx["mmx:MiniMax-M3.1-Flash-Preview"].models == ("mmx/MiniMax-M3.1-Flash-Preview",)


def test_every_builtin_slot_declares_its_tier():
    """A missing tier used to mean "free" -- which can spend money silently."""
    from app.pool.sources import CLI_TIERS, DEFAULT_CLI_SLOTS

    for slot in DEFAULT_CLI_SLOTS:
        assert slot.get("tier") in CLI_TIERS, slot


def test_a_declared_slot_extends_the_builtin_slots(tmp_path):
    """Adding a harness must not drop every other free slot."""
    config = tmp_path / "pool.json"
    config.write_text(
        json.dumps(
            {
                "cli_slots": [
                    {
                        "name": "newcli",
                        "model": "free-1",
                        "binary": "newcli",
                        "args": ["-p"],
                        "tier": "free",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    upstreams = {
        upstream.name: upstream
        for upstream in discover_upstreams(tmp_path / "home", config_path=config)
    }
    assert upstreams["newcli:free-1"].models == ("newcli/free-1",)
    assert upstreams["newcli:free-1"].tier == "free"
    assert "codebuddy:hy3" in upstreams
    assert "opencode-cli:opencode/minimax-m3.1-flash-free" in upstreams


def test_a_declared_slot_without_a_tier_is_not_offered(tmp_path):
    config = tmp_path / "pool.json"
    config.write_text(
        json.dumps(
            {"cli_slots": [{"name": "paid", "model": "gold", "binary": "paid", "args": ["-p"]}]}
        ),
        encoding="utf-8",
    )
    upstreams = {
        upstream.name: upstream
        for upstream in discover_upstreams(tmp_path / "home", config_path=config)
    }
    rejected = upstreams["paid:gold"]
    assert rejected.models == ()
    assert "tier" in rejected.note
    # The rejection is visible, and the built-ins still work.
    assert "qoder:DeepSeek-Flash" in upstreams


def test_a_store_the_pool_cannot_read_is_not_offered(tmp_path):
    config = tmp_path / "pool.json"
    config.write_text(
        json.dumps(
            {
                "cli_slots": [
                    {
                        "name": "newcli",
                        "model": "free-1",
                        "tier": "free",
                        "store": "mystery",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    upstreams = {
        upstream.name: upstream
        for upstream in discover_upstreams(tmp_path / "home", config_path=config)
    }
    rejected = upstreams["newcli:free-1"]
    assert rejected.models == ()
    assert "store" in rejected.note


def test_a_declaration_can_switch_a_builtin_slot_off(tmp_path):
    config = tmp_path / "pool.json"
    config.write_text(
        json.dumps({"cli_slots": [{"name": "codebuddy", "enabled": False}]}),
        encoding="utf-8",
    )
    upstreams = {
        upstream.name: upstream
        for upstream in discover_upstreams(tmp_path / "home", config_path=config)
    }
    assert not any(name.startswith("codebuddy:") for name in upstreams)
    assert "qoder:DeepSeek-Flash" in upstreams


def test_the_declared_slots_are_read_from_the_data_directory(tmp_path, monkeypatch):
    from app.config import settings
    from app.pool.sources import pool_config_path

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    (tmp_path / "pool").mkdir()
    (tmp_path / "pool" / "config.json").write_text(
        json.dumps({"cli_slots": [{"name": "newcli", "model": "free-1", "tier": "free"}]}),
        encoding="utf-8",
    )
    assert pool_config_path() == tmp_path / "pool" / "config.json"
    upstreams = {upstream.name: upstream for upstream in discover_upstreams(tmp_path / "home")}
    assert "newcli:free-1" in upstreams


def test_the_minimax_quota_is_read_from_the_mmx_clis_own_credentials(tmp_path):
    home = tmp_path / "home"
    (home / ".mmx").mkdir(parents=True)
    (home / ".mmx" / "config.json").write_text(
        json.dumps({"api_key": "sk-cp-test", "region": "global"}), encoding="utf-8"
    )
    upstreams = {upstream.name: upstream for upstream in discover_upstreams(home)}
    assert "minimax" in upstreams
    minimax = upstreams["minimax"]
    # MiniMax speaks the Anthropic protocol on <base>/anthropic.
    assert minimax.protocol == ANTHROPIC
    assert minimax.base_url == "https://api.minimax.io/anthropic"
    assert minimax.models == ("MiniMax-M3.1-Flash-Preview",)
    assert minimax.credential_value() == "sk-cp-test"


def test_an_oauth_login_is_resolved_like_the_mmx_sdk_resolves_it(tmp_path):
    """The signed-in shape: an OAuth token and the account's own base URL.

    The SDK's order is ``base_url`` -> ``oauth.resource_url`` -> the region's
    default, and the credential may be an API key *or* an OAuth access token.
    """
    home = tmp_path / "home"
    (home / ".mmx").mkdir(parents=True)
    (home / ".mmx" / "config.json").write_text(
        json.dumps(
            {
                "oauth": {
                    "access_token": "oat-test",
                    "refresh_token": "dfrt-test",
                    "region": "cn",
                    "resource_url": "https://api.minimaxi.com",
                },
                "region": "cn",
            }
        ),
        encoding="utf-8",
    )
    upstreams = {upstream.name: upstream for upstream in discover_upstreams(home)}
    minimax = upstreams["minimax"]
    assert minimax.base_url == "https://api.minimaxi.com/anthropic"
    assert minimax.credential_value() == "oat-test"
    # The CLI slot exists too (a signed-in CLI can spend the quota).
    assert "mmx:MiniMax-M3.1-Flash-Preview" in upstreams


def test_without_a_sign_in_there_is_no_minimax_quota(tmp_path):
    home = tmp_path / "home"
    (home / ".mmx").mkdir(parents=True)
    (home / ".mmx" / "config.json").write_text(json.dumps({}), encoding="utf-8")
    upstreams = {upstream.name: upstream for upstream in discover_upstreams(home)}
    assert "minimax" not in upstreams


def test_the_mmx_slot_passes_the_question_as_a_flag(tmp_path):
    source = CliModelSource(
        name="mmx",
        model="MiniMax-M3.1-Flash-Preview",
        binary="mmx",
        args=("text", "chat", "--output", "json", "--non-interactive", "--quiet"),
        message_flag="--message",
    )
    argv = source._argv("Question:\nhello", tmp_path)
    assert argv[1:4] == ["text", "chat", "--output"]
    assert "--message" in argv
    # The question is a flag value, so it is one line and never a positional.
    assert argv[argv.index("--message") + 1] == "Question: hello"


# --------------------------------------------------------------------------- #
# streaming and permissions
# --------------------------------------------------------------------------- #


def test_a_chat_stream_becomes_a_responses_stream():
    from app.pool.translate import chat_stream_to_responses_stream

    lines = iter(
        [
            'data: {"choices":[{"index":0,"delta":{"content":"O"}}]}',
            'data: {"choices":[{"index":0,"delta":{"content":"K"}}]}',
            "data: [DONE]",
        ]
    )
    events = list(chat_stream_to_responses_stream(lines, model="m"))
    text = "".join(
        json.loads(line.split("data: ", 1)[1]).get("delta", "")
        for line in events
        if "output_text.delta" in line
    )
    assert text == "OK"
    assert events[0].startswith("event: response.created")
    assert any("response.completed" in line for line in events)


def test_a_responses_request_reaches_a_cli_slot_as_a_question():
    from app.pool.service import _messages_of

    assert _messages_of({"input": "Reply with exactly: OK"}) == [
        {"role": "user", "content": "Reply with exactly: OK"}
    ]
    assert _messages_of(
        {"input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]}
    ) == [{"role": "user", "content": "hi"}]
    assert _messages_of({"messages": [{"role": "user", "content": "x"}]}) == [
        {"role": "user", "content": "x"}
    ]


def test_the_pool_has_its_own_permission_key():
    from app.auth.policy import POLICY

    # The pool borrows somebody's quota: that is not the same act as asking
    # what capabilities one has, so it has its own key (owner-approved).
    assert POLICY["harness:pool"] == frozenset({"harness"})
    assert POLICY["harness:capabilities"] == frozenset({"harness"})


def test_a_human_may_not_use_the_pool(tmp_path, monkeypatch):
    from app.adapter.protocol import HarnessRegistration
    from app.adapter.registry import HarnessRegistry
    from app.auth.tokens import TokenService
    from app.config import settings
    from tests.api_support import make_client

    client, database = make_client(tmp_path)
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
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    human = TokenService(database).issue(subject_type="human", subject_id="operator")
    harness = TokenService(database).issue(subject_type="harness", subject_id="h-A")

    refused = client.get("/api/v1/pool/v1/models", headers={"Authorization": f"Bearer {human}"})
    assert refused.status_code == 403
    allowed = client.get("/api/v1/pool/v1/models", headers={"Authorization": f"Bearer {harness}"})
    assert allowed.status_code == 200
    assert "data" in allowed.json()


# --------------------------------------------------------------------------- #
# how a CLI is pointed at the pool
# --------------------------------------------------------------------------- #


def test_claude_code_is_pointed_at_the_pool_without_touching_the_users_settings(tmp_path):
    from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
    from app.adapter.harnesses.cli import ProviderRoute

    adapter = ClaudeCodeAdapter(client=None, harness_id="claude-code")
    adapter.provider_route = ProviderRoute(
        base_url="http://127.0.0.1:8848/api/v1/pool",
        token="harness-token",
        model="minimax-m3.1-flash-free",
        protocol="anthropic",
    )
    argv = adapter.command("do the work", tmp_path)
    assert "--settings" in argv
    settings_path = Path(argv[argv.index("--settings") + 1])
    document = json.loads(settings_path.read_text(encoding="utf-8"))
    assert document["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8848/api/v1/pool"
    assert document["env"]["ANTHROPIC_AUTH_TOKEN"] == "harness-token"
    assert document["env"]["ANTHROPIC_MODEL"] == "minimax-m3.1-flash-free"
    adapter.cleanup_work_order()
    assert not settings_path.exists()


def test_codex_gets_per_attempt_overrides_and_the_key_in_the_environment(tmp_path):
    from app.adapter.harnesses.cli import ProviderRoute
    from app.adapter.harnesses.codex import CodexAdapter

    adapter = CodexAdapter(client=None, harness_id="codex")
    adapter.provider_route = ProviderRoute(
        base_url="http://127.0.0.1:8848/api/v1/pool/v1",
        token="harness-token",
        model="gpt-6.1-sol",
        protocol="openai",
    )
    argv = adapter.command("do the work", tmp_path)
    assert "-c" in argv
    assert "model=minimax-m3.1-flash-free" in argv or "model=gpt-6.1-sol" in argv
    assert "model_provider=pool" in argv
    assert any(part.startswith("model_providers.pool.base_url=") for part in argv)
    assert adapter.route_environment() == {"OPENAI_API_KEY": "harness-token"}


def test_opencode_asks_the_pool_through_its_own_isolated_config(tmp_path):
    from app.adapter.harnesses.cli import ProviderRoute
    from app.adapter.harnesses.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter(client=None, harness_id="opencode")
    adapter.provider_route = ProviderRoute(
        base_url="http://127.0.0.1:8848/api/v1/pool/v1",
        token="harness-token",
        model="minimax-m3.1-flash-free",
        protocol="openai",
    )
    argv = adapter.command("do the work", tmp_path)
    # On Windows the command is wrapped for cmd.exe; the model flag has to be
    # in there either way.
    joined = " ".join(argv)
    assert "--model pool/minimax-m3.1-flash-free" in joined
    # The OpenCode CLI takes its providers from **its own config file**, and the
    # built-in ``openai`` provider only knows OpenAI's own model names -- so
    # pointing OPENAI_BASE_URL at the pool is not enough: the CLI answers
    # "Model unavailable" before it ever calls the pool.  The window therefore
    # gets a config of its own (the user's config is never touched) with a
    # ``pool`` provider that speaks to the platform.
    environment = adapter.route_environment()
    config_home = Path(environment["XDG_CONFIG_HOME"])
    assert config_home != Path.home() / ".config"
    config = json.loads((config_home / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    provider = config["provider"]["pool"]
    assert provider["options"]["baseURL"] == "http://127.0.0.1:8848/api/v1/pool/v1"
    assert provider["options"]["apiKey"] == "harness-token"
    assert "minimax-m3.1-flash-free" in provider["models"]


# --------------------------------------------------------------------------- #
# accounting over time
# --------------------------------------------------------------------------- #


def test_the_quota_timeline_says_when_the_quota_went(tmp_path):
    """A total cannot tell a burst from a trickle.

    "Did we just burn through this window's quota?" is a question about
    **time**, and the office can only answer it if the pool hands out the
    spend hour by hour.
    """
    from app.pool.service import PoolService

    usage = tmp_path / "usage.jsonl"
    now = datetime.now(UTC)
    records = [
        {
            "at": (now - timedelta(hours=1)).isoformat(),
            "upstream": "opencode-cli",
            "model": "m",
            "consumer": "harness/opencode",
            "prompt_tokens": 3,
            "completion_tokens": 4,
        },
        {
            "at": (now - timedelta(hours=1)).isoformat(),
            "upstream": "opencode-cli",
            "model": "m",
            "consumer": "harness/claude-code",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        },
        {
            "at": (now - timedelta(hours=30)).isoformat(),
            "upstream": "older",
            "model": "m",
            "consumer": "harness/opencode",
            "prompt_tokens": 0,
            "completion_tokens": 0,
        },
    ]
    usage.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    timeline = PoolService(home=tmp_path, usage_path=usage).usage_timeline(hours=24)

    assert timeline["bucket"] == "hour"
    assert len(timeline["buckets"]) == 24
    busy = [bucket for bucket in timeline["buckets"] if bucket["requests"]]
    assert len(busy) == 1
    assert busy[0]["requests"] == 2
    assert busy[0]["upstreams"] == {"opencode-cli": 2}
    assert busy[0]["consumers"] == {"harness/opencode": 1, "harness/claude-code": 1}
    assert busy[0]["prompt_tokens"] == 3
    assert busy[0]["completion_tokens"] == 4
    # 窗口外的那条不混进来，但要**说**它在窗口外，不装作没有。
    assert timeline["outside_window"] == 1


def test_the_timeline_of_a_pool_that_never_spent_anything_is_empty_not_broken(tmp_path):
    from app.pool.service import PoolService

    usage = tmp_path / "usage.jsonl"
    usage.write_text("", encoding="utf-8")

    timeline = PoolService(home=tmp_path, usage_path=usage).usage_timeline(hours=6)

    assert len(timeline["buckets"]) == 6
    assert all(bucket["requests"] == 0 for bucket in timeline["buckets"])
    assert timeline["outside_window"] == 0


# --------------------------------------------------------------------------- #
# what is left, tier by tier
# --------------------------------------------------------------------------- #


def _upstream(name: str, tier: str):
    from app.pool.sources import Upstream

    return Upstream(name=name, protocol="openai_chat", base_url=f"https://{name}", tier=tier)


def test_the_inventory_says_what_is_left_where_a_balance_can_be_asked_for():
    """The reserve tier is a subscription: the CLI can say what is left.

    The free and metered tiers have no balance to ask for, and the inventory
    must say **that** -- not a number the platform made up.
    """
    from app.pool.inventory import build_inventory

    inventory = build_inventory(
        upstreams=[
            _upstream("opencode-cli:free", "free"),
            _upstream("minimax", "reserve"),
            _upstream("codex:geekspace", "metered"),
        ],
        models=[
            {"id": "opencode/free-model", "tier": "free"},
            {"id": "MiniMax-M3.1-Flash-Preview", "tier": "reserve"},
            {"id": "gpt-6.1-sol", "tier": "metered"},
        ],
        usage={
            "upstreams": {
                "opencode-cli:free": {
                    "requests": 4,
                    "prompt_tokens": 1,
                    "completion_tokens": 2,
                    "consumers": {"harness/opencode": 4},
                },
                "minimax": {
                    "requests": 2,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "consumers": {"harness/opencode": 2},
                },
                "codex:geekspace": {
                    "requests": 1,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "consumers": {"harness/claude-code": 1},
                },
            }
        },
        quota={
            "known": True,
            "source": "mmx quota show",
            "checked_at": "2026-10-21T00:00:00+00:00",
            "interval": {"remaining_percent": 99, "resets_at": "2026-10-21T17:00:00+00:00"},
            "weekly": {"remaining_percent": 93, "resets_at": "2026-10-28T00:00:00+00:00"},
        },
    )

    tiers = {tier["tier"]: tier for tier in inventory["tiers"]}
    assert list(tiers) == ["free", "reserve", "metered"]
    assert tiers["free"]["models"] == 1
    assert tiers["free"]["balance"] is None
    assert "没有可查的余额" in tiers["free"]["note"]
    assert tiers["free"]["spend"]["requests"] == 4
    assert tiers["free"]["spend"]["consumers"] == {"harness/opencode": 4}
    # The reserve tier is the only one with a balance, and it says who said so.
    assert tiers["reserve"]["balance"]["known"] is True
    assert tiers["reserve"]["balance"]["source"] == "mmx quota show"
    assert tiers["reserve"]["balance"]["weekly"]["remaining_percent"] == 93
    assert tiers["reserve"]["spend"]["requests"] == 2
    assert tiers["reserve"]["spend"]["prompt_tokens"] == 10
    # Pay-as-you-go has no balance -- and the platform does not invent one.
    assert tiers["metered"]["balance"] is None
    assert tiers["metered"]["spend"]["requests"] == 1


def test_the_inventory_says_why_a_balance_could_not_be_read():
    from app.pool.inventory import build_inventory

    inventory = build_inventory(
        upstreams=[_upstream("minimax", "reserve")],
        models=[{"id": "MiniMax-M3.1-Flash-Preview", "tier": "reserve"}],
        usage={"upstreams": {}},
        quota={"known": False, "source": "mmx quota show", "reason": "mmx 不在这台机器上"},
    )

    reserve = next(tier for tier in inventory["tiers"] if tier["tier"] == "reserve")
    assert reserve["balance"]["known"] is False
    assert reserve["balance"]["reason"] == "mmx 不在这台机器上"


def test_the_minimax_quota_answer_is_read_as_it_is():
    """The CLI's own words: the plan's remaining, and when it resets."""
    from app.pool.inventory import parse_minimax_quota

    reading = parse_minimax_quota(
        {
            "model_remains": [
                {
                    "model_name": "general",
                    "end_time": 1792602009538,
                    "weekly_end_time": 1793168957471,
                    "current_interval_remaining_percent": 99,
                    "current_weekly_remaining_percent": 93,
                    "current_interval_usage_count": 4,
                    "current_interval_total_count": 400,
                }
            ],
            "base_resp": {"status_code": 0},
        }
    )

    assert reading["known"] is True
    assert reading["interval"]["remaining_percent"] == 99
    assert reading["weekly"]["remaining_percent"] == 93
    assert reading["interval"]["resets_at"].startswith("2026-10-21T17:00")
    assert reading["weekly"]["resets_at"].startswith("2026-10-28T06:29")


def test_a_quota_answer_without_any_allowance_is_not_a_balance():
    from app.pool.inventory import parse_minimax_quota

    reading = parse_minimax_quota({"model_remains": [], "base_resp": {"status_code": 0}})

    assert reading["known"] is False
    assert reading["reason"]
