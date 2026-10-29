"""窗口的**在场**事实（进程还在不在跑）。

登记表里的 ``status`` 是**注册那一刻**写下的声明：窗口自己报「我上线了」，
平台把它记下来。进程被关掉，它不会变 —— 于是「N 个窗口在线」可能一个都没在
跑（2026-10-29 的真实演练：注册表 13 个，在跑的 worker 只有 7 个）。

这两件事必须分开：

* **注册状态**（登记面）：这个窗口存在，并且当初声明能干活。
* **在场**（观察面）：这个窗口的**进程现在还活着**。

worker 定期来说一声「我还在」（``POST /adapter/presence``）；超过窗口没来，
就当它不在线。这里的窗口给得比心跳宽，是为了容忍一次心跳的抖动，
又不至于让一个被杀掉的进程在界面上一直算「在线」。
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

from app.db.connection import Database

#: 超过这个时间没来报，就当它不在线了。
#:
#: 心跳是 20 秒一次；90 秒 ≈ 允许连丢 4 次（一次抖动不算掉线）。
PRESENCE_FRESH_SECONDS = 90.0

#: worker 多久报一次「我还在」。
PRESENCE_ANNOUNCE_SECONDS = 20.0


def _parse(stamp: str) -> datetime | None:
    try:
        at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=UTC)


def record_presence(
    database: Database, harness_id: str, *, role: str | None = None
) -> dict[str, Any]:
    """记下「这个窗口的进程还在跑」。

    ``started_at`` 是**这一次进程**开始的时刻：进程重启后重新报，起点也跟着
    换 —— 不然「跑了多久」会把两次进程的时间加在一起。
    """
    harness = str(harness_id or "").strip()
    if not harness:
        raise ValueError("harness_id is required")
    now = datetime.now(UTC)
    with database.transaction() as conn:
        row = conn.execute(
            "SELECT started_at FROM harness_presence WHERE harness_id = ?", (harness,)
        ).fetchone()
        started = now.isoformat()
        if row is not None and row["started_at"]:
            # 同一个进程在续报：起点不变（重启会换掉它，见下）。
            started = str(row["started_at"])
        conn.execute(
            "INSERT INTO harness_presence(harness_id, role, started_at, seen_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT(harness_id) DO UPDATE SET role = excluded.role,"
            " seen_at = excluded.seen_at",
            (harness, str(role or ""), started, now.isoformat()),
        )
    return {
        "harness_id": harness,
        "role": str(role or ""),
        "started_at": started,
        "seen_at": now.isoformat(),
    }


def presence_snapshot(
    database: Database,
    *,
    now: datetime | None = None,
    fresh_seconds: float = PRESENCE_FRESH_SECONDS,
) -> dict[str, dict[str, Any]]:
    """每个窗口现在在不在跑。

    读不到表（迁移没跑）时返回空表 —— 界面据此说「没有窗口在跑」，
    而不是假装全都在线。
    """
    moment = now or datetime.now(UTC)
    try:
        rows = database.read(
            "SELECT harness_id, role, started_at, seen_at FROM harness_presence"
        )
    except Exception:  # pragma: no cover - 表还没建时不该把整个界面拖垮
        return {}
    snapshot: dict[str, dict[str, Any]] = {}
    for row in rows:
        harness = str(row["harness_id"] or "")
        if not harness:
            continue
        seen_at = str(row["seen_at"] or "")
        at = _parse(seen_at)
        age = None if at is None else (moment - at).total_seconds()
        snapshot[harness] = {
            "role": str(row["role"] or ""),
            "started_at": str(row["started_at"] or ""),
            "seen_at": seen_at,
            "age_seconds": age,
            "live": age is not None and age <= fresh_seconds,
        }
    return snapshot


def announce(post: Any, *, role: str = "") -> bool:
    """worker 报一声「我还在」。

    ``post`` 是「把这块 body 发出去」的 callable —— 各 worker 的客户端形状
    不一样（httpx 与 PlatformClient 的路径前缀、入参方式都不同），这里不替
    它们统一，只管发。

    报不上不算错：presence 只是观察面，它挂掉不该让 worker 停下来。
    返回是否报上了。
    """
    try:
        post({"role": str(role or "")})
    except Exception:  # noqa: BLE001 - 观察面不该拖垮干活的那条线
        return False
    return True


def start_heartbeat(
    post: Any, *, role: str = "", every_seconds: float = PRESENCE_ANNOUNCE_SECONDS
) -> threading.Thread:
    """起一个后台心跳线程，worker 一直报「我还在」。

    为什么不能挂在干活循环上：成员跑一条 CLI 可能要好几分钟（模型在写文件、
    在提交），主循环那时候腾不出手来报心跳 —— 界面就会把一个**正在干活**的
    窗口说成「没在跑」（2026-10-29 实测：opencode 正在执行，窗口列表里却写着
    「进程没在跑」，而它的进程明明活着）。

    所以心跳走自己的线程：干活那条线卡住多久，它照样每 20 秒报一次。
    """
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(every_seconds):
            announce(post, role=role)

    thread = threading.Thread(target=loop, name="presence-heartbeat", daemon=True)
    thread.start()
    return thread
