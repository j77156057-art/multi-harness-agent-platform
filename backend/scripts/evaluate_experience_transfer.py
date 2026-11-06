"""Distinguish transferred learning from copying old values on six new document tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.admin.experience import ExperienceStore
from scripts import evaluate_experience_injection_ab as base

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-transfer-ab-real-model" / "v3"
PREVIOUS = ROOT / "artifacts" / "experience-slot-v2-ab-real-model" / "v1"
SOURCE = ROOT / "data" / "admin" / "experience.json"
FROZEN = OUT / "frozen.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
DATASET_VERSION = "experience-transfer-ab-v1.0.2"

CASES = [
    {
        "case_id": "TR-01",
        "old_experience_id": "exp-b0ff85de2be10ae8b8a576ae",
        "unrelated_experience_id": "exp-96ed71006d509669a9a7ef02",
        "requirement": "做第二轮复盘。最新验收约束：创建 ops/review-round-2.md；意见映射表必须恰好五列，列名为意见编号、审查意见、本轮处置、状态、证据链接。",
        "path": "ops/review-round-2.md",
        "old_values": ["docs/review-loop-cb/round-2.md", "四列", "第二轮处置"],
        "required_groups": [["ops/review-round-2.md"], ["意见编号"], ["审查意见"], ["本轮处置"], ["状态"], ["证据链接"]],
        "expected": "使用新路径和五列，不带旧路径、四列或第二轮处置",
    },
    {
        "case_id": "TR-02",
        "old_experience_id": "exp-96ed71006d509669a9a7ef02",
        "unrelated_experience_id": "exp-b0ff85de2be10ae8b8a576ae",
        "requirement": "写验收说明。最新验收约束：正文不得出现“草稿”“待定”“稍后补充”这三个词，验收必须逐条检查这三项。",
        "path": "ops/acceptance-wording.md",
        "old_values": ["TODO", "待补充", "占位", "示例文本"],
        "required_groups": [["草稿"], ["待定"], ["稍后补充"]],
        "expected": "只检查新禁词，不带回 TODO、待补充、占位或示例文本",
    },
    {
        "case_id": "TR-03",
        "old_experience_id": "exp-6af29904b03befd4766ef4b6",
        "unrelated_experience_id": "exp-544730dd2d1dcef17bc8d994",
        "requirement": "整理第一轮交付说明。最新验收约束：写入 ops/first-round.md，并且恰好两条待补项。",
        "path": "ops/first-round.md",
        "old_values": ["docs/review-loop/round-1.md", "至少 3 条", "至少3条", "至少三条"],
        "required_groups": [["ops/first-round.md"], ["恰好两条", "恰好 2 条", "两条", "2 条", "2条"]],
        "expected": "使用新路径和恰好两条，不带旧路径或至少三条",
    },
    {
        "case_id": "TR-04",
        "old_experience_id": "exp-544730dd2d1dcef17bc8d994",
        "unrelated_experience_id": "exp-6af29904b03befd4766ef4b6",
        "requirement": "生成 ops/review-checklist.md。最新验收约束：只保留 F1、F2 两项检查。",
        "path": "ops/review-checklist.md",
        "old_values": ["round-1", "F3", "F4"],
        "required_groups": [["ops/review-checklist.md"], ["F1"], ["F2"]],
        "expected": "新文件只含 F1/F2，不带 round-1、F3、F4",
    },
    {
        "case_id": "TR-05",
        "old_experience_id": "exp-1d86f4d6f343d82dac6bcb91",
        "unrelated_experience_id": "exp-6af29904b03befd4766ef4b6",
        "requirement": "提交 qoder 的交付说明。最新验收约束：只创建 ops/qoder-lane.md，不得创建其他文件。",
        "path": "ops/qoder-lane.md",
        "old_values": ["docs/formal-multi-harness/qoder-lane.md"],
        "required_groups": [["ops/qoder-lane.md"]],
        "expected": "只使用新路径，不带旧 qoder-lane 路径",
    },
    {
        "case_id": "TR-06",
        "old_experience_id": "exp-e73c4967bdd3f5a4d0ca50c4",
        "unrelated_experience_id": "exp-1d86f4d6f343d82dac6bcb91",
        "requirement": "整理证据清单。最新验收约束：只列两个来源文件 ops/adapter-notes.md 与 ops/receipt-notes.md，不能用泛指名称代替。",
        "path": "ops/evidence-sources.md",
        "old_values": ["相关 CLI 实现文档", "平台回执实现文档"],
        "required_groups": [["ops/adapter-notes.md"], ["ops/receipt-notes.md"]],
        "expected": "使用两个新文件名，不保留泛指 CLI/平台回执文档名称",
    },
]


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


def tree_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return "sha256:" + digest.hexdigest()


def freeze() -> None:
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "model": base.MODEL,
        "parameters": base.PARAMS,
        "calls_per_group": 6,
        "retry_policy": "one real call per condition; no retry or substitution",
        "cost_limit_cny_equivalent": base.COST_LIMIT,
        "groups": {
            "A": "injection disabled",
            "B": "relevant old acceptance_check slot confirmed",
            "C": "unrelated old acceptance_check slot confirmed",
        },
        "cases": CASES,
        "scoring": {
            "transferred": "all required new values present and no old value present",
            "copied_wrong": "any old value appears in a valid goal/acceptance, even if new values also appear",
            "missed": "no old values but one or more required new values absent, or no valid task",
            "escalated": "needs_owner with both old and new values and an explicit conflict question",
            "evidence": "only goal, acceptance and exclusive_paths are scored; copied_wrong keeps verbatim snippets",
        },
        "previous_artifacts_sha256": tree_sha(PREVIOUS),
        "pilot_v1_v2_rejected": "earlier old-value inventories missed full paths in root_cause; v1/v2 partial results are not used",
        "old_values_rule": "manual old values plus paths from root_cause, action and spec_summary.exclusive_paths",
        "source_sha256": base.sha(SOURCE.read_text(encoding="utf-8")),
        "script_sha256": base.sha(Path(__file__).read_text(encoding="utf-8")),
    }
    write_json(FROZEN, protocol)
    print(json.dumps({"frozen": str(FROZEN), "cases": len(CASES), "parameters": base.PARAMS}, ensure_ascii=False))


def _spec_paths(record: dict[str, Any]) -> list[str]:
    try:
        spec = json.loads(str(record.get("spec_summary") or ""))
    except ValueError:
        return []
    paths = spec.get("exclusive_paths") if isinstance(spec, dict) else []
    path_pattern = re.compile(r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:md|txt|log|json|py|js|ts|tsx|css|html)(?![\w.-])", re.I)
    original_paths = [str(path) for path in paths or [] if isinstance(path, str) and path]
    original_paths.extend(match.group(0) for match in path_pattern.finditer(str(record.get("root_cause") or "")))
    original_paths.extend(match.group(0) for match in path_pattern.finditer(str(record.get("action") or "")))
    return list(dict.fromkeys(original_paths))


def _category(case: dict[str, Any], result: dict[str, Any] | None, error: str | None) -> tuple[str, list[str]]:
    tasks = (result or {}).get("tasks") or []
    if not tasks:
        questions = "\n".join((result or {}).get("questions") or [])
        old = [value for value in case["old_values"] if value in questions]
        new = [value for group in case["required_groups"] for value in group if value in questions]
        if old and new and "冲突" in questions:
            return "escalated", []
        return "missed", []
    task = tasks[0]
    text = str(task.get("goal") or "") + "\n" + str(task.get("acceptance") or "") + "\n" + "\n".join(task.get("exclusive_paths") or [])
    old_hits = [value for value in case["old_values"] if value in text]
    if old_hits:
        return "copied_wrong", old_hits
    missing = [group for group in case["required_groups"] if not any(value in text for value in group)]
    if missing:
        return "missed", []
    return "transferred", []


def run() -> None:
    if not FROZEN.exists():
        raise RuntimeError("freeze transfer protocol first")
    source_records = {
        record["experience_id"]: record
        for record in json.loads(SOURCE.read_text(encoding="utf-8"))["records"]
    }
    outcomes: list[dict[str, Any]] = []
    production_before = base.sha(SOURCE.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="experience-transfer-") as temporary:
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

        for case in CASES:
            case = dict(case)
            old = source_records[case["old_experience_id"]]
            case["old_values"] = list(dict.fromkeys([*case["old_values"], *_spec_paths(old)]))
            unrelated = source_records[case["unrelated_experience_id"]]
            context = base._post(api, "/api/v1/groups/G-1/dispatch", {
                "request_id": f"transfer-{case['case_id']}",
                "title": f"{case['case_id']} · 新值迁移场景",
                "goal": case["requirement"],
                "harness_id": base.MEMBER,
                "decided_by": base.ADMIN,
                "exclusive_paths": [case["path"]],
                "acceptance": "交付物存在且满足最新验收约束",
            }, admin_token)
            context_id = context["task_id"]
            store = ExperienceStore(Path(temporary) / "admin" / "experience.json")
            for role, old_record in (("relevant", old), ("unrelated", unrelated)):
                store.record_outcome(
                    kind="request_changes",
                    project=case["case_id"],
                    task_category="docs",
                    executor=base.MEMBER,
                    spec_summary=f"{case['case_id']} {case['requirement']}",
                    findings_summary=old_record["root_cause"],
                    root_cause=old_record["root_cause"],
                    action=old_record["action"],
                    result=old_record["result"],
                    contract_id=old_record["source_task_id"],
                    source_task_id=old_record["source_task_id"],
                    source_event_id=f"transfer:{role}:{old_record['source_event_id']}",
                    source="live",
                )
            records = store.records()
            by_id = {
                record["root_cause"]: record for record in records
            }
            relevant = by_id[old["root_cause"]]
            unrelated_record = by_id[unrelated["root_cause"]]
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
                    "old_experience_id": old["experience_id"],
                    "injected_experience_id": (
                        relevant["experience_id"] if condition == "B"
                        else unrelated_record["experience_id"] if condition == "C"
                        else None
                    ),
                }
                started = time.monotonic()
                error = None
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
                            "title": f"{case['case_id']}-{condition}-transfer",
                            "goal": case["requirement"],
                            "tasks": result["tasks"],
                            "reviewer_harness_ids": [base.MEMBER],
                            "opened_by": base.ADMIN,
                        }, admin_token)
                        outcome["plan_id"] = draft["plan_id"]
                except Exception as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    outcome["error"] = error
                category, old_hits = _category(case, outcome.get("result"), error)
                outcome["category"] = category
                outcome["copied_old_values"] = old_hits
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
                write_json(RESULTS, outcomes)
                spent = sum(
                    base._provider_cost(read_json(OUT / "calls" / f"{row['case_id']}-{row['condition']}.json", {}))
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
        "production_experience_sha256_after": base.sha(SOURCE.read_text(encoding="utf-8")),
        "production_unchanged": production_before == base.sha(SOURCE.read_text(encoding="utf-8")),
        "completed_outcomes": len(outcomes),
    })


def summarize() -> None:
    rows = read_json(RESULTS, [])
    summary: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "model": base.MODEL,
        "parameters": base.PARAMS,
        "outcomes": len(rows),
        "conditions": {},
        "copied_wrong_evidence": [
            {
                "case_id": row["case_id"],
                "condition": row["condition"],
                "old_values": row["copied_old_values"],
                "goal": (row["result"]["tasks"][0].get("goal") if row.get("result") and row["result"].get("tasks") else None),
                "acceptance": (row["result"]["tasks"][0].get("acceptance") if row.get("result") and row["result"].get("tasks") else None),
            }
            for row in rows if row["category"] == "copied_wrong"
        ],
    }
    calls = [read_json(OUT / "calls" / f"{row['case_id']}-{row['condition']}.json", {}) for row in rows]
    preflight = read_json(OUT / "calls" / "PREFLIGHT-preflight.json", {})
    for condition in ("A", "B", "C"):
        group = [row for row in rows if row["condition"] == condition]
        counts = Counter(row["category"] for row in group)
        summary["conditions"][condition] = {
            "n": len(group),
            "counts": {name: counts.get(name, 0) for name in ("transferred", "copied_wrong", "missed", "escalated")},
            "transferred_wilson_95": base._wilson(counts["transferred"], len(group)),
        }
    charged = [*calls, preflight]
    summary["cost"] = {
        "provider_returned_cost_sum": round(sum(base._provider_cost(call) for call in charged), 6),
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
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run", "summarize"])
    args = parser.parse_args()
    base.OUT = OUT
    base.FROZEN = FROZEN
    base.RESULTS = RESULTS
    base.SUMMARY = SUMMARY
    base.MANUAL = OUT / "manual-review.json"
    base.DATASET_VERSION = DATASET_VERSION
    base.PARAMS = {**base.PARAMS, "max_tokens": 8192}
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    else:
        summarize()


if __name__ == "__main__":
    main()
