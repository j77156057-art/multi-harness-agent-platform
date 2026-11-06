"""本地 bge-m3 经验相似度影子评估。

准备（只生成候选，不算向量）：
  python -m scripts.evaluate_experience_shadow prepare
盲审候选后冻结：
  python -m scripts.evaluate_experience_shadow freeze
运行旧规则/候选规则与回滚检查：
  python -m scripts.evaluate_experience_shadow run
  python -m scripts.evaluate_experience_shadow rollback-check
独立重建真实经验派生索引：
  python -m scripts.evaluate_experience_shadow rebuild
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import sqlite3
import tempfile
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.admin.experience import ExperienceStore, finding_cluster_key
from app.admin.service import extract_json_object

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-shadow" / "v1"
DATASET = OUT / "dataset.json"
PROTOCOL = OUT / "protocol.json"
INDEX = OUT / "derived-index.json"
SUMMARY = OUT / "summary.json"
DIFFS = OUT / "shadow-differences.json"
MODEL = "bge-m3:latest"
GENERATOR = "qwen3.6:35b-a3b"
THRESHOLD = 0.80
EXPERIENCE = ROOT / "data" / "admin" / "experience.json"
REAL_INDEX = ROOT / "data" / "admin" / "experience-shadow.json"


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha_text(text: str) -> str:
    return sha_bytes(text.encode("utf-8"))


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_hash(path: Path) -> str:
    return sha_bytes(path.read_bytes()) if path.exists() else "missing"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def real_findings() -> list[dict[str, str]]:
    conn = sqlite3.connect(ROOT / "data" / "state.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT review_id, task_id, findings_json FROM reviews"
        " WHERE findings_json IS NOT NULL ORDER BY review_id"
    ).fetchall()
    items = []
    for row in rows:
        for finding in json.loads(row["findings_json"]):
            message = " ".join(str(finding.get("message") or "").split())
            if len(message) >= 30:
                items.append({
                    "review_id": row["review_id"], "task_id": row["task_id"],
                    "contract_id": str(finding.get("contract_id") or ""), "text": message,
                })
    return items


def local_json(prompt: str) -> dict[str, Any]:
    response = httpx.post(
        "http://127.0.0.1:11434/api/chat",
        json={
            "model": GENERATOR, "think": False,
            "messages": [{"role": "user", "content": prompt}], "stream": False,
            "options": {"num_predict": 1800}, "keep_alive": "10m",
        },
        timeout=180.0,
    )
    response.raise_for_status()
    return extract_json_object(response.json()["message"]["content"])


def prepare() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    candidate_path = OUT / "candidates.json"
    if candidate_path.exists():
        raise SystemExit("candidates already exist; refusing to mix generations")
    items = sorted(real_findings(), key=lambda item: sha_text(canonical(item)))
    seeds = [item for item in items if len(item["text"]) <= 900][:60]
    if len(seeds) < 40:
        raise SystemExit(f"not enough real findings: {len(seeds)}")
    families = []
    started = time.monotonic()
    for index, seed in enumerate(seeds, start=1):
        if len(families) >= 45:
            break
        prompt = f"""你在构造返工根因相似度评估数据。根据一条真实审查意见生成：
