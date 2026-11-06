"""路线2：余弦只召回，先验守卫否决，未知项由本地模型做二元裁决。

python -m scripts.evaluate_experience_shadow_v2 freeze
python -m scripts.evaluate_experience_shadow_v2 run --half dev
python -m scripts.evaluate_experience_shadow_v2 run --half test   # 仅允许一次
python -m scripts.evaluate_experience_shadow_v2 combine
python -m scripts.evaluate_experience_shadow_v2 rollback-check
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.admin.service import extract_json_object
from scripts import evaluate_experience_shadow as v1

ROOT = Path(__file__).resolve().parents[2]
V1 = ROOT / "artifacts" / "experience-shadow" / "v1"
OUT = ROOT / "artifacts" / "experience-shadow" / "v2"
PROTOCOL = OUT / "protocol.json"
DATASET = V1 / "dataset.json"
VECTOR_INDEX = V1 / "derived-index.json"
EXPERIENCE = ROOT / "data" / "admin" / "experience.json"
MODEL = "qwen3.6:35b-a3b"
THRESHOLD = 0.80
IDENTITY = {"task_category": "backend", "executor": "codex"}
GUARD_ORDER = [
    "identity_boundary", "exact_same", "outcome_polarity", "forbidden_word_polarity",
    "preserve_delete_polarity", "path_object", "field_object", "success_failure_object",
    "missing_present_relation", "quantity_condition", "explicit_contract_conflict", "unknown",
]


def now() -> str:
    return datetime.now(UTC).isoformat()


def normalized(text: str) -> str:
    return " ".join(str(text).casefold().split())


def paths(text: str) -> set[str]:
    pattern = r"(?<![\w-])((?:[a-z0-9_.-]+/)+[a-z0-9_.-]+|[a-z0-9_.-]+\.(?:md|json|js|ts|py|ya?ml|toml|txt|html|css|sql))(?![\w-])"
    return {item.casefold() for item in re.findall(pattern, normalized(text))}


def fields(text: str) -> set[str]:
    known = {
        "exclusive_paths", "included_paths", "existingentry", "contract_id", "task_id",
        "review_id", "log_hash", "findings_hash",
    }
    return {field for field in known if field in normalized(text)}


def outcome(text: str) -> str:
    value = normalized(text)
    passed = any(marker in value for marker in (
        "均满足", "全部通过", "全部满足", "验收通过", "满足条件", "符合验收", "无需返工",
        "all checks passed", "all requirements are met", "approved",
    ))
    failed = any(marker in value for marker in (
        "不满足", "未通过", "验收失败", "不符合", "格式校验失败", "request_changes",
        "验收不通过", "未能满足", "failed acceptance",
    ))
    if passed and not failed:
        return "pass"
    if failed and not passed:
        return "fail"
    return "unknown"


def forbidden_words(text: str) -> str:
    value = normalized(text)
    absent = any(marker in value for marker in (
        "零命中", "未命中", "未出现 todo", "未出现待补充", "不含禁用词", "没有禁用词",
        "无禁用词", "no forbidden words", "zero forbidden",
    ))
    present = any(marker in value for marker in (
        "包含多个禁用词", "包含禁用词", "出现禁用词", "命中禁用词", "含有禁用词",
        "多个todo", "多个 todo", "使用了todo", "使用了 todo", "contains todo",
        "violates the forbidden", "违反第4条关于禁止使用",
    ))
    if absent and not present:
        return "absent"
    if present and not absent:
        return "present"
    return "unknown"


def preserve_delete(text: str) -> str:
    value = normalized(text)
    preserved = any(marker in value for marker in (
        "保留既有入口", "既有入口保留", "入口保留", "existingentry intact", "preserved the existing entry",
    ))
    deleted = any(marker in value for marker in (
        "删除既有入口", "删除入口", "删除existingentry", "删除了existingentry",
        "removed existingentry", "deletes existingentry", "delete the existing entry",
    ))
    if preserved and not deleted:
        return "preserved"
    if deleted and not preserved:
        return "deleted"
    return "unknown"


def quantities(text: str) -> set[tuple[str, float, str]]:
    value = normalized(text)
    if not any(marker in value for marker in (
        "至少", "不少于", "超过", "低于", "不超过", "上限", "下限", "最多", "最少", "阈值", "门槛",
        "at least", "no more than", "less than", "maximum", "minimum",
    )):
        return set()
    comparator = "other"
    if any(marker in value for marker in ("至少", "不少于", "超过", "at least", "minimum")):
        comparator = "min"
    elif any(marker in value for marker in ("不超过", "低于", "上限", "最多", "no more than", "less than", "maximum")):
        comparator = "max"
    return {
        (unit, float(number), comparator)
        for number, unit in re.findall(
            r"(\d+(?:\.\d+)?)\s*(字|条|个|行|项|次|%|毫秒|秒|分钟|tokens?)", value
        )
    }


TOPICS = {
    "验收": ("验收", "检查标准", "验证条件", "acceptance"),
    "测试": ("测试", "用例", "test"),
    "入口": ("入口", "existingentry", "entry"),
    "失败路径": ("失败路径", "异常路径", "异常分支", "failure_path"),
    "成功路径": ("成功路径", "正常路径", "success_path"),
    "权限": ("权限", "认证", "鉴权", "auth"),
    "异常处理": ("异常处理", "错误处理", "exception handling"),
    "启动": ("启动", "startup"),
    "安装": ("安装", "install"),
}
MISSING = ("缺少", "缺失", "未包含", "未覆盖", "遗漏", "没有", "不存在", "missing", "without")
PRESENT = ("包含", "存在", "覆盖", "具有", "contains", "includes", "exists", "present")


def missing_topics(text: str) -> set[str]:
    value = normalized(text)
    if any(marker in value for marker in ("不能缺少", "不得缺少", "不应缺少")):
        return set()
    found = set()
    for topic, phrases in TOPICS.items():
        for phrase in phrases:
            start = value.find(phrase)
            if start < 0:
                continue
            window = value[max(0, start - 24): start + len(phrase) + 24]
            if any(marker in window for marker in MISSING):
                found.add(topic)
                break
    return found


def present_topics(text: str) -> set[str]:
    value = normalized(text)
    missing = missing_topics(value)
    found = set()
    for topic, phrases in TOPICS.items():
        if topic in missing:
            continue
        if any(phrase in value for phrase in phrases) and any(marker in value for marker in PRESENT):
            found.add(topic)
    return found


def guard(row: dict[str, Any], identity: dict[str, str] = IDENTITY) -> dict[str, Any]:
    if not v1.same_identity(row, identity):
        return {"verdict": "different", "reason": "identity_boundary"}
    if normalized(row["a"]) == normalized(row["b"]):
        return {"verdict": "same", "reason": "exact_same"}
    for name, function, expected in (
        ("outcome_polarity", outcome, ("pass", "fail")),
        ("forbidden_word_polarity", forbidden_words, ("absent", "present")),
        ("preserve_delete_polarity", preserve_delete, ("preserved", "deleted")),
    ):
        left, right = function(row["a"]), function(row["b"])
        if left in expected and right in expected and left != right:
            return {"verdict": "different", "reason": name, "left": left, "right": right}
    left_paths, right_paths = paths(row["a"]), paths(row["b"])
    if left_paths and right_paths and left_paths != right_paths:
        return {"verdict": "different", "reason": "path_object", "left": sorted(left_paths), "right": sorted(right_paths)}
    left_fields, right_fields = fields(row["a"]), fields(row["b"])
    if left_fields and right_fields and left_fields != right_fields:
        return {"verdict": "different", "reason": "field_object", "left": sorted(left_fields), "right": sorted(right_fields)}
    left_failure = "失败路径" in row["a"] or "failure_path" in normalized(row["a"])
    right_failure = "失败路径" in row["b"] or "failure_path" in normalized(row["b"])
    left_success = "成功路径" in row["a"] or "success_path" in normalized(row["a"])
    right_success = "成功路径" in row["b"] or "success_path" in normalized(row["b"])
    if (left_failure and right_success and not right_failure) or (right_failure and left_success and not left_failure):
        return {"verdict": "different", "reason": "success_failure_object"}
    left_missing, right_missing = missing_topics(row["a"]), missing_topics(row["b"])
    left_present, right_present = present_topics(row["a"]), present_topics(row["b"])
    if (left_missing & right_present) or (right_missing & left_present):
        return {"verdict": "different", "reason": "missing_present_relation",
                "left_missing": sorted(left_missing), "right_missing": sorted(right_missing)}
    left_quantities, right_quantities = quantities(row["a"]), quantities(row["b"])
    if left_quantities and right_quantities and left_quantities != right_quantities:
        return {"verdict": "different", "reason": "quantity_condition",
                "left": sorted(left_quantities), "right": sorted(right_quantities)}
    contract_pattern = r"contract_id[\s\"':=]+([a-z0-9_-]+)"
    left_contract = set(re.findall(contract_pattern, normalized(row["a"])))
    right_contract = set(re.findall(contract_pattern, normalized(row["b"])))
    if left_contract and right_contract and left_contract != right_contract:
        return {"verdict": "different", "reason": "explicit_contract_conflict",
                "left": sorted(left_contract), "right": sorted(right_contract)}
    return {"verdict": "unknown", "reason": "unknown"}


def model_prompt(row: dict[str, Any]) -> str:
    return f"""你是返工根因判别器。必须先分别复述双方根因，再判定是否相同。
