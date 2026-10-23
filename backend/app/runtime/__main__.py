"""Run the runtime driver: ``python -m app.runtime [--check|--once|--loop]``.

Same module-entry-point pattern as ``app.scheduler`` (and for the same reason:
the operator CLI is HTTP-only by design, while the runtime must call the
workers in-process). Output is one JSON object per round, same line-oriented
spirit as the other console tools.

``--recheck-health <project_id>`` is the operator-side way out of a
stop-the-line merge queue: it runs the configured integration profile at the
canonical tip and, with ``--apply``, puts the project's health projection back
to green **only** if that check passed.

Exit codes: 0 a round (or a check) completed; 1 the integration recheck ran and
failed; 2 refused -- no database, or the
policy file is missing/unusable, which is a refusal because executing without
a human-maintained policy is exactly what R10 §16 forbids.
"""

from __future__ import annotations

import argparse
import json
import time

from app.config import settings
from app.db import Database
from app.git.repo import CanonicalRepo
from app.merge.worker import MergeError
from app.runtime.driver import RuntimeDriver, RuntimePolicyError, load_runtime_policy


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.runtime", description="run verify/merge runtime")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true", help="read-only: policy state and the scan, then exit"
    )
    mode.add_argument("--once", action="store_true", help="run one round and exit (default)")
    mode.add_argument("--loop", action="store_true", help="keep running on the configured interval")
    mode.add_argument(
        "--recheck-health",
        metavar="PROJECT_ID",
        help=(
            "re-run the integration profile at the canonical tip for one project;"
            " read-only unless --apply"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="with --recheck-health: write the recovered health row when the check passes",
    )
    parser.add_argument(
        "--interval", type=int, default=None, help="seconds between rounds (with --loop)"
    )
    args = parser.parse_args(argv)

    repo = CanonicalRepo.from_settings()
    try:
        policy = load_runtime_policy(settings.runtime_policy_file, repo=repo)
    except RuntimePolicyError as exc:
        _emit({"refused": str(exc)})
        return 2

    database = Database()
    if not database.path.exists():
        _emit({"refused": f"no database at {database.path}; run `python -m app.db` first"})
        return 2

    driver = RuntimeDriver(database, repo, policy)

    if args.recheck_health:
        try:
            outcome = driver.recheck_integration_health(args.recheck_health, apply=args.apply)
        except MergeError as exc:
            _emit({"recheck": "error", "project_id": args.recheck_health, "detail": str(exc)})
            return 2
        _emit({"recheck": outcome.as_json()})
        return 0 if outcome.result == "passed" else 1

    if args.check:
        _emit(
            {
                "policy": {
                    "source": str(policy.source) if policy.source is not None else None,
                    "repo_source": policy.repo_source,
                    "version": policy.verify.version,
                    "fingerprint": policy.verify.fingerprint(),
                    "profiles": sorted(policy.verify.profiles),
                    "integration_profile": policy.effective_integration_profile(),
                    "concurrent_verify": driver.concurrent_verify,
                },
                "scan": driver.scan().as_json(),
            }
        )
        return 0

    interval = args.interval or settings.runtime_interval_seconds
    while True:
        _emit(driver.run_once().as_json())
        if not args.loop:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
