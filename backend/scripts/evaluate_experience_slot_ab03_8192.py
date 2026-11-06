"""Single-scene AB-03 rerun with max_tokens=8192; never rewrites prior aggregates."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts import evaluate_experience_slots_ab as slot
from scripts import evaluate_experience_injection_ab as base

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-slot-ab03-8192" / "v1"
PREVIOUS = ROOT / "artifacts" / "experience-slot-ab-real-model" / "v1"
FROZEN = OUT / "frozen.json"
SUMMARY = OUT / "summary.json"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def tree_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return "sha256:" + digest.hexdigest()


def freeze() -> None:
    protocol = {
        "dataset_version": "experience-slot-ab03-8192-v1.0.0",
        "frozen_at": datetime.now(UTC).isoformat(),
        "case_id": "AB-03",
        "only_change": "max_tokens 4096 -> 8192",
        "model": base.MODEL,
        "parameters": base.PARAMS,
        "conditions": ["A", "B", "C"],
        "calls_per_condition": 1,
        "retry_policy": "no retry or substitution; no other scene rerun",
        "previous_result": str(PREVIOUS / "results.json"),
        "previous_artifacts_sha256": tree_sha(PREVIOUS),
        "cost_limit_cny_equivalent": 5.0,
        "primary_scoring": "same acceptance_check rule as the frozen slot run; standalone scene report only",
    }
    write_json(FROZEN, protocol)
    print(json.dumps({"frozen": str(FROZEN), "parameters": base.PARAMS}, ensure_ascii=False))


def run() -> None:
    if not FROZEN.exists():
        raise RuntimeError("freeze AB-03 protocol first")
    slot.run(case_ids=["AB-03"])


def summarize() -> None:
    previous = [
        row for row in json.loads((PREVIOUS / "results.json").read_text(encoding="utf-8"))
        if row["case_id"] == "AB-03"
    ]
    current = json.loads((OUT / "results.json").read_text(encoding="utf-8"))
    previous_b = next(row for row in previous if row["condition"] == "B")
    current_b = next(row for row in current if row["condition"] == "B")
    summary = {
        "case_id": "AB-03",
        "standalone": True,
        "previous_aggregate_unchanged": {"machine_B_hits": 2, "human_B_hits": 3},
        "previous_B_finish_reason": previous_b["receipt"]["finish_reason"],
        "current_B_finish_reason": current_b["receipt"]["finish_reason"],
        "B_no_longer_length": current_b["receipt"]["finish_reason"] != "length",
        "B_acceptance_check_hit": current_b["machine"]["hit"],
        "conditions": {
            condition: next(row for row in current if row["condition"] == condition)
            for condition in ("A", "B", "C")
        },
        "previous_conditions": {
            condition: next(row for row in previous if row["condition"] == condition)
            for condition in ("A", "B", "C")
        },
        "environment": json.loads((OUT / "environment.json").read_text(encoding="utf-8")),
        "previous_artifacts_sha256_before": json.loads(FROZEN.read_text(encoding="utf-8"))["previous_artifacts_sha256"],
        "previous_artifacts_sha256_after": tree_sha(PREVIOUS),
    }
    summary["previous_artifacts_unchanged"] = (
        summary["previous_artifacts_sha256_before"] == summary["previous_artifacts_sha256_after"]
    )
    write_json(SUMMARY, summary)
    print(json.dumps({
        "B_no_longer_length": summary["B_no_longer_length"],
        "B_acceptance_check_hit": summary["B_acceptance_check_hit"],
        "previous_artifacts_unchanged": summary["previous_artifacts_unchanged"],
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run", "summarize"])
    args = parser.parse_args()
    base.OUT = OUT
    base.RESULTS = OUT / "results.json"
    base.SUMMARY = SUMMARY
    base.MANUAL = OUT / "manual-review.json"
    base.DATASET_VERSION = "experience-slot-ab03-8192-v1.0.0"
    base.PARAMS = {**base.PARAMS, "max_tokens": 8192}
    slot.OUT = OUT
    slot.FROZEN = FROZEN
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    else:
        summarize()


if __name__ == "__main__":
    main()
