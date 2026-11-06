"""v4: compare original experience slots with deterministic value-free derived constraints."""

from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

from app.admin.experience import ExperienceStore
from app.admin.injection import confirmed_prompt
from app.admin.slots import slot_for_experience, slot_prompt
from scripts import evaluate_experience_injection_ab as base
from scripts import evaluate_experience_transfer as v3

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-transfer-ab-real-model" / "v4"
V3_OUT = ROOT / "artifacts" / "experience-transfer-ab-real-model" / "v3"
PREVIOUS = {
    version: ROOT / "artifacts" / "experience-transfer-ab-real-model" / version
    for version in ("v1", "v2", "v3")
}
SOURCE = ROOT / "data" / "admin" / "experience.json"
SCRIPT = Path(__file__).resolve()
FROZEN = OUT / "frozen.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
DATASET_VERSION = "experience-transfer-ab-v4-derived-constraints-v1.0.0"
ARMS = ("A", "B", "D")
CASES = copy.deepcopy(v3.CASES)

# The first matching rule selects a fixed template. No model participates in derivation.
TEMPLATE_RULES = [
    (
        "banned_words_current",
        r"禁用词|禁用串",
        "禁词清单必须与派单当前要求一致，不得沿用历史清单；交付正文不得复现禁词字面，并须全文逐项自检；复盘只能陈述已由证据确认的事实，不得把计划或预期写成已完成。",
    ),
    (
        "test_wiring",
        r"浏览器自测|Playwright|断言恒为|route 注册顺序",
        "验收必须先确认自测能够真实运行且断言有效：异步调用必须等待结果，断言应读取实际存在的状态或页面证据，路由匹配顺序不得让通配规则截获具体请求；修复后须核对计划与执行结果一致。",
    ),
    (
        "project_config",
        r"项目对话框|项目配置|自定义路径用例",
        "验收必须断言派单路径与验收口径来自当前项目配置，并覆盖默认与自定义配置；不得把允许路径或验收文本写死。",
    ),
    (
        "current_check_set",
        r"已核[\s\S]*未发现新问题",
        "验收必须按当前派单列出的检查集合逐项复核，不得沿用历史检查项编号、数量、路径或内容；已核结论只能陈述实际核对结果，最终是否通过由人类验收决定。",
    ),
    (
        "mapping_table_current",
        r"意见映射表|列固定为",
        "验收必须检查派单点名的交付文件实际存在；表格结构、列集合和条目编号必须与当前派单要求一致；意见逐条引用且不得改写或合并；待补项必须具体可核验，交付风格保持一致。",
    ),
    (
        "source_inventory",
        r"authoritative source paths|实际存在的相关 CLI|实际文件路径、对应章节",
        "证据文件清单只能包含派单点名的文件；每个文件必须实际存在，并逐项给出可核查章节或段落、能支持的结论及限制。平台注入信息、背景材料或泛指名称不能替代对仓库实际文件和正文的核实，未确认项必须明确标注。",
    ),
    (
        "source_traceability",
        r"占位或示例性引用|可核查的具体权威文件路径",
        "每个字段或结论都必须给出可核查的权威来源位置；占位、示例或不可追溯引用不得登记为已确认，无法核实的条目必须明确标为待确认。",
    ),
    (
        "count_and_diff",
        r"待补项[\s\S]*可读 diff|待补项[\s\S]*可读的 diff",
        "验收必须核对派单点名的交付路径实际存在，待补项数量必须与派单要求一致，并通过完整可读 diff 确认未创建或改动允许范围之外的文件。",
    ),
    (
        "factual_scope",
        r"将来时|未来时|预期口吻",
        "复盘只能陈述已经由提交记录和实际 diff 证实的事实，不得预述未来动作或作未经落实的自我声明；交付范围陈述必须与派单允许路径和实际变更一致。",
    ),
    (
        "consistency_and_complete_diff",
        r"自相矛盾",
        "验收必须核对文档结论之间以及结论与证据是否一致，并检查交付 diff 完整可读、文档结构闭合；未核实内容不得写成既定事实。",
    ),
    (
        "evidence_boundary",
        r"incomplete|time-bounded|证据边界",
        "验收必须检查 diff 与文档结构完整闭合；所有结论都应能由所引证据独立复核，未最终确认或超出证据边界的内容必须标明时限与不确定性，不得写成既定事实。",
    ),
    (
        "complete_diff",
        r"readable diff|可读的实际 diff|readable patch",
        "验收必须要求提交完整、可读、覆盖全部变更文件的 diff，并据此核对派单点名交付路径实际存在及变更范围仅包含允许路径。",
    ),
]

