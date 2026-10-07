"""Run the alert notifier: ``python -m app.alerts [--once|--loop]``.

Same shape as ``python -m app.exporter``: module entry, one JSON line per run,
ops-facing exit codes. Each ``--loop`` round detects new escalation events and
attempts every pending delivery once; the loop interval (or an external cron)
is the retry cadence.

Exit codes: 0 success (or explicitly unconfigured no-op), 1 at least one
delivery attempt failed this run, 2 refused (no database / schema behind /
invalid webhook configuration).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.alerts.service import AlertConfigError, AlertNotifier
from app.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.alerts", description="outbound alert webhook")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one run and exit (default)")
    mode.add_argument("--loop", action="store_true", help="keep running on the interval")
    parser.add_argument(
        "--interval", type=int, default=60, help="seconds between rounds (with --loop)"
    )
    args = parser.parse_args(argv)

    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return 2
    schema_ready = database.read(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alert_deliveries'"
    )
    if not schema_ready:
        print(
            "schema is behind (alert_deliveries missing); run `python -m app.db` first",
            file=sys.stderr,
        )
        return 2

    notifier = AlertNotifier(database)
    while True:
        try:
            report = notifier.run_once()
        except AlertConfigError as exc:
            print(f"alert webhook refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if report.failed > 0 and not args.loop:
            print(f"{report.failed} delivery attempt(s) failed", file=sys.stderr)
            return 1
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
