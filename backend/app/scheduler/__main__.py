"""Run the sweeper family: ``python -m app.scheduler [--loop] [--interval N]``.

Deliberately not part of ``app.cli``: the operator CLI is HTTP-only by design
— MVP-09's invariant, pinned by ``test_cli_flow`` — while sweeps must apply
transitions in-process, in the same transaction as the condition they read.
So the runner gets its own module entry point instead of widening that
contract (deviation recorded in the sweeper-runner card and the proposal §3).

Output is one JSON object per sweep per round, same spirit as the CLI's
line-oriented JSON. Exit codes: 0 success, 2 refused (missing database or
sweeper disabled for ``--loop``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from app.config import settings
from app.db import Database
from app.git.repo import CanonicalRepo
from app.scheduler.runner import run_all_sweeps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.scheduler", description="run sweeper family")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one round and exit (default)")
    mode.add_argument(
        "--loop", action="store_true", help="keep sweeping on the configured interval"
    )
    parser.add_argument(
        "--interval", type=int, default=None, help="seconds between rounds (with --loop)"
    )
    args = parser.parse_args(argv)

    if args.loop and not settings.sweeper_enabled:
        print("sweeper disabled by settings (AP_SWEEPER_ENABLED=0)", file=sys.stderr)
        return 2

    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return 2

    try:
        repo: CanonicalRepo | None = CanonicalRepo.from_settings()
    except Exception:  # noqa: BLE001 - a missing repo must not kill the loop
        repo = None

    interval = args.interval or settings.sweeper_interval_seconds
    while True:
        for report in run_all_sweeps(database, repo=repo):
            print(json.dumps(report.as_json(), ensure_ascii=False))
        if not args.loop:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
