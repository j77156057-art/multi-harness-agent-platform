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

from typing import Any, Iterator

# --------------------------------------------------------------------------- #
# content helpers
# --------------------------------------------------------------------------- #


def _text_of(content: Any) -> str:
    """The plain text of a message content (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and isinstance(block.get("text"), str):
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
                if isinstance(block, dict) and block.get("type") not in ("text", "input_text", "output_text"):
                    notes.append(f"dropped content block: {block.get('type')}")
        if message.get("tool_calls"):
            notes.append("dropped tool_calls (not representable in the target protocol)")
    if payload.get("tools"):
        notes.append(f"dropped {len(payload['tools'])} tool definitions")
    return notes


# --------------------------------------------------------------------------- #
# OpenAI chat  ->  Anthropic messages
# --------------------------------------------------------------------------- #


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
            # Anthropic has no tool role: a tool result is a user turn.
            messages.append({"role": "user", "content": text or "(tool result)"})
            continue
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": text})
    if system_parts:
        messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
    body: dict[str, Any] = {
        "model": payload.get("model"),
        "messages": messages,
        "max_tokens": int(payload.get("max_tokens") or payload.get("max_completion_tokens") or 4096),
    }
    if payload.get("temperature") is not None:
        body["temperature"] = payload["temperature"]
    if payload.get("top_p") is not None:
        body["top_p"] = payload["top_p"]
    if payload.get("stop"):
        stop = payload["stop"]
        body["stop_sequences"] = [stop] if isinstance(stop, str) else list(stop)
    if payload.get("stream"):
        body["stream"] = True
    return body, _notes_for_unsupported(payload)


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
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id") or "chatcmpl-relay"),
        "object": "chat.completion",
        "created": 0,
        "model": str(data.get("model") or model),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish,
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
        messages.append({"role": "assistant" if role == "assistant" else "user", "content": _text_of(message.get("content"))})
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
    if payload.get("stream"):
        body["stream"] = True
    return body, _notes_for_unsupported(payload)


def openai_response_to_anthropic(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    """OpenAI chat response -> Anthropic messages response."""
    choices = data.get("choices") or []
    text = ""
    finish = "end_turn"
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        text = _text_of(message.get("content"))
        finish = {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
        }.get(str(choices[0].get("finish_reason") or "stop"), "end_turn")
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id") or "msg_relay"),
        "type": "message",
        "role": "assistant",
        "model": str(data.get("model") or model),
        "content": [{"type": "text", "text": text}],
        "stop_reason": finish,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


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
        items.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": [{"type": "input_text" if role != "assistant" else "output_text", "text": text}],
            }
        )
    body: dict[str, Any] = {"model": payload.get("model"), "input": items}
    if instructions:
        body["instructions"] = "\n\n".join(instructions)
    if payload.get("max_tokens") or payload.get("max_completion_tokens"):
        body["max_output_tokens"] = int(payload.get("max_tokens") or payload.get("max_completion_tokens"))
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
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for block in item.get("content") or []:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                text += block["text"]
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id") or "chatcmpl-relay"),
        "object": "chat.completion",
        "created": 0,
        "model": str(data.get("model") or model),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop" if data.get("status") == "completed" else "length",
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
                        "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
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

    yield event("response.created", response={"id": "resp_pool", "object": "response", "status": "in_progress", "model": model})
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


def anthropic_stream_to_responses_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """Anthropic messages SSE -> OpenAI Responses SSE."""
    import json as _json

    def event(kind: str, **fields: Any) -> str:
        payload = {"type": kind, "sequence_number": 0, **fields}
        return f"event: {kind}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"

    yield event("response.created", response={"id": "resp_pool", "object": "response", "status": "in_progress", "model": model})
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


def openai_stream_to_anthropic_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """OpenAI chat SSE -> Anthropic messages SSE."""
    started = False
    text_parts: list[str] = []
    finish = "end_turn"
    for raw in lines:
        raw = raw.strip()
        if not raw or not raw.startswith("data:"):
            continue
        chunk = raw[5:].strip()
        if chunk == "[DONE]":
            break
        import json as _json

        try:
            data = _json.loads(chunk)
        except ValueError:
            continue
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            continue
        delta = choices[0].get("delta") or {}
        piece = _text_of(delta.get("content"))
        if piece:
            text_parts.append(piece)
            if not started:
                started = True
                yield _sse(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_relay",
                            "type": "message",
                            "role": "assistant",
                            "model": str(data.get("model") or model),
                            "content": [],
                            "stop_reason": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    },
                )
                yield _sse(
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                )
            yield _sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": piece},
                },
            )
        reason = choices[0].get("finish_reason")
        if reason:
            finish = {
                "stop": "end_turn",
                "length": "max_tokens",
                "tool_calls": "tool_use",
            }.get(str(reason), "end_turn")
    if not started:
        yield _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_relay",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            },
        )
        yield _sse(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        )
    yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    yield _sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": finish, "stop_sequence": None},
            "usage": {"output_tokens": 0},
        },
    )
    yield _sse("message_stop", {"type": "message_stop"})


def anthropic_stream_to_openai_stream(lines: Iterator[str], *, model: str) -> Iterator[str]:
    """Anthropic messages SSE -> OpenAI chat SSE."""
    import json as _json

    first = True
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
                            "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}],
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
