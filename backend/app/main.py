"""FastAPI 应用入口（MVP-01 骨架）。

本阶段只提供：
- /health：存活 + 配置摘要
- /spec/status：跑 docstate 检查器并返回结果

业务端点在 MVP-02 及之后按需添加。新增端点必须：
1. 路径与 R10 §7 Adapter 接口命名对齐（claim / heartbeat / submit_result /
   publish_task_branch 等），避免 Adapter 层做无意义转换；
2. 在 docs/traceability.md 登记对应 test_id。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# 允许 `python -m app.main` 与 `uvicorn app.main:app` 两种启动方式
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))

from fastapi import FastAPI, HTTPException  # noqa: E402

from app.api.app import create_app  # noqa: E402
from app.config import settings  # noqa: E402

app: FastAPI = create_app()


@app.get("/health", tags=["ops"])
async def health() -> dict[str, Any]:
    """存活探针。刻意不返回任何凭据或路径敏感信息之外的内部状态。"""
    return {
        "status": "ok",
        "service": "agent-platform-mvp",
        "version": app.version,
        "config": settings.summary(),
    }


@app.get("/spec/status", tags=["ops"])
async def spec_status() -> dict[str, Any]:
    """对 R10 规范跑 docstate 机械检查。

    这是 H-2 硬约束的运行时体现：规范与代码的一致性检查必须是可执行的门禁，
    不能靠人工记忆。CI 与本端点共用同一份实现。
    """
    try:
        from docstate.docstate import run  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - 环境问题
        raise HTTPException(
            status_code=500,
            detail=f"docstate 检查器不可用: {exc}。请确认 tools/ 在 sys.path 中。",
        ) from exc

    if not settings.spec_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"规范文件不存在: {settings.spec_path}",
        )

    result = run(str(settings.spec_path))
    return {
        "spec": str(settings.spec_path),
        "total": len(result.checks),
        "passed": len(result.passed),
        "failed": len(result.failed),
        "ok": not result.failed,
        "failures": [c["msg"] for c in result.failed],
    }


def _check_spec_or_exit() -> None:
    """启动时校验规范（R10 §29.3）。失败则拒绝启动，避免带着不自洽的规范 coding。"""
    if not settings.check_spec_on_startup:
        return
    try:
        from docstate.docstate import run  # type: ignore[import-not-found]
    except ImportError:
        print("[warn] docstate 检查器不可用，跳过启动校验", file=sys.stderr)
        return
    if not settings.spec_path.exists():
        print(f"[warn] 规范文件不存在，跳过启动校验: {settings.spec_path}", file=sys.stderr)
        return

    result = run(str(settings.spec_path))
    if result.failed:
        print(
            f"[error] 规范一致性检查失败 {len(result.failed)}/{len(result.checks)} 项：",
            file=sys.stderr,
        )
        for c in result.failed:
            print(f"  - {c['msg']}", file=sys.stderr)
        print("拒绝启动。修复规范或设置 AP_CHECK_SPEC=0 临时跳过。", file=sys.stderr)
        raise SystemExit(1)

    print(f"[ok] 规范一致性检查通过 {len(result.checks)} 项")


@app.on_event("startup")
async def on_startup() -> None:
    settings.ensure_dirs()
    _check_spec_or_exit()


def main() -> None:
    import uvicorn

    settings.ensure_dirs()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,  # reload 会重复触发 spec 检查，MVP 阶段关闭
    )


if __name__ == "__main__":
    main()
