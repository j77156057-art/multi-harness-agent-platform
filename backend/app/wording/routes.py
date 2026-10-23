"""翻译服务的人机面：把机器文本翻成人话。

两条路由：

* ``POST /api/v1/wording/translate`` —— 规则层，批量、确定、永远可用；
* ``POST /api/v1/wording/summarize`` —— 模型层，自由文本翻成一句人话（带缓存）。

权限：只给**人**（``wording:read``）。让机器自己给自己的失败日志翻成好话，
那是给日志化妆，不是翻译。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from app.api.auth_deps import Principal, require_for
from app.api.errors import ApiError
from app.db.connection import Database
from app.wording import service

router = APIRouter(tags=["wording"])

_guard = Annotated[Principal | None, Depends(require_for("wording:read"))]


def _database(request: Request) -> Database:
    """取应用里的数据库（连接注册表用）。"""
    return request.app.state.database


_DatabaseDep = Annotated[Database, Depends(_database)]


def _harness_names(database: Database) -> dict[str, str]:
    """登记名表：窗口 id → 它在平台上登记的名字。

    平台自己知道每个窗口叫什么，所以"翻译"这件事该由平台做 ——
    前端拿不到注册表，就只能自己猜或者印 id。
    """
    try:
        rows = database.read("SELECT harness_id, name FROM harnesses")
    except Exception:  # pragma: no cover - 注册表读不到就不翻这一类
        return {}
    return {str(row["harness_id"]): str(row["name"] or row["harness_id"]) for row in rows}


@router.post("/wording/translate")
def translate_texts(
    payload: dict[str, Any],
    principal: _guard,
    database: _DatabaseDep,
) -> dict[str, Any]:
    """一批机器文本 → 人话。认不出的原样返回（``translated: false``）。"""
    texts = payload.get("texts")
    if texts is None:
        texts = [payload.get("text") or ""]
    if not isinstance(texts, list) or len(texts) > 200:
        raise ApiError("request.invalid", 400, "texts must be a list of at most 200 items")
    kind = str(payload.get("kind") or "auto")
    if kind not in {"auto", "reason", "event", "status", "harness", "path", "id", "prose"}:
        raise ApiError("request.invalid", 400, f"unknown kind {kind!r}")
    names = _harness_names(database)
    return {"texts": service.translate_many([str(item) for item in texts], kind=kind, names=names)}


@router.post("/wording/summarize")
def summarize_text(
    payload: dict[str, Any],
    principal: _guard,
    request: Request,
) -> dict[str, Any]:
    """自由文本（方案原文、规格）→ 一句人话。

    走**管理员自己的模型通道**（管理员窗口的那台 CLI）：翻译要用的是那台 CLI，
    别的通道说的不是同一件事。翻不出来就报明确原因 —— 不给半句假话。
    """
    from app.admin.service import AdminHarnessError, AdminService

    text = str(payload.get("text") or "").strip()
    if not text:
        raise ApiError("request.invalid", 400, "text must not be empty")
    max_chars = int(payload.get("max_chars") or 60)
    if max_chars < 10 or max_chars > 400:
        raise ApiError("request.invalid", 400, "max_chars must be between 10 and 400")
    admin_id = _admin_identity(request)
    if not admin_id:
        raise ApiError("wording.no_admin_channel", 409, "no administrator channel is configured")
    admin = AdminService(request.app.state.database, harness_id=admin_id)
    try:
        result = service.summarize(text, invoke_model=admin.invoke_model, max_chars=max_chars)
    except AdminHarnessError as error:
        raise ApiError("wording.channel_unavailable", 409, str(error)) from error
    except ValueError as error:
        raise ApiError("wording.no_summary", 502, str(error)) from error
    return result


def _admin_identity(request: Request) -> str | None:
    """管理员是谁：平台的 ``admin_subjects`` 里第一个（那是部署时定的）。"""
    from app.config import settings

    subjects = list(getattr(settings, "admin_subjects", ()) or ())
    return subjects[0] if subjects else None
