"""Run the liveness watch: ``python -m app.liveness [--once|--check|--loop]``.

``--check`` is read-only (no alert enqueueing); ``--once`` scans and enqueues
``run.no_commit`` alarms into the alert queue (delivery happens when
``python -m app.alerts`` runs). Both exit 1 when any task reads ``critical``,
so an ops cron can page off the exit code alone.

Exit codes: 0 success / 1 critical readings / 2 refused (no database, or
schema behind for ``--once``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.alerts.service import AlertNotifier
from app.db import Database
from app.liveness.service import LivenessWatch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.liveness", description="liveness watch")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="scan + enqueue alarms, then exit")
    mode.add_argument("--check", action="store_true", help="read-only scan, no alarm queueing")
    mode.add_argument("--loop", action="store_true", help="keep scanning on the interval")
    parser.add_argument(
        "--interval", type=int, default=60, help="seconds between rounds (with --loop)"
    )
    args = parser.parse_args(argv)

    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return 2

    enqueue = not args.check
    if enqueue:
        schema_ready = database.read(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alert_deliveries'"
        )
        if not schema_ready:
            print(
                "schema is behind (alert_deliveries missing); run `python -m app.db` first",
                file=sys.stderr,
            )
            return 2

    notifier = AlertNotifier(database) if enqueue else None
    watch = LivenessWatch(database, notifier=notifier)
    while True:
        report = watch.run_once(enqueue=enqueue)
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if report.critical > 0:
            print(f"{report.critical} critical reading(s)", file=sys.stderr)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
