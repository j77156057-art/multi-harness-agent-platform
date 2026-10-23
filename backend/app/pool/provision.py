"""Read-only discovery of harnesses that may need a pool declaration."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.adapter.harnesses.generic import load_declarations
from app.config import settings
from app.pool.sources import DEFAULT_CLI_SLOTS, pool_config_path

ALERT_KIND = "pool.provision_candidates"
DEFAULT_SNAPSHOT_PATH = Path(settings.data_dir) / "pool" / "provision-candidates.json"


class Enqueuer(Protocol):
    def enqueue(
        self, *, kind: str, payload: dict[str, Any], source_key: str, **kwargs: Any
    ) -> bool: ...


@dataclass
class ProvisionReport:
    changed: bool
    first_snapshot: bool
    previous: list[dict[str, Any]] | None
    current: list[dict[str, Any]]
    fingerprint: str
    alerts_enqueued: int = field(default=0)

    def as_json(self) -> dict[str, Any]:
        return {
            "sweep": "pool_self_provision",
            "changed": self.changed,
            "first_snapshot": self.first_snapshot,
            "previous": self.previous,
            "current": self.current,
            "fingerprint": self.fingerprint,
            "alerts_enqueued": self.alerts_enqueued,
        }


def _slot_key(slot: dict[str, Any]) -> tuple[str, str]:
    return (str(slot.get("name") or "cli-slot"), str(slot.get("model") or "").strip())


def _merged_slots(declared: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {_slot_key(slot): dict(slot) for slot in DEFAULT_CLI_SLOTS}
    order = list(merged)
    for slot in declared:
        key = _slot_key(slot)
        if slot.get("enabled") is False:
            if key[1]:
                merged.pop(key, None)
            else:
                for existing in [item for item in merged if item[0] == key[0]]:
                    merged.pop(existing, None)
            continue
        if key not in merged:
            order.append(key)
        merged[key] = dict(slot)
    return [merged[key] for key in order if key in merged]


def _coverage_tokens(slots: list[dict[str, Any]], declarations: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for slot in slots:
        for key in ("name", "binary", "store"):
            value = str(slot.get(key) or "").strip().lower()
            if value:
                tokens.add(value)
    # A harness CLI declaration is not a pool quota declaration. Keep it as
    # evidence, but do not let it suppress a candidate without a matching slot.
    return tokens


def _is_covered(row: dict[str, Any], tokens: set[str]) -> bool:
    values = {
        str(row.get("provider") or "").strip().lower(),
        str(row.get("adapter") or "").strip().lower(),
    }
    values.discard("")
    return bool(values & tokens)


def candidates(
    registrations: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    declarations: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic, non-authorizing candidates with source evidence."""
    declarations = declarations or {}
    merged = _merged_slots(slots)
    tokens = _coverage_tokens(merged, declarations)
    found: list[dict[str, Any]] = []
    seen_harnesses: set[str] = set()
    for row in sorted(registrations, key=lambda item: str(item.get("harness_id") or "")):
        harness_id = str(row.get("harness_id") or "").strip()
        if not harness_id or harness_id in seen_harnesses or _is_covered(row, tokens):
            continue
        seen_harnesses.add(harness_id)
        found.append(
            {
                "kind": "uncovered_harness",
                "harness_id": harness_id,
                "provider": str(row.get("provider") or ""),
                "adapter": str(row.get("adapter") or ""),
                "tier": "unknown",
                "reason": "registered harness has no matching pool slot or CLI declaration",
                "evidence": {
                    "source": "harnesses registry",
                    "registration_version": str(row.get("registration_version") or ""),
                    "status": str(row.get("status") or ""),
                },
            }
        )
    for slot in merged:
        if not str(slot.get("model") or "").strip():
            continue
        if str(slot.get("tier") or "").strip().lower() in {"free", "reserve", "metered"}:
            continue
        found.append(
            {
                "kind": "tier_missing",
                "slot": str(slot.get("name") or "cli-slot"),
                "model": str(slot.get("model") or ""),
                "tier": "unknown",
                "reason": "declared slot has no explicit free/reserve/metered tier",
                "evidence": {"source": "pool cli_slots declaration"},
            }
        )
    return sorted(
        found,
        key=lambda item: (
            str(item["kind"]),
            str(item.get("harness_id", item.get("slot", ""))),
        ),
    )


def _fingerprint(items: list[dict[str, Any]]) -> str:
    encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _load_snapshot(path: Path) -> list[dict[str, Any]] | None:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    items = document.get("candidates") if isinstance(document, dict) else None
    return items if isinstance(items, list) else None


def check(
    registrations: list[dict[str, Any]],
    slots: list[dict[str, Any]],
    enqueuer: Enqueuer,
    *,
    declarations: dict[str, Any] | None = None,
    snapshot_path: Path = DEFAULT_SNAPSHOT_PATH,
) -> ProvisionReport:
    current = candidates(registrations, slots, declarations)
    fingerprint = _fingerprint(current)
    previous = _load_snapshot(snapshot_path)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    report = ProvisionReport(False, False, previous, current, fingerprint)
    if previous is None:
        report.first_snapshot = True
    elif _fingerprint(previous) != fingerprint:
        report.changed = True
        if enqueuer.enqueue(
            kind=ALERT_KIND,
            payload={"candidates": current, "previous": previous, "current": current},
            source_key=fingerprint,
        ):
            report.alerts_enqueued = 1
    snapshot_path.write_text(
        json.dumps({"candidates": current, "fingerprint": fingerprint}, ensure_ascii=False),
        encoding="utf-8",
    )
    return report


def _read_slots(path: Path) -> list[dict[str, Any]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    slots = document.get("cli_slots") if isinstance(document, dict) else None
    return [slot for slot in slots if isinstance(slot, dict)] if isinstance(slots, list) else []


def main(argv: list[str] | None = None) -> int:
    from app.adapter.registry import HarnessRegistry
    from app.alerts.service import AlertNotifier
    from app.db import Database

    parser = argparse.ArgumentParser(prog="app.pool.provision")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval", type=int, default=300)
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
    while True:
        registrations = HarnessRegistry(database).listing()
        slots = _read_slots(pool_config_path())
        report = check(registrations, slots, notifier, declarations=load_declarations())
        print(json.dumps(report.as_json(), ensure_ascii=False))
        if not args.loop:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
