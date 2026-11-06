"""v6: conditional injection vs injection off, six cases, twelve real calls.

Arms:
    A  injection switch off
    Q  injection switch on, relevant experience confirmed; the gate decides

Only a dispatch that is *missing* a field (path / count / banned words /
evidence sources) can trigger a check-type line. Non-triggered cases must
produce byte-identical A/Q prompts; the run and finalize both check that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.admin.experience import ExperienceStore
from app.admin.injection_gate import decide
from app.admin.service import AdminService
from scripts import evaluate_experience_injection_ab as base
from scripts.semantic_transfer_review import judge, literal_judge

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-transfer-ab-real-model" / "v6-conditional"
FROZEN = OUT / "frozen.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
ENVIRONMENT = OUT / "environment.json"
SOURCE = ROOT / "data" / "admin" / "experience.json"
HISTORY_DIRS = ("v1", "v2", "v3", "v4", "v5-review")
DATASET_VERSION = "experience-transfer-ab-v6.0.0"
ARMS = ("A", "Q")

CASES: list[dict[str, Any]] = [
    {
        "case_id": "TR-01",
        "old_experience_id": "exp-b0ff85de2be10ae8b8a576ae",
        "unrelated_experience_id": "exp-96ed71006d509669a9a7ef02",
        "requirement": (
            "做第二轮复盘。最新验收约束：创建 ops/review-round-2.md；"
            "意见映射表的列名依次为意见编号、审查意见、本轮处置、状态、证据链接。"
        ),
        "path": "ops/review-round-2.md",
        "old_values": ["docs/review-loop-cb/round-2.md", "四列", "第二轮处置"],
        "required_groups": [
            ["ops/review-round-2.md"],
            ["意见编号"],
            ["审查意见"],
            ["本轮处置"],
            ["状态"],
            ["证据链接"],
        ],
        "expected_gap": "count",
        "gap_fill": {"all": [], "any": ["五列", "5列", "5 列", "五个列名"]},
        "expected": "使用新路径和五列，不带旧路径、四列或第二轮处置",
    },
    {
        "case_id": "TR-02",
        "old_experience_id": "exp-96ed71006d509669a9a7ef02",
        "unrelated_experience_id": "exp-b0ff85de2be10ae8b8a576ae",
        "requirement": "写验收说明。最新验收约束：正文不得出现“草稿”“待定”“稍后补充”这三个词。",
        "path": "ops/acceptance-wording.md",
        "old_values": ["TODO", "待补充", "占位", "示例文本"],
        "required_groups": [["草稿"], ["待定"], ["稍后补充"]],
        "expected_gap": "banned_words",
        "gap_fill": {
            "all": ["草稿", "待定", "稍后补充"],
            "any": ["逐条", "逐项", "分别", "每条", "每项", "单独"],
        },
        "expected": "只检查新禁词，且验收写明逐条检查",
    },
    {
        "case_id": "TR-03",
        "old_experience_id": "exp-6af29904b03befd4766ef4b6",
        "unrelated_experience_id": "exp-544730dd2d1dcef17bc8d994",
        "requirement": "整理第一轮交付说明。最新验收约束：写入 ops/first-round.md，并且恰好两条待补项。",
        "path": "ops/first-round.md",
        "old_values": ["docs/review-loop/round-1.md", "至少 3 条", "至少3条", "至少三条"],
        "required_groups": [
            ["ops/first-round.md"],
            ["恰好两条", "恰好 2 条", "两条", "2 条", "2条"],
        ],
        "expected_gap": None,
        "gap_fill": {"all": [], "any": []},
        "expected": "使用新路径和恰好两条，不带旧路径或至少三条；本派单不缺项，不应触发",
    },
    {
        "case_id": "TR-04",
        "old_experience_id": "exp-544730dd2d1dcef17bc8d994",
        "unrelated_experience_id": "exp-6af29904b03befd4766ef4b6",
        "requirement": "生成 ops/review-checklist.md。最新验收约束：只保留 F1、F2 检查。",
        "path": "ops/review-checklist.md",
        "old_values": ["round-1", "F3", "F4"],
        "required_groups": [["ops/review-checklist.md"], ["F1"], ["F2"]],
        "expected_gap": "count",
        "gap_fill": {"all": [], "any": ["两项", "2项", "2 项"]},
        "expected": "新文件只含 F1/F2，不带 round-1、F3、F4；数量应写明两项",
    },
    {
        "case_id": "TR-05",
        "old_experience_id": "exp-1d86f4d6f343d82dac6bcb91",
        "unrelated_experience_id": "exp-6af29904b03befd4766ef4b6",
        "requirement": "提交 qoder 的交付说明。最新验收约束：只创建 ops/qoder-lane.md，不得创建其他文件。",
        "path": "ops/qoder-lane.md",
        "old_values": ["docs/formal-multi-harness/qoder-lane.md"],
        "required_groups": [["ops/qoder-lane.md"]],
        "expected_gap": None,
        "gap_fill": {"all": [], "any": []},
        "expected": "只使用新路径；本派单不缺项，不应触发",
    },
    {
        "case_id": "TR-06",
        "old_experience_id": "exp-e73c4967bdd3f5a4d0ca50c4",
        "unrelated_experience_id": "exp-1d86f4d6f343d82dac6bcb91",
        "requirement": (
            "整理证据清单。最新验收约束：只列两个来源文件"
            " ops/adapter-notes.md 与 ops/receipt-notes.md。"
        ),
        "path": "ops/evidence-sources.md",
        "old_values": ["相关 CLI 实现文档", "平台回执实现文档"],
        "required_groups": [["ops/adapter-notes.md"], ["ops/receipt-notes.md"]],
        "expected_gap": "evidence_sources",
        "gap_fill": {
            "all": [],
            "any": ["实际存在", "可核查", "可核实", "章节", "段落", "不得泛指", "不能泛指"],
        },
        "expected": "使用两个新文件名，且验收写明来源实际存在/可核查",
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


def history_sha() -> dict[str, str]:
    return {
        name: tree_sha(ROOT / "artifacts" / "experience-transfer-ab-real-model" / name)
        for name in HISTORY_DIRS
    }


def _spec_paths(record: dict[str, Any]) -> list[str]:
    try:
        spec = json.loads(str(record.get("spec_summary") or ""))
    except ValueError:
        return []
    paths = spec.get("exclusive_paths") if isinstance(spec, dict) else []
    pattern = re.compile(
        r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:md|txt|log|json|py|js|ts|tsx|css|html)(?![\w.-])",
        re.I,
    )
    found = [str(path) for path in paths or [] if isinstance(path, str) and path]
    found.extend(match.group(0) for match in pattern.finditer(str(record.get("root_cause") or "")))
    found.extend(match.group(0) for match in pattern.finditer(str(record.get("action") or "")))
    return list(dict.fromkeys(found))


def _source_records() -> dict[str, dict[str, Any]]:
    return {
        record["experience_id"]: record
        for record in json.loads(SOURCE.read_text(encoding="utf-8"))["records"]
    }


def _prompt_text(case: dict[str, Any], experience_text: str) -> str:
    """The administrator prompt for one arm; ``_prompt`` reads no instance state."""
    return AdminService._prompt(  # noqa: SLF001 - pure method, no self use
        object(),
        case["requirement"],
        group_id=base.GROUP,
        max_tasks=1,
        members=[base.MEMBER],
        answers=[],
        project_name=case["case_id"],
        experience=experience_text,
    )


def freeze() -> None:
    records = _source_records()
    cases: list[dict[str, Any]] = []
    for case in CASES:
        case = dict(case)
        record = records[case["old_experience_id"]]
        case["old_values"] = list(dict.fromkeys([*case["old_values"], *_spec_paths(record)]))
        case["source_root_cause"] = record["root_cause"]
        case["source_action"] = record["action"]
        case["source_task_id"] = record["source_task_id"]
        decision = decide(
            dispatch_text=case["requirement"],
            items=[{"record": record, "selection": {}}],
            enabled=True,
        )
        case["freeze_gate"] = {
            key: decision[key]
            for key in (
                "triggered",
                "reason",
                "signals",
                "missing_dimensions",
                "uncovered_dimensions",
                "matched",
                "check_types",
                "line_count",
                "text",
            )
        }
        prompt_a = _prompt_text(case, "")
        prompt_q = _prompt_text(case, str(decision["text"]))
        case["prompt_sha256_a"] = base.sha(prompt_a)
        case["prompt_sha256_q"] = base.sha(prompt_q)
        case["prompts_equal_when_not_triggered"] = (
            not decision["triggered"] and base.sha(prompt_a) == base.sha(prompt_q)
        )
        cases.append(case)

    triggered = [case["case_id"] for case in cases if case["freeze_gate"]["triggered"]]
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "model": base.MODEL,
        "parameters": {**base.PARAMS, "max_tokens": 8192},
        "arms": {
            "A": "injection switch off",
            "Q": "switch on; relevant experience confirmed; gate decides per dispatch",
        },
        "calls_planned": 12,
        "retry_policy": "one real call per case per arm; no retry, no substitution",
        "cost_limit_cny_equivalent": base.COST_LIMIT,
        "gate_rule": (
            "inject only when the dispatch lacks path / count / banned-word check / "
            "source verification, and a confirmed experience names that dimension"
        ),
        "injection_content_rule": (
            "fixed check-type sentence only; no old value, no old slot text, no source task id"
        ),
        "cases": cases,
        "expected_triggered": triggered,
        "expected_trigger_count": len(triggered),
        "scoring": {
            "semantic": (
                "transferred / copied_wrong / missed / escalated / unjudgeable; a negative, "
                "exclusion or removal statement is not pollution"
            ),
            "unjudgeable": "truncated (length), timeout or invalid JSON; never merged into missed",
            "gap_fill": "case-specific predicate on the Q output; only for triggered cases",
        },
        "history_sha256": history_sha(),
        "source_sha256": base.sha(SOURCE.read_text(encoding="utf-8")),
        "script_sha256": base.sha(Path(__file__).read_text(encoding="utf-8")),
    }
    write_json(FROZEN, protocol)
    print(
        json.dumps(
            {
                "frozen": str(FROZEN),
                "cases": len(cases),
                "expected_triggered": triggered,
                "parameters": protocol["parameters"],
            },
            ensure_ascii=False,
        )
    )


def _record_experiences(store: ExperienceStore, case: dict[str, Any], records: dict[str, dict[str, Any]]) -> None:
    for role, experience_id in (
        ("relevant", case["old_experience_id"]),
        ("unrelated", case["unrelated_experience_id"]),
    ):
        source = records[experience_id]
        store.record_outcome(
            kind="request_changes",
            project=case["case_id"],
            task_category="docs",
            executor=base.MEMBER,
            spec_summary=f"{case['case_id']} {case['requirement']}",
            findings_summary=source["root_cause"],
            root_cause=source["root_cause"],
            action=source["action"],
            result=source["result"],
            contract_id=source["source_task_id"],
            source_task_id=source["source_task_id"],
            source_event_id=f"v6:{role}:{source['source_event_id']}",
            source="live",
        )


def _one_call(
    case: dict[str, Any],
    arm: str,
    records: dict[str, dict[str, Any]],
    pool_token: str,
    index: int,
) -> dict[str, Any]:
    """One isolated environment, one real call, full receipt."""
    with tempfile.TemporaryDirectory(prefix=f"experience-v6-{case['case_id']}-{arm}-") as temporary:
        chain, api, admin_token, _member_token, human_token = base._setup(Path(temporary))
        context = base._post(
            api,
            "/api/v1/groups/G-1/dispatch",
            {
                "request_id": f"v6-{case['case_id']}-{arm}",
                "title": f"{case['case_id']} · 条件触发注入场景",
                "goal": case["requirement"],
                "harness_id": base.MEMBER,
                "decided_by": base.ADMIN,
                "exclusive_paths": [case["path"]],
                "acceptance": "交付物存在且满足最新验收约束",
            },
            admin_token,
        )
        context_id = context["task_id"]
        store = ExperienceStore(Path(temporary) / "admin" / "experience.json")
        _record_experiences(store, case, records)
        relevant = next(
            record
            for record in store.records()
            if record["root_cause"] == records[case["old_experience_id"]]["root_cause"]
        )
        if arm == "Q":
            base._post(
                api,
                "/api/v1/experience-injection",
                {"enabled": True, "decided_by": "human-1"},
                human_token,
            )
            base._post(
                api,
                f"/api/v1/tasks/{context_id}/experiences/{relevant['experience_id']}/decision",
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
            condition=arm,
        )
        outcome: dict[str, Any] = {
            "case_id": case["case_id"],
            "arm": arm,
            "index": index,
            "context_task_id": context_id,
            "old_experience_id": relevant["experience_id"],
            "prompt_sha256_a": case["prompt_sha256_a"],
            "prompt_sha256_q": case["prompt_sha256_q"],
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
                draft = base._post(
                    api,
                    "/api/v1/adapter/admin/plan_draft",
                    {
                        "group_id": base.GROUP,
                        "title": f"{case['case_id']}-{arm}-v6",
                        "goal": case["requirement"],
                        "tasks": result["tasks"],
                        "reviewer_harness_ids": [base.MEMBER],
                        "opened_by": base.ADMIN,
                    },
                    admin_token,
                )
                outcome["plan_id"] = draft["plan_id"]
        except Exception as exc:  # noqa: BLE001 - the receipt must keep the error verbatim
            error = f"{type(exc).__name__}: {exc}"
            outcome["error"] = error
        outcome["trace"] = (outcome.get("result") or {}).get("experience_injection")
        outcome["error"] = error
        outcome["duration_seconds"] = round(time.monotonic() - started, 3)
        call = read_json(OUT / "calls" / f"{case['case_id']}-{arm}.json", {})
        outcome["receipt"] = {
            key: call.get(key)
            for key in (
                "requested_model",
                "actual_model",
                "parameters",
                "usage",
                "provider_cost",
                "ledger_cost",
                "duration_seconds",
                "finish_reason",
                "http_status",
                "prompt_sha256",
                "error",
            )
        }
    outcome["temporary_directory_removed"] = not Path(temporary).exists()
    return outcome


def _assert_injection_is_clean(case: dict[str, Any], trace: dict[str, Any]) -> None:
    text = str(trace.get("text") or "")
    leaked = [value for value in case["old_values"] if value and value in text]
    if leaked:
        raise RuntimeError(f"{case['case_id']}: injection text leaked old values {leaked}")
    if "exp-" in text or str(case["source_task_id"]) in text:
        raise RuntimeError(f"{case['case_id']}: injection text leaked a source id")


def run() -> None:
    if not FROZEN.exists():
        raise RuntimeError("freeze the v6 protocol first")
    frozen = read_json(FROZEN, {})
    cases = {case["case_id"]: case for case in frozen["cases"]}
    records = _source_records()
    production_before = base.sha(SOURCE.read_text(encoding="utf-8"))
    history_before = history_sha()
    pool_token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()

    preflight_env = tempfile.TemporaryDirectory(prefix="experience-v6-preflight-")
    chain, api, _admin_token, _member_token, _human_token = base._setup(Path(preflight_env.name))
    preflight = base.RealModelService(
        chain.database,
        harness_id=base.ADMIN,
        experience=ExperienceStore(Path(preflight_env.name) / "admin" / "experience.json"),
        scratch_dir=Path(preflight_env.name) / "admin-scratch",
        timeout_seconds=240,
        token=pool_token,
        case_id="PREFLIGHT",
        condition="preflight",
    )
    preflight._invoke_model('Return only {"ready":true}.')
    preflight_call = read_json(OUT / "calls" / "PREFLIGHT-preflight.json", {})
    preflight_env.cleanup()
    if preflight_call.get("error") or not preflight_call.get("usage"):
        raise RuntimeError("real-model preflight lacks a successful usage receipt")

    outcomes: list[dict[str, Any]] = []
    spent = base._provider_cost(preflight_call)
    for index, case in enumerate(frozen["cases"], start=1):
        for arm in ARMS:
            outcome = _one_call(case, arm, records, pool_token, index)
            trace = outcome.get("trace") or {}
            if arm == "Q":
                _assert_injection_is_clean(case, trace)
                if trace.get("triggered"):
                    outcome["gap_filled"] = _gap_filled(case, outcome.get("result"))
                else:
                    outcome["gap_filled"] = None
            outcomes.append(outcome)
            write_json(RESULTS, outcomes)
            call = read_json(OUT / "calls" / f"{case['case_id']}-{arm}.json", {})
            spent += base._provider_cost(call)
            if spent > base.COST_LIMIT:
                raise RuntimeError(f"cost limit reached: {spent}")

    production_after = base.sha(SOURCE.read_text(encoding="utf-8"))
    history_after = history_sha()
    write_json(
        ENVIRONMENT,
        {
            "temporary_directories_removed": all(
                row.get("temporary_directory_removed") for row in outcomes
            ),
            "production_experience_sha256_before": production_before,
            "production_experience_sha256_after": production_after,
            "production_unchanged": production_before == production_after,
            "history_sha256_before": history_before,
            "history_sha256_after": history_after,
            "history_unchanged": history_before == history_after,
            "completed_outcomes": len(outcomes),
        },
    )


def _gap_filled(case: dict[str, Any], result: dict[str, Any] | None) -> bool:
    tasks = (result or {}).get("tasks") or []
    if not tasks:
        return False
    task = tasks[0]
    text = str(task.get("goal") or "") + "\n" + str(task.get("acceptance") or "")
    predicate = case["gap_fill"]
    if not all(word in text for word in predicate.get("all", [])):
        return False
    any_words = predicate.get("any", [])
    return bool(any_words) and any(word in text for word in any_words)


def finalize() -> None:
    frozen = read_json(FROZEN, {})
    outcomes = read_json(RESULTS, [])
    cases = {case["case_id"]: case for case in frozen["cases"]}
    if len(outcomes) != 12:
        raise RuntimeError(f"expected 12 finished calls, found {len(outcomes)}")

    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        case = cases[outcome["case_id"]]
        judged = judge(
            old_values=case["old_values"],
            required_groups=case["required_groups"],
            result=outcome.get("result"),
            error=outcome.get("error"),
            finish_reason=(outcome.get("receipt") or {}).get("finish_reason"),
        )
        trace = outcome.get("trace") or {}
        rows.append(
            {
                "case_id": outcome["case_id"],
                "arm": outcome["arm"],
                "literal": literal_judge(
                    old_values=case["old_values"],
                    required_groups=case["required_groups"],
                    result=outcome.get("result"),
                ),
                **judged,
                "expected_gap": case["expected_gap"],
                "triggered": bool(trace.get("triggered")),
                "check_types": trace.get("check_types") or [],
                "gate_reason": trace.get("reason"),
                "missing_dimensions": trace.get("missing_dimensions") or [],
                "uncovered_dimensions": trace.get("uncovered_dimensions") or [],
                "gap_filled": outcome.get("gap_filled"),
                "prompt_sha256": (outcome.get("receipt") or {}).get("prompt_sha256"),
                "finish_reason": (outcome.get("receipt") or {}).get("finish_reason"),
                "error": outcome.get("error"),
            }
        )

    arms: dict[str, Any] = {}
    for arm in ARMS:
        group = [row for row in rows if row["arm"] == arm]
        counts = Counter(row["semantic"] for row in group)
        arms[arm] = {
            "n": len(group),
            "counts": {name: counts.get(name, 0) for name in ("transferred", "copied_wrong", "missed", "escalated", "unjudgeable")},
            "literal_counts": {
                name: sum(row["literal"] == name for row in group)
                for name in ("transferred", "copied_wrong", "missed", "escalated")
            },
            "transferred_wilson_95": base._wilson(counts.get("transferred", 0), len(group)),
            "triggered": sum(row["triggered"] for row in group),
            "gap_filled": sum(bool(row["gap_filled"]) for row in group),
            "unjudgeable": counts.get("unjudgeable", 0),
        }

    triggered_cases = [
        row for row in rows if row["arm"] == "Q" and row["triggered"]
    ]
    prompt_equality_violations = []
    for case in frozen["cases"]:
        if not case["freeze_gate"]["triggered"]:
            a_row = next(row for row in rows if row["case_id"] == case["case_id"] and row["arm"] == "A")
            q_row = next(row for row in rows if row["case_id"] == case["case_id"] and row["arm"] == "Q")
            if a_row["prompt_sha256"] != q_row["prompt_sha256"]:
                prompt_equality_violations.append(case["case_id"])

    pairs = []
    for case in frozen["cases"]:
        a_row = next(row for row in rows if row["case_id"] == case["case_id"] and row["arm"] == "A")
        q_row = next(row for row in rows if row["case_id"] == case["case_id"] and row["arm"] == "Q")
        pairs.append(
            {
                "case_id": case["case_id"],
                "expected_gap": case["expected_gap"],
                "a": a_row["semantic"],
                "q": q_row["semantic"],
                "a_triggered": a_row["triggered"],
                "q_triggered": q_row["triggered"],
                "q_gap_filled": q_row["gap_filled"],
                "pollution_direction": (
                    "fewer" if a_row["semantic"] == "copied_wrong" and q_row["semantic"] != "copied_wrong"
                    else "more" if q_row["semantic"] == "copied_wrong" and a_row["semantic"] != "copied_wrong"
                    else "same"
                ),
                "transfer_direction": (
                    "up" if a_row["semantic"] != "transferred" and q_row["semantic"] == "transferred"
                    else "down" if a_row["semantic"] == "transferred" and q_row["semantic"] != "transferred"
                    else "same"
                ),
            }
        )

    calls = [
        read_json(OUT / "calls" / f"{row['case_id']}-{row['arm']}.json", {}) for row in rows
    ]
    preflight = read_json(OUT / "calls" / "PREFLIGHT-preflight.json", {})
    charged = [*calls, preflight]
    environment = read_json(ENVIRONMENT, {})
    summary: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "model": base.MODEL,
        "parameters": frozen["parameters"],
        "calls_scored": len(rows),
        "arms": arms,
        "triggered_cases": [row["case_id"] for row in triggered_cases],
        "trigger_count": len(triggered_cases),
        "expected_trigger_count": frozen["expected_trigger_count"],
        "gap_filled": {
            row["case_id"]: {
                "check_types": row["check_types"],
                "gap_filled": bool(row["gap_filled"]),
                "semantic": row["semantic"],
                "a_semantic": next(
                    item["semantic"] for item in rows
                    if item["case_id"] == row["case_id"] and item["arm"] == "A"
                ),
            }
            for row in triggered_cases
        },
        "prompt_equality_violations": prompt_equality_violations,
        "pairs": pairs,
        "direction_counts": {
            "pollution": dict(Counter(pair["pollution_direction"] for pair in pairs)),
            "transfer": dict(Counter(pair["transfer_direction"] for pair in pairs)),
        },
        "wilcoxon": {
            "applicable": False,
            "reason": (
                "five-class outcome is nominal; a 6-pair signed-rank test would treat ranks as "
                "interval differences, so it is not computed"
            ),
        },
        "cost": {
            "provider_returned_cost_sum": round(sum(base._provider_cost(call) for call in charged), 6),
            "provider_cost_missing_calls": sum(call.get("provider_cost") is None for call in charged),
            "prompt_tokens": sum(int((call.get("usage") or {}).get("prompt_tokens") or 0) for call in charged),
            "completion_tokens": sum(int((call.get("usage") or {}).get("completion_tokens") or 0) for call in charged),
            "total_tokens": sum(int((call.get("usage") or {}).get("total_tokens") or 0) for call in charged),
            "usage_missing_calls": sum(not call.get("usage") for call in charged),
            "finish_reasons": dict(Counter(str(call.get("finish_reason")) for call in charged)),
            "calls_including_preflight": len(charged),
        },
        "environment": environment,
        "history_sha256_frozen": frozen["history_sha256"],
        "history_sha256_after": history_sha(),
        "rows": rows,
    }
    summary["history_unchanged"] = summary["history_sha256_frozen"] == summary["history_sha256_after"]
    write_json(SUMMARY, summary)
    write_json(OUT / "rows.json", rows)
    print(json.dumps({key: summary[key] for key in ("arms", "triggered_cases", "gap_filled", "direction_counts", "cost")}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run", "finalize"])
    args = parser.parse_args()
    base.OUT = OUT
    base.PARAMS = {**base.PARAMS, "max_tokens": 8192}
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    else:
        finalize()


if __name__ == "__main__":
    main()