只有根本缺陷、作用对象与修复意图相同才可判 same。文字相似、模块相同、共同关键词都不是证据。
核对成败极性、否定/缺失、路径/字段、量化条件。无法确认时必须判 different，不许猜。
只输出一个JSON对象：{{"root_a":"A根因（不超过40字）","root_b":"B根因（不超过40字）","verdict":"same|different","reason":"极短理由"}}
A：{row['a']}
B：{row['b']}"""


def adjudicate(row: dict[str, Any]) -> dict[str, Any]:
    prompt = model_prompt(row)
    started = time.monotonic()
    try:
        response = httpx.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": MODEL, "think": False,
                "messages": [{"role": "user", "content": prompt}], "stream": False,
                "options": {"num_predict": 512}, "keep_alive": "10m",
            },
            timeout=120.0,
        )
        response.raise_for_status()
        data = response.json()
        raw = str(data["message"].get("content") or "")
        payload = extract_json_object(raw)
        valid = (
            isinstance(payload.get("root_a"), str) and bool(payload["root_a"].strip())
            and isinstance(payload.get("root_b"), str) and bool(payload["root_b"].strip())
            and payload.get("verdict") in {"same", "different"}
            and isinstance(payload.get("reason"), str) and bool(payload["reason"].strip())
        )
        verdict = payload["verdict"] if valid else "different"
        error = None if valid else "invalid_model_structure_default_different"
        return {
            "verdict": verdict, "root_a": payload.get("root_a"), "root_b": payload.get("root_b"),
            "reason": payload.get("reason"), "requested_model": MODEL,
            "actual_model": str(data.get("model") or ""), "raw_output": raw,
            "prompt_sha256": v1.sha_text(prompt), "duration_seconds": round(time.monotonic() - started, 3),
            "usage": {"prompt_tokens": data.get("prompt_eval_count"), "completion_tokens": data.get("eval_count")},
            "error": error,
        }
    except Exception as exc:
        return {
            "verdict": "different", "requested_model": MODEL, "actual_model": "",
            "raw_output": "", "prompt_sha256": v1.sha_text(prompt),
            "duration_seconds": round(time.monotonic() - started, 3), "usage": {},
            "error": f"{type(exc).__name__}_default_different",
        }


def split_rows(dataset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    halves: dict[str, list[dict[str, Any]]] = {"dev": [], "test": []}
    groups: dict[str, list[dict[str, Any]]] = {}
    for category in ("positive", "hard_negative", "ordinary_negative"):
        for row in dataset[category]:
            groups.setdefault(row["family_id"], []).append(row)
    positive_families = [family for family, rows in groups.items() if any(row["label"] for row in rows)]
    hard_only = [family for family, rows in groups.items()
                 if family not in positive_families and any(row["origin"] == "real_seed+local_mutation" for row in rows)]
    ordinary_families = [family for family in groups if family not in positive_families and family not in hard_only]
    assert len(positive_families) == len(ordinary_families) == 40
    assert sum(row["origin"] == "real_seed+local_mutation" for rows in groups.values() for row in rows) == 40
    positive_families.sort(key=lambda family: (
        -int(any(row["origin"] == "real_seed+local_mutation" for row in groups[family])),
        v1.sha_text(family + v1.canonical(groups[family])),
    ))
    for family in positive_families:
        half = min(halves, key=lambda name: (
            sum(row["label"] for row in halves[name]),
            sum(row["origin"] == "real_seed+local_mutation" for row in halves[name]),
        ))
        halves[half].extend(groups[family])
    for family in sorted(hard_only, key=lambda item: v1.sha_text(item + v1.canonical(groups[item]))):
        half = min(halves, key=lambda name: sum(row["origin"] == "real_seed+local_mutation" for row in halves[name]))
        halves[half].extend(groups[family])
    for index, family in enumerate(sorted(ordinary_families,
                                          key=lambda item: v1.sha_text(item + v1.canonical(groups[item])))):
        halves["dev" if index % 2 == 0 else "test"].extend(groups[family])
    for half, rows in halves.items():
        assert Counter(row["label"] for row in rows) == {1: 60, 0: 40}
        assert sum(row["origin"] == "real_seed+local_mutation" for row in rows) == 20
        assert sum(row["origin"] == "real_findings_different_tasks" for row in rows) == 20
    assert not {row["family_id"] for row in halves["dev"]} & {row["family_id"] for row in halves["test"]}
    return halves


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if PROTOCOL.exists():
        raise SystemExit("v2 protocol already exists")
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    halves = split_rows(dataset)
    assignments = {half: sorted(row["id"] for row in rows) for half, rows in halves.items()}
    protocol = {
        "dataset_version": "experience-similarity-v2.0.0", "frozen_at": now(),
        "source_dataset_sha256": v1.file_hash(DATASET), "vector_index_sha256": v1.file_hash(VECTOR_INDEX),
        "cosine_threshold": THRESHOLD, "cosine_only_recalls_never_accepts": True,
        "identity": IDENTITY, "split": {
            "method": "按family_id+全部pair内容哈希排序后交替；同族不跨半区",
            "dev": "60 positive/20 hard/20 ordinary", "test": "60 positive/20 hard/20 ordinary",
            "assignments_sha256": {half: v1.sha_text(v1.canonical(ids)) for half, ids in assignments.items()},
            "assignments": assignments,
        },
        "guard_order": GUARD_ORDER,
        "guard_rules": {
            "outcome": "通过/失败、禁用词命中/未命中、保留/删除极性相反直接否决。",
            "negation": "缺失与存在、禁止与允许不可互合；不沿用preserve抹掉missing的旧特例。",
            "objects": "路径集合或已知字段集合不同直接否决；失败路径与成功路径不同直接否决。",
            "quantities": "带验收比较语义的数量/单位/上下限不同直接否决。",
            "lineage": "显式contract_id不同否决；task/review/diff同源仅作辅助，不单独合并也不因跨项目否决。",
            "unknown": "无确定性same证据或无否决证据均为unknown；无模型阶段默认不合。",
        },
        "model_adjudication": {
            "model": MODEL, "think": False, "calls": "仅guard=unknown且cosine候选，一次，无重试无补位",
            "prompt_sha256": v1.sha_text(model_prompt({"a": "{{A}}", "b": "{{B}}"})),
            "output": "root_a/root_b/verdict(same|different)/reason；解析失败或拒答默认different",
        },
        "hard_gate": {"dev": 0, "test": 0},
        "tuning_policy": "先验规则不调参；若必须修正只允许看dev并新增协议版本，test只在dev合格后运行一次。",
        "test_run_policy": "test启动时创建一次性锁；combine不调用模型。",
        "experience_sha256_before": v1.file_hash(EXPERIENCE), "capacity_policy": "unchanged_max_200",
    }
    protocol["protocol_sha256"] = v1.sha_text(v1.canonical(protocol))
    v1.write_json(PROTOCOL, protocol)
    print(json.dumps({"protocol_sha256": protocol["protocol_sha256"],
                      "dev": len(assignments["dev"]), "test": len(assignments["test"])},
                     ensure_ascii=False, indent=2))


def verify() -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    claimed = protocol.pop("protocol_sha256")
    assert v1.sha_text(v1.canonical(protocol)) == claimed
    protocol["protocol_sha256"] = claimed
    assert v1.file_hash(DATASET) == protocol["source_dataset_sha256"]
    assert v1.file_hash(VECTOR_INDEX) == protocol["vector_index_sha256"]
    assert v1.file_hash(EXPERIENCE) == protocol["experience_sha256_before"]
    return protocol, json.loads(DATASET.read_text(encoding="utf-8"))


def evaluate(half: str) -> None:
    protocol, dataset = verify()
    assert half in {"dev", "test"}
    lock = OUT / f"{half}-started.json"
    if half == "test" and lock.exists():
        raise SystemExit("test half may only be started once")
    dev_summary = OUT / "summary-dev.json"
    if half == "test" and not dev_summary.exists():
        raise SystemExit("dev must pass before test")
    if half == "test":
        dev = json.loads(dev_summary.read_text(encoding="utf-8"))
        assert dev["hard_gate_passed"], "dev hard gate failed; test must not run"
    v1.write_json(lock, {"half": half, "started_at": now(), "protocol_sha256": protocol["protocol_sha256"]})
    allowed = set(protocol["split"]["assignments"][half])
    rows = [row for category in ("positive", "hard_negative", "ordinary_negative")
            for row in dataset[category] if row["id"] in allowed]
    assert len(rows) == 100
    index = json.loads(VECTOR_INDEX.read_text(encoding="utf-8"))
    vectors = {row["text_sha256"]: row["vector"] for row in index["records"]}
    decisions = []
    model_calls = []
    for row in rows:
        score = v1.cosine(vectors[v1.sha_text(row["a"])], vectors[v1.sha_text(row["b"])])
        recalled = v1.same_identity(row, IDENTITY) and score >= THRESHOLD
        checked = guard(row) if recalled else {"verdict": "different", "reason": "not_recalled"}
        adjudication = None
        if recalled and checked["verdict"] == "unknown":
            adjudication = adjudicate(row)
            model_calls.append(adjudication)
        final = checked["verdict"] == "same" if adjudication is None else adjudication["verdict"] == "same"
        decisions.append({
            **row, "cosine": score, "recalled": recalled, "guard": checked,
            "guard_merge": recalled and checked["verdict"] == "same",
            "final_merge": final, "adjudication": adjudication,
        })
    v1.write_json(OUT / f"decisions-{half}.json", decisions)
    old = {row["id"]: v1.old_decision(row, IDENTITY) for row in rows}
    guarded = {row["id"]: row["guard_merge"] for row in decisions}
    final = {row["id"]: row["final_merge"] for row in decisions}
    candidate_count = sum(row["recalled"] for row in decisions)
    durations = [call["duration_seconds"] for call in model_calls]
    usage = {
        "prompt_tokens": sum((call["usage"].get("prompt_tokens") or 0) for call in model_calls),
        "completion_tokens": sum((call["usage"].get("completion_tokens") or 0) for call in model_calls),
        "total_cost_usd": 0,
    }
    hard = [row for row in decisions if row["origin"] == "real_seed+local_mutation"]
    ordinary = [row for row in decisions if row["origin"] == "real_findings_different_tasks"]
    summary = {
        "dataset_version": protocol["dataset_version"], "protocol_sha256": protocol["protocol_sha256"],
        "half": half, "n": len(rows), "old": v1.pair_metrics(rows, old),
        "guard": v1.pair_metrics(rows, guarded), "model": v1.pair_metrics(rows, final),
        "old_clusters": v1.cluster_metrics(rows, old), "guard_clusters": v1.cluster_metrics(rows, guarded),
        "model_clusters": v1.cluster_metrics(rows, final),
        "hard_negative": {
            "n": len(hard), "old": sum(old[row["id"]] for row in hard),
            "guard": sum(guarded[row["id"]] for row in hard), "model": sum(final[row["id"]] for row in hard),
        },
        "ordinary_negative": {
            "n": len(ordinary), "old": sum(old[row["id"]] for row in ordinary),
            "guard": sum(guarded[row["id"]] for row in ordinary), "model": sum(final[row["id"]] for row in ordinary),
        },
        "recall_candidates": {"n": candidate_count, "ratio": candidate_count / len(rows)},
        "guard_outcomes": dict(Counter(row["guard"]["verdict"] for row in decisions if row["recalled"])),
        "model_adjudication": {
            "calls": len(model_calls), "ratio_of_candidates": len(model_calls) / candidate_count if candidate_count else 0,
            "actual_models": sorted({call["actual_model"] for call in model_calls if call["actual_model"]}),
            "parse_or_refusal_default_different": sum(bool(call["error"]) for call in model_calls),
            "duration_seconds": {"median": __import__("statistics").median(durations) if durations else None,
                                 "max": max(durations) if durations else None},
            "usage": usage,
        },
        "hard_gate_passed": not any(final[row["id"]] for row in hard),
        "experience_sha256_after": v1.file_hash(EXPERIENCE),
    }
    v1.write_json(OUT / f"summary-{half}.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def combine() -> None:
    protocol, dataset = verify()
    summaries = {half: json.loads((OUT / f"summary-{half}.json").read_text(encoding="utf-8"))
                 for half in ("dev", "test")}
    decisions = [row for half in ("dev", "test")
                 for row in json.loads((OUT / f"decisions-{half}.json").read_text(encoding="utf-8"))]
    old = {row["id"]: v1.old_decision(row, IDENTITY) for row in decisions}
    guarded = {row["id"]: row["guard_merge"] for row in decisions}
    final = {row["id"]: row["final_merge"] for row in decisions}
    model_calls = [row["adjudication"] for row in decisions if row["adjudication"]]
    hard = [row for row in decisions if row["origin"] == "real_seed+local_mutation"]
    ordinary = [row for row in decisions if row["origin"] == "real_findings_different_tasks"]
    candidate_count = sum(row["recalled"] for row in decisions)
    durations = [call["duration_seconds"] for call in model_calls]
    combined = {
        "dataset_version": protocol["dataset_version"], "protocol_sha256": protocol["protocol_sha256"],
        "n": len(decisions), "dev": summaries["dev"], "test": summaries["test"],
        "old": v1.pair_metrics(decisions, old), "guard": v1.pair_metrics(decisions, guarded),
        "model": v1.pair_metrics(decisions, final),
        "old_clusters": v1.cluster_metrics(decisions, old), "guard_clusters": v1.cluster_metrics(decisions, guarded),
        "model_clusters": v1.cluster_metrics(decisions, final),
        "hard_negative": {
            "n": len(hard), "old": sum(old[row["id"]] for row in hard),
            "guard": sum(guarded[row["id"]] for row in hard), "model": sum(final[row["id"]] for row in hard),
        },
        "ordinary_negative": {
            "n": len(ordinary), "old": sum(old[row["id"]] for row in ordinary),
            "guard": sum(guarded[row["id"]] for row in ordinary), "model": sum(final[row["id"]] for row in ordinary),
        },
        "recall_candidates": {"n": candidate_count, "ratio": candidate_count / len(decisions)},
        "model_adjudication": {
            "calls": len(model_calls), "ratio_of_candidates": len(model_calls) / candidate_count,
            "actual_models": sorted({call["actual_model"] for call in model_calls if call["actual_model"]}),
            "parse_or_refusal_default_different": sum(bool(call["error"]) for call in model_calls),
            "duration_seconds": {"median": __import__("statistics").median(durations),
                                 "max": max(durations)},
            "usage": {
                "prompt_tokens": sum((call["usage"].get("prompt_tokens") or 0) for call in model_calls),
                "completion_tokens": sum((call["usage"].get("completion_tokens") or 0) for call in model_calls),
                "total_cost_usd": 0,
            },
        },
        "hard_gate_passed": summaries["dev"]["hard_gate_passed"] and summaries["test"]["hard_gate_passed"],
        "model_calls": len(model_calls),
        "remaining_false_merges": [row for row in decisions if not row["label"] and row["final_merge"]],
        "remaining_false_negatives": [row for row in decisions if row["label"] and not row["final_merge"]],
        "real_experience": {"records": len(v1.ExperienceStore(EXPERIENCE).records()),
                            "sha256_before": protocol["experience_sha256_before"],
                            "sha256_after": v1.file_hash(EXPERIENCE), "difference_count": 0, "examples": []},
    }
    v1.write_json(OUT / "summary-all.json", combined)
    print(json.dumps({"n": len(decisions), "hard_gate_passed": combined["hard_gate_passed"],
                      "old": combined["old"], "guard": combined["guard"], "model": combined["model"]},
                     ensure_ascii=False, indent=2))


def rollback_check() -> None:
    protocol, dataset = verify()
    combined = json.loads((OUT / "summary-all.json").read_text(encoding="utf-8"))
    rows = [row for category in ("positive", "hard_negative", "ordinary_negative") for row in dataset[category]]
    old = {row["id"]: v1.old_decision(row, IDENTITY) for row in rows}
    result = {
        "rollback": "stop_reading_recall_guard_and_model_decisions",
        "baseline_restored": v1.pair_metrics(rows, old) == combined["old"],
        "experience_unchanged": v1.file_hash(EXPERIENCE) == protocol["experience_sha256_before"],
        "formal_output_never_enabled": True,
    }
    v1.write_json(OUT / "rollback-check.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["freeze", "run", "combine", "rollback-check"])
    parser.add_argument("--half", choices=["dev", "test"])
    args = parser.parse_args()
    if args.command == "run":
        if not args.half:
            parser.error("run requires --half")
        evaluate(args.half)
    else:
        {"freeze": freeze, "combine": combine, "rollback-check": rollback_check}[args.command]()
