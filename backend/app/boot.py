"""启动阶段的检查，由 ``create_app()`` 的 lifespan 直接调用。

为什么单独一个模块：``app.main`` 已经从 ``app.api.app`` 导入 ``create_app``，
反向 import 会成环。检查只依赖 ``app.config``，放在这里两边都能用。

为什么不能退回事件钩子：``create_app()`` 传入了自定义 ``lifespan``，Starlette
在这种情况**不会**执行 ``@app.on_event("startup")``。规范校验历史上就挂在那上面，
从上线到 2026-11 一次都没有真正跑过（启动日志里既没有通过行，也没有失败行）。
"""

from __future__ import annotations

import sys

from app.config import settings


def check_spec_on_startup() -> None:
    """校验 R10 正文与代码侧镜像是否一致（R10 §29.3）。

    默认只告警，服务继续启动：这个守卫从未真正执行过，直接硬退出很可能把一个
    本来能用的启动变成起不来。设 ``AP_SPEC_STRICT=1`` 才会拒绝启动。

    返回即表示允许继续启动；严格模式下不一致会抛 ``SystemExit(1)``。
    """
    if not settings.check_spec_on_startup:
        print(
            "[warn] 规范启动校验已关闭（AP_CHECK_SPEC=0），跳过",
            file=sys.stderr,
            flush=True,
        )
        return

    try:
        from docstate.docstate import run  # type: ignore[import-not-found]
    except ImportError:
        print(
            "[warn] docstate 检查器不可用，跳过规范启动校验（tools/ 不在 import 路径中？）",
            file=sys.stderr,
            flush=True,
        )
        return

    if not settings.spec_path.exists():
        _report(
            [f"规范文件不存在：{settings.spec_path}"],
            total=0,
            fix="把 AP_SPEC_PATH 指向实际的 R10 文件，或恢复 docs/spec/0.3-R10.md。",
        )
        return

    try:
        result = run(str(settings.spec_path))
    except Exception as exc:  # noqa: BLE001 - 检查器坏掉不能拖垮默认启动
        _report(
            [f"docstate 无法解析规范：{type(exc).__name__}: {exc}"],
            total=0,
            fix="确认 spec 文件完整、章节标题未被改动；也可先设 AP_CHECK_SPEC=0 跳过。",
        )
        return

    if not result.failed:
        print(f"[ok] 规范一致性检查通过 {len(result.checks)} 项", flush=True)
        return

    _report([c["msg"] for c in result.failed], total=len(result.checks))


def _report(failures: list[str], *, total: int, fix: str | None = None) -> None:
    """打印不一致项；严格模式下抛 ``SystemExit(1)``。"""
    strict = settings.spec_strict_on_startup
    tag = "[error]" if strict else "[warn]"
    head = f"{tag} 规范一致性检查失败 {len(failures)}/{total} 项"
    print(f"{head}（{settings.spec_path}）", file=sys.stderr, flush=True)
    for msg in failures:
        print(f"  - {msg}", file=sys.stderr, flush=True)
    print(
        "  影响：代码侧镜像（backend/app/spec.py 等）与 R10 正文口径不一致，"
        "状态机与事件名可能按错误的口径运行。",
        file=sys.stderr,
    )
    print(
        "  怎么修："
        + (fix or "按上面每条改 R10 正文或代码镜像，两边必须落在同一次提交；只改一侧会持续失败。"),
        file=sys.stderr,
    )
    if strict:
        print("  已设置 AP_SPEC_STRICT=1，拒绝启动。", file=sys.stderr, flush=True)
        raise SystemExit(1)
    print("  当前为告警模式，服务继续启动。", file=sys.stderr)
    print("  要让它拦下启动：设置 AP_SPEC_STRICT=1 后重启。", file=sys.stderr, flush=True)
