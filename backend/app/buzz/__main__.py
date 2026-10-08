"""运行 Buzz 桥：``python -m app.buzz [--check|--once|--loop]``。

与 ``python -m app.alerts`` 同形：模块入口、每轮一行 JSON、面向运维的退出码。

退出码：0 成功（或三项全空的显式 no-op，不连 WS）；1 本轮的发布失败（relay
不可达/拒绝）——游标不动，下一轮同批重试；2 拒绝（无数据库/库落后/配置只配了
一半/私钥文件缺失或非法）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.buzz.bridge import BuzzBridge, BuzzConfigError
from app.config import settings
from app.db import Database


def _open_database() -> Database | None:
    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return None
    schema_ready = database.read(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'buzz_cursors'"
    )
    if not schema_ready:
        print(
            "schema is behind (buzz_cursors missing); run `python -m app.db` first", file=sys.stderr
        )
        return None
    return database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.buzz", description="one-way platform-event bridge to a Buzz (Nostr) channel"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="config + cursor + next candidates, then exit"
    )
    mode.add_argument("--once", action="store_true", help="one publish run and exit (default)")
    mode.add_argument("--loop", action="store_true", help="keep publishing on the interval")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="seconds between rounds (with --loop; default AP_BUZZ_INTERVAL)",
    )
    args = parser.parse_args(argv)

    database = _open_database()
    if database is None:
        return 2
    bridge = BuzzBridge(database)

    if args.check:
        try:
            ok, payload = bridge.check()
        except BuzzConfigError as exc:
            ok, payload = False, {"configured": False, "ok": False, "reason": str(exc)}
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if ok else 2

    interval = int(args.interval) if args.interval is not None else settings.buzz_interval_seconds
    while True:
        try:
            report = bridge.run_once()
        except BuzzConfigError as exc:
            print(f"buzz bridge refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if report.failed > 0 and not args.loop:
            print(f"{report.failed} event(s) not published; cursor stays put", file=sys.stderr)
            return 1
        if not args.loop:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
