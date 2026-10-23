"""A CLI joins the platform by declaration, not by a new Python class.

The built-in adapters are regression-pinned elsewhere.  These tests pin the
*generic* contract: the registry is read from the data directory, a declared
base becomes a real formal worker identity, the argv is the declared one, a
review without a read-only argv is refused, and a broken declaration is
refused with a reason instead of silently becoming a different CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapter.formal_worker import (
    ADAPTER_TYPES,
    adapter_for_harness,
    base_harness_id,
    known_bases,
)
from app.adapter.harnesses import generic
from app.adapter.harnesses.cli import ProviderRoute
from app.adapter.harnesses.generic import GenericCliAdapter, load_declarations
from app.config import settings

NEW_CLI = {
    "base": "newcli",
    "binary": "newcli-test-bin",
    "args": ["run", "--workdir", "{workdir}", "--model", "{model}"],
    "model": "newcli-default",
    "display_name": "New CLI",
    "adapter": "newcli-print",
    "capabilities": ["coding", "review"],
}


@pytest.fixture
def declared(tmp_path, monkeypatch):
    """Point the registry at a temp data directory, never at the repo's data."""
    scoped = tmp_path / "adapters" / "cli.json"
    scoped.parent.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(generic, "DEFAULT_CONFIG_PATH", tmp_path / "missing" / "cli.json")

    def write(payload):
        scoped.write_text(json.dumps(payload), encoding="utf-8")
        return scoped

    return write


def _binary(tmp_path: Path) -> Path:
    path = tmp_path / "newcli-test-bin.exe"
    path.write_text("", encoding="utf-8")
    return path


def _adapter(tmp_path: Path, **overrides) -> GenericCliAdapter:
    entry = {**NEW_CLI, **overrides, "binary": str(_binary(tmp_path))}
    declaration = generic._declaration(0, entry)
    return GenericCliAdapter(
        SimpleNamespace(), harness_id="newcli-member-1", declaration=declaration
    )


def test_a_declared_base_is_a_formal_identity(declared):
    declared({"adapters": [NEW_CLI]})

    # The fixture pointed data_dir at the temp directory; reading it again is
    # what a worker does at startup.
    assert base_harness_id("newcli") == "newcli"
    assert base_harness_id("newcli-member-1") == "newcli"
    assert "newcli" in known_bases()


def test_the_adapter_names_the_executable_not_a_builtin(declared, tmp_path):
    declared({"adapters": [{**NEW_CLI, "binary": str(_binary(tmp_path))}]})
    adapter = adapter_for_harness(SimpleNamespace(), "newcli-member-1")
    assert isinstance(adapter, GenericCliAdapter)
    assert adapter.registration.harness_id == "newcli-member-1"
    assert adapter.registration.provider == "newcli"
    assert adapter.registration.adapter == "newcli-print"
    assert adapter.registration.capabilities == ("coding", "review")


def test_a_work_order_and_workdir_land_in_the_declared_argv(tmp_path):
    adapter = _adapter(tmp_path)
    argv = adapter.command("do the thing", tmp_path)
    assert argv == [
        str(tmp_path / "newcli-test-bin.exe"),
        "run",
        "--workdir",
        str(tmp_path),
        "--model",
        "newcli-default",
        "do the thing",
    ]


def test_the_pool_model_replaces_the_declared_default(tmp_path):
    adapter = _adapter(tmp_path)
    adapter.provider_route = ProviderRoute(base_url="http://pool/v1", token="t", model="pool-model")
    argv = adapter.command("do it", tmp_path)
    assert "pool-model" in argv
    assert "newcli-default" not in argv


def test_stdin_declarations_keep_the_question_out_of_argv(tmp_path):
    adapter = _adapter(tmp_path, prompt_via_stdin=True)
    argv = adapter.command("a very long question", tmp_path)
    assert "a very long question" not in argv
    assert adapter.prompt_via_stdin is True


