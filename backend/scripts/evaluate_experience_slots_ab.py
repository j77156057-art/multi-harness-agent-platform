"""Paired real-model evaluation of hard experience slots versus the prior free-text run."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.admin.experience import ExperienceStore
from app.admin.slots import SLOT_NAMES, slot_for_experience
from scripts import evaluate_experience_injection_ab as base

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-slot-ab-real-model" / "v1"
PREVIOUS_OUT = ROOT / "artifacts" / "experience-ab-real-model" / "v1"
FROZEN = OUT / "frozen.json"
SUMMARY = OUT / "summary.json"
MANUAL = OUT / "manual-review.json"
DATASET_VERSION = "experience-slot-ab-v1.0.0"


def now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def classify_production() -> dict[str, Any]:
    path = ROOT / "data" / "admin" / "experience.json"
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    rows = []
    hard_fits = []
    for record in records:
        slot = slot_for_experience(record)
        row = {
            "experience_id": record["experience_id"],
            "kind": record["kind"],
            "slot": slot["slot"] if slot else "unactionable",
            "expected": slot["expected"] if slot else None,
            "source_task_id": record["source_task_id"],
        }
        rows.append(row)
        if slot and slot["expected"] not in json.dumps(record, ensure_ascii=False):
            hard_fits.append(row)
    distribution = Counter(row["slot"] for row in rows)
    return {
        "source_file": str(path),
        "source_sha256": base.sha(path.read_text(encoding="utf-8")),
        "total": len(rows),
        "actionable": sum(row["slot"] != "unactionable" for row in rows),
        "unactionable": sum(row["slot"] == "unactionable" for row in rows),
        "distribution": {name: distribution.get(name, 0) for name in (*SLOT_NAMES, "unactionable")},
        "hard_fits": hard_fits,
        "rows": rows,
    }


def previous_slot_mapping() -> dict[str, Any]:
    previous = {
        row["case_id"]: row for row in read_json(PREVIOUS_OUT / "results.json", [])
        if row["condition"] == "B"
    }
    mapping = {}
    for case in base.CASES:
        case_id = case["case_id"]
        record = {
            "action": case["action"],
            "root_cause": case["root_cause"],
            "spec_summary": json.dumps({"exclusive_paths": [case["path"]]}, ensure_ascii=False),
            "source_task_id": previous[case_id]["context_task_id"],
            "contract_id": previous[case_id]["context_task_id"],
        }
        slot = slot_for_experience(record)
        mapping[case_id] = slot
    return mapping


def freeze() -> None:
    classification = classify_production()
    mapping = previous_slot_mapping()
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "comparison_baseline": str(PREVIOUS_OUT / "summary.json"),
        "model": base.MODEL,
        "model_tier": base.MODEL_TIER,
        "parameters": base.PARAMS,
        "only_change": "free-text experience presentation -> finite hard slots",
        "groups": {
            "A": "injection disabled",
            "B": "relevant hard slot confirmed by human-1",
            "C": "unrelated hard slot confirmed by human-1",
        },
        "calls_per_group": 6,
        "retry_policy": "one real model call per condition; no retry or substitution",
        "cost_limit_cny_equivalent": base.COST_LIMIT,
        "slot_enum": SLOT_NAMES,
        "slot_rule": "expected must occur verbatim in the source record; otherwise unactionable and never injected",
        "production_classification": classification,
        "previous_six_slot_mapping": mapping,
        "previous_five_abstract_slots": {
            key: mapping[key] for key in ("AB-01", "AB-03", "AB-04", "AB-05", "AB-06")
        },
        "hard_fit_cases": classification["hard_fits"],
        "primary_scoring": "same deterministic rules as previous run; AB-01 now expects the source FD task id",
        "script_sha256": base.sha(Path(__file__).read_text(encoding="utf-8")),
        "base_script_sha256": base.sha(Path(base.__file__).read_text(encoding="utf-8")),
    }
    write_json(FROZEN, protocol)
    print(json.dumps({
        "frozen": str(FROZEN),
        "actionable": classification["actionable"],
        "unactionable": classification["unactionable"],
        "distribution": classification["distribution"],
        "hard_fits": len(classification["hard_fits"]),
    }, ensure_ascii=False))


def _score(case: dict[str, Any], result: dict[str, Any] | None, source_task_id: str) -> dict[str, Any]:
    if case["case_id"] == "AB-01":
        if not result or not result.get("tasks"):
            return {"hit": False, "defect_present": True, "reason": "no valid task result"}
        task = result["tasks"][0]
        goal = str(task.get("goal") or "")
        acceptance = str(task.get("acceptance") or "")
        hit = source_task_id in (goal + "\n" + acceptance)
        return {
            "hit": hit,
            "defect_present": not hit,
            "reason": "source FD task id expected from slot",
            "goal": goal,
            "acceptance": acceptance,
            "exclusive_paths": task.get("exclusive_paths") or [],
        }
    return base._score(case, result)


def run(case_ids: list[str] | None = None) -> None:
    if not FROZEN.exists():
        raise RuntimeError("freeze slot protocol first")
    previous = {
        row["case_id"]: row for row in read_json(PREVIOUS_OUT / "results.json", [])
        if row["condition"] == "B"
    }
    outcomes: list[dict[str, Any]] = []
    production = ROOT / "data" / "admin" / "experience.json"
    production_before = base.sha(production.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="experience-slot-ab-") as temporary:
        chain, api, admin_token, member_token, human_token = base._setup(Path(temporary))
        pool_token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
        preflight = base.RealModelService(
            chain.database,
            harness_id=base.ADMIN,
            experience=ExperienceStore(Path(temporary) / "admin" / "experience.json"),
            scratch_dir=Path(temporary) / "admin-scratch",
            timeout_seconds=240,
            token=pool_token,
            case_id="PREFLIGHT",
            condition="preflight",
        )
        preflight._invoke_model('Return only {"ready":true}.')
        preflight_call = read_json(OUT / "calls" / "PREFLIGHT-preflight.json", {})
        if preflight_call.get("error") or not preflight_call.get("usage"):
            raise RuntimeError("real-model preflight lacks a successful usage receipt")

        selected_cases = [
            case for case in base.CASES if case_ids is None or case["case_id"] in case_ids
        ]
        for case in selected_cases:
            unrelated = base.CASES[(base.CASES.index(case) + 1) % len(base.CASES)]
            context = base._post(api, "/api/v1/groups/G-1/dispatch", {
                "request_id": f"slot-context-{case['case_id']}",
                "title": f"{case['case_id']} · 缺陷场景",
                "goal": case["requirement"],
                "harness_id": base.MEMBER,
                "decided_by": base.ADMIN,
                "exclusive_paths": [case["path"]],
                "acceptance": "交付物存在且可检查",
            }, admin_token)
            context_id = context["task_id"]
            source_task_id = previous[case["case_id"]]["context_task_id"]
            store = ExperienceStore(Path(temporary) / "admin" / "experience.json")
            for role, experience_case in (("relevant", case), ("unrelated", unrelated)):
                store.record_outcome(
                    kind="request_changes",
                    project=case["case_id"],
                    task_category="docs",
                    executor=base.MEMBER,
                    spec_summary=f"{case['case_id']} {case['requirement']}",
                    findings_summary=experience_case["root_cause"],
                    root_cause=experience_case["root_cause"],
                    action=experience_case["action"],
                    result="历史验收未通过",
                    contract_id=source_task_id,
                    source_task_id=source_task_id,
                    source_event_id=f"slot:{role}:{case['case_id']}",
                    source="live",
                )
            records = store.records()
            by_root = {record["root_cause"]: record for record in records}
            relevant = by_root[case["root_cause"]]
            unrelated_record = by_root[unrelated["root_cause"]]
            assert slot_for_experience(relevant) is not None
            assert slot_for_experience(unrelated_record) is not None
            decision_base = f"/api/v1/tasks/{context_id}/experiences"
            for condition in ("A", "B", "C"):
                base._post(
                    api,
                    "/api/v1/experience-injection",
                    {"enabled": condition != "A", "decided_by": "human-1"},
                    human_token,
                )
                if condition != "A":
                    selected = relevant if condition == "B" else unrelated_record
                    base._post(
                        api,
                        f"{decision_base}/{selected['experience_id']}/decision",
                        {"action": "confirm", "decided_by": "human-1"},
                        human_token,
                    )
                service = base.RealModelService(
                    chain.database,
                    harness_id=base.ADMIN,
                    experience=store,
                    scratch_dir=Path(temporary) / "admin-scratch",
                    timeout_seconds=240,
                    token=pool_token,
                    case_id=case["case_id"],
                    condition=condition,
                )
                outcome: dict[str, Any] = {
                    "case_id": case["case_id"],
                    "condition": condition,
                    "context_task_id": context_id,
                    "source_task_id": source_task_id,
                    "relevant_experience_id": relevant["experience_id"],
                    "injected_experience_id": (
                        relevant["experience_id"] if condition == "B"
                        else unrelated_record["experience_id"] if condition == "C"
                        else None
                    ),
                    "slot": slot_for_experience(
                        relevant if condition == "B" else unrelated_record if condition == "C" else relevant
                    ),
                }
                started = time.monotonic()
                try:
                    result = service.refine(
                        requirement=case["requirement"],
                        group_id=base.GROUP,
                        max_tasks=1,
                        members=[base.MEMBER],
                        project_name=case["case_id"],
                    )
                    outcome["result"] = result
                    if result.get("tasks"):
                        draft = base._post(api, "/api/v1/adapter/admin/plan_draft", {
                            "group_id": base.GROUP,
                            "title": f"{case['case_id']}-{condition}-slot",
                            "goal": case["requirement"],
                            "tasks": result["tasks"],
                            "reviewer_harness_ids": [base.MEMBER],
                            "opened_by": base.ADMIN,
                        }, admin_token)
                        outcome["plan_id"] = draft["plan_id"]
                    outcome["machine"] = _score(case, result, source_task_id)
                except Exception as exc:
                    outcome["error"] = f"{type(exc).__name__}: {exc}"
                    outcome["machine"] = _score(case, None, source_task_id)
                outcome["duration_seconds"] = round(time.monotonic() - started, 3)
                call = read_json(OUT / "calls" / f"{case['case_id']}-{condition}.json", {})
                outcome["receipt"] = {
                    key: call.get(key)
                    for key in (
                        "requested_model", "actual_model", "parameters", "usage",
                        "provider_cost", "ledger_cost", "duration_seconds", "finish_reason",
                        "http_status", "error",
                    )
                }
                outcomes.append(outcome)
                write_json(base.RESULTS, outcomes)
                spent = sum(
                    base._provider_cost(
                        read_json(OUT / "calls" / f"{row['case_id']}-{row['condition']}.json", {})
                    )
                    for row in outcomes
                ) + base._provider_cost(preflight_call)
                if spent > base.COST_LIMIT:
                    raise RuntimeError(f"cost limit reached: {spent}")
                if condition == "B":
                    base._post(api, f"{decision_base}/{relevant['experience_id']}/decision", {"action": "revoke", "decided_by": "human-1"}, human_token)
                elif condition == "C":
                    base._post(api, f"{decision_base}/{unrelated_record['experience_id']}/decision", {"action": "revoke", "decided_by": "human-1"}, human_token)
    write_json(OUT / "environment.json", {
        "temporary_directory_removed": not Path(temporary).exists(),
        "production_experience_sha256_before": production_before,
        "production_experience_sha256_after": base.sha(production.read_text(encoding="utf-8")),
        "production_unchanged": production_before == base.sha(production.read_text(encoding="utf-8")),
        "completed_outcomes": len(outcomes),
    })


def summarize() -> None:
    base.summarize()
    summary = read_json(SUMMARY, {})
    previous_rows = read_json(PREVIOUS_OUT / "results.json", [])
    current_rows = read_json(OUT / "scored-results.json", [])
    comparison = []
    for case in base.CASES:
        case_id = case["case_id"]
        previous_b = next(row for row in previous_rows if row["case_id"] == case_id and row["condition"] == "B")
        current_b = next(row for row in current_rows if row["case_id"] == case_id and row["condition"] == "B")
        comparison.append({
            "case_id": case_id,
            "previous_hit": previous_b["machine"]["hit"],
            "current_hit": current_b["machine"]["hit"],
            "change": (
                "flipped_to_hit" if not previous_b["machine"]["hit"] and current_b["machine"]["hit"]
                else "regressed" if previous_b["machine"]["hit"] and not current_b["machine"]["hit"]
                else "unchanged"
            ),
        })
    summary["previous_comparison"] = {
        "previous_B_hits": sum(bool(row["previous_hit"]) for row in comparison),
        "current_B_hits": sum(bool(row["current_hit"]) for row in comparison),
        "delta": sum(bool(row["current_hit"]) for row in comparison) - sum(bool(row["previous_hit"]) for row in comparison),
        "rows": comparison,
    }
    summary["slot_classification"] = read_json(FROZEN, {}).get("production_classification")
    write_json(SUMMARY, summary)
    print(json.dumps({"summary": str(SUMMARY), "previous_comparison": summary["previous_comparison"]}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run", "summarize"])
    args = parser.parse_args()
    base.OUT = OUT
    base.FROZEN = FROZEN
    base.RESULTS = OUT / "results.json"
    base.SUMMARY = SUMMARY
    base.MANUAL = MANUAL
    base.DATASET_VERSION = DATASET_VERSION
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    else:
        summarize()


if __name__ == "__main__":
    main()
