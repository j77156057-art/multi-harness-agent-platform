"""HTTP-side subject checks: tokens, the matrix, and the denial audit (auth-layer).

Enabled by ``AP_AUTH_MODE=enforce``; ``off`` makes every dependency a no-op, so
the existing suites, drills and the frontend behave exactly as before.

Order (pinned by tests): the subject check runs before the service's fact
checks -- an unauthenticated caller gets 401/403, never a 404 that would tell
it which resources exist. For authenticated callers the fact semantics are
untouched. (The design-input report floated "fact first, subject second"; the
divergence is deliberate and recorded in the card.)

Every refusal -- 401 or 403 -- writes exactly one ``permission_denied`` audit
event (R10 §17 L1057; event name legalised by R10-fix-12) and nothing else.
"""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import Depends, Request

from app.api.errors import ApiError
from app.auth.policy import decide
from app.auth.tokens import Principal, TokenInvalid, TokenService
from app.config import settings
from app.db.connection import Database
from app.events.store import EventStore
from app.git.branch import BranchNameError, parse_branch


def _database(request: Request) -> Database:
    return request.app.state.database


DatabaseDep = Annotated[Database, Depends(_database)]


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def _audit_denial(
    *,
    database: Database,
    request: Request,
    principal: Principal | None,
    key: str,
    reason: str,
) -> None:
    """One event per refusal; actor = the token's subject when it is known."""
    actor_type = principal.subject_type if principal is not None else "anonymous"
    actor_id = principal.subject_id if principal is not None else "anonymous"
    store = EventStore(database)
    with database.transaction() as conn:
        store.append(
            event_name="permission_denied",
            aggregate_type="auth",
            aggregate_id=actor_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload={
                "permission": key,
                "reason": reason,
                "method": request.method,
                "path": request.url.path,
            },
            connection=conn,
        )


def _refuse(
    *,
    database: Database,
    request: Request,
    principal: Principal | None,
    key: str,
    reason: str,
    status: int,
    code: str,
) -> NoReturn:
    # Audit first, then refuse: a refusal without a trace is what §17 forbids.
    _audit_denial(database=database, request=request, principal=principal, key=key, reason=reason)
    raise ApiError(code, status, "permission denied", details={"permission": key})


def _resolve(request: Request, database: Database, *, key: str) -> Principal | None:
    if settings.auth_mode != "enforce":
        return None
    try:
        principal = TokenService(database).verify(_bearer_token(request))
    except TokenInvalid as exc:
        _refuse(
            database=database,
            request=request,
            principal=None,
            key=key,
            reason=exc.reason,
            status=401,
            code="auth.token_invalid",
        )
    decision = decide(principal, key)
    if not decision.allowed:
        _refuse(
            database=database,
            request=request,
            principal=principal,
            key=key,
            reason=decision.reason or "role_not_allowed",
            status=403,
            code="auth.permission_denied",
        )
    return principal


def require_for(key: str):
    """Build the dependency enforcing one permission key (subject type from POLICY)."""

    def dependency(request: Request, database: DatabaseDep) -> Principal | None:
        return _resolve(request, database, key=key)

    dependency.__name__ = f"require_{key.replace(':', '_')}"
    return dependency


# ------------------------------------------------------------------ bindings


def bind_declared(
    principal: Principal | None,
    declared: str | None,
    *,
    key: str,
    database: Database,
    request: Request,
) -> None:
    """The declared subject in the body must be the token's subject (§17 L1055)."""
    if principal is None:
        return
    if declared is None or declared.strip() != principal.subject_id:
        _refuse(
            database=database,
            request=request,
            principal=principal,
            key=key,
            reason="identity_mismatch",
            status=403,
            code="auth.permission_denied",
        )


def bind_task_owner(
    principal: Principal | None,
    *,
    task_id: str,
    key: str,
    database: Database,
    request: Request,
) -> None:
    """Owner-scoped verbs: the token's subject must be the task's owner."""
    if principal is None:
        return
    rows = database.read("SELECT owner FROM tasks WHERE task_id = ?", (task_id,))
    owner = None if not rows else rows[0]["owner"]
    if owner is None or str(owner) != principal.subject_id:
        _refuse(
            database=database,
            request=request,
            principal=principal,
            key=key,
            reason="not_task_owner",
            status=403,
            code="auth.permission_denied",
        )


def bind_branch_subject(
    principal: Principal | None,
    branch: str,
    *,
    key: str,
    database: Database,
    request: Request,
) -> None:
    """§14 naming is the publish-face identity check: branch harness == subject."""
    if principal is None:
        return
    try:
        parsed = parse_branch(branch)
    except BranchNameError:
        return  # not an agent branch at all; the service's fact check rejects it
    if parsed.harness != principal.subject_id:
        _refuse(
            database=database,
            request=request,
            principal=principal,
            key=key,
            reason="branch_identity_mismatch",
            status=403,
            code="auth.permission_denied",
        )
