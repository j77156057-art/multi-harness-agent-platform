"""Real-model paired A/B/C evaluation for confirmed administrator experience injection."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.admin.experience import ExperienceStore
from app.admin.service import AdminHarnessError, AdminService, extract_json_object
from app.api.app import create_app
from app.auth.tokens import TokenService
from app.config import settings
from fastapi.testclient import TestClient
from tests.merge_support import build_chain, register_harness

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-ab-real-model" / "v1"
FROZEN = OUT / "frozen.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
MANUAL = OUT / "manual-review.json"
MODEL = "MiniMax-M3.1-Flash-Preview"
MODEL_TIER = "reserve-subscription"
UPSTREAM = "minimax"
PARAMS = {"temperature": 0, "max_tokens": 4096, "stream": False}
COST_LIMIT = 5.0
GROUP = "G-1"
ADMIN = "qoder-window-admin"
MEMBER = "qoder-member-1"
DATASET_VERSION = "experience-injection-ab-v1.0.0"

CASES = [
    {
        "case_id": "AB-01",
        "defect": "missing_task_id",
        "requirement": "补一条启动说明，写清怎么启动服务。",
        "path": "docs/ab-01.md",
        "root_cause": "派单目标漏了任务 id，返工时无法对应原任务",
        "action": "派单目标必须显式包含任务 id，不能只写一句泛泛的启动说明",
        "rule": "goal/acceptance 匹配“任务 id”或“任务标识”（不区分大小写）",
    },
    {
        "case_id": "AB-02",
        "defect": "unclear_output_path",
        "requirement": "补一份启动说明，交给已登记成员。",
        "path": "docs/startup.md",
        "root_cause": "产出物路径不明确，成员不知道该写到哪里",
        "action": "交付路径必须明确固定为 docs/startup.md",
        "rule": "exclusive_paths 包含且仅包含 docs/startup.md",
    },
    {
        "case_id": "AB-03",
        "defect": "missing_failure_acceptance",
        "requirement": "写启动文档，验收只检查文件是否存在。",
        "path": "docs/ab-03.md",
        "root_cause": "验收缺少启动命令失败时的失败路径检查",
        "action": "验收必须包含启动命令失败时的失败路径检查",
        "rule": "acceptance 同时包含“启动命令”和“失败/异常”",
    },
    {
        "case_id": "AB-04",
        "defect": "entry_not_preserved",
        "requirement": "更新 README 的启动段落。",
        "path": "README.md",
        "root_cause": "改写启动段落时既有 README.md 入口可能被删除",
        "action": "目标和验收都必须明确要求保留既有 README.md 入口",
        "rule": "goal 与 acceptance 均出现“保留”，且合并文本出现 README.md 或既有入口",
    },
    {
        "case_id": "AB-05",
        "defect": "missing_step_count",
        "requirement": "写启动步骤，不要只写一句能正常启动。",
        "path": "docs/ab-05.md",
        "root_cause": "验收没有限定启动步骤条数，无法检查是否完整",
        "action": "验收必须明确要求恰好三条启动步骤",
        "rule": "acceptance 同时包含“三条/3 条”和“步骤”",
    },
    {
        "case_id": "AB-06",
        "defect": "missing_log_path",
        "requirement": "写故障排查文档。",
        "path": "docs/ab-06.md",
        "root_cause": "故障排查没有指定错误日志路径，现场不可复现",
        "action": "验收必须要求记录错误日志路径 logs/startup.log",
        "rule": "goal 或 acceptance 出现 logs/startup.log",
    },
]


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def freeze() -> None:
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "model": MODEL,
        "model_tier": MODEL_TIER,
        "parameters": PARAMS,
        "channel": "real platform pool /pool/v1/chat/completions",
        "groups": {
            "A": "injection disabled; relevant experience unconfirmed",
            "B": "injection enabled; relevant experience confirmed by human-1",
            "C": "injection enabled; unrelated same-category experience confirmed by human-1",
        },
        "calls_per_group": 6,
        "preflight_calls_excluded_from_scoring": 1,
        "retry_policy": "one real model call per condition; no fallback, no retry, no model substitution",
        "cost_limit_cny_equivalent": COST_LIMIT,
        "cost_rule": "record provider cost and pool ledger deltas; selected tier is reserve subscription, never estimate unit prices",
        "cases": CASES,
        "unrelated_mapping": {
            case["case_id"]: CASES[(index + 1) % len(CASES)]["case_id"]
            for index, case in enumerate(CASES)
        },
        "primary_scoring": {
            "hit": "case-specific deterministic rule in CASES",
            "defect_present": "not hit for AB-01/03/04/05/06; AB-02 requires exact sole path",
            "human_review": "independent reading of raw goal/acceptance/paths; disagreements retained",
        },
        "positive_control": "C must not be credited with the target case's specific practice",
        "environment": "real Git, SQLite and FastAPI TestClient; real pool credentials; temporary repositories removed",
        "production_protection": "temporary experience.json only; production file SHA-256 compared before/after",
        "script_sha256": sha(Path(__file__).read_text(encoding="utf-8")),
    }
    write_json(FROZEN, protocol)
    print(json.dumps({"frozen": str(FROZEN), "calls_planned": 18, "cost_limit": COST_LIMIT}, ensure_ascii=False))


class RealModelService(AdminService):
    """AdminService prompt path with a single real pool call and full receipt capture."""

    def __init__(self, *args, token: str, case_id: str, condition: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.pool_token = token
        self.case_id = case_id
        self.condition = condition
        self.real_calls = 0

    def _pool_ledger(self) -> dict[str, Any]:
        try:
            response = httpx.get(
                "http://127.0.0.1:8848/api/v1/pool/status",
                headers={"Authorization": f"Bearer {self.pool_token}"},
                timeout=30.0,
            )
            response.raise_for_status()
            upstreams = (response.json().get("usage") or {}).get("upstreams") or {}
            return dict(upstreams.get(UPSTREAM) or {})
        except Exception:
            return {}

    def _invoke_model(self, prompt: str, route=None, log_key="") -> tuple[dict[str, Any], dict[str, Any]]:
        if self.real_calls:
            raise AdminHarnessError("evaluation one-call limit reached")
        self.real_calls += 1
        started = time.monotonic()
        ledger_before = self._pool_ledger()
        record: dict[str, Any] = {
            "case_id": self.case_id,
            "condition": self.condition,
            "started_at": now(),
            "requested_model": MODEL,
            "parameters": PARAMS,
            "prompt_sha256": sha(prompt),
        }
        try:
            response = httpx.post(
                "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.pool_token}"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    **PARAMS,
                },
                timeout=240.0,
            )
            document = response.json()
            ledger_after = self._pool_ledger()
            ledger_cost = None
            if isinstance(ledger_before.get("credits"), (int, float)) and isinstance(
                ledger_after.get("credits"), (int, float)
            ):
                ledger_cost = ledger_after["credits"] - ledger_before["credits"]
            record.update({
                "http_status": response.status_code,
                "duration_seconds": round(time.monotonic() - started, 3),
                "actual_model": document.get("model"),
                "usage": document.get("usage") or {},
                "finish_reason": (
                    (document.get("choices") or [{}])[0].get("finish_reason")
                    if document.get("choices") else None
                ),
                "provider_cost": document.get("cost", (document.get("usage") or {}).get("cost")),
                "ledger_before": ledger_before,
                "ledger_after": ledger_after,
                "ledger_cost": ledger_cost,
                "raw_response": document,
            })
            response.raise_for_status()
            content = str(document["choices"][0]["message"].get("content") or "")
            record["content"] = content
            payload = extract_json_object(content)
            record["parsed_payload"] = payload
            write_json(OUT / "calls" / f"{self.case_id}-{self.condition}.json", record)
            return payload, {
                "adapter": "real-pool-http",
                "channel": MODEL,
                "duration_seconds": record["duration_seconds"],
            }
        except Exception as exc:
            record.update({
                "duration_seconds": round(time.monotonic() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
            })
            write_json(OUT / "calls" / f"{self.case_id}-{self.condition}.json", record)
            if isinstance(exc, AdminHarnessError):
                raise
            raise AdminHarnessError(record["error"]) from exc


def _post(api: TestClient, path: str, body: dict[str, Any], token: str) -> dict[str, Any]:
    response = api.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
    if response.status_code >= 400:
        raise RuntimeError(f"{path} refused: {response.status_code} {response.text[:500]}")
    return response.json()


def _setup(chain_root: Path):
    chain = build_chain(chain_root)
    settings.data_dir = chain_root
    settings.auth_mode = "enforce"
    settings.admin_subjects = frozenset({ADMIN})
    register_harness(chain.database, ADMIN, provider="qoder")
    register_harness(chain.database, MEMBER, provider="tencent")
    tokens = TokenService(chain.database)
    admin_token = tokens.issue(subject_type="harness", subject_id=ADMIN)
    member_token = tokens.issue(subject_type="harness", subject_id=MEMBER)
    human_token = tokens.issue(subject_type="human", subject_id="human-1")
    api = TestClient(create_app(chain.database, chain.repo), base_url="http://127.0.0.1")
    return chain, api, admin_token, member_token, human_token


def _score(case: dict[str, Any], result: dict[str, Any] | None) -> dict[str, Any]:
    if not result or not result.get("tasks"):
        return {"hit": False, "defect_present": True, "reason": "no valid task result"}
    task = result["tasks"][0]
    goal = str(task.get("goal") or "")
    acceptance = str(task.get("acceptance") or "")
    paths = [str(item) for item in task.get("exclusive_paths") or []]
    text = goal + "\n" + acceptance
    defect = case["defect"]
    if defect == "missing_task_id":
        hit = bool(re.search(r"任务\s*(?:id|标识)", text, flags=re.IGNORECASE))
    elif defect == "unclear_output_path":
        hit = paths == ["docs/startup.md"]
    elif defect == "missing_failure_acceptance":
        hit = "启动命令" in acceptance and ("失败" in acceptance or "异常" in acceptance)
    elif defect == "entry_not_preserved":
        hit = "保留" in goal and "保留" in acceptance and (
            "README.md" in text or "既有入口" in text
        )
    elif defect == "missing_step_count":
        hit = ("三条" in acceptance or "3 条" in acceptance or "3条" in acceptance) and "步骤" in acceptance
    else:
        hit = "logs/startup.log" in text
    return {
        "hit": bool(hit),
        "defect_present": not bool(hit),
        "reason": "frozen deterministic rule",
        "goal": goal,
        "acceptance": acceptance,
        "exclusive_paths": paths,
    }


def _provider_cost(call: dict[str, Any]) -> float:
    value = call.get("ledger_cost")
    if isinstance(value, (int, float)):
        return float(value)
    value = call.get("provider_cost")
    return float(value) if isinstance(value, (int, float)) else 0.0


def run() -> None:
    if not FROZEN.exists():
        raise RuntimeError("freeze protocol first")
    calls: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    production_experience = Path(settings.data_dir) / "admin" / "experience.json"
    production_before = (
        sha(production_experience.read_text(encoding="utf-8")) if production_experience.exists() else None
    )
    with tempfile.TemporaryDirectory(prefix="experience-ab-") as temporary:
        chain, api, admin_token, member_token, human_token = _setup(Path(temporary))
        pool_token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
        # One preflight verifies the real channel's usage and finish_reason before the 18 scored calls.
        preflight = RealModelService(
            chain.database,
            harness_id=ADMIN,
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

        for index, case in enumerate(CASES):
            unrelated = CASES[(index + 1) % len(CASES)]
            context = _post(api, "/api/v1/groups/G-1/dispatch", {
                "request_id": f"context-{case['case_id']}",
                "title": f"{case['case_id']} · 缺陷场景",
                "goal": case["requirement"],
                "harness_id": MEMBER,
                "decided_by": ADMIN,
                "exclusive_paths": [case["path"]],
                "acceptance": "交付物存在且可检查",
            }, admin_token)
            context_id = context["task_id"]
            store = ExperienceStore(Path(temporary) / "admin" / "experience.json")
            for role, experience_case in (("relevant", case), ("unrelated", unrelated)):
                store.record_outcome(
                    kind="request_changes",
                    project=case["case_id"],
                    task_category="docs",
                    executor=MEMBER,
                    spec_summary=f"{case['case_id']} {case['requirement']}",
                    findings_summary=experience_case["root_cause"],
                    root_cause=experience_case["root_cause"],
                    action=experience_case["action"],
                    result="历史验收未通过",
                    contract_id=context_id,
                    source="live",
                )
            records = store.records()
            by_root = {record["root_cause"]: record for record in records}
            relevant = by_root[case["root_cause"]]
            unrelated_record = by_root[unrelated["root_cause"]]
            decision_base = f"/api/v1/tasks/{context_id}/experiences"
            _post(api, "/api/v1/experience-injection", {"enabled": False, "decided_by": "human-1"}, human_token)

            for condition in ("A", "B", "C"):
                if condition == "A":
                    _post(api, "/api/v1/experience-injection", {"enabled": False, "decided_by": "human-1"}, human_token)
                else:
                    _post(api, "/api/v1/experience-injection", {"enabled": True, "decided_by": "human-1"}, human_token)
                    selected = relevant if condition == "B" else unrelated_record
                    _post(
                        api,
                        f"{decision_base}/{selected['experience_id']}/decision",
                        {"action": "confirm", "decided_by": "human-1"},
                        human_token,
                    )
                service = RealModelService(
                    chain.database,
                    harness_id=ADMIN,
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
                    "relevant_experience_id": relevant["experience_id"],
                    "injected_experience_id": (
                        relevant["experience_id"] if condition == "B"
                        else unrelated_record["experience_id"] if condition == "C"
                        else None
                    ),
                }
                started = time.monotonic()
                try:
                    result = service.refine(
                        requirement=case["requirement"],
                        group_id=GROUP,
                        max_tasks=1,
                        members=[MEMBER],
                        project_name=case["case_id"],
                    )
                    outcome["result"] = result
                    if result.get("tasks"):
                        draft = _post(api, "/api/v1/adapter/admin/plan_draft", {
                            "group_id": GROUP,
                            "title": f"{case['case_id']}-{condition}",
                            "goal": case["requirement"],
                            "tasks": result["tasks"],
                            "reviewer_harness_ids": [MEMBER],
                            "opened_by": ADMIN,
                        }, admin_token)
                        outcome["plan_id"] = draft["plan_id"]
                    outcome["machine"] = _score(case, result)
                except Exception as exc:
                    outcome["error"] = f"{type(exc).__name__}: {exc}"
                    outcome["machine"] = _score(case, None)
                outcome["duration_seconds"] = round(time.monotonic() - started, 3)
                call = read_json(OUT / "calls" / f"{case['case_id']}-{condition}.json", {})
                outcome["receipt"] = {
                    key: call.get(key)
                    for key in (
                        "requested_model", "actual_model", "parameters", "usage",
                        "provider_cost", "ledger_cost", "duration_seconds", "finish_reason", "http_status", "error",
                    )
                }
                outcomes.append(outcome)
                write_json(RESULTS, outcomes)
                calls.append(call)
                spent = sum(_provider_cost(item) for item in [*calls, preflight_call])
                if spent > COST_LIMIT:
                    raise RuntimeError(f"cost limit reached: {spent}")
                if condition == "B":
                    _post(
                        api,
                        f"{decision_base}/{relevant['experience_id']}/decision",
                        {"action": "revoke", "decided_by": "human-1"},
                        human_token,
                    )
                elif condition == "C":
                    _post(
                        api,
                        f"{decision_base}/{unrelated_record['experience_id']}/decision",
                        {"action": "revoke", "decided_by": "human-1"},
                        human_token,
                    )
        settings.data_dir = ROOT / "data"
    production_after = (
        sha(production_experience.read_text(encoding="utf-8")) if production_experience.exists() else None
    )
    write_json(OUT / "environment.json", {
        "temporary_directory_removed": not Path(temporary).exists(),
        "production_experience_sha256_before": production_before,
        "production_experience_sha256_after": production_after,
        "production_unchanged": production_before == production_after,
        "completed_outcomes": len(outcomes),
    })


def _wilson(successes: int, total: int) -> list[float]:
    if not total:
        return [0.0, 0.0]
    z = 1.96
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4)]


def summarize() -> None:
    outcomes = read_json(RESULTS, [])
    manual = read_json(MANUAL, {})
    rows = []
    for outcome in outcomes:
        review = manual.get(f"{outcome['case_id']}-{outcome['condition']}", {})
        outcome["human"] = review or None
        machine = outcome.get("machine") or {}
        if review and bool(review.get("hit")) != bool(machine.get("hit")):
            outcome["scoring_disagreement"] = True
        rows.append(outcome)
    summary: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "model": MODEL,
        "parameters": PARAMS,
        "outcomes": len(rows),
        "conditions": {},
        "disagreements": [f"{row['case_id']}-{row['condition']}" for row in rows if row.get("scoring_disagreement")],
    }
    all_calls = [read_json(OUT / "calls" / f"{row['case_id']}-{row['condition']}.json", {}) for row in rows]
    preflight = read_json(OUT / "calls" / "PREFLIGHT-preflight.json", {})
    for condition in ("A", "B", "C"):
        group = [row for row in rows if row["condition"] == condition]
        hits = sum(bool((row.get("machine") or {}).get("hit")) for row in group)
        defects = sum(bool((row.get("machine") or {}).get("defect_present")) for row in group)
        human_hits = sum(bool((row.get("human") or {}).get("hit")) for row in group if row.get("human"))
        summary["conditions"][condition] = {
            "n": len(group),
            "machine_hits": hits,
            "machine_hit_rate": round(hits / len(group), 4) if group else None,
            "machine_hit_rate_wilson_95": _wilson(hits, len(group)),
            "defects_present": defects,
            "defect_rate": round(defects / len(group), 4) if group else None,
            "human_reviewed": sum(bool(row.get("human")) for row in group),
            "human_hits": human_hits,
            "errors": sum(bool(row.get("error")) for row in group),
        }
    pairs = []
    for case in CASES:
        group = {row["condition"]: row for row in rows if row["case_id"] == case["case_id"]}
        if all(condition in group for condition in ("A", "B", "C")):
            pairs.append({
                "case_id": case["case_id"],
                "B_fixed_A_defect": bool(group["A"]["machine"]["defect_present"] and group["B"]["machine"]["hit"]),
                "B_regressed": bool(group["A"]["machine"]["hit"] and group["B"]["machine"]["defect_present"]),
                "C_fixed_A_defect_without_relevant_experience": bool(
                    group["A"]["machine"]["defect_present"] and group["C"]["machine"]["hit"]
                ),
            })
    summary["paired"] = {
        "n": len(pairs),
        "B_fixed_A_defect": sum(pair["B_fixed_A_defect"] for pair in pairs),
        "B_regressed": sum(pair["B_regressed"] for pair in pairs),
        "C_fixed_A_defect_without_relevant_experience": sum(
            pair["C_fixed_A_defect_without_relevant_experience"] for pair in pairs
        ),
        "pairs": pairs,
    }
    charged = [*all_calls, preflight]
    summary["cost"] = {
        "provider_returned_cost_sum": round(sum(_provider_cost(call) for call in charged), 6),
        "pool_tier": MODEL_TIER,
        "cost_note": "actual pool ledger deltas; subscription quota, never an estimated unit price",
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
    write_json(SUMMARY, summary)
    write_json(OUT / "scored-results.json", rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["freeze", "run", "summarize"])
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    else:
        summarize()


if __name__ == "__main__":
    main()
