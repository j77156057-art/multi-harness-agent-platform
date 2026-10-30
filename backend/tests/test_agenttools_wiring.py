"""把平台工具接到适配器上（不是接到模型上）。

这一层的风险很具体，测试就盯这几点：

* 令牌不许出现在 argv 里（Windows 命令行对全机可见）；
* 写进工作树的配置文件必须被声明成"临时"，否则每次带工具的交付都会被判成
  留了脏东西而被整单拒收；
* 反过来，**声明不能用来藏交付物**：落在授权落点里的路径必须被丢掉；
* 没确认过机制的 CLI 不许"假装有工具"——基类返回 ``None``。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.adapter.formal_worker import ScopedRunner
from app.adapter.harnesses.claude_code import ClaudeCodeAdapter
from app.adapter.harnesses.opencode import OpenCodeAdapter
from app.adapter.harnesses.platform import PlatformToolsAdapter
from app.agenttools import mcp_config


class FakeClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8848", token: str = "apt_demo") -> None:
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}


def _worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    (root / "docs").mkdir(parents=True)
    return root


# ------------------------------------------------------------------ 通道描述


def test_no_task_context_means_no_tools(tmp_path: Path) -> None:
    """评审、一次性调用都不该拿到指向某个任务工作树的工具。"""
    assert mcp_config.binding_for(ClaudeCodeAdapter(FakeClient())) is None


def test_the_binding_carries_the_attempt_and_keeps_the_token_out_of_the_url() -> None:
    adapter = ClaudeCodeAdapter(FakeClient())
    adapter.task_context = {"task_id": "T-1", "attempt_id": "T-1.a1"}
    binding = mcp_config.binding_for(adapter)
    assert binding is not None
    assert binding.url().endswith("/api/v1/agenttools/mcp?task_id=T-1&attempt_id=T-1.a1")
    assert "apt_demo" not in binding.url()
    assert binding.headers() == {"Authorization": "Bearer apt_demo"}


def test_a_client_without_a_token_gets_no_binding() -> None:
    class NoToken:
        base_url = "http://127.0.0.1:8848"
        headers = {}

    adapter = ClaudeCodeAdapter(NoToken())
    adapter.task_context = {"task_id": "T-1", "attempt_id": "T-1.a1"}
    assert mcp_config.binding_for(adapter) is None


# -------------------------------------------------------------------- claude


def test_claude_gets_an_mcp_config_file_not_an_inline_token(tmp_path: Path) -> None:
    worktree = _worktree(tmp_path)
    adapter = ClaudeCodeAdapter(FakeClient())
    adapter.task_context = {"task_id": "T-1", "attempt_id": "T-1.a1"}
    argv = adapter.command("干活", worktree)
    try:
        assert "--mcp-config" in argv
        path = Path(argv[argv.index("--mcp-config") + 1])
        assert path.is_file()
        document = json.loads(path.read_text(encoding="utf-8"))
        server = document["mcpServers"]["platform"]
        assert server["url"].endswith("task_id=T-1&attempt_id=T-1.a1")
        assert server["headers"]["Authorization"] == "Bearer apt_demo"
        # 令牌在文件里，不在命令行上。
        assert "apt_demo" not in " ".join(argv)
        # 也不许落在工作树里（那样会被判成脏文件）。
        assert worktree not in path.parents
    finally:
        adapter.cleanup_work_order()
    assert not path.exists(), "这次尝试的 MCP 配置用完就要删掉"


def test_claude_without_task_context_gets_no_mcp_flag(tmp_path: Path) -> None:
    adapter = ClaudeCodeAdapter(FakeClient())
    assert "--mcp-config" not in adapter.command("干活", _worktree(tmp_path))


# ------------------------------------------------------------------ opencode


def test_opencode_writes_the_project_config_and_declares_it_transient(tmp_path: Path) -> None:
    worktree = _worktree(tmp_path)
    adapter = OpenCodeAdapter(FakeClient(), platform="nt", bash_path="")
    adapter.task_context = {"task_id": "T-1", "attempt_id": "T-1.a1"}
    adapter.command("干活", worktree)
    target = worktree / "opencode.json"
    assert target.is_file()
    document = json.loads(target.read_text(encoding="utf-8"))
    assert "platform" in document["mcp"]["servers"]
    assert adapter.transient_paths() == ("opencode.json",)


def test_opencode_merges_into_the_isolated_pool_config(tmp_path: Path) -> None:
    """走了池子的时候，MCP 块并进那份隔离配置，工作树一个字都不多。"""
    worktree = _worktree(tmp_path)
    root = tmp_path / "cfg"
    (root / "opencode").mkdir(parents=True)
    (root / "opencode" / "opencode.json").write_text(
        json.dumps({"provider": {"pool": {"name": "platform pool"}}}), encoding="utf-8"
    )
    adapter = OpenCodeAdapter(FakeClient(), platform="nt", bash_path="")
    adapter._pool_config_root = root
    adapter.task_context = {"task_id": "T-1", "attempt_id": "T-1.a1"}
    adapter.command("干活", worktree)
    document = json.loads((root / "opencode" / "opencode.json").read_text(encoding="utf-8"))
    assert "pool" in document["provider"], "并进去，不是覆盖：窗口的模型不能被弄丢"
    assert "platform" in document["mcp"]["servers"]
    assert not (worktree / "opencode.json").exists()


# ------------------------------------------------------------------- 反作弊


def _runner(paths: list[str], scope: list[str]) -> ScopedRunner:
    return ScopedRunner(
        {"exclusive_paths": scope},
        "HEAD~1",
        transient=lambda: tuple(paths),
    )


def test_a_transient_declaration_inside_the_scope_is_dropped() -> None:
    """声明不能用来把真实交付物藏起来 —— 那等于平台替它作弊。"""
    runner = _runner(["docs/a.md", "opencode.json"], ["docs"])
    assert runner._transient_names() == {"opencode.json"}


@pytest.mark.parametrize("bad", ["/etc/passwd", "../outside.json", ""])
def test_transient_declarations_must_be_inside_the_worktree(bad: str) -> None:
    runner = _runner([bad], ["docs"])
    assert runner._transient_names() == set()


def test_an_adapter_that_does_not_know_mcp_gets_no_tools() -> None:
    """没有确认过机制的 CLI 不许假装有工具。"""
    adapter = PlatformToolsAdapter(object(), harness_id="platform-member-1")
    assert adapter.mcp_binding() is None
    assert adapter.transient_paths() == ()


def test_the_platform_harness_reports_no_transient_files(tmp_path: Path) -> None:
    adapter: Any = PlatformToolsAdapter(object(), harness_id="platform-member-1")
    adapter.provider_route = None
    assert adapter.transient_paths() == ()
