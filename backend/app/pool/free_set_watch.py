"""The free-set watch (``pool-free-set-watch``): tell a human when the free
tier's roster changed.

The free tier is volatile -- free credits expire, models get retired, new
seeds appear.  The pool's catalogue already tracks all of that dynamically
(``PoolService.models()`` with ``tier`` labels); what it never does is *wake
anyone up*.  This module does exactly one thing: snapshot the set of free
model ids, diff it against the last snapshot, and queue an alert through the
platform's own alert table when the set changed -- the delivery is elsewhere
(``python -m app.alerts``), the same split the budget guard uses.

Design points, pinned:

* **Passive, not probing.**  The check reads the pool's in-process catalogue;
  it never sends a model request to verify a model still works.  A probe to
  a CLI slot *is* a real invocation, and burning free-quota to check on
  free-quota is how the free tier dies.
* **Idempotent by content.**  The dedupe key is the hash of the current set
  (``UNIQUE(kind, source_key)`` in the alerts table) -- the same roster seen
  again never re-pages anyone.  A *flapping* set (A → B → A) does re-page:
  each state genuinely deserves a look, and B was already reported.
* **First run establishes a baseline.**  No snapshot on disk yet means no
  prior known state, so there is nothing to diff -- write the baseline and
  say so, honestly, instead of reporting "everything changed".

Run as ``python -m app.pool.free_set_watch [--once|--loop]``.  No pool HTTP
route, no new table, no task state touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import settings

ALERT_KIND = "pool.free_set_changed"

#: The snapshot lives under the data dir so every harness/operator sees the
#: same baseline; the path is injectable for tests.
DEFAULT_SNAPSHOT_PATH = Path(settings.data_dir) / "pool" / "free-set.json"


class _Snapshot(Protocol):
    """The slice of PoolService this check needs (duck type)."""

    def models(self, *, refresh: bool = False) -> list[dict[str, Any]]: ...


class Enqueuer(Protocol):
    """The alerts notifier's enqueue face (duck type, budget-guard style)."""

    def enqueue(
        self,
        *,
        kind: str,
        payload: dict[str, Any],
        source_key: str,
        task_id: str | None = None,
        source_seq: int | None = None,
    ) -> bool: ...


def free_set(pool: _Snapshot, *, refresh: bool = False) -> list[str]:
    """The sorted free-tier model ids, right now, from the live catalogue."""
    names = sorted(
        str(entry["id"])
        for entry in pool.models(refresh=refresh)
        if isinstance(entry, dict) and entry.get("tier") == "free" and entry.get("id")
    )
    return names


def _fingerprint(names: list[str]) -> str:
    joined = "\n".join(names)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass
class WatchReport:
    """One check round, honest about first-run and unchanged days."""

    changed: bool
    first_snapshot: bool
    previous: list[str] | None
    current: list[str]
    fingerprint: str
    alerts_enqueued: int = field(default=0)

    def as_json(self) -> dict[str, Any]:
        return {
            "sweep": "free_set_watch",
            "changed": self.changed,
            "first_snapshot": self.first_snapshot,
            "previous": self.previous,
            "current": self.current,
            "fingerprint": self.fingerprint,
            "alerts_enqueued": self.alerts_enqueued,
        }


def _load_snapshot(path: Path) -> list[str] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        # A half-written or corrupt snapshot is a lost baseline, not a diff
        # trigger: rebuilding it counts as first-run again.
        return None
    names = data.get("names") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return None
    return [str(name) for name in names]


def check(
    pool: _Snapshot,
    enqueuer: Enqueuer,
    *,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
    refresh: bool = False,
) -> WatchReport:
    """Snapshot the free roster, diff against the baseline, alert on change.

    The alert's dedupe key is the *current* roster's fingerprint: re-running
    on an unchanged roster (loop mode) enqueues nothing, and a change seen
    from a restart still pages exactly once.
    """
    current = free_set(pool, refresh=refresh)
    fingerprint = _fingerprint(current)
    previous = _load_snapshot(snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    report = WatchReport(
        changed=False,
        first_snapshot=False,
        previous=previous,
        current=current,
        fingerprint=fingerprint,
    )
    if previous is None:
        report.first_snapshot = True
        snapshot_path.write_text(
            json.dumps({"names": current, "fingerprint": fingerprint}, ensure_ascii=False),
            encoding="utf-8",
        )
        return report
    if _fingerprint(previous) == fingerprint:
        return report
    report.changed = True
    if enqueuer.enqueue(
        kind=ALERT_KIND,
        payload={
            "previous": previous,
            "current": current,
            "added": [name for name in current if name not in previous],
            "removed": [name for name in previous if name not in current],
        },
        source_key=fingerprint,
    ):
        report.alerts_enqueued = 1
    snapshot_path.write_text(
        json.dumps({"names": current, "fingerprint": fingerprint}, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    """``python -m app.pool.free_set_watch [--once|--loop]``.

    ``--once`` (default) checks and exits 0; ``--loop`` repeats on the
    interval.  Everything refused-shape (no database, schema behind) exits 2
    like the budget runner.  Delivery is *not* this process's job.
    """
    from app.alerts.service import AlertNotifier
    from app.db import Database
    from app.pool.service import PoolService

    parser = argparse.ArgumentParser(
        prog="app.pool.free_set_watch", description="free-tier roster change watch"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one check and exit (default)")
    mode.add_argument("--loop", action="store_true", help="keep checking on the interval")
    parser.add_argument("--interval", type=int, default=300, help="seconds (with --loop)")
    args = parser.parse_args(argv)

    database = Database()
    if not database.path.exists():
        print(f"no database at {database.path}; run `python -m app.db` first", file=sys.stderr)
        return 2
    if not database.read(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alert_deliveries'"
    ):
        print(
            "schema is behind (alert queue missing); run `python -m app.db` first", file=sys.stderr
        )
        return 2

    notifier = AlertNotifier(database)
    pool = PoolService()
    while True:
        report = check(pool, notifier)
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if args.loop:
            time.sleep(args.interval)
            continue
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
