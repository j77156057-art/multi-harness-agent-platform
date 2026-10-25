"""OFFICE-UI-30: the assistant's harness-fit check (and the declaration draft).

Three things are pinned here:

* the gap list says what is missing and **why** -- no guessing, no advice;
* the read face answers with the declared bases, their templates and their gaps,
  plus the built-in base names (a built-in needs no declaration);
* the assistant's tool table carries the new read tool.
"""

from __future__ import annotations

import json

import pytest

from app.adapter.harnesses import generic
from app.adapter.harnesses.generic import base_gaps, base_template, load_declarations
from app.assistant.tools import build_registry
from app.config import settings

DECLARED = {
    "base": "newcli",
    "binary": "newcli",
    "args": ["run", "--workdir", "{workdir}", "--model", "{model}"],
    "model": "newcli-default",
    "display_name": "New CLI",
    "adapter": "newcli-print",
    "capabilities": ["coding"],
}


@pytest.fixture
def declared(tmp_path, monkeypatch):
    """Point the declaration registry at a temp data directory."""
    scoped = tmp_path / "adapters" / "cli.json"
    scoped.parent.mkdir(parents=True)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(generic, "DEFAULT_CONFIG_PATH", tmp_path / "missing" / "cli.json")

    def write(payload):
        scoped.write_text(json.dumps(payload), encoding="utf-8")
        return scoped

    return write


def test_a_declared_base_without_a_review_cannot_review(declared):
    """No review_args: a review that can edit files is not a review."""
    declared({"adapters": [DECLARED]})

    gaps = base_gaps(load_declarations()["newcli"])
    assert any("review_args" in gap for gap in gaps)


def test_a_declared_base_with_a_pool_slot_needs_a_tier(declared):
    """A slot without a tier is not offered -- an unreported quota must be visible."""
    declared({"adapters": [DECLARED]})

    gaps = base_gaps(load_declarations()["newcli"], pool_slot={"name": "newcli"})
    assert any("tier" in gap for gap in gaps)
    assert not any("槽位" in gap and "没有" not in gap for gap in gaps)


def test_a_declared_base_without_a_pool_slot_cannot_serve_models(declared):
    """No slot: it cannot lend its quota to anybody else yet."""
    declared({"adapters": [DECLARED]})

    gaps = base_gaps(load_declarations()["newcli"])
    assert any("槽位" in gap for gap in gaps)


def test_a_complete_declaration_has_no_gaps(declared):
    """Everything declared: nothing missing."""
    declared({"adapters": [{**DECLARED, "review_args": ["--read-only", "{workdir}"]}]})

    declaration = load_declarations()["newcli"]
    assert base_gaps(declaration, pool_slot={"name": "newcli", "tier": "free"}) == []


def test_the_template_and_the_gaps_come_from_the_same_declaration(declared):
    """The face must not invent facts: the template is the declaration's own."""
    declared({"adapters": [DECLARED]})

    declaration = load_declarations()["newcli"]
    template = base_template(declaration)
    assert template["harness_id"] == "newcli"
    assert template["name"] == "New CLI"
    assert template["adapter"] == "newcli-print"
    assert template["capabilities"] == ["coding"]


def test_the_assistant_gets_a_read_only_tool_for_it(declared):
    """The tool is read-only: the assistant reports gaps, it does not install."""
    declared({"adapters": [DECLARED]})

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, route, **kwargs):
            self.calls.append(route)
            return {"bases": [], "builtin_bases": ["codex"]}

    client = Client()
    registry = build_registry(client, group_id="g", author_id="office-assistant")
    tool = registry["harness.bases"]
    assert tool.kind == "read"
    assert tool.call({}) == {"bases": [], "builtin_bases": ["codex"]}
    assert client.calls == ["/harnesses/bases"]
