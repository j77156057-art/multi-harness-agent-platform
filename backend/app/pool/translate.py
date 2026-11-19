"""Protocol translation for the platform's model relay.

The relay speaks one public protocol (OpenAI chat completions, plus an
Anthropic-compatible surface so the Claude CLI can be pointed at it) while the
upstreams it fronts speak different ones.  This module is the *translation
only*: no network, no keys, no state.

Two rules shape everything here:

* **never lose the text.** A model answer is the deliverable; a translation
  bug must not turn into an empty answer.
* **say what was lost.** Tool calls, images and thinking blocks that cannot
  cross the protocol boundary are not silently dropped: they are reported in
  ``notes`` so the caller (and the office) can see the difference.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

# --------------------------------------------------------------------------- #
# content helpers
# --------------------------------------------------------------------------- #


def message_text(message: Any) -> str:
    """The text a model actually said, whichever field it put it in.

    Some models answer **entirely in the reasoning field** and leave
    ``content`` empty -- the local ollama's thinking models do exactly that
    (their answer arrives as ``message.reasoning``, ``content`` is "").

    A caller that asked for text must get text: otherwise the agent loop sees
    an empty turn, calls it "no output" and gives up, while the model had
    answered perfectly well (2026-11-17 真机走查：五个成员 CLI 一起"没有回答"，
    而模型其实答了 —— 原因就是这一段）。

    The rule only fires when ``content`` is empty, so a model that answers in
    both fields (thinking *and* answer) is untouched.
    """
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    text = _text_of(content) if content is not None else ""
    if text.strip():
        return text
    for key in ("reasoning", "reasoning_content"):
        value = message.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _text_of(content: Any) -> str:
    """The plain text of a message content (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                parts.append(block["text"])
        return "\n".join(part for part in parts if part)
    return ""


def _notes_for_unsupported(payload: dict[str, Any]) -> list[str]:
    """What could not cross the protocol boundary."""
    notes: list[str] = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") not in (
                    "text",
                    "input_text",
                    "output_text",
                ):
                    notes.append(f"dropped content block: {block.get('type')}")
        if message.get("tool_calls"):
            notes.append("dropped tool_calls (not representable in the target protocol)")
    if payload.get("tools"):
        notes.append(f"dropped {len(payload['tools'])} tool definitions")
    return notes


# --------------------------------------------------------------------------- #
# OpenAI chat  ->  Anthropic messages
# --------------------------------------------------------------------------- #


def _openai_tools_to_anthropic(tools: Any) -> list[dict[str, Any]]:
    """OpenAI 的 tools 定义 -> Anthropic 的 tools 定义。

    这一层以前**直接丢掉**（notes 里写一句 "dropped N tool definitions"）。代价不是
    "少个功能"，而是把 agent 弄残：调用方（pi、claude 这类自己有工具的 CLI）拿到的是
    一个只会说话、永远不调工具的模型，于是它把活干成了"我在回答里写好了内容"，
    平台再判它"没有真正提交"（2026-10-29 实测：pi 10 次领活、0 次交付）。
    """
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        # OpenAI: {"type":"function","function":{"name","description","parameters"}}
        function = tool.get("function") if isinstance(tool.get("function"), dict) else None
        if function is None:
            # 已经是 Anthropic 形状（调用方就是 anthropic 的），原样留下。
            if tool.get("name") and ("input_schema" in tool or "parameters" in tool):
                converted.append(
                    {
                        "name": str(tool["name"]),
                        "description": str(tool.get("description") or ""),
                        "input_schema": tool.get("input_schema")
                        or tool.get("parameters")
                        or {"type": "object", "properties": {}},
                    }
                )
            continue
        converted.append(
            {
                "name": str(function.get("name") or ""),
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return [tool for tool in converted if tool["name"]]


def _openai_tool_choice_to_anthropic(choice: Any) -> dict[str, Any] | None:
    """`tool_choice` 也要跟着翻译，否则上游会用默认值（auto / none）。"""
    if choice is None:
        return None
    if isinstance(choice, str):
        return {"type": {"auto": "auto", "none": "none", "required": "any"}.get(choice, "auto")}
    if isinstance(choice, dict):
        function = choice.get("function") if isinstance(choice.get("function"), dict) else None
        name = str((function or {}).get("name") or "")
        if name:
            return {"type": "tool", "name": name}
        wanted = str(choice.get("type") or "auto")
        return {"type": {"auto": "auto", "none": "none", "required": "any"}.get(wanted, "auto")}
    return None


def openai_to_anthropic(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """OpenAI chat request -> Anthropic messages request."""
    messages: list[dict[str, Any]] = []
    system_parts: list[str] = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        text = _text_of(message.get("content"))
        if role == "system":
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            # Anthropic 没有 tool 角色：工具结果是**一轮 user 消息里的 tool_result 块**。
            # 光把结果当普通文字塞进去，模型就不知道自己刚才调过什么，会重复调用。
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id") or ""),
                            "content": text or "(tool result)",
                        }
                    ],
                }
            )
            continue
        if role == "assistant" and message.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if text:
                blocks.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                raw_arguments = function.get("arguments")
                try:
                    parsed = (
                        json.loads(raw_arguments)
                        if isinstance(raw_arguments, str) and raw_arguments.strip()
                        else (raw_arguments if isinstance(raw_arguments, dict) else {})
                    )
                except ValueError:
                    parsed = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": str(call.get("id") or "toolu_relay"),
                        "name": str(function.get("name") or ""),
                        "input": parsed if isinstance(parsed, dict) else {},
                    }
                )
            messages.append({"role": "assistant", "content": blocks})
            continue
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": text})
    if system_parts:
        messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    body: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages,
        "max_tokens": int(
            payload.get("max_tokens") or payload.get("max_completion_tokens") or 4096
        ),
    }
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        body["top_p"] = payload["top_p"]
    if payload.get("stop"):
        stop = payload["stop"]
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    tools = _openai_tools_to_anthropic(payload.get("tools"))
    if tools:
        body["tools"] = tools
        choice = _openai_tool_choice_to_anthropic(payload.get("tool_choice"))
        if choice is not None:
            body["tool_choice"] = choice
    if payload.get("stream"):
        body["stream"] = True
    notes = [note for note in _notes_for_unsupported(payload) if "tool definitions" not in note]
    return body, notes


