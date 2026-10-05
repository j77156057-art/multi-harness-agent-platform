"""Uniform error envelope and domain-exception mapping (MVP-08).

Codes are the ones defined in ``docs/prep/MVP-08/error-codes.md``. The envelope
is::

    {"error": {"code", "message", "retryable", "task_id"?, "seq"?, "details"?}}

``message`` is a static template. Nothing here echoes request bodies -- an error
response must never become a path for secret material to reach a log or a
client (MVP-08 prep, decision D-8).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.claim.guarded_write import (
    AttemptMismatch,
    GuardError,
    StaleFencingEpoch,
    StatusNotAllowed,
    TaskNotFound,
)
from app.claim.service import IdempotencyConflict
from app.claim.transitions import ConcurrentStateChange, IllegalTransition
from app.git.publish import PublishRejected
from app.lease.service import LeaseExpired, LeaseNotFound, LeaseTokenMismatch
from app.tasks.service import Conflict, InvalidInput, NotFound

#: Codes for which an as-is retry within the same attempt may succeed.
RETRYABLE: frozenset[str] = frozenset(
    {
        "claim.concurrency_full",
        "merge.queue_full",
        "publish.pack_transfer_failed",
        "submit.verify_capacity_full",
        "verify.resource_blocked",
    }
)


class ApiError(Exception):
    """An error with an explicit wire contract."""

    def __init__(
        self,
        code: str,
        http_status: int,
        message: str,
        *,
        retryable: bool | None = None,
        details: dict[str, Any] | None = None,
        seq: int | None = None,
        task_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.message = message
        self.retryable = code in RETRYABLE if retryable is None else retryable
        self.details = details
        self.seq = seq
        self.task_id = task_id


def envelope(error: ApiError) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
    }
    if error.task_id is not None:
        body["task_id"] = error.task_id
    if error.seq is not None:
        body["seq"] = error.seq
    if error.details:
        body["details"] = error.details
    return {"error": body}


def _from_exception(exc: Exception) -> ApiError:
    """Map a domain exception onto a wire code. Order matters."""
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, PublishRejected):
        retryable = exc.code in RETRYABLE
        return ApiError(exc.code, exc.http_status, str(exc), retryable=retryable)
    if isinstance(exc, IdempotencyConflict):
        return ApiError(exc.code.value, 409, str(exc))
    if isinstance(exc, StaleFencingEpoch):
        return ApiError("fencing.epoch_stale", 409, str(exc), task_id=None)
    if isinstance(exc, AttemptMismatch):
        return ApiError("submit.attempt_mismatch", 409, str(exc))
    if isinstance(exc, TaskNotFound):
        return ApiError("resource.not_found", 404, str(exc))
    if isinstance(exc, LeaseNotFound):
        return ApiError("lease.not_found", 404, str(exc))
    if isinstance(exc, LeaseExpired):
        return ApiError("lease.expired", 409, str(exc))
    if isinstance(exc, LeaseTokenMismatch):
        return ApiError("lease.wrong_owner", 403, str(exc))
    if isinstance(exc, IllegalTransition):
        return ApiError("state.transition_undefined", 409, str(exc))
    if isinstance(exc, ConcurrentStateChange):
        return ApiError("claim.concurrent_claim", 409, str(exc), retryable=True)
    if isinstance(exc, StatusNotAllowed):
        return ApiError("request.conflict", 409, str(exc))
    if isinstance(exc, GuardError):
        return ApiError("fencing.epoch_stale", 409, str(exc))
    if isinstance(exc, NotFound):
        return ApiError("resource.not_found", 404, str(exc))
    if isinstance(exc, InvalidInput):
        return ApiError("request.invalid", 422, str(exc))
    if isinstance(exc, Conflict):
        code = getattr(exc, "code", None) or "request.conflict"
        return ApiError(code, 409, str(exc))
    if isinstance(exc, ValueError):
        return ApiError("request.invalid", 400, str(exc))
    return ApiError("internal.error", 500, "internal error", retryable=False)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.http_status, content=envelope(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Only field locations and error types -- never the submitted values.
        details = {
            "fields": sorted(
                ".".join(str(part) for part in item.get("loc", ())) for item in exc.errors()
            )[:20]
        }
        error = ApiError("request.invalid", 400, "request body failed validation", details=details)
        return JSONResponse(status_code=400, content=envelope(error))

    @app.middleware("http")
    async def _domain_errors(request: Request, call_next):
        """Translate domain exceptions that escaped their route.

        A registered handler for ``Exception`` would not do: Starlette routes that
        through ServerErrorMiddleware, which re-raises afterwards, so a test client
        would see the raw exception instead of the envelope. Catching here keeps a
        single wire format for every rejection.
        """
        try:
            return await call_next(request)
        except Exception as exc:  # noqa: BLE001 - the mapping is the point
            mapped = _from_exception(exc)
            return JSONResponse(status_code=mapped.http_status, content=envelope(mapped))
