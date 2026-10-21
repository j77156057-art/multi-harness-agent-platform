"""The platform's quota pool, on the platform's own API.

The pool is not a separate service and not a new product: it is a few routes
on the platform, under ``/api/v1/pool``.  A harness CLI is pointed at it (per
attempt, through its own argv or environment) and the platform answers with a
model -- from its own provider credentials, or from another harness's quota
served through that harness's CLI.

Two rules:

* **the caller is a harness.**  The pool is reached with the calling
  harness's own platform token, so the platform knows exactly *who* spent
  *whose* quota -- that is the whole point of a pool.
* **the pool is not a dispatcher.**  It serves model calls.  It never claims a
  task, never moves an attempt, and never speaks for a harness: the execution
  slot stays with the CLI that was asked to do the work.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.api.auth_deps import Principal, require_for
from app.pool.routing import plan_attempt
from app.pool.service import PoolService

router = APIRouter(tags=["pool"])


def _guard(key: str):
    """The same guard the rest of the platform uses.

    The pool has its own key (``harness:pool``, harness tokens only), added
    to the matrix on the owner's approval (2026-10-21): the pool is a
    platform surface of its own, and borrowing somebody's quota is not the
    same act as asking what capabilities one has.
    """
    return Annotated[Principal | None, Depends(require_for(key))]


def _service() -> PoolService:
    return PoolService()


@router.get("/pool/v1/models")
def pool_models(principal: _guard("harness:pool")) -> dict[str, Any]:
    """The models the pool can serve right now, and whose quota each one is."""
    return {"object": "list", "data": _service().models()}


@router.get("/pool/status")
def pool_status(principal: _guard("harness:pool")) -> dict[str, Any]:
    """The pool's state: the sources, the models, and who spent what."""
    service = _service()
    return {
        "upstreams": [
            {
                "name": upstream.name,
                "protocol": upstream.protocol,
                "base_url": upstream.base_url,
                "models": list(upstream.models),
                "origin": upstream.origin,
                "note": upstream.note,
            }
            for upstream in service.upstreams()
        ],
        "models": service.models(),
        "usage": service.usage_summary(),
    }


@router.post("/pool/v1/chat/completions")
def pool_chat_completions(
    payload: dict[str, Any], principal: _guard("harness:pool"), request: Request
) -> Any:
    """Serve an OpenAI chat completion from the pool."""
    service = _service()
    if payload.get("stream"):
        return StreamingResponse(
            service.chat_completions_stream(payload, consumer=_consumer(principal)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return service.chat_completions(payload, consumer=_consumer(principal))


@router.post("/pool/v1/messages")
def pool_messages(
    payload: dict[str, Any], principal: _guard("harness:pool"), request: Request
) -> Any:
    """Serve an Anthropic messages request from the pool (the Claude CLI's protocol)."""
    return _service().messages(payload, consumer=_consumer(principal))


@router.post("/pool/v1/responses")
def pool_responses(
    payload: dict[str, Any], principal: _guard("harness:pool"), request: Request
) -> Any:
    """Serve an OpenAI Responses request from the pool (the codex CLI's protocol).

    The codex CLI is a streaming client, so the pool has to be able to
    *produce* the Responses event stream -- from a chat upstream, from an
    Anthropic upstream, or by passing the chain's own stream through.
    """
    service = _service()
    if payload.get("stream"):
        return StreamingResponse(
            service.responses_stream(payload, consumer=_consumer(principal)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return service.responses(payload, consumer=_consumer(principal))


@router.get("/pool/plan")
def pool_plan(
    principal: _guard("harness:pool"),
    task_id: str,
    harness_id: str,
    attempt_no: int = 0,
) -> dict[str, Any]:
    """Where should this attempt's model calls go?

    The worker asks before it runs; the platform answers, because the platform
    is the one that knows what failed and which quota is alive.
    """
    return plan_attempt(
        task_id,
        harness_id=harness_id,
        attempt_no=attempt_no,
        pool=_service(),
    )


def _consumer(principal: Any) -> str:
    """Who is spending the quota: the harness that holds the token."""
    return f"{getattr(principal, 'subject_type', '?')}/{getattr(principal, 'subject_id', '?')}"