def anthropic_response_to_openai(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    """Anthropic messages response -> OpenAI chat response."""
    text = _text_of(data.get("content"))
    stop_reason = str(data.get("stop_reason") or "stop")
    finish = {
        "end_turn": "stop",
        "max_tokens": "length",
        "stop_sequence": "stop",
        "tool_use": "tool_calls",
    }.get(stop_reason, "stop")
    # Anthropic 把工具调用放在 content 的 tool_use 块里；OpenAI 那边是 message.tool_calls。
    # 不转这一步，调用方永远看不到工具调用，只能看到一段"我打算这么做"的文字。
    tool_calls: list[dict[str, Any]] = []
    for block in data.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        tool_calls.append(
            {
                "id": str(block.get("id") or f"call_relay_{len(tool_calls)}"),
                "type": "function",
                "function": {
                    "name": str(block.get("name") or ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            }
        )
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id") or "chatcmpl-relay"),
        "object": "chat.completion",
        "created": 0,
        "model": str(data.get("model") or model),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else finish,
            }
        ],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("input_tokens") or 0)
            + int(usage.get("output_tokens") or 0),
        },
    }


# --------------------------------------------------------------------------- #
# Anthropic messages  ->  OpenAI chat
# --------------------------------------------------------------------------- #


def anthropic_to_openai(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Anthropic messages request -> OpenAI chat request."""
    messages: list[dict[str, Any]] = []
    system = payload.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system})
    elif isinstance(system, list):
        text = _text_of(system)
        if text:
            messages.append({"role": "system", "content": text})
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        content = message.get("content")
        # Anthropic carries the tool exchange **inside** the content blocks:
        # the assistant turn has tool_use blocks, the user turn has
        # tool_result blocks.  Both have to become OpenAI tool_calls / tool
        # messages -- dropped, the model loses the thread after the first
        # tool call and repeats itself (2026-11-18).
        if isinstance(content, list):
            calls = [
                block
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_use"
            ]
            results = [
                block
                for block in content
                if isinstance(block, dict) and block.get("type") == "tool_result"
            ]
            if role == "assistant" and calls:
                messages.append(
                    {
                        "role": "assistant",
                        "content": _text_of(content) or None,
                        "tool_calls": [
                            {
                                "id": str(block.get("id") or "call_relay_" + str(index)),
                                "type": "function",
                                "function": {
                                    "name": str(block.get("name") or ""),
                                    "arguments": json.dumps(
                                        block.get("input") or {}, ensure_ascii=False
                                    ),
                                },
                            }
                            for index, block in enumerate(calls)
                        ],
                    }
                )
                continue
            if results:
                for block in results:
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": str(block.get("tool_use_id") or ""),
                            "content": _text_of(block.get("content"))
                            or str(block.get("content") or "(tool result)"),
                        }
                    )
                continue
        messages.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": _text_of(content),
            }
        )
    body: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages,
        "max_tokens": int(payload.get("max_tokens") or 4096),
    }
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        body["top_p"] = payload["top_p"]
    if payload.get("stop_sequences"):
        body["stop"] = list(payload["stop_sequences"])
    tools = _anthropic_tools_to_openai(payload.get("tools"))
    if tools:
        body["tools"] = tools
        if payload.get("tool_choice"):
            choice = payload["tool_choice"]
            if isinstance(choice, dict):
                kind = str(choice.get("type") or "")
                if kind == "auto":
                    body["tool_choice"] = "auto"
                elif kind == "any":
                    body["tool_choice"] = "required"
                elif kind == "tool" and choice.get("name"):
                    body["tool_choice"] = {
                        "type": "function",
                        "function": {"name": str(choice["name"])},
                    }
    if payload.get("stream"):
        body["stream"] = True
    return body, _notes_for_unsupported(payload)


def _anthropic_tools_to_openai(tools: Any) -> list[dict[str, Any]]:
    """Anthropic tool definitions -> OpenAI tool definitions.

    The tool definitions have to cross the protocol boundary **on the way in**
    as well: the answer side was translated (OpenAI tool_calls -> Anthropic
    tool_use), but the request side was not, so the model never learned that
    it had tools.  It then answered "I don't have a Bash tool available" and
    the window could do nothing at all (2026-11-18: the Claude Code window
    produced text instead of files, every attempt).
    """
    result: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        schema = tool.get("input_schema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        result.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description") or ""),
                    "parameters": schema,
                },
            }
        )
    return result


def openai_response_to_anthropic(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    """OpenAI chat response -> Anthropic messages response."""
    choices = data.get("choices") or []
    text = ""
    tool_calls: list[dict[str, Any]] = []
    finish = "end_turn"
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        text = message_text(message)
        raw_calls = message.get("tool_calls") or []
        tool_calls = [call for call in raw_calls if isinstance(call, dict)]
        finish = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
        }.get(str(choices[0].get("finish_reason") or "stop"), "end_turn")
    usage = data.get("usage") or {}
    content: list[dict[str, Any]] = []
    if text:
        content.append({"type": "text", "text": text})
    # 工具调用要**过协议边界**：OpenAI 的 tool_calls 在 Anthropic 那边是 tool_use
    # 块。丢了它，Claude CLI 就只看见一段文字，于是"想写文件"变成"说想写文件"，
    # 交付永远出不来（2026-11-17 真机走查实测）。
    for call in tool_calls:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        try:
            payload = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
        except (TypeError, ValueError):
            payload = {}
        content.append(
            {
                "type": "tool_use",
                "id": str(call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "input": payload if isinstance(payload, dict) else {},
            }
        )
    if tool_calls:
        finish = "tool_use"
    return {
        "id": str(data.get("id") or "msg_relay"),
        "type": "message",
        "role": "assistant",
        "model": str(data.get("model") or model),
        "content": content,
        "stop_reason": finish,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def anthropic_response_to_stream(data: dict[str, Any]) -> Iterator[str]:
    """An Anthropic response -> the Anthropic **event stream** (SSE).

    The Claude CLI always streams: it asks for ``stream: true`` and parses
    ``message_start / content_block_delta / message_stop``.  A relay that
    answers such a request with a plain JSON body leaves the CLI with nothing
    it can parse ("API Error: 502" on its side, 2026-11-17 真机走查).

    The whole answer goes out as one text delta: the upstream answered in one
    go anyway, and the CLI only needs a well-formed event sequence.
    """
    content = data.get("content") or []
    text_parts: list[str] = []
    tool_blocks: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "")
        if kind == "text":
            text_parts.append(str(block.get("text") or ""))
        elif kind == "tool_use":
            tool_blocks.append(block)
    text = "".join(text_parts)
    message_id = str(data.get("id") or "msg_relay")
    usage = data.get("usage") or {}
    stop_reason = str(data.get("stop_reason") or "end_turn")

    def event(name: str, payload: dict[str, Any]) -> str:
        return f"event: {name}\n" "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

    yield event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": str(data.get("model") or ""),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": 0,
                },
            },
        },
    )
    yield event(
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    )
    if text:
        yield event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": text},
            },
        )
    yield event("content_block_stop", {"type": "content_block_stop", "index": 0})
    # A tool call has to be **its own content block** in the Anthropic event
    # stream: the CLI reads ``content_block_start`` with a ``tool_use`` block
    # (it carries the id, the name and the input), then a delta with the
    # partial JSON.  Dropping it -- while still announcing
    # ``stop_reason: tool_use`` -- leaves the CLI with a tool call it never
    # received, and it answers "the model's tool call could not be parsed"
    # (2026-11-18: every Claude Code attempt, thousands of them).
    index = 1
    for block in tool_blocks:
        tool_id = str(block.get("id") or f"toolu_relay_{index}")
        tool_input = block.get("input")
        if not isinstance(tool_input, dict):
            tool_input = {}
        yield event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": str(block.get("name") or ""),
                    "input": {},
                },
            },
        )
        yield event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input, ensure_ascii=False),
                },
            },
        )
        yield event("content_block_stop", {"type": "content_block_stop", "index": index})
        index += 1
    yield event(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": int(usage.get("output_tokens") or 0)},
        },
    )
    yield event("message_stop", {"type": "message_stop"})


# --------------------------------------------------------------------------- #
# OpenAI chat  <->  OpenAI Responses
# --------------------------------------------------------------------------- #


def chat_to_responses(payload: dict[str, Any]) -> dict[str, Any]:
    """OpenAI chat request -> OpenAI Responses request."""
    messages = payload.get("messages") or []
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user")
        text = _text_of(message.get("content"))
        if role == "system":
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            # Responses 里工具结果是**独立的一条 function_call_output**，
            # 不是一段 user 文字（否则模型不知道自己在接着上一步）。
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(message.get("tool_call_id") or ""),
                    "output": text or "(tool result)",
                }
            )
            continue
        if role == "assistant" and message.get("tool_calls"):
            if text:
                items.append(
                    {
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": text}],
                    }
                )
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") if isinstance(call.get("function"), dict) else {}
                raw = function.get("arguments")
                items.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or "call_relay"),
                        "name": str(function.get("name") or ""),
                        "arguments": raw if isinstance(raw, str) else json.dumps(raw or {}),
                    }
                )
            continue
        items.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": [
                    {"type": "input_text" if role != "assistant" else "output_text", "text": text}
                ],
            }
        )
    body: dict[str, Any] = {"model": payload.get("model"), "input": items}
    # 工具定义也要过去：以前这里整块没写，模型收到的就是「没有工具可用」。
    tools = payload.get("tools")
    if tools:
        converted = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function") if isinstance(tool.get("function"), dict) else None
            if function is not None:
                converted.append(
                    {
                        "type": "function",
                        "name": str(function.get("name") or ""),
                        "description": str(function.get("description") or ""),
                        "parameters": function.get("parameters")
                        or {"type": "object", "properties": {}},
                    }
                )
            elif tool.get("name"):
                converted.append(
                    {
                        "type": "function",
                        "name": str(tool["name"]),
                        "description": str(tool.get("description") or ""),
                        "parameters": tool.get("parameters")
                        or {"type": "object", "properties": {}},
                    }
                )
        converted = [tool for tool in converted if tool["name"]]
        if converted:
            body["tools"] = converted
            choice = payload.get("tool_choice")
            if isinstance(choice, str) and choice in {"auto", "none", "required"}:
                body["tool_choice"] = choice
            elif isinstance(choice, dict):
                function = (
                    choice.get("function") if isinstance(choice.get("function"), dict) else {}
                )
                if function.get("name"):
                    body["tool_choice"] = {
                        "type": "function",
                        "name": str(function["name"]),
                    }
    if instructions:
        body["instructions"] = "\n\n".join(instructions)
    if payload.get("max_tokens") or payload.get("max_completion_tokens"):
        body["max_output_tokens"] = int(
            payload.get("max_tokens") or payload.get("max_completion_tokens")
        )
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        body["top_p"] = payload["top_p"]
    if payload.get("stream"):
        body["stream"] = True
    return body


def responses_to_chat(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    """OpenAI Responses response -> OpenAI chat response."""
    text = ""
    tool_calls: list[dict[str, Any]] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "function_call":
            arguments = item.get("arguments")
            tool_calls.append(
                {
                    "id": str(
                        item.get("call_id") or item.get("id") or f"call_relay_{len(tool_calls)}"
                    ),
                    "type": "function",
                    "function": {
                        "name": str(item.get("name") or ""),
                        "arguments": arguments
                        if isinstance(arguments, str)
                        else json.dumps(arguments or {}, ensure_ascii=False),
                    },
                }
            )
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text += block["text"]
    usage = data.get("usage") or {}
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": str(data.get("id") or "chatcmpl-relay"),
        "object": "chat.completion",
        "created": 0,
        "model": str(data.get("model") or model),
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": (
                    "tool_calls"
                    if tool_calls
                    else ("stop" if data.get("status") == "completed" else "length")
                ),
            }
        ],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }


def responses_stream_to_openai_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """OpenAI Responses SSE -> OpenAI chat SSE."""
    import json as _json

    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            data = _json.loads(chunk)
        except ValueError:
            continue
        kind = str(data.get("type") or "")
        if kind.endswith("output_text.delta"):
            piece = data.get("delta")
            if not isinstance(piece, str) or not piece:
                continue
            yield (
                "data: "
                + _json.dumps(
                    {
                        "id": "chatcmpl-relay",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model,
                        "choices": [
                            {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        elif kind.endswith("completed"):
            yield (
                "data: "
                + _json.dumps(
                    {
                        "id": "chatcmpl-relay",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
    yield "data: [DONE]\n\n"


def chat_stream_to_responses_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """OpenAI chat SSE -> OpenAI Responses SSE."""
    import json as _json

    def event(kind: str, **fields: Any) -> str:
        payload = {"type": kind, "sequence_number": 0, **fields}
        return f"event: {kind}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"

    yield event(
        "response.created",
        response={"id": "resp_pool", "object": "response", "status": "in_progress", "model": model},
    )
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            data = _json.loads(chunk)
        except ValueError:
            continue
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            continue
        piece = _text_of((choices[0].get("delta") or {}).get("content"))
        if piece:
            yield event(
                "response.output_text.delta",
                delta=piece,
                item_id="msg_pool",
                output_index=0,
                content_index=0,
            )
    yield event(
        "response.completed",
        response={
            "id": "resp_pool",
            "object": "response",
            "status": "completed",
            "model": model,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": ""}],
                }
            ],
        },
    )


def responses_message_item(text: str = "", *, status: str = "") -> dict[str, Any]:
    """The assistant message item of a Responses stream."""
    item: dict[str, Any] = {
        "id": "msg_pool",
        "type": "message",
        "role": "assistant",
        "status": status or ("completed" if text else "in_progress"),
        "content": [{"type": "output_text", "text": text}] if text else [],
    }
    return item


def responses_stream_events(text: str, *, model: str) -> Iterator[str]:
    """One finished text -> the full OpenAI Responses event sequence.

    A Responses client does not read a single completion event: it tracks an
    **active output item**.  The Codex CLI logs "OutputTextDelta without active
    item" and renders an empty turn when the delta arrives without the item
    being opened first -- so the answer is silently lost.  The sequence is
    therefore: ``response.created`` -> ``response.output_item.added`` -> the
    text as ``response.output_text.delta`` -> ``...done`` -> ``response.completed``.
    """
    import json as _json

    def event(kind: str, **fields: Any) -> str:
        payload = {"type": kind, "sequence_number": 0, **fields}
        return f"event: {kind}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"

    yield event(
        "response.created",
        response={"id": "resp_pool", "object": "response", "status": "in_progress", "model": model},
    )
    yield event(
        "response.output_item.added",
        output_index=0,
        item=responses_message_item(),
    )
    if text:
        yield event(
            "response.output_text.delta",
            delta=text,
            item_id="msg_pool",
            output_index=0,
            content_index=0,
        )
        yield event(
            "response.output_text.done",
            text=text,
            item_id="msg_pool",
            output_index=0,
            content_index=0,
        )
    yield event(
        "response.output_item.done",
        output_index=0,
        item=responses_message_item(text),
    )
    yield event(
        "response.completed",
        response={
            "id": "resp_pool",
            "object": "response",
            "status": "completed",
            "model": model,
            "output": [responses_message_item(text)],
        },
    )


def text_to_responses_stream(text: str, *, model: str) -> Iterator[str]:
    """One finished text -> the OpenAI Responses SSE shape a client expects.

    A CLI slot answers with the whole text at once (the CLI prints one final
    message), but the Responses protocol is a **sequence**: a client that only
    sees ``response.completed`` may render an empty turn -- which is what the
    Codex CLI did, and the answer was simply lost.  So the same parts the chat
    upstream path emits are produced here.
    """
    yield from responses_stream_events(text, model=model)


def anthropic_stream_to_responses_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """Anthropic messages SSE -> OpenAI Responses SSE."""
    import json as _json

    def event(kind: str, **fields: Any) -> str:
        payload = {"type": kind, "sequence_number": 0, **fields}
        return f"event: {kind}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"

    yield event(
        "response.created",
        response={"id": "resp_pool", "object": "response", "status": "in_progress", "model": model},
    )
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if not chunk:
            continue
        try:
            data = _json.loads(chunk)
        except ValueError:
            continue
        if data.get("type") != "content_block_delta":
            continue
        delta = data.get("delta") or {}
        piece = delta.get("text") if isinstance(delta.get("text"), str) else ""
        if piece:
            yield event(
                "response.output_text.delta",
                delta=piece,
                item_id="msg_pool",
                output_index=0,
                content_index=0,
            )
    yield event(
        "response.completed",
        response={
            "id": "resp_pool",
            "object": "response",
            "status": "completed",
            "model": model,
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": ""}],
                }
            ],
        },
    )


# --------------------------------------------------------------------------- #
# streaming
# --------------------------------------------------------------------------- #


def _sse(event: str, data: dict[str, Any]) -> str:
    import json as _json

    return f"event: {event}\ndata: {_json.dumps(data, ensure_ascii=False)}\n\n"


def anthropic_stream_to_openai_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """Anthropic messages SSE -> OpenAI chat SSE."""
    import json as _json

    created = 0
    for raw in lines:
        raw = raw.strip()
        if not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if chunk == "[DONE]":
            break
        try:
            data = _json.loads(chunk)
        except ValueError:
            continue
        kind = data.get("type")
        if kind == "message_start":
            created = 0
            continue
        if kind == "content_block_delta":
            delta = data.get("delta") or {}
            piece = delta.get("text") if isinstance(delta.get("text"), str) else ""
            if piece:
                yield (
                    "data: "
                    + _json.dumps(
                        {
                            "id": "chatcmpl-relay",
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {"index": 0, "delta": {"content": piece}, "finish_reason": None}
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
        elif kind == "message_delta":
            stop = (data.get("delta") or {}).get("stop_reason")
            finish = {
                "end_turn": "stop",
                "max_tokens": "length",
                "tool_use": "tool_calls",
            }.get(str(stop or "end_turn"), "stop")
            yield (
                "data: "
                + _json.dumps(
                    {
                        "id": "chatcmpl-relay",
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
    yield "data: [DONE]\n\n"
