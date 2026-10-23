"""Application factory for the MVP-08 REST surface."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.api import routes
from app.api.errors import ApiError, envelope, install_error_handlers
from app.config import settings
from app.db.connection import Database
from app.git.repo import CanonicalRepo
from app.pool import routes as pool_routes
from app.spec import R10_ALLOWLIST_COUNTS, R10_TRANSITION_EDGE_COUNT
from app.wording import routes as wording_routes

API_PREFIX = "/api/v1"

#: The human entry page. Humans reach the API from a browser by port, so the
#: root is the one address they will open first -- answering 404 there made a
#: healthy server look broken (the coordinator hit exactly this). The human UI
#: is the Agent Office on 4174; this page only lists the doors.
_LANDING_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Agent 协作平台 · 入口</title>
<style>
  body { margin: 0; padding: 3rem 2rem; background: #faf8f5; color: #1a1a1a;
         font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; }
  h1 { font-size: 1.15rem; margin: 0 0 0.3rem; }
  h1 span { color: #1a7f37; }
  p { color: #5f5c56; margin: 0.2rem 0 1.2rem; }
  ul { list-style: none; padding: 0; max-width: 46rem; }
  li { padding: 0.55rem 0.8rem; border: 1px solid #d8d4cd; border-radius: 4px;
       margin-bottom: 0.4rem; }
  a { color: #1f5fbf; text-decoration: none; }
  a:hover { text-decoration: underline; }
  code { color: #5f5c56; }
</style>
</head>
<body>
<h1>Agent 协作平台 · 后端<span>已启动</span></h1>
<p>人类界面是 Agent Office（独立应用）：
   <a href="http://127.0.0.1:4174">http://127.0.0.1:4174</a>
   （在 Agent Office 仓单独启动）</p>
<ul>
  <li><a href="/health">/health</a> — 存活与配置摘要</li>
  <li><a href="/docs">/docs</a> — OpenAPI 交互文档（全部动词可在此试）</li>
  <li><a href="/spec/status">/spec/status</a> — 规范一致性（90 项）</li>
  <li><code>/api/v1/*</code> — REST API 前缀；示例
      <a href="/api/v1/projects">/api/v1/projects</a></li>
</ul>
</body>
</html>
"""

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
        # No CORS middleware is installed: the Agent Office reaches the API
        # through its own gateway, so the scheduler is not driveable from an
        # arbitrary page.
        return await call_next(request)

    api = APIRouter(prefix=API_PREFIX)
    api.include_router(routes.management)
    api.include_router(routes.adapter)
    api.include_router(routes.human)
    api.include_router(routes.review)
    api.include_router(routes.allocation)
    api.include_router(routes.read)
    # The platform's quota pool: a harness CLI is pointed at these routes when
    # its own provider is out of quota, and the platform answers with a model
    # from the pool.  It is a model surface, not a dispatcher.
    api.include_router(pool_routes.router)
    # 平台自己的"翻译"：把机器写的文本翻成人话（界面上只显示人话）。
    api.include_router(wording_routes.router)
    app.include_router(api)

    @app.get("/", include_in_schema=False)
    def landing() -> HTMLResponse:
        """The human entry page (static; the UI is the Agent Office on 4174)."""
        return HTMLResponse(_LANDING_HTML)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> Response:
        """Quiet 204 so browsers stop logging a 404 for an icon nobody needs."""
        return Response(status_code=204)

    return app