PATH_RE = re.compile(
    r"(?<![\w.-])(?:[\w.-]+/)*[\w.-]+\.(?:md|txt|log|json|py|js|mjs|ts|tsx|css|html)(?![\w.-])",
    re.I,
)
NEGATIVE_PREFIX_RE = re.compile(
    r"不存在|不包含|不含|不得存在|不得包含|不得出现|不得沿用|不能包含|不能出现|"
    r"不可包含|不可出现|禁止|移除|删除|排除|避免|无需|不带|未出现|未包含|没有"
)
NEGATIVE_SUFFIX_RE = re.compile(r"不得出现|不得使用|不得沿用|应予删除|应移除|被排除|零命中")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def source_records() -> dict[str, dict[str, Any]]:
    return {
        record["experience_id"]: record
        for record in json.loads(SOURCE.read_text(encoding="utf-8"))["records"]
    }


def primary_old_values(case: dict[str, Any], records: dict[str, dict[str, Any]]) -> list[str]:
    old = records[case["old_experience_id"]]
    return list(dict.fromkeys([*case["old_values"], *v3._spec_paths(old)]))


def _add_value(values: list[str], value: str) -> None:
    cleaned = value.strip().strip("`\"'“”‘’「」『』()[]（） ")
    cleaned = cleaned.rstrip("，。；、,:;")
    if cleaned and cleaned not in values:
        values.append(cleaned)


def erased_values(record: dict[str, Any], manual_old_values: list[str]) -> list[str]:
    """Extract concrete value classes from the exact original slot and source text."""
    slot = slot_for_experience(record)
    original_prompt = slot_prompt(slot) if slot else ""
    text = "\n".join([str(record.get("root_cause") or ""), str(record.get("action") or ""), original_prompt])
    values: list[str] = []
    for value in manual_old_values:
        _add_value(values, value)
    for match in PATH_RE.finditer(text):
        path = match.group(0)
        _add_value(values, path)
        parts = path.split("/")
        if parts:
            _add_value(values, parts[-1])
        for part in parts[:-1]:
            _add_value(values, part)
    for pattern in (
        r"§\s*\d+(?:\.\d+)*",
        r"附录\s*[A-Za-z]",
        r"第\s*(?:\d+|[零一二三四五六七八九十两]+)\s*(?:条|行|项|节)",
        r"(?<![A-Za-z0-9_.])(?:\d+|[零一二三四五六七八九十两]+)\s*(?:列|条|项|处|个|串|种|秒|行|节)",
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:topics|files|columns|steps|items)\b",
        r"第[一二三四五六七八九十两\d]+轮",
        r"\bREADME(?:\.md)?\b",
        r"「意见映射表」",
        r"\bF\d+\b",
        r"\bround-\d+\b",
        r"\b[0-9a-f]{40}\b",
        r"\b[A-Za-z_][\w-]*=[A-Za-z0-9_.-]+\b",
        r"\b[a-z]+[A-Z][A-Za-z0-9]*\b",
        r"(?<!\w)/(?:api|verification|output|tasks)(?:/[\w*.-]+)+",
    ):
        for match in re.finditer(pattern, text):
            _add_value(values, match.group(0))
    for match in re.finditer(r"`([^`\n]{1,160})`", text):
        _add_value(values, match.group(1))
    for match in re.finditer(r"(?<![\w])'([A-Za-z0-9_.-]+)'", text):
        _add_value(values, match.group(1))
    default_paths = re.search(r"默认\s*([A-Za-z0-9_.,-]+)", text)
    if default_paths:
        for value in default_paths.group(1).split(","):
            _add_value(values, value)
    custom_path = re.search(r"只填\s*([A-Za-z0-9_.-]+)", text)
    if custom_path:
        _add_value(values, custom_path.group(1))
    for match in re.finditer(r"禁用词(?:零命中)?[（(]([^）)]+)[）)]", text):
        for value in re.split(r"[、,/]", match.group(1)):
            _add_value(values, value)
    for match in re.finditer(r"禁用词『([^』]+)』", text):
        _add_value(values, match.group(1))
    columns = re.search(r"列固定为：([^。]+)", text)
    if columns:
        for value in columns.group(1).split("/"):
            _add_value(values, value)
            _add_value(values, re.sub(r"（.*", "", value))
            options = re.search(r"（([^）]+)）", value)
            if options:
                for option in options.group(1).split("·"):
                    _add_value(values, option)
    for match in re.finditer(r"“([^”]{1,80})”(?=占位)|“([^”]{1,80})”", text):
        value = match.group(1) or match.group(2)
        if "文档" in value and ("CLI" in value or "回执" in value):
            _add_value(values, value)
    source_task = str(record.get("source_task_id") or "")
    if source_task:
        _add_value(values, source_task)
    return values


