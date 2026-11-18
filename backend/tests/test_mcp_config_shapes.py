"""MCP 配置文件里那个"传输方式"的词 —— 每家 CLI 认的不一样。

为什么单独立一组用例：这个词写错**不会**报错，CLI 只会把服务器**跳过**。
2026-11-19 真机实测：平台给 claude 写的是 ``"type": "remote"``，而这台机器上的
claude CLI（2.1.274）回的是

    mcp_server_errors: [{"name": "platform", "type": "unknown_type",
                          "message": "Skipped — unknown MCP server type \"remote\""}]

—— 平台工具根本没接上，窗口里的模型就说"我没有这些工具"。同一次实测里
``http`` / ``sse`` / ``streamable-http`` 都被接受。

所以这里钉住三件事：
1. claude 的那份用 ``http``（不是 ``remote``）；
2. opencode 仍用 ``remote`` —— 那是**它自己**的词，别按 claude 的改；
3. 令牌走 headers，不进 URL（URL 会进命令行/日志）。
"""

from __future__ import annotations

from app.agenttools.mcp_config import SERVER_NAME, Binding


def _binding() -> Binding:
    return Binding(
        base_url="http://127.0.0.1:8848",
        token="tok-123",
        task_id="FD-1",
        attempt_id="FD-1.a1",
    )


def test_claude_gets_a_transport_word_the_cli_accepts():
    """claude CLI 不认 "remote"：认了就把服务器跳过，窗口等于没有平台工具。"""
    entry = _binding().claude_document()["mcpServers"][SERVER_NAME]
    assert entry["type"] == "http"
    assert entry["type"] != "remote"


def test_codebuddy_and_qoder_keep_http():
    """这两家只认 http/sse/ws/stdio 那一组。"""
    entry = _binding().http_document()["mcpServers"][SERVER_NAME]
    assert entry["type"] == "http"


def test_opencode_keeps_its_own_word():
    """opencode 的配置形状与词都是它自己的：别按 claude 的改。"""
    entry = _binding().opencode_document()["mcp"]["servers"][SERVER_NAME]
    assert entry["type"] == "remote"


def test_the_token_travels_in_headers_not_in_the_url():
    """URL 会进命令行与日志；令牌不能在那儿。"""
    binding = _binding()
    for document in (
        binding.claude_document(),
        binding.http_document(),
        binding.opencode_document(),
    ):
        text = str(document)
        assert "tok-123" not in binding.url()
        assert "Bearer tok-123" in text


def test_the_url_carries_the_attempt_identity():
    """端点要带得上这次尝试，否则平台不知道是哪个窗口在问。"""
    binding = _binding()
    url = binding.url()
    assert "task_id=FD-1" in url
    assert "attempt_id=FD-1.a1" in url
