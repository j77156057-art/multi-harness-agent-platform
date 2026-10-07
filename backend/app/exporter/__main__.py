"""Run the JSONL exporter: ``python -m app.exporter [--once|--check|--loop]``.

Same shape as ``python -m app.scheduler`` (module entry, JSON lines, exit
codes), because this is an ops tool, not part of the HTTP-only operator CLI.
The spec's "重启和巡检比较 max(seq) 与 exported_seq，自动补导并告警" maps onto:
every run backfills from the watermark, and the alarm surface is the
``jsonl_exports`` row plus this process's stderr and exit codes.

Exit codes: 0 success, 1 failed run / reconciliation drift, 2 refused
(no database).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.db import Database
from app.exporter.service import JsonlExporter


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.exporter", description="SQLite -> JSONL export")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="export one batch and exit (default)")
    mode.add_argument("--check", action="store_true", help="reconcile file vs database, no writes")
    mode.add_argument(
        "--loop", action="store_true", help="keep exporting on the configured interval"
    )
    parser.add_argument(
        "--interval", type=int, default=60, help="seconds between rounds (with --loop)"
    )
    args = parser.parse_args(argv)

    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return 2
    schema_ready = database.read(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jsonl_exports'"
    )
    if not schema_ready:
        # Same convention as the rest of the ops surface: migrations run via
        # `python -m app.db`, not as a side effect of whatever command you ran.
        print(
            "schema is behind (jsonl_exports missing); run `python -m app.db` first",
            file=sys.stderr,
        )
        return 2
    exporter = JsonlExporter(database)

    if args.check:
        report = exporter.reconcile()
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if not report.ok:
            print(f"reconcile drift: {'; '.join(report.problems)}", file=sys.stderr)
            return 1
        return 0

    while True:
        report = exporter.export_once()
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if report.failed:
            print(f"export failed: {report.detail}", file=sys.stderr)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