def derive_constraints() -> list[dict[str, Any]]:
    records = source_records()
    manual_by_id = {
        case["old_experience_id"]: primary_old_values(case, records) for case in CASES
    }
    rows: list[dict[str, Any]] = []
    for record in records.values():
        slot = slot_for_experience(record)
        if not slot or slot["slot"] != "acceptance_check":
            continue
        root = str(record.get("root_cause") or "")
        matched = next(
            ((name, pattern, text) for name, pattern, text in TEMPLATE_RULES if re.search(pattern, root, re.I)),
            None,
        )
        if matched is None:
            raise RuntimeError(f"no derived template for {record['experience_id']}")
        template_id, pattern, derived = matched
        erased = erased_values(record, manual_by_id.get(record["experience_id"], []))
        leaks = [value for value in erased if value in derived]
        if leaks:
            raise RuntimeError(f"derived constraint retains old values for {record['experience_id']}: {leaks}")
        original_slot = slot_prompt(slot)
        derived_slot = slot_prompt({
            "slot": "acceptance_check",
            "target_field": "验收.必检项",
            "expected": derived,
            "rule": derived,
            "evidence_task": "",
        })
        reparsed = slot_for_experience({
            "kind": "request_changes", "root_cause": derived,
            "action": derived, "source_task_id": "",
        })
        if not reparsed or reparsed["slot"] != "acceptance_check" or reparsed["expected"] != derived:
            raise RuntimeError(f"derived constraint is not injectable for {record['experience_id']}")
        rows.append({
            "experience_id": record["experience_id"],
            "source_task_id": record["source_task_id"],
            "template_id": template_id,
            "template_match_pattern": pattern,
            "derived_constraint": derived,
            "erased_values": erased,
            "original_slot_sha256": base.sha(original_slot),
            "derived_slot_prompt": derived_slot,
            "derived_slot_sha256": base.sha(derived_slot),
            "derivation": "first matching frozen regex selects a fixed template; no model rewriting",
        })
    if len(rows) != 15:
        raise RuntimeError(f"expected 15 acceptance_check rows, got {len(rows)}")
    return rows


def _record_original(store: ExperienceStore, case: dict[str, Any], role: str, record: dict[str, Any]) -> None:
    store.record_outcome(
        kind="request_changes",
        project=case["case_id"],
        task_category="docs",
        executor=base.MEMBER,
        spec_summary=f"{case['case_id']} {case['requirement']}",
        findings_summary=record["root_cause"],
        root_cause=record["root_cause"],
        action=record["action"],
        result=record["result"],
        contract_id=record["source_task_id"],
        source_task_id=record["source_task_id"],
        source_event_id=f"transfer:{role}:{record['source_event_id']}",
        source="live",
    )


def _prepare_case(case: dict[str, Any], temporary: Path, records: dict[str, dict[str, Any]]):
    chain, api, admin_token, member_token, human_token = base._setup(temporary)
    old = records[case["old_experience_id"]]
    unrelated = records[case["unrelated_experience_id"]]
    context = base._post(api, "/api/v1/groups/G-1/dispatch", {
        "request_id": f"transfer-{case['case_id']}",
        "title": f"{case['case_id']} · 新值迁移场景",
        "goal": case["requirement"],
        "harness_id": base.MEMBER,
        "decided_by": base.ADMIN,
        "exclusive_paths": [case["path"]],
        "acceptance": "交付物存在且满足最新验收约束",
    }, admin_token)
    store = ExperienceStore(temporary / "admin" / "experience.json")
    _record_original(store, case, "relevant", old)
    _record_original(store, case, "unrelated", unrelated)
    by_root = {record["root_cause"]: record for record in store.records()}
    return {
        "chain": chain,
        "api": api,
        "admin_token": admin_token,
        "human_token": human_token,
        "context_id": context["task_id"],
        "store": store,
        "relevant": by_root[old["root_cause"]],
        "unrelated": by_root[unrelated["root_cause"]],
        "old": old,
    }