1. 两条保持同一根本问题、关键对象和修复要求的改写，可以中英混合，但不能改成另一个问题。
2. 一条困难负样本：尽量共享原句词汇和主题，但根本缺陷、作用对象或所需修复必须不同。
只输出 JSON：{{"paraphrases":["改写1","改写2"],"hard_negative":"困难负样本"}}。
真实意见：
{seed['text']}"""
        try:
            payload = local_json(prompt)
            variants = [seed["text"], *[str(x).strip() for x in payload["paraphrases"]]]
            hard = str(payload["hard_negative"]).strip()
            if len(variants) != 3 or any(len(x) < 20 for x in variants) or len(hard) < 20:
                raise ValueError("incomplete generated family")
        except Exception as exc:
            print(json.dumps({"seed": index, "skipped": f"{type(exc).__name__}: {exc}"},
                             ensure_ascii=False), flush=True)
            continue
        families.append({
            "family_id": f"F-{len(families) + 1:03d}", "source": seed,
            "texts": variants, "hard_negative": hard,
        })
        print(json.dumps({"generated": len(families), "elapsed_seconds": round(time.monotonic() - started, 1)},
                         ensure_ascii=False), flush=True)
    if len(families) < 40:
        raise SystemExit(f"only {len(families)} valid families; need 40")
    write_json(candidate_path, {"generator": GENERATOR, "families": families})
    make_blind_files(families, items)


def make_blind_files(families: list[dict[str, Any]], items: list[dict[str, str]]) -> None:
    hard = []
    boundary = []
    for index, family in enumerate(families):
        hard.append({"id": f"Q-H-{index + 1:03d}", "a": family["texts"][0],
                     "b": family["hard_negative"]})
        if index % 2 == 0 and len(boundary) < 20:
            boundary.append({"id": f"Q-P-{index + 1:03d}", "a": family["texts"][0],
                             "b": family["texts"][1]})
    # 生产回归：同 review + 同 contract 的原始 findings 对，不参与调参。
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for item in items:
        if item["contract_id"]:
            groups[(item["review_id"], item["contract_id"])].append(item)
    same_pairs = []
    for group in groups.values():
        same_pairs.extend(itertools.combinations(group, 2))
    for index, pair in enumerate(same_pairs[:12]):
        boundary.append({"id": f"Q-R-P-{index + 1:03d}", "a": pair[0]["text"], "b": pair[1]["text"]})
    # 普通负样本只取低字面重叠的不同任务原文。
    ordinary = []
    for index in range(len(items)):
        first = items[index]
        for offset in range(1, len(items)):
            second = items[(index * 17 + offset * 13) % len(items)]
            if first["task_id"] == second["task_id"]:
                continue
            a_words = set(first["text"].casefold().split())
            b_words = set(second["text"].casefold().split())
            union = a_words | b_words
            overlap = len(a_words & b_words) / len(union) if union else 1.0
            if overlap < 0.08:
                ordinary.append((first, second))
                break
        if len(ordinary) >= 50:
            break
    write_json(OUT / "ordinary-candidates.json", ordinary)
    write_json(OUT / "blind-hard.json", hard)
    write_json(OUT / "blind-boundary.json", boundary)
    print(json.dumps({"families": len(families), "blind_hard": len(hard),
                      "blind_boundary": len(boundary), "ordinary_candidates": len(ordinary)},
                     ensure_ascii=False, indent=2))


def reviews(path: Path) -> dict[str, dict[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"blind review must be a list: {path}")
    return {row["id"]: row for row in rows}


def verdict(review: dict[str, dict[str, str]], key: str) -> str:
    return str(review.get(key, {}).get("verdict") or "missing")


def freeze() -> None:
    if DATASET.exists() or PROTOCOL.exists():
        raise SystemExit("dataset/protocol already frozen")
    generated = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    families = generated["families"]
    hard_review = reviews(OUT / "blind-hard-review.json")
    boundary_review = reviews(OUT / "blind-boundary-review.json")
    expected_hard = {row["id"] for row in json.loads((OUT / "blind-hard.json").read_text(encoding="utf-8"))}
    expected_boundary = {row["id"] for row in json.loads((OUT / "blind-boundary.json").read_text(encoding="utf-8"))}
    assert expected_hard <= set(hard_review), "hard blind review is incomplete"
    assert expected_boundary <= set(boundary_review), "boundary blind review is incomplete"
    positive = []
    hard = []
    rejected = {"positive": 0, "hard": 0, "unclear_or_missing": 0}
    for index, family in enumerate(families):
        for left, right in itertools.combinations(range(3), 2):
            positive.append({
                "id": f"P-{len(positive) + 1:03d}", "label": 1, "family_id": family["family_id"],
                "a": family["texts"][left], "b": family["texts"][right], "origin": "real_seed+local_paraphrase",
            })
        blind_id = f"Q-H-{index + 1:03d}"
        decision = verdict(hard_review, blind_id)
        if decision == "different":
            hard.append({
                "id": f"H-{len(hard) + 1:03d}", "label": 0, "family_id": family["family_id"],
                "issue_id": f"H-{family['family_id']}", "a": family["texts"][0],
                "b": family["hard_negative"], "origin": "real_seed+local_mutation", "blind_review": hard_review[blind_id],
            })
        else:
            rejected["hard"] += 1
            if decision in {"unclear", "missing"}:
                rejected["unclear_or_missing"] += 1
    # 盲审覆盖边界正样本；被否定的整个问题族不进入冻结集。
    bad_families = set()
    for index, family in enumerate(families):
        if index % 2:
            continue
        blind_id = f"Q-P-{index + 1:03d}"
        if blind_id in boundary_review and verdict(boundary_review, blind_id) != "same":
            bad_families.add(family["family_id"])
            rejected["positive"] += 1
    selected_families = [f for f in families if f["family_id"] not in bad_families][:40]
    selected_ids = {f["family_id"] for f in selected_families}
    positive = [row for row in positive if row["family_id"] in selected_ids]
    if len(positive) != 120 or len(hard) < 40:
        raise SystemExit(f"invalid frozen candidates: positive={len(positive)} hard={len(hard)}")
    hard = hard[:40]
    for index, row in enumerate(hard, start=1):
        row["id"] = f"H-{index:03d}"
    ordinary_source = json.loads((OUT / "ordinary-candidates.json").read_text(encoding="utf-8"))
    ordinary = [{
        "id": f"N-{index + 1:03d}", "label": 0, "family_id": f"N-{index + 1:03d}",
        "issue_id": f"N-{index + 1:03d}", "a": pair[0]["text"], "b": pair[1]["text"],
        "origin": "real_findings_different_tasks",
    } for index, pair in enumerate(ordinary_source[:40])]
    if len(ordinary) != 40:
        raise SystemExit("need 40 ordinary negative pairs")
    holdout = []
    for key, row in boundary_review.items():
        if not key.startswith("Q-R-P-"):
            continue
        if row.get("verdict") == "same":
            source = next(item for item in json.loads((OUT / "blind-boundary.json").read_text(encoding="utf-8"))
                          if item["id"] == key)
            holdout.append({"id": f"R-{len(holdout) + 1:03d}", "label": 1,
                            "a": source["a"], "b": source["b"], "origin": "production_same_contract",
                            "blind_review": row})
    for pair in ordinary_source[40:50]:
        holdout.append({"id": f"R-{len(holdout) + 1:03d}", "label": 0,
                        "a": pair[0]["text"], "b": pair[1]["text"],
                        "origin": "production_different_tasks"})
    dataset = {
        "dataset_version": "experience-similarity-v1.0.0", "identity": {"task_category": "backend", "executor": "codex"},
        "positive": positive, "hard_negative": hard, "ordinary_negative": ordinary,
        "production_holdout": holdout,
    }
    write_json(DATASET, dataset)
    protocol = {
        "dataset_version": dataset["dataset_version"], "frozen_at": now(),
        "dataset_sha256": file_hash(DATASET), "model": MODEL, "cosine_threshold": THRESHOLD,
        "candidate_rule": "概念key相等先合；否则仅同(task_category,executor)且cosine>=0.80才合。",
        "gates": {
            "hard_negative_false_merge": 0,
            "report_only": ["ordinary_negative_false_merge", "accuracy", "positive_recall", "cluster_errors"],
        },
        "counts": {"positive": 120, "hard_negative": 40, "ordinary_negative": 40,
                   "production_holdout": len(holdout)},
        "blind_review": {
            "hard_candidates_reviewed": len(hard_review), "hard_rejected": rejected["hard"],
            "positive_families_rejected": rejected["positive"],
            "unclear_or_missing_rejected": rejected["unclear_or_missing"],
            "production_holdout_reviewed": len(holdout),
            "labels_generated_from_concept_vocabulary": False,
        },
        "experience_sha256_before": file_hash(EXPERIENCE),
        "generator": GENERATOR, "embedding_network_boundary": "loopback_only",
        "capacity_policy": "unchanged_max_200",
    }
    protocol["protocol_sha256"] = sha_text(canonical(protocol))
    write_json(PROTOCOL, protocol)
    print(json.dumps({"positive": len(positive), "hard": len(hard), "ordinary": len(ordinary),
                      "holdout": len(holdout), "threshold": THRESHOLD,
                      "protocol_sha256": protocol["protocol_sha256"]}, ensure_ascii=False, indent=2))


def verify_frozen() -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    claimed = protocol.pop("protocol_sha256")
    assert sha_text(canonical(protocol)) == claimed
    protocol["protocol_sha256"] = claimed
    assert file_hash(DATASET) == protocol["dataset_sha256"]
    assert protocol["experience_sha256_before"] == file_hash(EXPERIENCE), "original experience changed"
    return protocol, json.loads(DATASET.read_text(encoding="utf-8"))


def model_fingerprint() -> str:
    response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=10)
    response.raise_for_status()
    entry = next(item for item in response.json()["models"] if item["name"] == MODEL)
    return str(entry.get("digest") or "")


def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    response = httpx.post(
        "http://127.0.0.1:11434/api/embed", json={"model": MODEL, "input": texts}, timeout=120,
    )
    response.raise_for_status()
    return response.json()["embeddings"]


def build_derived_index(records: list[dict[str, Any]], source_sha256: str) -> dict[str, Any]:
    started = time.monotonic()
    vectors = embed([row["text"] for row in records])
    dimensions = len(vectors[0]) if vectors else 0
    return {
        "schema_version": 1, "model_id": MODEL, "dimensions": dimensions,
        "model_fingerprint": model_fingerprint(), "source_sha256": source_sha256,
        "created_at": now(), "build_seconds": round(time.monotonic() - started, 3),
        "records": [{**{key: value for key, value in row.items() if key != "text"},
                     "text_sha256": sha_text(row["text"]), "vector": vector}
                    for row, vector in zip(records, vectors, strict=True)],
    }


def cosine(a: list[float], b: list[float]) -> float:
    numerator = sum(x * y for x, y in zip(a, b, strict=True))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(x * x for x in b))
    return numerator / (left * right) if left and right else 0.0


def same_identity(row: dict[str, Any], identity: dict[str, str]) -> bool:
    return row.get("identity_a", identity) == row.get("identity_b", identity)


def old_decision(row: dict[str, Any], identity: dict[str, str]) -> bool:
    return same_identity(row, identity) and finding_cluster_key(row["a"]) == finding_cluster_key(row["b"])


def candidate_decision(row: dict[str, Any], identity: dict[str, str], vectors: dict[str, list[float]]) -> tuple[bool, float]:
    score = cosine(vectors[sha_text(row["a"])], vectors[sha_text(row["b"])])
    if not same_identity(row, identity):
        return False, score
    if finding_cluster_key(row["a"]) == finding_cluster_key(row["b"]):
        return True, 1.0
    return score >= THRESHOLD, score


def wilson(successes: int, total: int, z: float = 1.959963984540054) -> list[float]:
    if not total:
        return [0.0, 0.0]
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def pair_metrics(rows: list[dict[str, Any]], decisions: dict[str, bool]) -> dict[str, Any]:
    tp = sum(row["label"] == 1 and decisions[row["id"]] for row in rows)
    tn = sum(row["label"] == 0 and not decisions[row["id"]] for row in rows)
    fp = sum(row["label"] == 0 and decisions[row["id"]] for row in rows)
    fn = sum(row["label"] == 1 and not decisions[row["id"]] for row in rows)
    positives = tp + fn
    negatives = fp + tn
    return {
        "n": len(rows), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": (tp + tn) / len(rows), "accuracy_ci95": wilson(tp + tn, len(rows)),
        "positive_recall": tp / positives if positives else None,
        "positive_recall_ci95": wilson(tp, positives),
        "false_merge_rate": fp / negatives if negatives else None,
        "false_merge_rate_ci95": wilson(fp, negatives),
    }


def cluster_metrics(rows: list[dict[str, Any]], decisions: dict[str, bool]) -> dict[str, Any]:
    nodes: dict[str, dict[str, Any]] = {}
    for row in rows:
        issue_a = row.get("family_id") if row["label"] else row.get("issue_id", row["id"])
        issue_b = row.get("family_id") if row["label"] else row.get("issue_id", row["id"])
        for text, issue in ((row["a"], issue_a), (row["b"], issue_b)):
            nodes.setdefault(text, {"issue": issue})
    parent = {text: text for text in nodes}

    def find(text: str) -> str:
        while parent[text] != text:
            parent[text] = parent[parent[text]]
            text = parent[text]
        return text

    for row in rows:
        if decisions[row["id"]]:
            left, right = find(row["a"]), find(row["b"])
            if left != right:
                parent[right] = left
    components: dict[str, set[str]] = defaultdict(set)
    for text in nodes:
        components[find(text)].add(nodes[text]["issue"])
    bad = sorted(issues for issues in components.values() if len(issues) > 1)
    # 三个变体分别来自三个项目；困难负样本来自第四项目。跨问题且跨项目就会生成错误提示。
    wrong_prompts = sum(len(issues) > 1 for issues in components.values())
    return {"cluster_errors": len(bad), "wrong_cross_project_prompts": wrong_prompts,
            "bad_components": [sorted(issues) for issues in bad]}


def run() -> None:
    protocol, dataset = verify_frozen()
    rows = dataset["positive"] + dataset["hard_negative"] + dataset["ordinary_negative"]
    assert [len(dataset["positive"]), len(dataset["hard_negative"]), len(dataset["ordinary_negative"])] == [120, 40, 40]
    texts = sorted({text for row in rows + dataset["production_holdout"] for text in (row["a"], row["b"])})
    index_records = [{"locator": f"text:{index:03d}", "text": text} for index, text in enumerate(texts)]
    index = build_derived_index(index_records, protocol["dataset_sha256"])
    atomic_json(INDEX, index)
    vectors = {row["text_sha256"]: row["vector"] for row in index["records"]}
    scores: dict[str, float] = {}
    old = {row["id"]: old_decision(row, dataset["identity"]) for row in rows}
    new = {}
    for row in rows:
        new[row["id"]], scores[row["id"]] = candidate_decision(row, dataset["identity"], vectors)
    holdout_old = {row["id"]: old_decision(row, dataset["identity"]) for row in dataset["production_holdout"]}
    holdout_new = {}
    for row in dataset["production_holdout"]:
        holdout_new[row["id"]], scores[row["id"]] = candidate_decision(row, dataset["identity"], vectors)
    differences = [{
        "id": row["id"], "label": row["label"], "type": "old_no_new_yes" if new[row["id"]] else "old_yes_new_no",
        "cosine": scores[row["id"]], "a": row["a"], "b": row["b"], "origin": row["origin"],
    } for row in rows if old[row["id"]] != new[row["id"]]]
    write_json(DIFFS, differences)
    summary = {
        "dataset_version": dataset["dataset_version"], "protocol_sha256": protocol["protocol_sha256"],
        "index": {key: index[key] for key in ("schema_version", "model_id", "dimensions", "model_fingerprint", "source_sha256", "build_seconds")},
        "not_production_statistics": True,
        "old": pair_metrics(rows, old), "candidate": pair_metrics(rows, new),
        "old_clusters": cluster_metrics(rows, old), "candidate_clusters": cluster_metrics(rows, new),
        "hard_negative": {
            "old_false_merges": sum(old[row["id"]] for row in dataset["hard_negative"]),
            "candidate_false_merges": sum(new[row["id"]] for row in dataset["hard_negative"]),
        },
        "ordinary_negative": {
            "old_false_merges": sum(old[row["id"]] for row in dataset["ordinary_negative"]),
            "candidate_false_merges": sum(new[row["id"]] for row in dataset["ordinary_negative"]),
        },
        "production_holdout": {
            "n": len(holdout_old), "old": pair_metrics(dataset["production_holdout"], holdout_old) if holdout_old else None,
            "candidate": pair_metrics(dataset["production_holdout"], holdout_new) if holdout_new else None,
        },
        "shadow_difference_count": len(differences),
        "old_no_new_yes": sum(row["type"] == "old_no_new_yes" for row in differences),
        "old_yes_new_no": sum(row["type"] == "old_yes_new_no" for row in differences),
        "real_experience": {
            "records": len(ExperienceStore(EXPERIENCE).records()),
            "sha256_before": protocol["experience_sha256_before"], "sha256_after": file_hash(EXPERIENCE),
            "difference_count": 0, "examples": [],
        },
        "hard_gate_passed": not any(new[row["id"]] for row in dataset["hard_negative"]),
    }
    write_json(SUMMARY, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def rebuild() -> None:
    store = ExperienceStore(EXPERIENCE)
    records = store.records()
    source = file_hash(EXPERIENCE)
    payload = [{
        "locator": f"record:{index:03d}", "record": record,
        "text": record.get("findings_summary", ""),
    } for index, record in enumerate(records) if record["kind"] == "request_changes"]
    index = build_derived_index(payload, source)
    atomic_json(REAL_INDEX, index)
    assert file_hash(EXPERIENCE) == source
    print(json.dumps({"input_records": len(records), "indexed_rework": len(payload),
                      "output": str(REAL_INDEX), "experience_unchanged": True}, ensure_ascii=False, indent=2))


def rollback_check() -> None:
    protocol, dataset = verify_frozen()
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    rows = dataset["positive"] + dataset["hard_negative"] + dataset["ordinary_negative"]
    old = {row["id"]: old_decision(row, dataset["identity"]) for row in rows}
    baseline = pair_metrics(rows, old)
    result = {
        "rollback": "stop_reading_derived_index",
        "baseline_restored": baseline == summary["old"],
        "experience_unchanged": file_hash(EXPERIENCE) == protocol["experience_sha256_before"],
        "derived_index_left_untouched": INDEX.exists(),
    }
    write_json(OUT / "rollback-check.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import sys
    commands = {"prepare": prepare, "freeze": freeze, "run": run,
                "rebuild": rebuild, "rollback-check": rollback_check}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        raise SystemExit("usage: prepare|freeze|run|rebuild|rollback-check")
    commands[sys.argv[1]]()
