"""Regression tests for the real multi-Harness worker path."""

import multiprocessing
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapter.formal_worker import adapter_for_harness
from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
from app.adapter.harnesses.codex import CodexAdapter
from app.adapter.harnesses.opencode import OpenCodeAdapter
from app.git.repo import CanonicalRepo


def _ensure_repo(path: str) -> None:
    CanonicalRepo(Path(path)).ensure()


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


def test_windowsapps_bash_uses_cmd_shim_for_opencode(tmp_path):
    adapter = OpenCodeAdapter(
        SimpleNamespace(),
        platform="nt",
        bash_path=r"C:\Users\operator\AppData\Local\Microsoft\WindowsApps\bash.exe",
    )
    command = adapter.command("Reply with READY", tmp_path)
    assert command[:3] == ["cmd.exe", "/d", "/s"]
    assert "opencode run --format json --auto" in command[4]


def test_canonical_repo_initialization_is_safe_for_parallel_workers(tmp_path):
    repo_path = str(tmp_path / "canonical.git")
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_ensure_repo, args=(repo_path,)) for _ in range(3)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=30)
    assert all(worker.exitcode == 0 for worker in workers)
    assert CanonicalRepo(Path(repo_path)).ref_commit("refs/heads/integration")
