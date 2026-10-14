"""Regression tests for selecting the real CLI adapter per Harness identity."""

from types import SimpleNamespace

import pytest

from app.adapter.formal_worker import adapter_for_harness
from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
from app.adapter.harnesses.codex import CodexAdapter
from app.adapter.harnesses.opencode import OpenCodeAdapter


@pytest.mark.parametrize(
    ("harness_id", "adapter_type"),
    [
        ("codex", CodexAdapter),
        ("claude-code", ClaudeCodeAdapter),
        ("opencode", OpenCodeAdapter),
    ],
)
def test_formal_worker_selects_the_matching_cli(harness_id, adapter_type):
    adapter = adapter_for_harness(SimpleNamespace(), harness_id)
    assert isinstance(adapter, adapter_type)
    assert adapter.registration.harness_id == harness_id


def test_formal_worker_rejects_unknown_cli_instead_of_falling_back():
    with pytest.raises(ValueError, match="unsupported formal Harness"):
        adapter_for_harness(SimpleNamespace(), "not-registered")
