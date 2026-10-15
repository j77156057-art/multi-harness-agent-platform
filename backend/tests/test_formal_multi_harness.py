"""Regression tests for the real multi-Harness worker path."""

import multiprocessing
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapter.formal_worker import adapter_for_harness
from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
from app.adapter.harnesses.codex import CodexAdapter
from app.adapter.harnesses.opencode import OpenCodeAdapter
from app.adapter.harnesses.pi import PiAdapter
from app.git.repo import CanonicalRepo


def _ensure_repo(path: str) -> None:
    CanonicalRepo(Path(path)).ensure()


@pytest.mark.parametrize(
    ("harness_id", "adapter_type"),
    [
        ("codex", CodexAdapter),
        ("claude-code", ClaudeCodeAdapter),
        ("opencode", OpenCodeAdapter),
        ("pi", PiAdapter),
    ],
)
def test_formal_worker_selects_the_matching_cli(harness_id, adapter_type):
    adapter = adapter_for_harness(SimpleNamespace(), harness_id)
    assert isinstance(adapter, adapter_type)
    assert adapter.registration.harness_id == harness_id


def test_formal_worker_rejects_unknown_cli_instead_of_falling_back():
    with pytest.raises(ValueError, match="unsupported formal Harness"):
        adapter_for_harness(SimpleNamespace(), "not-registered")


def test_pi_adapter_isolates_ollama_config_and_enables_tools(tmp_path):
    adapter = PiAdapter(SimpleNamespace(), config_root=tmp_path / "pi-config")
    command = adapter.command("make the file", tmp_path)
    environment = adapter.process_environment()
    assert Path(command[0]).stem.lower() == "pi"
    assert command[1:4] == ["--approve", "--model", "ollama/qwen3:8b"]
    assert "--tools" in command and "read,bash,edit,write" in command
    assert environment["PI_CODING_AGENT_DIR"] == str(tmp_path / "pi-config")
    assert (tmp_path / "pi-config" / "models.json").exists()
    assert environment["PI_CODING_AGENT_DIR"] != str(Path.home() / ".pi" / "agent")
    adapter.cleanup_work_order()


def test_claude_adapter_allows_only_the_attempt_gitdir(tmp_path):
    worktree = tmp_path / "attempt"
    git_dir = tmp_path / "attempt.git"
    worktree.mkdir()
    git_dir.mkdir()
    (worktree / ".git").write_text("gitdir: ../attempt.git\n", encoding="utf-8")
    adapter = ClaudeCodeAdapter(SimpleNamespace())

    command = adapter.command("work order", worktree)

    assert command[:3] == ["claude", "--add-dir", str(git_dir.resolve())]
    assert "--dangerously-skip-permissions" not in command
    assert command[-1] == "work order"


def test_windowsapps_bash_uses_cmd_shim_for_opencode(tmp_path):
    adapter = OpenCodeAdapter(
        SimpleNamespace(),
        platform="nt",
        bash_path=r"C:\Users\operator\AppData\Local\Microsoft\WindowsApps\bash.exe",
    )
    command = adapter.command("Reply with READY", tmp_path)
    assert command[:3] == ["cmd.exe", "/d", "/s"]
    assert "opencode run --format json --auto" in command[4]


def test_windowsapps_opencode_moves_multiline_order_to_attachment(tmp_path):
    adapter = OpenCodeAdapter(
        SimpleNamespace(),
        platform="nt",
        bash_path=r"C:\Users\operator\AppData\Local\Microsoft\WindowsApps\bash.exe",
    )
    work_order = 'line one\nJSON {"path":"a&b"} %PATH%\nline three'
    prepared = adapter.prepare_work_order(work_order, tmp_path)
    command = adapter.command(prepared, tmp_path)
    bundle = adapter._work_order_bundle
    assert bundle is not None
    assert (bundle / "work-order.txt").read_text(encoding="utf-8") == work_order
    assert "line one" not in " ".join(command)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "opencode.cmd").write_text("@echo off\r\nexit /b 0\r\n", encoding="utf-8")
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    completed = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    adapter.cleanup_work_order()
    assert not bundle.exists()


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
