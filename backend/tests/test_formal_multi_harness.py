"""Regression tests for the real multi-Harness worker path."""

import json
import multiprocessing
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapter.formal_worker import adapter_for_harness, base_harness_id
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


@pytest.mark.parametrize(
    ("instance_id", "base_id", "adapter_type"),
    [
        ("codex-member-1", "codex", CodexAdapter),
        ("codex-window-2", "codex", CodexAdapter),
        ("claude-code-member-1", "claude-code", ClaudeCodeAdapter),
        ("opencode-window-review", "opencode", OpenCodeAdapter),
    ],
)
def test_formal_worker_keeps_window_identity_while_reusing_adapter(
    instance_id, base_id, adapter_type
):
    assert base_harness_id(instance_id) == base_id
    adapter = adapter_for_harness(SimpleNamespace(), instance_id)
    assert isinstance(adapter, adapter_type)
    assert adapter.registration.harness_id == instance_id


@pytest.mark.parametrize("instance_id", ["codex-member", "codex-admin-1", "unknown-member-1"])
def test_formal_worker_rejects_ambiguous_window_identity(instance_id):
    with pytest.raises(ValueError, match="unsupported formal Harness"):
        base_harness_id(instance_id)


def test_pi_adapter_isolates_ollama_config_and_enables_tools(tmp_path):
    adapter = PiAdapter(SimpleNamespace(), config_root=tmp_path / "pi-config")
    prepared = adapter.prepare_work_order("make the file", tmp_path)
    command = adapter.command(prepared, tmp_path)
    environment = adapter.process_environment()
    assert Path(command[0]).stem.lower() == "pi"
    # The model is **resolved**: an explicitly configured one wins, and when
    # there is none the window uses a model this machine actually has (the
    # default tag is often not installed, and the CLI then dies with a bare
    # "Model not found").
    assert command[1:4] == ["--approve", "--model", adapter.resolve_model()]
    assert adapter.resolve_model().startswith("ollama/")
    assert "--tools" in command and command[command.index("--tools") + 1] == "read,edit,write"
    assert any(item.startswith("@") for item in command)
    assert environment["PI_CODING_AGENT_DIR"] == str(tmp_path / "pi-config")
    assert (tmp_path / "pi-config" / "models.json").exists()
    assert environment["PI_CODING_AGENT_DIR"] != str(Path.home() / ".pi" / "agent")
    adapter.cleanup_work_order()


def test_pi_adapter_keeps_an_explicitly_configured_model(tmp_path):
    """人点名要的模型就是那个模型：装没装都照用，不悄悄换一个。"""
    adapter = PiAdapter(SimpleNamespace(), config_root=tmp_path / "pi-config", model="ollama/chosen:1b")
    assert adapter.resolve_model() == "ollama/chosen:1b"


def test_pi_adapter_asks_the_pool_through_its_own_config(tmp_path):
    """走额度池时，Pi 的 provider 指向池子，模型是池子里的那个。

    Pi 的模型 id 里带斜杠（provider/model），而 Pi 自己按 "provider/id" 拆
    模型 —— 所以这里必须把 provider 与 model 分开给：拆错了就找不到模型。
    """
    from app.adapter.harnesses.cli import ProviderRoute

    adapter = PiAdapter(SimpleNamespace(), config_root=tmp_path / "pi-config")
    adapter.provider_route = ProviderRoute(
        base_url="http://127.0.0.1:8848/api/v1/pool/v1",
        token="harness-token",
        model="codebuddy/minimax-m3.1-flash",
        protocol="openai",
    )
    prepared = adapter.prepare_work_order("make the file", tmp_path)
    command = adapter.command(prepared, tmp_path)
    assert command[1:5] == ["--approve", "--provider", "pool", "--model"]
    assert command[5] == "codebuddy/minimax-m3.1-flash"
    config = json.loads((tmp_path / "pi-config" / "models.json").read_text(encoding="utf-8"))
    provider = config["providers"]["pool"]
    assert provider["baseUrl"] == "http://127.0.0.1:8848/api/v1/pool/v1"
    assert provider["apiKey"] == "harness-token"
    assert provider["models"][0]["id"] == "codebuddy/minimax-m3.1-flash"


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
    # The recipe carries ``--standalone`` since the OpenCode transport
    # hardening (the CLI must run its **own** server, not attach to whatever
    # background service happens to be running).  The shim itself is unchanged.
    assert "opencode run --standalone --format json --auto" in command[4]


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