def _prepare_arm(
    prepared: dict[str, Any], case: dict[str, Any], condition: str, derived_by_id: dict[str, dict[str, Any]],
    temporary: Path,
):
    api = prepared["api"]
    human_token = prepared["human_token"]
    context_id = prepared["context_id"]
    store = prepared["store"]
    base._post(api, "/api/v1/experience-injection", {
        "enabled": condition != "A", "decided_by": "human-1",
    }, human_token)
    selected = None
    derived = None
    if condition != "A":
        if condition == "B":
            selected = prepared["relevant"]
        else:
            derived = derived_by_id[case["old_experience_id"]]
            store.record_outcome(
                kind="request_changes",
                project=case["case_id"],
                task_category="docs",
                executor=base.MEMBER,
                spec_summary=f"{case['case_id']} {case['requirement']}",
                findings_summary=derived["derived_constraint"],
                root_cause=derived["derived_constraint"],
                action=derived["derived_constraint"],
                result="derived constraint",
                contract_id="",
                source_task_id="",
                source_event_id=f"transfer:derived:{derived['experience_id']}",
                source="live",
            )
            selected = next(
                record for record in store.records()
                if record["root_cause"] == derived["derived_constraint"]
            )
        base._post(
            api,
            f"/api/v1/tasks/{context_id}/experiences/{selected['experience_id']}/decision",
            {"action": "confirm", "decided_by": "human-1"},
            human_token,
        )
    service = base.RealModelService(
        prepared["chain"].database,
        harness_id=base.ADMIN,
        experience=store,
        scratch_dir=temporary / "admin-scratch",
        timeout_seconds=240,
        token=(ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip(),
        case_id=case["case_id"],
        condition=condition,
    )
    return service, selected, derived


def _prompt_for(service: Any, case: dict[str, Any]) -> str:
    experience, _ = confirmed_prompt(
        service.database, service.experience, group_id=base.GROUP, project_name=case["case_id"]
    )
    return service._prompt(
        case["requirement"],
        group_id=base.GROUP,
        max_tasks=1,
        members=[base.MEMBER],
        answers=[],
        project_name=case["case_id"],
        experience=experience,
    )


def build_prompt_plan(derived_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = source_records()
    derived_by_id = {row["experience_id"]: row for row in derived_rows}
    plan: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="experience-transfer-v4-prompts-") as temporary:
        for case in CASES:
            prepared = _prepare_case(case, Path(temporary) / case["case_id"], records)
            for condition in ARMS:
                service, selected, derived = _prepare_arm(
                    prepared, case, condition, derived_by_id, Path(temporary)
                )
                prompt = _prompt_for(service, case)
                prompt_hash = base.sha(prompt)
                old_v3 = read_json(V3_OUT / "calls" / f"{case['case_id']}-{condition}.json", {}) if condition != "D" else {}
                injected_old_values = (
                    derived_by_id[case["old_experience_id"]]["erased_values"]
                    if condition == "B" else []
                )
                new_literals = [
                    value for group in case["required_groups"] for value in group
                ]
                discriminating_injected_old_values = [
                    value for value in injected_old_values
                    if not any(value in new_value for new_value in new_literals)
                ]
                expanded = list(dict.fromkeys([
                    *primary_old_values(case, records), *discriminating_injected_old_values,
                ]))
                row = {
                    "case_id": case["case_id"],
                    "condition": condition,
                    "source_experience_id": case["old_experience_id"] if condition != "A" else None,
                    "derived_template_id": derived["template_id"] if derived else None,
                    "prompt_sha256": prompt_hash,
                    "v3_prompt_sha256": old_v3.get("prompt_sha256"),
                    "matches_v3_prompt": prompt_hash == old_v3.get("prompt_sha256") if condition != "D" else None,
                    "injected_old_values": injected_old_values,
                    "expanded_old_values": expanded,
                }
                if condition == "D":
                    leaked = [
                        value for value in derived["erased_values"]
                        if value in derived["derived_slot_prompt"]
                    ]
                    if leaked:
                        raise RuntimeError(f"D prompt contains erased values for {case['case_id']}: {leaked}")
                plan.append(row)
                if condition != "A":
                    base._post(
                        prepared["api"],
                        f"/api/v1/tasks/{prepared['context_id']}/experiences/{selected['experience_id']}/decision",
                        {"action": "revoke", "decided_by": "human-1"},
                        prepared["human_token"],
                    )
    mismatches = [f"{row['case_id']}-{row['condition']}" for row in plan if row["matches_v3_prompt"] is False]
    if mismatches:
        raise RuntimeError(f"A/B prompts differ from v3: {mismatches}")
    return plan


def protocol() -> dict[str, Any]:
    derived = derive_constraints()
    records = source_records()
    prompt_plan = build_prompt_plan(derived)
    return {
        "dataset_version": DATASET_VERSION,
        "frozen_at": v3.now(),
        "model": base.MODEL,
        "parameters": {**base.PARAMS, "max_tokens": 8192},
        "calls_per_group": 6,
        "groups": {
            "A": "injection disabled; prompt must equal v3 A",
            "B": "original relevant old acceptance_check slot; prompt must equal v3 B",
            "D": "deterministically derived value-free constraint from the same source experience",
        },
        "retry_policy": "one real call per arm per case; no retry, fallback, substitution, or token-budget increase",
        "cost_limit_cny_equivalent": base.COST_LIMIT,
        "cases": CASES,
        "primary_old_values_by_case": {
            case["case_id"]: primary_old_values(case, records) for case in CASES
        },
        "derived_constraints": derived,
        "derivation_rules": {
            "source": "all records classified as acceptance_check by app.admin.slots",
            "template_selection": "first matching TEMPLATE_RULES regex; unmatched source aborts freeze",
            "erasure": "manual v3 old values plus paths/basenames/directories, quantities, banned words, columns, F IDs, sections, inline identifiers, commit hashes and source task IDs",
            "validation": "15 rows required; no erased value may appear in derived text or D prompt",
            "prompt_metadata": "source experience IDs and erased values stay in frozen.json, not in the D prompt; evidence_task is 未记录",
        },
        "scoring": {
            "primary": v3.read_json(v3.FROZEN, {})["scoring"],
            "semantic_review": {
                "effect": "record only; never changes primary category",
                "clause_split": "Chinese/English sentence and semicolon boundaries plus newlines",
                "negative_prefix_window_chars": 40,
                "negative_suffix_window_chars": 20,
                "negative_prefix_regex": NEGATIVE_PREFIX_RE.pattern,
                "negative_suffix_regex": NEGATIVE_SUFFIX_RE.pattern,
                "labels": "not_applicable without literal old hits; negative_or_exclusion; positive_or_unclear; mixed",
            },
            "expanded_old_values": {
                "effect": "record only; never changes primary category and never compared directly with v3 counts",
                "rule": "union of case primary old values and erased_values for every original experience injected into that arm; A adds none, B adds the original relevant source values, D adds none because derived injection is value-free; the same rule would add unrelated injected values for any C arm; additional injected values contained in a required-new literal are removed as non-discriminating", 
                "category": "rerun the same four-tier rule using expanded_old_values",
                "change_label": "由判据扩展导致 when primary transferred becomes expanded copied_wrong",
            },
        },
        "prompt_plan": prompt_plan,
        "previous_artifacts_sha256": {version: v3.tree_sha(path) for version, path in PREVIOUS.items()},
        "source_sha256": base.sha(SOURCE.read_text(encoding="utf-8")),
        "script_sha256": base.sha(SCRIPT.read_text(encoding="utf-8")),
    }


def _task_text(result: dict[str, Any] | None) -> str:
    tasks = (result or {}).get("tasks") or []
    if not tasks:
        return "\n".join((result or {}).get("questions") or [])
    task = tasks[0]
    return str(task.get("goal") or "") + "\n" + str(task.get("acceptance") or "") + "\n" + "\n".join(task.get("exclusive_paths") or [])


def _category_with_values(case: dict[str, Any], result: dict[str, Any] | None, old_values: list[str]) -> tuple[str, list[str]]:
    tasks = (result or {}).get("tasks") or []
    if not tasks:
        questions = "\n".join((result or {}).get("questions") or [])
        old = [value for value in old_values if value in questions]
        new = [value for group in case["required_groups"] for value in group if value in questions]
        if old and new and "冲突" in questions:
            return "escalated", old
        return "missed", []
    text = _task_text(result)
    old_hits = [value for value in old_values if value in text]
    if old_hits:
        return "copied_wrong", old_hits
    missing = [group for group in case["required_groups"] if not any(value in text for value in group)]
    if missing:
        return "missed", []
    return "transferred", []


def semantic_review(text: str, old_hits: list[str]) -> dict[str, Any]:
    if not old_hits:
        return {
            "label": "not_applicable", "negative_or_exclusion_values": [],
            "positive_or_unclear_values": [], "effect": "record only",
        }
    clauses = [part for part in re.split(r"[。；;\n]+", text) if part]
    negative: list[str] = []
    positive: list[str] = []
    for value in old_hits:
        is_negative = False
        for clause in clauses:
            start = clause.find(value)
            if start < 0:
                continue
            prefix = clause[max(0, start - 40):start]
            suffix = clause[start + len(value):start + len(value) + 20]
            if NEGATIVE_PREFIX_RE.search(prefix) or NEGATIVE_SUFFIX_RE.search(suffix):
                is_negative = True
                break
        (negative if is_negative else positive).append(value)
    label = "mixed" if negative and positive else "negative_or_exclusion" if negative else "positive_or_unclear"
    return {
        "label": label,
        "negative_or_exclusion_values": negative,
        "positive_or_unclear_values": positive,
        "effect": "record only; primary category unchanged",
    }


def _receipt(call: dict[str, Any]) -> dict[str, Any]:
    return {
        key: call.get(key)
        for key in (
            "requested_model", "actual_model", "parameters", "usage", "provider_cost",
            "ledger_cost", "duration_seconds", "finish_reason", "http_status", "error",
        )
    }


def _assert_frozen_script() -> dict[str, Any]:
    frozen = read_json(FROZEN, {})
    if not frozen:
        raise RuntimeError("freeze v4 first")
    if base.sha(SCRIPT.read_text(encoding="utf-8")) != frozen["script_sha256"]:
        raise RuntimeError("evaluation script changed after freeze")
    return frozen


def run_one(case_id: str, condition: str) -> None:
    frozen = _assert_frozen_script()
    if condition not in ARMS or case_id not in {case["case_id"] for case in CASES}:
        raise RuntimeError("unknown case or arm")
    key = f"{case_id}-{condition}"
    outcome_path = OUT / "outcomes" / f"{key}.json"
    call_path = OUT / "calls" / f"{key}.json"
    if outcome_path.exists() or call_path.exists():
        raise RuntimeError(f"refusing to repeat {key}")
    case = next(case for case in CASES if case["case_id"] == case_id)
    case = {**case, "old_values": frozen["primary_old_values_by_case"][case_id]}
    plan = next(row for row in frozen["prompt_plan"] if row["case_id"] == case_id and row["condition"] == condition)
    derived_by_id = {row["experience_id"]: row for row in frozen["derived_constraints"]}
    records = source_records()
    production_before = base.sha(SOURCE.read_text(encoding="utf-8"))
    script_before = base.sha(SCRIPT.read_text(encoding="utf-8"))
    outcome: dict[str, Any] = {
        "case_id": case_id,
        "condition": condition,
        "old_experience_id": case["old_experience_id"],
        "source_experience_id": plan["source_experience_id"],
        "derived_template_id": plan["derived_template_id"],
        "terminal_state": "recorded",
    }
    temporary_name = ""
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="experience-transfer-v4-") as temporary:
            temporary_name = temporary
            prepared = _prepare_case(case, Path(temporary) / case["case_id"], records)
            outcome["context_task_id"] = prepared["context_id"]
            service, selected, derived = _prepare_arm(prepared, case, condition, derived_by_id, Path(temporary))
            outcome["injected_experience_id"] = selected["experience_id"] if selected else None
            try:
                result = service.refine(
                    requirement=case["requirement"],
                    group_id=base.GROUP,
                    max_tasks=1,
                    members=[base.MEMBER],
                    project_name=case_id,
                )
                outcome["result"] = result
                if result.get("tasks"):
                    draft = base._post(prepared["api"], "/api/v1/adapter/admin/plan_draft", {
                        "group_id": base.GROUP,
                        "title": f"{case_id}-{condition}-derived-transfer-v4",
                        "goal": case["requirement"],
                        "tasks": result["tasks"],
                        "reviewer_harness_ids": [base.MEMBER],
                        "opened_by": base.ADMIN,
                    }, prepared["admin_token"])
                    outcome["plan_id"] = draft["plan_id"]
            except Exception as exc:
                outcome["error"] = f"{type(exc).__name__}: {exc}"
            finally:
                if selected:
                    base._post(
                        prepared["api"],
                        f"/api/v1/tasks/{prepared['context_id']}/experiences/{selected['experience_id']}/decision",
                        {"action": "revoke", "decided_by": "human-1"},
                        prepared["human_token"],
                    )
    except Exception as exc:
        outcome["error"] = f"{type(exc).__name__}: {exc}"
    outcome["duration_seconds"] = round(time.monotonic() - started, 3)
    call = read_json(call_path, {})
    outcome["receipt"] = _receipt(call)
    outcome["prompt_sha256"] = call.get("prompt_sha256")
    outcome["prompt_matches_frozen"] = call.get("prompt_sha256") == plan["prompt_sha256"]
    outcome["prompt_matches_v3"] = (
        call.get("prompt_sha256") == plan["v3_prompt_sha256"] if condition != "D" else None
    )
    category, old_hits = _category_with_values(case, outcome.get("result"), case["old_values"])
    expanded_category, expanded_hits = _category_with_values(case, outcome.get("result"), plan["expanded_old_values"])
    outcome["category"] = category
    outcome["copied_old_values"] = old_hits
    outcome["semantic_review"] = semantic_review(_task_text(outcome.get("result")), old_hits)
    outcome["expanded_review"] = {
        "category": expanded_category,
        "old_value_hits": expanded_hits,
        "changed_from_primary": expanded_category != category,
        "note": "由判据扩展导致" if category == "transferred" and expanded_category == "copied_wrong" else "",
        "effect": "record only; not directly comparable with v3",
    }
    write_json(outcome_path, outcome)
    environment = {
        "temporary_directory_removed": not Path(temporary_name).exists() if temporary_name else True,
        "production_experience_sha256_before": production_before,
        "production_experience_sha256_after": base.sha(SOURCE.read_text(encoding="utf-8")),
        "production_unchanged": production_before == base.sha(SOURCE.read_text(encoding="utf-8")),
        "script_sha256_before": script_before,
        "script_sha256_after": base.sha(SCRIPT.read_text(encoding="utf-8")),
        "script_unchanged": script_before == base.sha(SCRIPT.read_text(encoding="utf-8")),
        "call_receipt_present": bool(call),
        "terminal_state": "recorded",
    }
    write_json(OUT / "environments" / f"{key}.json", environment)
    charged = [read_json(path, {}) for path in (OUT / "calls").glob("*.json")]
    spent = sum(base._provider_cost(item) for item in charged)
    if spent > base.COST_LIMIT:
        raise RuntimeError(f"cost limit reached: {spent}")
    print(json.dumps({
        "key": key, "category": category, "expanded_category": expanded_category,
        "semantic": outcome["semantic_review"]["label"], "finish_reason": call.get("finish_reason"),
        "usage": call.get("usage"), "ledger_cost": call.get("ledger_cost"),
        "prompt_matches_frozen": outcome["prompt_matches_frozen"],
        "prompt_matches_v3": outcome["prompt_matches_v3"], "error": outcome.get("error"),
    }, ensure_ascii=False))


