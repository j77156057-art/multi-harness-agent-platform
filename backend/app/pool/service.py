"""The platform's model relay: one OpenAI-compatible door in front of the
harness platform's model quota.

The problem it solves: every harness CLI carries its own provider credentials
and its own idea of how to talk to a model.  A window whose provider is dead
cannot work, and a window whose provider is expensive burns money.  The relay
turns "who has quota" into "what can I call":

* one public protocol (OpenAI chat completions, plus an Anthropic-compatible
  surface so the Claude CLI can be pointed at it) in front of *several*
  upstream protocols;
* the upstream key is injected **server side** -- a harness that uses the
  relay holds only the relay's own token, never a provider key;
* every request is accounted per upstream, so the office can say "this work
  was paid for by that quota".

What the relay deliberately does **not** do: it does not run tasks, does not
claim work, and does not speak for any harness.  The execution slot stays with
the CLI; only the model behind it is shared.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.pool.balance import cached_balances
from app.pool.inventory import build_inventory, cached_minimax_quota
from app.pool.sources import (
    ANTHROPIC,
    CLI,
    OPENAI_CHAT,
    OPENAI_RESPONSES,
    POOL_CONFIG_PATH,
    Upstream,
    discover_upstreams,
)
from app.pool.translate import (
    anthropic_response_to_openai,
    anthropic_stream_to_openai_stream,
    anthropic_stream_to_responses_stream,
    anthropic_to_openai,
    chat_stream_to_responses_stream,
    chat_to_responses,
    openai_response_to_anthropic,
    openai_to_anthropic,
    responses_stream_to_openai_stream,
    responses_to_chat,
)

#: How long a discovered upstream list / model list is reused.
DISCOVERY_TTL_SECONDS = 30.0


class PoolError(RuntimeError):
    """The relay cannot serve this request as asked."""

    def __init__(self, message: str, *, status: int = 502, code: str = "relay_error") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class Route:
    """Where a request goes."""

    upstream: Upstream
    model: str
    #: The model name the upstream knows it by (may differ from the alias).
    upstream_model: str


@dataclass(slots=True)
class Usage:
    """One accounted request."""

    at: str
    model: str
    upstream: str
    protocol: str
    status: int
    duration_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: False when the CLI did not report its usage at all -- then the zeros
    #: are the absence of a number, not a measurement.
    usage_reported: bool = True
    #: The CLI's own session id, when it reports one: it makes the record
    #: traceable back to the CLI's own store.
    session_id: str | None = None
    #: Which harness spent the quota (the pool's caller).
    consumer: str = ""
    note: str = ""


#: The discovery caches live in the module, not on the service.
#:
#: The pool service is built per request, so an instance-level TTL would never
#: be hit: every read of the pool would re-read the CLIs' configuration and
#: re-probe the upstreams for their model lists (a network round trip each).
#: The key says what the discovery depends on, so two different homes (or two
#: different relay port sets) never share an answer.
_DISCOVERY: dict[tuple[Any, ...], tuple[float, list[Upstream]]] = {}
_MODELS: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = {}


def _usage_blobs(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Every usage object in one SSE event, wherever the protocol puts it.

    The Responses API puts it under ``response.usage``, the chat API at the
    top level, and the Anthropic API under ``message.usage`` (the input
    arrives in ``message_start``, the output in ``message_delta``).
    """
    found: list[dict[str, Any]] = []
    usage = event.get("usage")
    if isinstance(usage, dict):
        found.append(usage)
    for key in ("response", "message"):
        holder = event.get(key)
        if isinstance(holder, dict) and isinstance(holder.get("usage"), dict):
            found.append(holder["usage"])
    return found


class _UsageSniffer:
    """Watch an SSE stream for the usage the upstream reports.

    The streamed paths used to account **zero** tokens: the upstream puts
    the usage in the last event, and the pool was yielding those lines
    without looking.  That made the metered tier's spend invisible -- the
    one number the pool is supposed to own.
    """

    def __init__(self) -> None:
        self.prompt = 0
        self.completion = 0

    def feed(self, line: str) -> None:
        text = line.strip()
        if not text.startswith("data:"):
            return
        payload = text[5:].strip()
        if not payload or payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        for usage in _usage_blobs(event):
            self.prompt = max(
                self.prompt, int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
            )
            self.completion = max(
                self.completion,
                int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
            )

    def watched(self, lines: Iterator[str]) -> Iterator[str]:
        for line in lines:
            self.feed(line)
            yield line


