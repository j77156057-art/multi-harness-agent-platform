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

import functools
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.adapter import model_choice
from app.api.auth_deps import DatabaseDep, Principal, require_for
from app.api.errors import ApiError
from app.pool import user_keys
from app.pool.routing import plan_attempt
from app.pool.service import PoolError, PoolService, invalidate_discovery
from app.pool.sources import (
    PoolDeclarationError,
    declare_cli_model,
    declare_upstream_tier,
    declared_cli_slots,
    declared_upstream_tiers,
    invalidate_rosters,
    withdraw_cli_model,
    withdraw_upstream_tier,
)

router = APIRouter(tags=["pool"])

#: What the pool says when it refuses a request, in the platform's own wire
#: contract (the same envelope every other rejection uses).
#:
#: The messages are **templates**: a refusal must not become a path for an
#: upstream's answer (or anything else) to reach the client.  The code is the
#: part a caller acts on.
_POOL_MESSAGES = {
    "pool_user_key_pending": ("the pool is waiting for the user to approve spending their own key"),
    "relay_model_required": "a model name is required",
    "relay_model_not_found": "no upstream serves that model",
    # 这一档由某个 CLI 当模型来服务，只能回文字。编码用的 CLI 一定带工具定义，
    # 所以它会撞上这句话 —— 与其让调用方看到"连接被关闭"或"池子抽风"，不如明说
    # 该怎么办：换一个真 provider 的模型。
    "relay_cli_cannot_use_tools": (
        "this model is served by a CLI slot, which can only return text and never "
        "tool_calls; a coding client always sends tool definitions, so point the "
        "window at a real provider model instead of this tier"
    ),
}


def _as_api_error(exc: PoolError) -> ApiError:
    """A pool refusal, in the envelope the rest of the platform uses."""
    return ApiError(
        exc.code,
        exc.status,
        _POOL_MESSAGES.get(exc.code, "the pool could not serve this request"),
    )


def _pool_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Turn a pool refusal into a status the caller can act on.

    Without this a refusal leaves as a 500 "internal error": the caller
    cannot tell "there is no such model" from "the user has not said yes
    yet", and a 500 invites a worker to retry the same thing forever.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except PoolError as exc:
            raise _as_api_error(exc) from exc

    return wrapper


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


def _pool_status(service: PoolService) -> dict[str, Any]:
    """The pool's state: the sources, the models, and who spent what."""
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
        # When the quota was spent, hour by hour: a total cannot tell a
        # burst from a trickle, and the human needs to see the burst.
        "timeline": service.usage_timeline(),
        # What is **left** in each tier -- where a balance can be asked for,
        # and an honest "there is no balance" where it cannot.
        "inventory": service.inventory(),
        # What each CLI says it can run: the catalogue, **not** a tier.  The
        # office compares it with the models above, so "a new model nobody
        # declared" is visible instead of silently missing.
        "rosters": service.rosters(),
        # 哪些定档是**人写下来的**（写在这台机器的池子配置里）。界面据此决定
        # 某一行能不能「改档 / 撤档」：内置档位没有可撤的声明，撤了它也不会消失。
        "declarations": [
            {
                "cli": str(slot.get("name") or ""),
                "model": str(slot.get("model") or ""),
                "tier": str(slot.get("tier") or ""),
            }
            for slot in declared_cli_slots()
            if slot.get("model")
        ],
        # 人给**非 CLI 上游**（写在各家 CLI 自己配置里那几条）写的档位。它们的默认
        # 档位在代码里，所以界面上只有这份清单能说明"这一行可以改、也可以撤"。
        "upstream_tiers": declared_upstream_tiers(),
    }


@router.get("/pool/status")
def pool_status(principal: _guard("harness:pool")) -> dict[str, Any]:
    """The pool's state: the sources, the models, and who spent what."""
    return _pool_status(_service())


