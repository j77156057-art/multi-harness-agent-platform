"""Run the budget guard: ``python -m app.budget [--check|--once|--loop|
--settle <attempt> --tokens N|--release <attempt>|--resolve <task>]``.

Same shape as ``python -m app.liveness``: module entry, one JSON line per run,
ops-facing exit codes.

* ``--check``  read-only overview (no reconcile, no queueing); exit 1 when any
  incident is open -- an ops cron can page on the exit code alone.
* ``--once``   scan: reconcile stale holds, raise/auto-resolve incidents, and
  enqueue alerts for open incidents (delivery happens when
  ``python -m app.alerts`` runs). ``--loop`` repeats on the interval.
* ``--settle`` / ``--release`` / ``--resolve``  human correction verbs: settle
  an unreported hold with a known actual, release a hold proven to have done
  no provider work, close a task's open incidents (history kept).

Exit codes: 0 success / 1 open incidents (``--check``) / 2 refused (no
database, schema behind, or verb argument invalid).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.alerts.service import AlertNotifier
from app.budget.service import BudgetGuard
from app.db import Database


def _schema_ready(database: Database, *, need_alerts: bool) -> bool:
    tables = {"budget_reservations"}
    if need_alerts:
        tables.add("alert_deliveries")
    for table in sorted(tables):
        if not database.read(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ):
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.budget", description="token budget guard")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="read-only overview")
    mode.add_argument("--once", action="store_true", help="scan + enqueue alerts, then exit")
    mode.add_argument("--loop", action="store_true", help="keep scanning on the interval")
    mode.add_argument("--settle", metavar="ATTEMPT", help="settle a hold with a known actual")
    mode.add_argument("--release", metavar="ATTEMPT", help="release a hold (no provider work)")
    mode.add_argument("--resolve", metavar="TASK", help="close a task's open incidents")
    parser.add_argument("--tokens", type=int, help="actual tokens (with --settle)")
    parser.add_argument("--note", help="note recorded on the row/incident")
    parser.add_argument(
        "--interval", type=int, default=60, help="seconds between rounds (with --loop)"
    )
    args = parser.parse_args(argv)

    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return 2

    scan_mode = not (args.check or args.settle or args.release or args.resolve)
    needs_alerts = scan_mode
    if not _schema_ready(database, need_alerts=needs_alerts):
        print(
            "schema is behind (budget tables / alert queue missing);"
            " run `python -m app.db` first",
            file=sys.stderr,
        )
        return 2

    if args.settle is not None:
        if args.tokens is None or args.tokens < 0:
            print("--settle needs --tokens >= 0", file=sys.stderr)
            return 2
        guard = BudgetGuard(database)
        if not guard.settle(args.settle, args.tokens, note=args.note):
            print(f"no reservation to settle for {args.settle}", file=sys.stderr)
            return 2
        print(json.dumps({"settled": args.settle, "tokens": args.tokens}, ensure_ascii=False))
        return 0

    if args.release is not None:
        guard = BudgetGuard(database)
        note = args.note or "released by cli"
        if not guard.release(args.release, note):
            print(f"no held reservation to release for {args.release}", file=sys.stderr)
            return 2
        print(json.dumps({"released": args.release}, ensure_ascii=False))
        return 0

    if args.resolve is not None:
        guard = BudgetGuard(database)
        resolved = guard.resolve(args.resolve, args.note or "resolved by cli")
        print(json.dumps({"resolved": resolved}, ensure_ascii=False))
        return 0

    guard = BudgetGuard(database)
    if args.check:
        rows = [row.as_json() for row in guard.overview()]
        open_incidents = sum(len(row["open_incidents"]) for row in rows)
        print(json.dumps({"tasks": rows, "open_incidents": open_incidents}, ensure_ascii=False))
        if open_incidents > 0:
            print(f"{open_incidents} open incident(s)", file=sys.stderr)
            return 1
        return 0

    guard = BudgetGuard(database, notifier=AlertNotifier(database))
    while True:
        report = guard.scan(enqueue=True)
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if args.loop:
            time.sleep(args.interval)
            continue
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
