"""Application factory for the MVP-08 REST surface."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse

from app.api import routes
from app.api.errors import ApiError, envelope, install_error_handlers
from app.config import settings
from app.db.connection import Database
from app.git.repo import CanonicalRepo
from app.spec import R10_ALLOWLIST_COUNTS, R10_TRANSITION_EDGE_COUNT

API_PREFIX = "/api/v1"

#: Hostnames accepted in the Host header. R10 §5 binds v1 to localhost; checking
#: the header as well is what stops a DNS-rebinding page from driving the
#: scheduler through the browser (docs/prep/MVP-08/README.md D-6).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})


def create_app(database: Database | None = None, repo: CanonicalRepo | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.ensure_dirs()
        app.state.database.initialize()
        yield

    app = FastAPI(
        title="Agent Collaboration Platform — MVP",
        version="0.1.0",
        lifespan=lifespan,
        description=(
            "多 Harness Agent 协作与调度平台 MVP。"
            f"规范基线：R10（迁移边 {R10_TRANSITION_EDGE_COUNT} 条，"
            f"allowlist {R10_ALLOWLIST_COUNTS['UNION']} 项）。"
        ),
    )
    app.state.database = database if database is not None else Database()
    # The publish proxy needs the canonical repository, and tests must be able
    # to point it at a temp one; defaulting it inside the route made the
    # alternates check compare against the repository root's path.
    app.state.canonical_repo = repo if repo is not None else CanonicalRepo.from_settings()

    install_error_handlers(app)

    @app.middleware("http")
    async def _loopback_only(request, call_next):
        raw = request.headers.get("host", "")
        hostname = raw.rsplit(":", 1)[0] if raw else ""
        if hostname not in LOOPBACK_HOSTS:
            error = ApiError(
                "auth.host_header_rejected", 400, "requests are accepted from loopback only"
            )
            return JSONResponse(status_code=400, content=envelope(error))
        # No CORS middleware is installed: the frontend reaches the API same-origin
        # through its dev proxy, so the scheduler is not driveable from any page.
        return await call_next(request)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(routes.management)
    api.include_router(routes.adapter)
    api.include_router(routes.review)
    api.include_router(routes.read)
    app.include_router(api)

    return app