def finalize() -> None:
    frozen = _assert_frozen_script()
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for case in CASES:
        for condition in ARMS:
            key = f"{case['case_id']}-{condition}"
            path = OUT / "outcomes" / f"{key}.json"
            row = read_json(path, None)
            if row is None:
                call = read_json(OUT / "calls" / f"{key}.json", {})
                if not call:
                    missing.append(key)
                    continue
                case = {**case, "old_values": frozen["primary_old_values_by_case"][case["case_id"]]}
                plan = next(item for item in frozen["prompt_plan"] if item["case_id"] == case["case_id"] and item["condition"] == condition)
                category, hits = _category_with_values(case, None, case["old_values"])
                expanded_category, expanded_hits = _category_with_values(case, None, plan["expanded_old_values"])
                row = {
                    "case_id": case["case_id"], "condition": condition,
                    "old_experience_id": case["old_experience_id"],
                    "source_experience_id": plan["source_experience_id"],
                    "derived_template_id": plan["derived_template_id"],
                    "error": call.get("error") or "process ended before outcome persistence; no retry",
                    "terminal_state": "recovered_from_call_receipt",
                    "receipt": _receipt(call), "category": category, "copied_old_values": hits,
                    "semantic_review": semantic_review("", []),
                    "expanded_review": {
                        "category": expanded_category, "old_value_hits": expanded_hits,
                        "changed_from_primary": expanded_category != category, "note": "",
                    },
                }
                write_json(path, row)
            rows.append(row)
    if missing:
        raise RuntimeError(f"calls never attempted; invoke each exactly once before finalize: {missing}")
    write_json(RESULTS, rows)
    environments = [read_json(OUT / "environments" / f"{row['case_id']}-{row['condition']}.json", {}) for row in rows]
    environment = {
        "completed_outcomes": len(rows),
        "all_terminal": len(rows) == 18 and all(row.get("terminal_state") for row in rows),
        "temporary_directories_removed": all(item.get("temporary_directory_removed") for item in environments),
        "production_unchanged": all(item.get("production_unchanged") for item in environments),
        "script_unchanged": all(item.get("script_unchanged") for item in environments),
        "production_experience_sha256_before": environments[0].get("production_experience_sha256_before"),
        "production_experience_sha256_after": base.sha(SOURCE.read_text(encoding="utf-8")),
        "script_sha256": base.sha(SCRIPT.read_text(encoding="utf-8")),
        "previous_artifacts_sha256_after": {version: v3.tree_sha(path) for version, path in PREVIOUS.items()},
    }
    environment["previous_artifacts_unchanged"] = environment["previous_artifacts_sha256_after"] == frozen["previous_artifacts_sha256"]
    write_json(OUT / "environment.json", environment)
    print(json.dumps(environment, ensure_ascii=False, indent=2))


