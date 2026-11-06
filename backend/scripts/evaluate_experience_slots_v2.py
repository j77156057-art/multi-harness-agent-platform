"""Evidence-correct slot classification and four-way real-model A/B/C evaluation."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.admin.slots import SLOT_NAMES, classify_experience
from scripts import evaluate_experience_slots_ab as slot
from scripts import evaluate_experience_injection_ab as base

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-slot-v2-ab-real-model" / "v1"
PREVIOUS = ROOT / "artifacts" / "experience-slot-ab-real-model" / "v1"
OLD_CLASSIFICATION = PREVIOUS / "frozen.json"
SOURCE = ROOT / "data" / "admin" / "experience.json"
FROZEN = OUT / "frozen.json"
SUMMARY = OUT / "summary.json"
MANUAL = OUT / "manual-review.json"
DATASET_VERSION = "experience-slot-v2-ab-v1.0.0"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def tree_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return "sha256:" + digest.hexdigest()


def classification_migration() -> dict[str, Any]:
    previous = read_json(OLD_CLASSIFICATION, {})["production_classification"]["rows"]
    previous_by_id = {row["experience_id"]: row for row in previous}
    records = json.loads(SOURCE.read_text(encoding="utf-8"))["records"]
    rows = []
    for record in records:
        classified = classify_experience(record)
        rows.append({
            "experience_id": record["experience_id"],
            "source_task_id": record["source_task_id"],
            "kind": record["kind"],
            "previous_category": previous_by_id[record["experience_id"]]["slot"],
            "category": classified["category"],
            "expected": classified["expected"],
            "evidence_quote": classified["evidence_quote"],
        })
    distribution = Counter(row["category"] for row in rows)
    moved_from_delivery = [row for row in rows if row["previous_category"] == "delivery_path"]
    return {
        "source_sha256": base.sha(SOURCE.read_text(encoding="utf-8")),
        "total": len(rows),
        "distribution": dict(sorted(distribution.items())),
        "from_delivery_path": {
            "total": len(moved_from_delivery),
            "to_no_lesson": sum(row["category"] == "no_lesson" for row in moved_from_delivery),
            "to_other_categories": sum(row["category"] != "no_lesson" for row in moved_from_delivery),
            "breakdown": dict(sorted(Counter(row["category"] for row in moved_from_delivery).items())),
        },
        "rows": rows,
    }


def freeze() -> None:
    migration = classification_migration()
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": datetime.now(UTC).isoformat(),
        "model": base.MODEL,
        "parameters": base.PARAMS,
        "calls_per_group": 6,
        "retry_policy": "one real call per condition; no retry or substitution",
        "cost_limit_cny_equivalent": base.COST_LIMIT,
        "classification_rules": {
            "merged": "always no_lesson; never infer from spec_summary",
            "request_changes": "classify only from explicit root_cause; evidence_quote required or unactionable",
            "injection": "single slot_for_experience filter; no_lesson and unactionable never inject",
        },
        "classification_migration": migration,
        "scoring": {
            "hit": "the frozen expected slot value is present in a valid task result",
            "resolved_by_escalation": "only AB-03-B with explicit existence-only vs failure-check conflict and a request for human decision",
            "miss": "other non-hit decisions, including clarification without a proven conflict",
            "other": "invalid JSON, length truncation, or infrastructure/parse failure",
            "conflict": {
                "user": "写启动文档，验收只检查文件是否存在",
                "experience": "验收必须包含启动命令失败时的失败路径检查",
            },
            "rates": {
                "hit_only": "hit / all calls",
                "hit_plus_escalation": "(hit + resolved_by_escalation) / all calls",
                "effective_processing": "(hit + resolved_by_escalation) / non-other calls",
            },
        },
        "previous_artifacts_sha256": tree_sha(PREVIOUS),
        "script_sha256": base.sha(Path(__file__).read_text(encoding="utf-8")),
    }
    write_json(FROZEN, protocol)
    print(json.dumps({
        "frozen": str(FROZEN),
        "distribution": migration["distribution"],
        "from_delivery_path": migration["from_delivery_path"],
    }, ensure_ascii=False))


def run() -> None:
    if not FROZEN.exists():
        raise RuntimeError("freeze v2 protocol first")
    slot.run()


def _escalation(row: dict[str, Any]) -> tuple[bool, str]:
    if row["case_id"] != "AB-03" or row["condition"] != "B":
        return False, ""
    result = row.get("result") or {}
    if result.get("status") != "needs_owner":
        return False, ""
    questions = "\n".join(result.get("questions") or [])
    has_user_side = "只检查文件是否存在" in questions or "仅检查文件存在" in questions
    has_experience_side = "失败路径" in questions and "启动" in questions
    asks_human = "请确认" in questions or "是否" in questions or "？" in questions
    if has_user_side and has_experience_side and asks_human:
        return True, (
            "用户：验收只检查文件是否存在；经验：验收必须包含启动命令失败时的失败路径检查"
        )
    return False, ""


def _category(row: dict[str, Any]) -> tuple[str, str]:
    if row.get("error") or (row.get("receipt") or {}).get("finish_reason") == "length":
        return "other", "非法 JSON、截断或解析失败"
    escalation, evidence = _escalation(row)
    if escalation:
        return "resolved_by_escalation", evidence
    if (row.get("machine") or {}).get("hit"):
        return "hit", "expected slot value present"
    return "miss", "expected slot value absent or no valid task"


def summarize() -> None:
    rows = read_json(OUT / "results.json", [])
    manual = read_json(MANUAL, {})
    for row in rows:
        row["category"], row["category_evidence"] = _category(row)
        key = f"{row['case_id']}-{row['condition']}"
        row["human"] = manual.get(key)
        if row["human"] and row["human"].get("category") != row["category"]:
            row["scoring_disagreement"] = True
    write_json(OUT / "scored-results.json", rows)
    summary: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "model": base.MODEL,
        "parameters": base.PARAMS,
        "outcomes": len(rows),
        "conditions": {},
        "disagreements": [f"{row['case_id']}-{row['condition']}" for row in rows if row.get("scoring_disagreement")],
    }
    calls = [read_json(OUT / "calls" / f"{row['case_id']}-{row['condition']}.json", {}) for row in rows]
    preflight = read_json(OUT / "calls" / "PREFLIGHT-preflight.json", {})
    for condition in ("A", "B", "C"):
        group = [row for row in rows if row["condition"] == condition]
        counts = Counter(row["category"] for row in group)
        non_other = len(group) - counts["other"]
        human_group = [row for row in group if row.get("human")]
        human_hits = sum(row["human"]["category"] == "hit" for row in human_group)
        human_escalations = sum(row["human"]["category"] == "resolved_by_escalation" for row in human_group)
        summary["conditions"][condition] = {
            "n": len(group),
            "counts": {name: counts.get(name, 0) for name in ("hit", "miss", "resolved_by_escalation", "other")},
            "hit_rate": base._wilson(counts["hit"], len(group)),
            "hit_plus_escalation_rate": base._wilson(counts["hit"] + counts["resolved_by_escalation"], len(group)),
            "effective_processing_rate": base._wilson(counts["hit"] + counts["resolved_by_escalation"], non_other),
            "human_reviewed": len(human_group),
            "human_hits": human_hits,
            "human_resolved_by_escalation": human_escalations,
            "errors": counts["other"],
        }
    previous_rows = read_json(PREVIOUS / "results.json", [])
    comparison = []
    for case in base.CASES:
        previous_b = next(row for row in previous_rows if row["case_id"] == case["case_id"] and row["condition"] == "B")
        current_b = next(row for row in rows if row["case_id"] == case["case_id"] and row["condition"] == "B")
        comparison.append({
            "case_id": case["case_id"],
            "previous_hit": previous_b["machine"]["hit"],
            "current_hit": current_b["category"] == "hit",
            "current_category": current_b["category"],
            "human_category": (current_b.get("human") or {}).get("category"),
        })
    current_b_hits = sum(row["current_hit"] for row in comparison)
    summary["previous_comparison"] = {
        "previous_machine_B_hits": 2,
        "previous_human_B_hits": 3,
        "current_machine_B_hits": current_b_hits,
        "machine_delta": current_b_hits - 2,
        "rows": comparison,
    }
    charged = [*calls, preflight]
    summary["cost"] = {
        "provider_returned_cost_sum": round(sum(base._provider_cost(call) for call in charged), 6),
        "pool_tier": base.MODEL_TIER,
        "provider_cost_missing_calls": sum(call.get("provider_cost") is None for call in charged),
        "prompt_tokens": sum(int((call.get("usage") or {}).get("prompt_tokens") or 0) for call in charged),
        "completion_tokens": sum(int((call.get("usage") or {}).get("completion_tokens") or 0) for call in charged),
        "total_tokens": sum(int((call.get("usage") or {}).get("total_tokens") or 0) for call in charged),
        "usage_missing_calls": sum(not call.get("usage") for call in charged),
        "finish_reasons": {
            reason: sum(call.get("finish_reason") == reason for call in charged)
            for reason in sorted({str(call.get("finish_reason")) for call in charged})
        },
        "duration_median_seconds": statistics.median(float(call.get("duration_seconds") or 0) for call in charged),
        "duration_max_seconds": max(float(call.get("duration_seconds") or 0) for call in charged),
        "calls_including_preflight": len(charged),
    }
    summary["environment"] = read_json(OUT / "environment.json", {})
    summary["previous_artifacts_sha256_before"] = read_json(FROZEN, {})["previous_artifacts_sha256"]
    summary["previous_artifacts_sha256_after"] = tree_sha(PREVIOUS)
    summary["previous_artifacts_unchanged"] = summary["previous_artifacts_sha256_before"] == summary["previous_artifacts_sha256_after"]
    summary["classification_migration"] = read_json(FROZEN, {})["classification_migration"]
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run", "summarize"])
    args = parser.parse_args()
    base.OUT = OUT
    base.RESULTS = OUT / "results.json"
    base.SUMMARY = SUMMARY
    base.MANUAL = MANUAL
    base.DATASET_VERSION = DATASET_VERSION
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