class PoolService:
    """Routing, translation and accounting for the platform's model pool."""

    def __init__(
        self,
        *,
        home: Path | None = None,
        usage_path: Path | None = None,
        relay_ports: tuple[int, ...] = (8848,),
        timeout: float = 300.0,
    ) -> None:
        self.home = home
        self.usage_path = (
            usage_path
            if usage_path is not None
            else Path(settings.data_dir) / "pool" / "usage.jsonl"
        )
        self.relay_ports = relay_ports
        self.timeout = timeout
        self._upstreams: list[Upstream] = []
        self._upstreams_at = 0.0
        self._models: list[dict[str, Any]] = []
        self._models_at = 0.0

    # ------------------------------------------------------------ discovery

    def upstreams(self, *, refresh: bool = False) -> list[Upstream]:
        now = time.monotonic()
        key = self._discovery_key()
        cached = _DISCOVERY.get(key)
        if refresh or cached is None or now - cached[0] > DISCOVERY_TTL_SECONDS:
            found = discover_upstreams(self.home, relay_ports=self.relay_ports)
            _DISCOVERY[key] = (now, found)
            self._upstreams = found
            self._upstreams_at = now
        else:
            self._upstreams = cached[1]
            self._upstreams_at = cached[0]
        return self._upstreams

    def _discovery_key(self) -> tuple[Any, ...]:
        """What the discovery depends on -- the cache is shared, so the key must say it."""
        return (str(self.home), self.relay_ports, str(POOL_CONFIG_PATH.resolve()))

    def models(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """The unified model list: every model the relay can serve, and from where.

        The list is what a client sees in ``GET /v1/models``; each entry keeps
        the upstream it belongs to so the office can show whose quota a model
        would spend.
        """
        now = time.monotonic()
        key = self._discovery_key()
        cached = _MODELS.get(key)
        if not refresh and cached is not None and now - cached[0] <= DISCOVERY_TTL_SECONDS:
            self._models = cached[1]
            self._models_at = cached[0]
            return self._models
        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for upstream in self.upstreams(refresh=refresh):
            names = list(upstream.models)
            if not names and upstream.protocol in (OPENAI_CHAT, OPENAI_RESPONSES):
                names = self._probe_models(upstream)
            for name in names:
                if name in seen:
                    continue
                seen.add(name)
                entries.append(
                    {
                        "id": name,
                        "object": "model",
                        "owned_by": upstream.name,
                        "upstream": upstream.name,
                        "protocol": upstream.protocol,
                        "tier": upstream.tier,
                        "source": upstream.origin,
                        "note": upstream.note,
                    }
                )
        self._models = entries
        self._models_at = time.monotonic()
        _MODELS[key] = (self._models_at, entries)
        return entries

    def _probe_models(self, upstream: Upstream) -> list[str]:
        """Ask an OpenAI-compatible upstream what it has."""
        headers = {"Accept": "application/json"}
        credential = upstream.credential_value()
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        candidates = [upstream.base_url + "/models"]
        if upstream.protocol == OPENAI_RESPONSES:
            # The codex chain serves its catalogue under /v1.
            candidates.insert(0, upstream.base_url + "/v1/models")
        for url in candidates:
            try:
                with httpx.Client(timeout=8.0) as client:
                    response = client.get(url, headers=headers)
            except httpx.HTTPError:
                continue
            if response.status_code >= 400:
                continue
            try:
                document = response.json()
            except ValueError:
                continue
            data = document.get("data") if isinstance(document, dict) else None
            if not isinstance(data, list):
                continue
            names: list[str] = []
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    names.append(item["id"])
            if names:
                return names
        return []

    # -------------------------------------------------------------- routing

    def route(self, model: str) -> Route:
        """Find the upstream that serves ``model``.

        The name may be plain (``gpt-6.1-sol``) or prefixed with the relay's
        own namespace (``relay/gpt-6.1-sol``) -- clients that need to be
        explicit can be, clients that just want a model can be lazy.
        """
        wanted = str(model or "").strip()
        if wanted.startswith("relay/"):
            wanted = wanted[len("relay/") :]
        if not wanted:
            raise PoolError("model is required", status=400, code="relay_model_required")
        entries = self.models()
        for entry in entries:
            if entry["id"] == wanted:
                upstream = self._upstream_by_name(str(entry["upstream"]))
                if upstream is None:
                    break
                return Route(upstream=upstream, model=wanted, upstream_model=wanted)
        lowered = wanted.lower()
        for entry in entries:
            if str(entry["id"]).lower() == lowered:
                upstream = self._upstream_by_name(str(entry["upstream"]))
                if upstream is not None:
                    return Route(upstream=upstream, model=wanted, upstream_model=str(entry["id"]))
        known = ", ".join(str(entry["id"]) for entry in entries[:12]) or "(none discovered)"
        raise PoolError(
            f"no upstream serves model {wanted!r}; available: {known}",
            status=404,
            code="relay_model_not_found",
        )

    def _upstream_by_name(self, name: str) -> Upstream | None:
        for upstream in self.upstreams():
            if upstream.name == name:
                return upstream
        return None

    # ------------------------------------------------------------- requests

    def chat_completions(self, payload: dict[str, Any], *, consumer: str = "") -> dict[str, Any]:
        """Serve an OpenAI chat completion from whichever upstream has the model."""
        route = self.route(str(payload.get("model") or ""))
        upstream = route.upstream
        if upstream.protocol == CLI:
            return self._cli_chat(route, payload, consumer=consumer)
        credential = upstream.credential_value()
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        body = dict(payload)
        body["model"] = route.upstream_model
        notes: list[str] = []
        started = time.monotonic()
        if upstream.protocol == ANTHROPIC:
            body, notes = openai_to_anthropic(body)
            url = upstream.base_url + "/v1/messages"
        elif upstream.protocol == OPENAI_RESPONSES:
            # The chain speaks the Responses API only; the caller speaks chat.
            body = chat_to_responses(body)
            url = upstream.base_url + "/responses"
        else:
            url = upstream.base_url + "/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise PoolError(f"upstream {upstream.name} is unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise PoolError(
                f"upstream {upstream.name} refused the request ({response.status_code}): "
                f"{response.text[:300]}",
                status=response.status_code,
                code="relay_upstream_rejected",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PoolError(f"upstream {upstream.name} answered with a non-JSON body") from exc
        if upstream.protocol == ANTHROPIC:
            data = anthropic_response_to_openai(data, model=route.model)
        elif upstream.protocol == OPENAI_RESPONSES:
            data = responses_to_chat(data, model=route.model)
        self._account(
            Usage(
                at=_now(),
                model=route.model,
                upstream=upstream.name,
                protocol=upstream.protocol,
                status=response.status_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=int((data.get("usage") or {}).get("prompt_tokens") or 0),
                completion_tokens=int((data.get("usage") or {}).get("completion_tokens") or 0),
                consumer=consumer,
                note="; ".join(notes),
            )
        )
        return data

    def messages(self, payload: dict[str, Any], *, consumer: str = "") -> dict[str, Any]:
        """Serve an Anthropic messages request (the Claude CLI's protocol)."""
        route = self.route(str(payload.get("model") or ""))
        upstream = route.upstream
        if upstream.protocol == CLI:
            return self._cli_chat(route, payload, consumer=consumer, protocol="anthropic")
        credential = upstream.credential_value()
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        body = dict(payload)
        body["model"] = route.upstream_model
        notes: list[str] = []
        started = time.monotonic()
        if upstream.protocol == ANTHROPIC:
            url = upstream.base_url + "/v1/messages"
        else:
            body, notes = anthropic_to_openai(body)
            if upstream.protocol == OPENAI_RESPONSES:
                body = chat_to_responses(body)
                url = upstream.base_url + "/responses"
            else:
                url = upstream.base_url + "/chat/completions"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise PoolError(f"upstream {upstream.name} is unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise PoolError(
                f"upstream {upstream.name} refused the request ({response.status_code}): "
                f"{response.text[:300]}",
                status=response.status_code,
                code="relay_upstream_rejected",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PoolError(f"upstream {upstream.name} answered with a non-JSON body") from exc
        if upstream.protocol != ANTHROPIC:
            if upstream.protocol == OPENAI_RESPONSES:
                data = responses_to_chat(data, model=route.model)
            data = openai_response_to_anthropic(data, model=route.model)
        self._account(
            Usage(
                at=_now(),
                model=route.model,
                upstream=upstream.name,
                protocol=upstream.protocol,
                status=response.status_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=int((data.get("usage") or {}).get("input_tokens") or 0),
                completion_tokens=int((data.get("usage") or {}).get("output_tokens") or 0),
                consumer=consumer,
                note="; ".join(notes),
            )
        )
        return data

    def responses(self, payload: dict[str, Any], *, consumer: str = "") -> dict[str, Any]:
        """Serve an OpenAI *Responses* request (the codex CLI's protocol).

        For an upstream that speaks the Responses API this is a pass-through;
        for a chat-only upstream the request is translated down and the answer
        translated back, so one model list can serve both CLI families.
        """
        route = self.route(str(payload.get("model") or ""))
        upstream = route.upstream
        credential = upstream.credential_value()
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        body = dict(payload)
        body["model"] = route.upstream_model
        started = time.monotonic()
        if upstream.protocol == OPENAI_RESPONSES:
            url = upstream.base_url + "/responses"
        else:
            chat_payload = _responses_to_chat(payload)
            if upstream.protocol == ANTHROPIC:
                chat_payload, _ = openai_to_anthropic(chat_payload)
                url = upstream.base_url + "/v1/messages"
            else:
                url = upstream.base_url + "/chat/completions"
            body = chat_payload
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise PoolError(f"upstream {upstream.name} is unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise PoolError(
                f"upstream {upstream.name} refused the request ({response.status_code}): "
                f"{response.text[:300]}",
                status=response.status_code,
                code="relay_upstream_rejected",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise PoolError(f"upstream {upstream.name} answered with a non-JSON body") from exc
        if upstream.protocol != OPENAI_RESPONSES:
            data = _chat_to_responses(data, model=route.model)
        self._account(
            Usage(
                at=_now(),
                model=route.model,
                upstream=upstream.name,
                protocol=upstream.protocol,
                status=response.status_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=int((data.get("usage") or {}).get("input_tokens") or 0),
                completion_tokens=int((data.get("usage") or {}).get("output_tokens") or 0),
                consumer=consumer,
            )
        )
        return data

    # ------------------------------------------------------------ streaming

    def _stream_setup(self, route: Route, payload: dict[str, Any], *, target: str):
        """The url, body and headers for a streaming call.

        ``target`` is the protocol the *caller* speaks ("chat" or
        "responses"); the upstream may speak another one, and the translation
        layer sits between.
        """
        upstream = route.upstream
        credential = upstream.credential_value()
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        body = dict(payload)
        body["model"] = route.upstream_model
        body["stream"] = True
        if upstream.protocol == ANTHROPIC:
            if target == "chat":
                body, _ = openai_to_anthropic(body)
            else:
                body, _ = openai_to_anthropic(chat_to_responses(body))
            url = upstream.base_url + "/v1/messages"
        elif upstream.protocol == OPENAI_RESPONSES:
            if target == "chat":
                body = chat_to_responses(body)
            url = upstream.base_url + "/responses"
        else:
            if target == "responses":
                body = chat_to_responses(body)
            url = upstream.base_url + "/chat/completions"
        return url, body, headers

    def _upstream_lines(
        self, url: str, body: dict[str, Any], headers: dict[str, str]
    ) -> Iterator[str]:
        with (
            httpx.Client(timeout=self.timeout) as client,
            client.stream("POST", url, json=body, headers=headers) as response,
        ):
            if response.status_code >= 400:
                detail = response.read().decode(errors="replace")[:300]
                raise PoolError(
                    f"the upstream refused the request ({response.status_code}): {detail}",
                    status=response.status_code,
                    code="pool_upstream_rejected",
                )
            for raw in response.iter_lines():
                if raw:
                    yield raw

    def chat_completions_stream(
        self, payload: dict[str, Any], *, consumer: str = ""
    ) -> Iterator[str]:
        """Streamed chat completions from whichever upstream has the model."""
        route = self.route(str(payload.get("model") or ""))
        upstream = route.upstream
        if upstream.protocol == CLI:
            # A CLI slot answers in one go; the caller still gets a stream.
            answer = self._cli_chat(route, payload, consumer=consumer)
            text = str((answer["choices"][0]["message"] or {}).get("content") or "")
            yield (
                "data: "
                + json.dumps(
                    {
                        "id": "chatcmpl-pool",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": route.model,
                        "choices": [
                            {"index": 0, "delta": {"content": text}, "finish_reason": None}
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            yield "data: [DONE]\n\n"
            return
        url, body, headers = self._stream_setup(route, payload, target="chat")
        started = time.monotonic()
        sniffer = _UsageSniffer()
        lines = sniffer.watched(self._upstream_lines(url, body, headers))
        if upstream.protocol == ANTHROPIC:
            stream = anthropic_stream_to_openai_stream(lines, model=route.model)
        elif upstream.protocol == OPENAI_RESPONSES:
            stream = responses_stream_to_openai_stream(lines, model=route.model)
        else:
            stream = lines
        yield from stream
        self._account(
            Usage(
                at=_now(),
                model=route.model,
                upstream=upstream.name,
                protocol=upstream.protocol,
                status=200,
                duration_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=sniffer.prompt,
                completion_tokens=sniffer.completion,
                consumer=consumer,
                note="streamed",
            )
        )

    def responses_stream(self, payload: dict[str, Any], *, consumer: str = "") -> Iterator[str]:
        """Streamed OpenAI Responses from whichever upstream has the model.

        The codex CLI is a Responses client: it asks for ``/responses`` and
        expects the Responses event stream, so the pool has to be able to
        *produce* that shape -- from a chat upstream, from an Anthropic
        upstream, or by passing the chain's own stream through.
        """
        route = self.route(str(payload.get("model") or ""))
        upstream = route.upstream
        if upstream.protocol == CLI:
            answer = self._cli_chat(route, payload, consumer=consumer)
            text = str((answer["choices"][0]["message"] or {}).get("content") or "")
            event = {
                "type": "response.completed",
                "response": {
                    "id": "resp_pool",
                    "object": "response",
                    "status": "completed",
                    "model": route.model,
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    ],
                },
            }
            yield f"event: response.completed\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            return
        url, body, headers = self._stream_setup(route, payload, target="responses")
        started = time.monotonic()
        sniffer = _UsageSniffer()
        lines = sniffer.watched(self._upstream_lines(url, body, headers))
        if upstream.protocol == ANTHROPIC:
            stream = anthropic_stream_to_responses_stream(lines, model=route.model)
        elif upstream.protocol == OPENAI_CHAT:
            stream = chat_stream_to_responses_stream(lines, model=route.model)
        else:
            stream = lines
        yield from stream
        self._account(
            Usage(
                at=_now(),
                model=route.model,
                upstream=upstream.name,
                protocol=upstream.protocol,
                status=200,
                duration_ms=int((time.monotonic() - started) * 1000),
                prompt_tokens=sniffer.prompt,
                completion_tokens=sniffer.completion,
                consumer=consumer,
                note="streamed (responses)",
            )
        )

    # ----------------------------------------------------------- CLI slots

    def _cli_chat(
        self,
        route: Route,
        payload: dict[str, Any],
        *,
        consumer: str = "",
        protocol: str = "openai",
    ) -> dict[str, Any]:
        """Answer from a CLI slot: the CLI is the only client allowed to spend it.

        The answer is wrapped in the protocol the caller asked for, so a Claude
        CLI and an OpenAI client can both spend the same quota.
        """
        upstream = route.upstream
        if upstream.cli is None:
            raise PoolError(f"upstream {upstream.name} has no CLI slot", status=500)
        started = time.monotonic()
        try:
            report = upstream.cli.run(_messages_of(payload))
        except RuntimeError as exc:
            raise PoolError(f"the CLI slot {upstream.name} failed: {exc}") from exc
        duration = int((time.monotonic() - started) * 1000)
        usage = report.get("usage")
        answer = str(report.get("text") or "")
        self._account(
            Usage(
                at=_now(),
                model=route.model,
                upstream=upstream.name,
                protocol=upstream.protocol,
                status=200,
                duration_ms=duration,
                prompt_tokens=int((usage or {}).get("prompt_tokens") or 0),
                completion_tokens=int((usage or {}).get("completion_tokens") or 0),
                # The CLI did not report its usage: say so, instead of
                # recording a zero that reads as "this call was free".
                usage_reported=usage is not None,
                session_id=report.get("session_id"),
                consumer=consumer,
                note="served by the CLI slot",
            )
        )
        if protocol == "anthropic":
            return {
                "id": "msg_pool",
                "type": "message",
                "role": "assistant",
                "model": route.model,
                "content": [{"type": "text", "text": answer}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        return {
            "id": "chatcmpl-pool",
            "object": "chat.completion",
            "created": 0,
            "model": route.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    # ----------------------------------------------------------- accounting

    def _account(self, usage: Usage) -> None:
        if self.usage_path is None:
            return
        try:
            self.usage_path.parent.mkdir(parents=True, exist_ok=True)
            with self.usage_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "at": usage.at,
                            "model": usage.model,
                            "upstream": usage.upstream,
                            "protocol": usage.protocol,
                            "status": usage.status,
                            "duration_ms": usage.duration_ms,
                            "prompt_tokens": usage.prompt_tokens,
                            "completion_tokens": usage.completion_tokens,
                            "usage_reported": usage.usage_reported,
                            "session_id": usage.session_id,
                            "consumer": usage.consumer,
                            "note": usage.note,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        except OSError:
            # Accounting must never break a request.
            pass

    def inventory(self) -> dict[str, Any]:
        """What is **left** in each tier, not just what was spent.

        Only the reserve tier (the MiniMax subscription) has a balance that
        can be asked for; the others say so rather than showing a number the
        platform made up.  See :mod:`app.pool.inventory`.
        """
        upstreams = self.upstreams()
        return build_inventory(
            upstreams=upstreams,
            models=self.models(),
            usage=self.usage_summary(),
            quota=self._quota_reading(upstreams),
            balances=cached_balances(upstreams),
        )

    def _quota_reading(self, upstreams: list[Upstream]) -> dict[str, Any]:
        """The MiniMax subscription's remaining, read at most every few minutes.

        No MiniMax upstream means no probe at all: there is nothing to ask
        about, and asking anyway would only cost a round trip.
        """
        if not any(str(getattr(upstream, "tier", "")) == "reserve" for upstream in upstreams):
            return {}
        return cached_minimax_quota()

    def usage_timeline(self, *, hours: int = 24) -> dict[str, Any]:
        """The quota spend over time, hour by hour.

        A total is not enough to answer "已用额度": the human needs to see
        **when** it was spent.  "Did we just burn through this window's
        quota?" is a question about time, not about a sum -- and a sum
        cannot tell a burst from a trickle.
        """
        hours = max(1, min(int(hours), 24 * 14))
        now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        start = now - timedelta(hours=hours - 1)
        buckets: dict[str, dict[str, Any]] = {}
        for index in range(hours):
            at = (start + timedelta(hours=index)).isoformat()
            buckets[at] = {
                "at": at,
                "requests": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "upstreams": {},
                "consumers": {},
            }
        outside = 0
        if self.usage_path is not None and self.usage_path.exists():
            try:
                lines = self.usage_path.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for line in lines:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                stamp = _parse_timestamp(str(record.get("at") or ""))
                if stamp is None:
                    continue
                key = stamp.replace(minute=0, second=0, microsecond=0).isoformat()
                bucket = buckets.get(key)
                if bucket is None:
                    outside += 1
                    continue
                bucket["requests"] += 1
                bucket["prompt_tokens"] += int(record.get("prompt_tokens") or 0)
                bucket["completion_tokens"] += int(record.get("completion_tokens") or 0)
                upstream = str(record.get("upstream") or "?")
                bucket["upstreams"][upstream] = bucket["upstreams"].get(upstream, 0) + 1
                consumer = str(record.get("consumer") or "?")
                bucket["consumers"][consumer] = bucket["consumers"].get(consumer, 0) + 1
        return {
            "bucket": "hour",
            "hours": hours,
            "from": start.isoformat(),
            "to": now.isoformat(),
            "outside_window": outside,
            "buckets": list(buckets.values()),
        }

    def _corrections(self) -> dict[tuple[str, str, str, str], dict[str, Any]]:
        """The corrections the pool's own log needs.

        A record written before the pool learned to read a stream's usage says
        ``0`` tokens -- and **0 is a false statement**: the tokens were never
        counted, not counted as none.  A correction says so, and the summary
        then reports "not recorded" instead of a zero that would read as
        "this call was free".
        """
        if self.usage_path is None:
            return {}
        path = self.usage_path.parent / "usage-corrections.jsonl"
        if not path.exists():
            return {}
        found: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return {}
        for line in lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                continue
            key = (
                str(record.get("at") or ""),
                str(record.get("upstream") or ""),
                str(record.get("model") or ""),
                str(record.get("consumer") or ""),
            )
            found[key] = record
        return found

    def usage_summary(self) -> dict[str, Any]:
        """Whose quota was spent, and by whom.

        Two views, because both questions matter: *which quota* was spent (the
        upstream) and *who* spent it (the consuming harness).
        """
        summary: dict[str, dict[str, Any]] = {}
        consumers: dict[str, dict[str, Any]] = {}
        if self.usage_path is None or not self.usage_path.exists():
            return {
                "upstreams": summary,
                "consumers": consumers,
                "requests": 0,
                "tokens_unknown": 0,
            }
        corrections = self._corrections()
        requests = 0
        unknown = 0
        try:
            for line in self.usage_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                requests += 1
                upstream = str(record.get("upstream") or "?")
                model = str(record.get("model") or "?")
                consumer = str(record.get("consumer") or "?")
                bucket = summary.setdefault(
                    upstream,
                    {
                        "requests": 0,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "tokens_unknown": 0,
                        "models": {},
                        "consumers": {},
                    },
                )
                bucket["requests"] += 1
                corrected = corrections.get(
                    (str(record.get("at") or ""), upstream, model, consumer)
                )
                prompt_tokens = int(record.get("prompt_tokens") or 0)
                completion_tokens = int(record.get("completion_tokens") or 0)
                if (
                    corrected is not None
                    or record.get("usage_reported") is False
                    or not (prompt_tokens or completion_tokens)
                ):
                    # The tokens were never counted: say "not recorded", not 0.
                    # A record with no tokens at all is that too -- a call
                    # always uses some, so zero means "not counted".
                    unknown += 1
                    bucket["tokens_unknown"] += 1
                else:
                    bucket["prompt_tokens"] += prompt_tokens
                    bucket["completion_tokens"] += completion_tokens
                bucket["models"][model] = bucket["models"].get(model, 0) + 1
                bucket["consumers"][consumer] = bucket["consumers"].get(consumer, 0) + 1
                spent = consumers.setdefault(
                    consumer, {"requests": 0, "upstreams": {}, "models": {}}
                )
                spent["requests"] += 1
                spent["upstreams"][str(record.get("upstream") or "?")] = (
                    spent["upstreams"].get(str(record.get("upstream") or "?"), 0) + 1
                )
                spent["models"][model] = spent["models"].get(model, 0) + 1
        except OSError:
            pass
        return {
            "upstreams": summary,
            "consumers": consumers,
            "requests": requests,
            "tokens_unknown": unknown,
        }


def _parse_timestamp(value: str) -> datetime | None:
    """An ISO timestamp, in UTC.  Unparseable is ``None`` -- not a guess."""
    try:
        stamp = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _messages_of(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The messages of a request, whichever protocol it arrived in.

    A chat client sends ``messages``; a Responses client sends ``input``
    (a string, or a list of items).  The CLI slot only ever wants a
    conversation, so both shapes are normalised here.
    """
    messages = payload.get("messages")
    if isinstance(messages, list) and messages:
        return [item for item in messages if isinstance(item, dict)]
    raw = payload.get("input")
    if isinstance(raw, str) and raw.strip():
        return [{"role": "user", "content": raw}]
    if isinstance(raw, list):
        items: list[dict[str, Any]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            content = entry.get("content")
            if isinstance(content, list):
                text = "\n".join(
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("text")
                )
            else:
                text = str(content or "")
            items.append({"role": str(entry.get("role") or "user"), "content": text})
        if items:
            return items
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        return [{"role": "system", "content": instructions}]
    return []


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _responses_to_chat(payload: dict[str, Any]) -> dict[str, Any]:
    """OpenAI Responses request -> OpenAI chat request (best effort)."""
    messages: list[dict[str, Any]] = []
    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
    raw_input = payload.get("input")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            if isinstance(item, dict):
                role = str(item.get("role") or "user")
                content = item.get("content")
                if isinstance(content, list):
                    text = "\n".join(
                        str(block.get("text") or "")
                        for block in content
                        if isinstance(block, dict) and block.get("text")
                    )
                else:
                    text = str(content or "")
                messages.append({"role": role, "content": text})
    body: dict[str, Any] = {"model": payload.get("model"), "messages": messages}
    if payload.get("max_output_tokens"):
        body["max_tokens"] = int(payload["max_output_tokens"])
    return body


def _chat_to_responses(data: dict[str, Any], *, model: str) -> dict[str, Any]:
    """OpenAI chat response -> OpenAI Responses response (best effort)."""
    choices = data.get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        text = str((choices[0].get("message") or {}).get("content") or "")
    usage = data.get("usage") or {}
    return {
        "id": str(data.get("id") or "resp_relay"),
        "object": "response",
        "created_at": 0,
        "model": str(data.get("model") or model),
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }
