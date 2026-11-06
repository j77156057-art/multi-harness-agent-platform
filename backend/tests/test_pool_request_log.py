"""请求体日志的脱敏与体积契约。

针对 2026-11-05 pi 502：需要知道「这次请求带了哪些工具」，但不能把提示词、
工具参数或密钥写进日志。下面的用例锁住三件事：
正文一律不出现、工具名按上限截断且可数、整行不超过 2048 字节。
"""

from __future__ import annotations

import json

from app.pool.request_log import MAX_LOG_BYTES, MAX_TOOL_NAME_CHARS, log_pool_request


def _last_record(capsys) -> dict:
    line = capsys.readouterr().out.strip().splitlines()[-1]
    return json.loads(line)


def test_prompt_and_arguments_never_reach_the_log(capsys):
    payload = {
        "model": "claude-sonnet-4",
        "stream": True,
        "messages": [
            {"role": "system", "content": "TOP-SECRET-SYSTEM-PROMPT"},
            {"role": "user", "content": [{"type": "text", "text": "SECRET-USER-TEXT"}]},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "SECRET-DESCRIPTION",
                    "parameters": {"type": "object", "properties": {"path": {"x": "SECRET"}}},
                },
            }
        ],
        "api_key": "sk-SECRET-KEY",
    }
    log_pool_request("/pool/v1/chat/completions", payload)
    record = _last_record(capsys)
    raw = json.dumps(record, ensure_ascii=False)

    assert "SECRET" not in raw
    assert record["pool_request"] == "/pool/v1/chat/completions"
    assert record["messages"] == {"count": 2, "roles": {"system": 1, "user": 1}, "chars": 40}
    assert record["tools"]["names"] == ["read_file"]
    assert record["tools"]["count"] == 1
    assert record["stream"] is True
    assert record["body_bytes"] > 0


def test_tool_names_are_capped_and_counted(capsys):
    tools = [
        {"type": "function", "function": {"name": f"tool_{index:02d}"}} for index in range(25)
    ]
    log_pool_request("/pool/v1/messages", {"model": "m", "tools": tools})
    record = _last_record(capsys)
    assert len(record["tools"]["names"]) == 20
    assert record["tools"]["names"] == sorted(record["tools"]["names"])
    assert record["tools"]["dropped"] == 5
    assert record["truncated"] is True


def test_long_name_is_clipped_and_flagged(capsys):
    name = "x" * 100
    log_pool_request("/pool/v1/responses", {"tools": [{"type": "function", "name": name}]})
    record = _last_record(capsys)
    assert record["tools"]["names"] == ["x" * MAX_TOOL_NAME_CHARS]
    assert record["truncated"] is True


def test_huge_prompt_stays_under_the_byte_cap(capsys):
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "y" * 200_000}],
        "tools": [{"type": "function", "function": {"name": f"tool_{i:03d}"}} for i in range(80)],
    }
    log_pool_request("/pool/v1/chat/completions", payload)
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert len(line.encode("utf-8")) <= MAX_LOG_BYTES
    record = json.loads(line)
    assert record["truncated"] is True
    assert record["tools"]["dropped"] == 80 - len(record["tools"]["names"])
    assert record["messages"]["chars"] == 200_000
