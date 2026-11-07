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

import shutil
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


def _check_git_or_warn() -> None:
    """启动时检查 git 是否可用。

    领取任务要读 canonical 仓库的 integration HEAD，每次 attempt 也都要跑 git。
    没有 git 时平台仍能通过 /health，但每一次领取都会以 claim.git_missing 失败。
    在启动日志里先说清楚，胜过等服务跑起来、派活时才炸。
    """
    if shutil.which("git") is not None:
        # flush: 后端常以 `.\start.bat` 起在重定向的窗口/日志里，stdout 是块缓冲，
        # 不 flush 的话这行要等进程退出才出现，等于没有启动检查。
        print("[ok] git found on PATH", flush=True)
        return
    print("=" * 72, file=sys.stderr)
    print("[error] 找不到 git：领取任务会直接失败（claim.git_missing）。", file=sys.stderr)
    print("  1) 安装 Git for Windows：https://git-scm.com/download/win", file=sys.stderr)
    print("  2) 装完重开终端，确认 `git --version` 能输出版本号，再重启后端。", file=sys.stderr)
    print("  3) 已装但不在 PATH：把 Git 的 cmd 目录加进 PATH，", file=sys.stderr)
    print(r"     例如 C:\Program Files\Git\cmd，然后重启后端。", file=sys.stderr)
    print("=" * 72, file=sys.stderr)


def main() -> None:
    import uvicorn

    settings.ensure_dirs()
    # 起在 uvicorn.run 之前：`create_app()` 自带 lifespan，`@app.on_event("startup")`
    # 在那种情况下不会执行（Starlette 只用传入的 lifespan），把检查放在这里才能
    # 保证 `python -m app.main` 一定打印出来。
    _check_git_or_warn()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,  # reload 会重复触发 spec 检查，MVP 阶段关闭
    )


if __name__ == "__main__":
    main()