@router.post("/pool/declaration")
def pool_declaration(
    body: dict[str, Any],
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """Give a model the CLI says it can run a tier.

    The **human** decides: free or paid is a fact about the account, not
    something the platform can sniff out.  The declaration reuses that CLI's
    argv contract, and the pool is read again right away -- the model is
    usable from then on (a paid one still needs a yes before it is spent).
    """
    cli = str(body.get("cli") or "").strip()
    model = str(body.get("model") or "").strip()
    tier = str(body.get("tier") or "").strip().lower()
    service = _service()
    try:
        slot = declare_cli_model(
            cli,
            model,
            tier,
            roster=(service.rosters() or {}).get(cli),
        )
    except PoolDeclarationError as error:
        raise ApiError("request.invalid", 400, str(error)) from error
    invalidate_discovery()
    invalidate_rosters()
    return {"declaration": slot, "pool": _pool_status(_service())}


@router.post("/pool/roster-probe")
def pool_roster_probe(principal: _guard("harness:register")) -> dict[str, Any]:
    """开始一轮**并行**名册探测：各家 CLI 各开一线程，探完一家就记一家。

    人要的是"探完哪家先看到哪家"，所以结果是**逐家长出来**的（界面轮询
    `GET /pool/roster-probe`），不是等最慢那家。探测不花额度，只花一次进程。
    """
    from app.pool import probe

    return probe.start_roster_probe()


@router.get("/pool/roster-probe")
def pool_roster_probe_state(principal: _guard("harness:register")) -> dict[str, Any]:
    """这一轮探测到哪了；每家的模型一探到就在这里出现。"""
    from app.pool import probe

    return probe.snapshot()


@router.post("/pool/model-test")
def pool_model_test(
    body: dict[str, Any], principal: _guard("harness:register")
) -> dict[str, Any]:
    """测一个模型能不能用：真的调用它一次，只发一句话、只让它回几个字。

    这会**花一次额度** —— 所以必须由人点名一个模型才做，平台不自己跑批。

    两条路（见 `app.pool.probe.test_model`）：池子里有就走池子那条**现成**的服务
    路径；池子里没有、但这家 CLI 的名册里有，就按同一条调用约定直接问一次 CLI
    （临时路由，**不写声明** —— 要不要给它定档仍是人的决定）。
    """
    from app.pool import probe

    model_id = str(body.get("model") or "").strip()
    if not model_id:
        raise ApiError("request.invalid", 400, "which model must be named")
    return probe.test_model(
        model_id,
        _service(),
        consumer=f"human/{getattr(principal, 'subject_id', 'operator')}",
    )


@router.post("/pool/upstream-tier")
def pool_upstream_tier(
    body: dict[str, Any],
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """给**非 CLI 上游**定档（`claude-provider`、`opencode:1`、`minimax` …）。

    这几条上游的档位本来是代码里的默认值，池子里没有可改的东西 —— 人若知道"这条
    其实是免费额度"，以前只能去改代码。现在写成一份覆盖；撤掉就回到代码默认。
    """
    name = str(body.get("name") or "").strip()
    tier = str(body.get("tier") or "").strip().lower()
    try:
        slot = declare_upstream_tier(name, tier)
    except PoolDeclarationError as error:
        raise ApiError("request.invalid", 400, str(error)) from error
    invalidate_discovery()
    return {"upstream_tier": slot, "pool": _pool_status(_service())}


@router.post("/pool/upstream-tier/withdraw")
def pool_upstream_tier_withdraw(
    body: dict[str, Any],
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """撤掉这条覆盖：那条上游回到代码默认的档位。"""
    name = str(body.get("name") or "").strip()
    try:
        withdrawn = withdraw_upstream_tier(name)
    except PoolDeclarationError as error:
        raise ApiError("request.invalid", 400, str(error)) from error
    invalidate_discovery()
    return {"withdrawn": withdrawn, "pool": _pool_status(_service())}


@router.post("/pool/declaration/withdraw")
def pool_declaration_withdraw(
    body: dict[str, Any],
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """Take back one declaration: the model goes back to 「没定档」.

    档位是人的一句话，而人会点错。没有这一步，一条错标成"免费"的收费额度会
    一直按免费花下去 —— 平台以为不花钱，就真的花了。撤的只是**人写的那条声明**；
    它若盖住了内置档位，撤完内置的那条会照原样回来。
    """
    cli = str(body.get("cli") or "").strip()
    model = str(body.get("model") or "").strip()
    try:
        withdrawn = withdraw_cli_model(cli, model)
    except PoolDeclarationError as error:
        raise ApiError("request.invalid", 400, str(error)) from error
    invalidate_discovery()
    invalidate_rosters()
    return {"withdrawn": withdrawn, "pool": _pool_status(_service())}


@router.post("/pool/probe")
def pool_probe(principal: _guard("harness:pool")) -> dict[str, Any]:
    """Read the pool again, from scratch.

    The discovery is cached (30 seconds) and nothing used to clear it, so a
    quota that was added -- or a free quota that turned into a paid one --
    could stay invisible.  The human asks for this: "probe it again".
    """
    invalidate_discovery()
    invalidate_rosters()
    return _pool_status(_service())


@router.post("/pool/v1/chat/completions")
@_pool_errors
def pool_chat_completions(
    payload: dict[str, Any], principal: _guard("harness:pool"), request: Request
) -> Any:
    """Serve an OpenAI chat completion from the pool."""
    service = _service()
    if payload.get("stream"):
        # Where it would go is decided **before** the stream starts: a refusal
        # has to be a status the caller can read, not a stream that breaks
        # halfway through.
        route = service.route(str(payload.get("model") or ""))
        # 同一句话也要在**开流之前**说完：CLI 槽位服务不了带工具定义的请求，而
        # 生成器里抛错时响应头已经发出去了，调用方只会看到「连接被关闭」。
        service.refuse_tools_for_cli(route, payload)
        return StreamingResponse(
            service.chat_completions_stream(payload, consumer=_consumer(principal)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return service.chat_completions(payload, consumer=_consumer(principal))


@router.post("/pool/v1/messages")
@_pool_errors
def pool_messages(
    payload: dict[str, Any], principal: _guard("harness:pool"), request: Request
) -> Any:
    """Serve an Anthropic messages request from the pool (the Claude CLI's protocol)."""
    return _service().messages(payload, consumer=_consumer(principal))


@router.post("/pool/v1/responses")
@_pool_errors
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
        service.route(str(payload.get("model") or ""))
        return StreamingResponse(
            service.responses_stream(payload, consumer=_consumer(principal)),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    return service.responses(payload, consumer=_consumer(principal))


@router.get("/pool/plan")
def pool_plan(
    principal: _guard("harness:pool"),
    database: DatabaseDep,
    task_id: str,
    harness_id: str,
    attempt_no: int = 0,
) -> dict[str, Any]:
    """Where should this attempt's model calls go?

    The worker asks before it runs; the platform answers, because the platform
    is the one that knows what failed and which quota is alive -- and what the
    human chose for this window.
    """
    return plan_attempt(
        task_id,
        harness_id=harness_id,
        attempt_no=attempt_no,
        pool=_service(),
        selection=model_choice.read_choice(database, harness_id),
    )


# ---------------------------------------------------------------- 自己的 key
#
# 用户自己在办公室里加的 API key：平台替它保管（没人别的能拿到），但**绝不
# 自己花** —— 要用得用户点头。下面这几条就是那个"点头"的面。

_user_guard = Annotated[Principal | None, Depends(require_for("pool:user_keys"))]


@router.get("/pool/user-keys")
def list_user_keys(principal: _user_guard) -> dict[str, Any]:
    """The keys the user has given the platform, and the open question.

    The key itself is **never** in this answer -- only a mask.
    """
    return {
        "keys": [key.as_dict() for key in user_keys.entries()],
        "pending": user_keys.pending().as_dict() if user_keys.pending() else None,
    }


@router.post("/pool/user-keys")
def add_user_key(payload: dict[str, Any], principal: _user_guard) -> dict[str, Any]:
    """Add one of the user's own provider keys."""
    try:
        key = user_keys.register(
            label=str(payload.get("label") or ""),
            protocol=str(payload.get("protocol") or "openai_chat"),
            base_url=str(payload.get("base_url") or ""),
            key=str(payload.get("key") or ""),
            models=[str(m) for m in (payload.get("models") or [])],
            note=str(payload.get("note") or ""),
        )
    except user_keys.UserKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    # 新加的 key 立刻算数：不然它要等发现缓存过期（30 秒）才被池子看见。
    invalidate_discovery()
    return {"key": key.as_dict(), "masked": user_keys.masked(key.id)}


@router.delete("/pool/user-keys/{key_id}")
def delete_user_key(key_id: str, principal: _user_guard) -> dict[str, Any]:
    """Forget one of the user's own keys."""
    if not user_keys.remove(key_id):
        raise HTTPException(status_code=404, detail="没有这个 key")
    # 删掉的 key 也不能再留在发现缓存里。
    invalidate_discovery()
    return {"removed": True}


@router.post("/pool/user-keys/{key_id}/approve")
def approve_user_key(
    key_id: str, payload: dict[str, Any], principal: _user_guard
) -> dict[str, Any]:
    """Say yes: ``once`` (this request) or ``always`` (until removed)."""
    try:
        key = user_keys.set_approval(key_id, str(payload.get("mode") or "once"))
    except user_keys.UserKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    user_keys.clear_request(key_id)
    return {"key": key.as_dict()}


@router.post("/pool/user-keys/{key_id}/deny")
def deny_user_key(key_id: str, principal: _user_guard) -> dict[str, Any]:
    """No: the question is closed and the key stays unspendable."""
    try:
        key = user_keys.set_approval(key_id, user_keys.APPROVAL_NONE)
    except user_keys.UserKeyError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    user_keys.clear_request(key_id)
    return {"key": key.as_dict()}


def _consumer(principal: Any) -> str:
    """Who is spending the quota: the harness that holds the token."""
    return f"{getattr(principal, 'subject_type', '?')}/{getattr(principal, 'subject_id', '?')}"
