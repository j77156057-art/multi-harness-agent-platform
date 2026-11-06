"""Relay 请求体的结构化留痕：只记形状与工具名，不记提示词正文。

为什么要有这一层：2026-11-05 的 pi 502（``relay_cli_cannot_use_tools``）
事后只能看到「POST /pool/v1/chat/completions → 502」这一行路由，请求里的
工具名、模型名、消息条数全丢了，既查不出是谁把带工具的请求带进了 CLI 档，
也没法复现。记 body 是对的，但**不能把整段提示词落盘**：提示词里可能有
源码、密钥、用户原话。

脱敏与体积（三层，任何一层生效都会在日志里写清）：

1. **只记结构，不记内容**：model / stream / 消息条数与角色计数 / 工具名清单
   / 字符数。消息正文、``tool.arguments``、``tool_calls`` 参数一律不出现在
   日志里；请求里的其它未知字段（例如 ``api_key``）根本不读。
2. **工具名逐个截断到 64 字符，最多记 20 个**；其余只记数量
   （``dropped_tools``）。
3. **整行 JSON 上限 2048 字节**：超了就继续从工具名清单尾部丢弃，直到装得下，
   并置 ``truncated = true``。截断后的日志仍然可解析，记录的是「有这么多工具、
   名字是这些」，而不是半截字符串。
"""

from __future__ import annotations

import json
from typing import Any

#: 一行日志的硬上限（字节，UTF-8）。
MAX_LOG_BYTES = 2048
#: 最多记录多少个工具名。
MAX_TOOL_NAMES = 20
#: 单个工具名的截断长度。
MAX_TOOL_NAME_CHARS = 64
#: model 名与 tool_choice 的截断长度。
MAX_FIELD_CHARS = 80


def _clip(value: Any, limit: int) -> str:
    return str(value or "")[:limit]


def _messages(payload: dict[str, Any]) -> tuple[int, dict[str, int], int]:
    """条数、角色计数与总字符数；不保留任何正文。"""
    raw = payload.get("messages")
    if raw is None:
        raw = payload.get("input")
    if not isinstance(raw, list):
        return 0, {}, len(raw) if isinstance(raw, str) else 0
    roles: dict[str, int] = {}
    chars = 0
    for item in raw:
        if isinstance(item, dict):
            role = _clip(item.get("role") or "unknown", 24)
            roles[role] = roles.get(role, 0) + 1
            content = item.get("content")
            if isinstance(content, str):
                chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chars += len(part["text"])
        elif isinstance(item, str):
            chars += len(item)
    return len(raw), roles, chars


def _tool_names(payload: dict[str, Any]) -> tuple[list[str], int, bool]:
    """工具名清单（去重、排序、截断），被丢掉的个数，是否有名字被截断。"""
    raw = payload.get("tools")
    if not isinstance(raw, list):
        return [], 0, False
    names: list[str] = []
    clipped = False
    for item in raw:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        source = function if isinstance(function, dict) else item
        name = source.get("name")
        if not isinstance(name, str) or not name:
            continue
        if len(name) > MAX_TOOL_NAME_CHARS:
            clipped = True
        names.append(name[:MAX_TOOL_NAME_CHARS])
    unique = sorted(set(names))
    kept = unique[:MAX_TOOL_NAMES]
    return kept, len(unique) - len(kept), clipped


def payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """把一次 relay 请求压成可落盘的形状；不含任何提示词/参数正文。"""
    count, roles, chars = _messages(payload)
    names, dropped, clipped = _tool_names(payload)
    tools_raw = payload.get("tools")
    summary: dict[str, Any] = {
        "model": _clip(payload.get("model"), MAX_FIELD_CHARS),
        "stream": bool(payload.get("stream")),
        "messages": {"count": count, "roles": roles, "chars": chars},
        "tools": {
            "count": len(tools_raw) if isinstance(tools_raw, list) else 0,
            "names": names,
            "dropped": dropped,
        },
        "tool_choice": _clip(payload.get("tool_choice"), MAX_FIELD_CHARS),
        "max_tokens": payload.get("max_tokens") or payload.get("max_output_tokens"),
        "body_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
        "truncated": dropped > 0 or clipped,
    }
    # 体积上限：从工具名尾部继续丢，直到整行装得下。角色计数与条数始终保留，
    # 因为它们才是「为什么这次会被拒」的直接证据。
    while len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) > MAX_LOG_BYTES:
        if not summary["tools"]["names"]:
            break
        summary["tools"]["names"].pop()
        summary["tools"]["dropped"] += 1
        summary["truncated"] = True
    return summary


def log_pool_request(endpoint: str, payload: dict[str, Any]) -> None:
    """按上面的策略打一行 ``pool_request`` 日志（stdout，供采集器收走）。"""
    record = {"pool_request": endpoint, **payload_summary(payload)}
    print(json.dumps(record, ensure_ascii=False), flush=True)