def test_a_review_without_a_read_only_argv_is_refused(tmp_path):
    adapter = _adapter(tmp_path)
    with pytest.raises(ValueError, match="read-only"):
        adapter.review_command("review this", tmp_path)


def test_a_declared_review_argv_is_used(tmp_path):
    adapter = _adapter(
        tmp_path,
        review_args=["run", "--workdir", "{workdir}", "--read-only", "--model", "{model}"],
    )
    argv = adapter.review_command("review this", tmp_path)
    assert "--read-only" in argv
    assert str(tmp_path) in argv
    assert argv[-1] == "review this"
    assert "review_args" not in argv


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        (
            "openai",
            {"OPENAI_BASE_URL": "http://pool/v1", "OPENAI_API_KEY": "tok"},
        ),
        (
            "anthropic",
            {
                "ANTHROPIC_BASE_URL": "http://pool/v1",
                "ANTHROPIC_AUTH_TOKEN": "tok",
                "ANTHROPIC_MODEL": "pool-model",
            },
        ),
    ],
)
def test_the_pool_is_reached_in_the_declared_protocol(tmp_path, protocol, expected):
    adapter = _adapter(tmp_path, pool_protocol=protocol)
    adapter.provider_route = ProviderRoute(
        base_url="http://pool/v1", token="tok", model="pool-model", protocol=protocol
    )
    assert adapter.route_environment() == expected


def test_without_a_pool_route_the_environment_is_empty(tmp_path):
    assert _adapter(tmp_path).route_environment() == {}


def test_json_lines_output_is_read_tolerantly(tmp_path):
    adapter = _adapter(tmp_path)
    parsed = adapter.parse_output('{"session_id": "s-1"}\n{"result": "done"}\n')
    assert parsed == {"session_id": "s-1", "final_message": "done"}


def test_plain_text_output_keeps_its_tail(tmp_path):
    adapter = _adapter(tmp_path)
    parsed = adapter.parse_output("no json here\njust a sentence")
    assert parsed["final_message"].endswith("just a sentence")


def test_an_undeclared_base_is_still_refused(declared):
    declared({"adapters": [NEW_CLI]})
    with pytest.raises(ValueError, match="unsupported formal Harness"):
        base_harness_id("other-member-1")
    with pytest.raises(ValueError, match="unsupported formal Harness"):
        base_harness_id("newcli-admin-1")


def test_a_missing_registry_is_simply_empty(declared):
    assert load_declarations() == {}
    assert set(ADAPTER_TYPES) <= known_bases()


def test_the_builtin_bases_are_the_builtin_adapters():
    assert set(generic.BUILTIN_BASES) == set(ADAPTER_TYPES)


@pytest.mark.parametrize(
    ("entry", "reason"),
    [
        ({**NEW_CLI, "base": "codex"}, "built-in"),
        ({**NEW_CLI, "base": "NewCLI"}, "lowercase"),
        ({**NEW_CLI, "binary": ""}, "binary is required"),
        ({**NEW_CLI, "pool_protocol": "grpc"}, "pool_protocol"),
        ({**NEW_CLI, "model": ""}, "no default model"),
        ({**NEW_CLI, "args": "run"}, "list of strings"),
    ],
)
def test_a_declaration_that_cannot_be_checked_is_refused(declared, entry, reason):
    declared({"adapters": [entry]})
    with pytest.raises(ValueError, match=reason):
        load_declarations()


def test_a_duplicate_base_is_refused(declared):
    declared({"adapters": [NEW_CLI, dict(NEW_CLI)]})
    with pytest.raises(ValueError, match="declared twice"):
        load_declarations()


def test_an_unparseable_registry_says_which_file(declared):
    path = declared({"adapters": [NEW_CLI]})
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot be read as JSON"):
        load_declarations()


def test_a_broken_registry_does_not_quietly_drop_a_base(declared):
    declared({"adapters": [{**NEW_CLI, "pool_protocol": "grpc"}]})
    with pytest.raises(ValueError, match="pool_protocol"):
        known_bases()