def summarize() -> None:
    frozen = _assert_frozen_script()
    rows = read_json(RESULTS, [])
    if len(rows) != 18:
        raise RuntimeError("finalize 18 terminal outcomes before summarize")
    summary: dict[str, Any] = {
        "dataset_version": DATASET_VERSION,
        "model": base.MODEL,
        "parameters": frozen["parameters"],
        "outcomes": len(rows),
        "conditions": {},
        "copied_wrong_evidence": [],
        "expanded_copied_wrong_evidence": [],
    }
    for row in rows:
        evidence = {
            "case_id": row["case_id"], "condition": row["condition"],
            "old_values": row["copied_old_values"],
            "semantic_review": row["semantic_review"],
            "goal": (row["result"]["tasks"][0].get("goal") if row.get("result") and row["result"].get("tasks") else None),
            "acceptance": (row["result"]["tasks"][0].get("acceptance") if row.get("result") and row["result"].get("tasks") else None),
            "exclusive_paths": (row["result"]["tasks"][0].get("exclusive_paths") if row.get("result") and row["result"].get("tasks") else None),
        }
        if row["category"] == "copied_wrong":
            summary["copied_wrong_evidence"].append(evidence)
        if row["expanded_review"]["category"] == "copied_wrong":
            summary["expanded_copied_wrong_evidence"].append({
                **evidence,
                "old_values": row["expanded_review"]["old_value_hits"],
                "note": row["expanded_review"]["note"] or "primary and expanded both copied_wrong",
            })
    calls = [read_json(OUT / "calls" / f"{row['case_id']}-{row['condition']}.json", {}) for row in rows]
    for condition in ARMS:
        group = [row for row in rows if row["condition"] == condition]
        counts = Counter(row["category"] for row in group)
        expanded_counts = Counter(row["expanded_review"]["category"] for row in group)
        semantic_counts = Counter(row["semantic_review"]["label"] for row in group)
        summary["conditions"][condition] = {
            "n": len(group),
            "counts": {name: counts.get(name, 0) for name in ("transferred", "copied_wrong", "missed", "escalated")},
            "transferred_wilson_95": base._wilson(counts["transferred"], len(group)),
            "semantic_review_counts": dict(sorted(semantic_counts.items())),
            "expanded_counts": {name: expanded_counts.get(name, 0) for name in ("transferred", "copied_wrong", "missed", "escalated")},
            "extension_changed": [
                {
                    "case_id": row["case_id"], "primary": row["category"],
                    "expanded": row["expanded_review"]["category"], "note": row["expanded_review"]["note"],
                }
                for row in group if row["expanded_review"]["changed_from_primary"]
            ],
        }
    summary["historical_reference_not_pooled"] = {
        "v3_A_transferred": 5,
        "v3_B_copied_wrong": 3,
        "v4_A_transferred": summary["conditions"]["A"]["counts"]["transferred"],
        "v4_B_copied_wrong": summary["conditions"]["B"]["counts"]["copied_wrong"],
        "v4_D_transferred": summary["conditions"]["D"]["counts"]["transferred"],
        "v4_D_copied_wrong": summary["conditions"]["D"]["counts"]["copied_wrong"],
        "rule": "v3 is a historical anchor only; v1/v2/v3 rows are excluded from all v4 distributions",
    }
    summary["prompt_comparability"] = {
        "all_A_B_prompts_match_v3": all(row["prompt_matches_v3"] for row in rows if row["condition"] != "D"),
        "all_prompts_match_frozen": all(row["prompt_matches_frozen"] for row in rows),
        "mismatches": [f"{row['case_id']}-{row['condition']}" for row in rows if not row["prompt_matches_frozen"]],
    }
    summary["cost"] = {
        "provider_returned_cost_sum": round(sum(base._provider_cost(call) for call in calls), 6),
        "provider_cost_missing_calls": sum(call.get("provider_cost") is None for call in calls),
        "ledger_cost_sum": round(sum(float(call.get("ledger_cost") or 0) for call in calls), 6),
        "prompt_tokens": sum(int((call.get("usage") or {}).get("prompt_tokens") or 0) for call in calls),
        "completion_tokens": sum(int((call.get("usage") or {}).get("completion_tokens") or 0) for call in calls),
        "total_tokens": sum(int((call.get("usage") or {}).get("total_tokens") or 0) for call in calls),
        "usage_missing_calls": sum(not call.get("usage") for call in calls),
        "finish_reasons": {
            reason: sum(call.get("finish_reason") == reason for call in calls)
            for reason in sorted({str(call.get("finish_reason")) for call in calls})
        },
        "duration_median_seconds": statistics.median(float(call.get("duration_seconds") or 0) for call in calls),
        "duration_max_seconds": max(float(call.get("duration_seconds") or 0) for call in calls),
        "calls": len(calls),
        "per_call": [
            {
                "key": f"{row['case_id']}-{row['condition']}",
                "usage": (row["receipt"] or {}).get("usage"),
                "ledger_cost": (row["receipt"] or {}).get("ledger_cost"),
                "provider_cost": (row["receipt"] or {}).get("provider_cost"),
                "finish_reason": (row["receipt"] or {}).get("finish_reason"),
                "duration_seconds": (row["receipt"] or {}).get("duration_seconds"),
            }
            for row in rows
        ],
    }
    summary["environment"] = read_json(OUT / "environment.json", {})
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preview")
    sub.add_parser("freeze")
    run_parser = sub.add_parser("run-one")
    run_parser.add_argument("case_id")
    run_parser.add_argument("condition", choices=ARMS)
    sub.add_parser("finalize")
    sub.add_parser("summarize")
    args = parser.parse_args()
    base.OUT = OUT
    base.FROZEN = FROZEN
    base.RESULTS = RESULTS
    base.SUMMARY = SUMMARY
    base.MANUAL = OUT / "manual-review.json"
    base.DATASET_VERSION = DATASET_VERSION
    base.PARAMS = {**base.PARAMS, "max_tokens": 8192}
    if args.command == "preview":
        value = protocol()
        print(json.dumps({
            "derived_count": len(value["derived_constraints"]),
            "derived": value["derived_constraints"],
            "all_A_B_prompts_match_v3": all(row["matches_v3_prompt"] for row in value["prompt_plan"] if row["condition"] != "D"),
            "prompt_plan": value["prompt_plan"],
        }, ensure_ascii=False, indent=2))
    elif args.command == "freeze":
        if FROZEN.exists():
            raise RuntimeError("v4 frozen protocol already exists; refusing to rewrite")
        value = protocol()
        write_json(FROZEN, value)
        print(json.dumps({"frozen": str(FROZEN), "derived": len(value["derived_constraints"]), "calls_planned": 18}, ensure_ascii=False))
    elif args.command == "run-one":
        run_one(args.case_id, args.condition)
    elif args.command == "finalize":
        finalize()
    else:
        summarize()


if __name__ == "__main__":
    main()
