"""Deterministic stratified audit of delivery_path slots; manual verdicts stay separate."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from app.admin.slots import slot_for_experience

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "admin" / "experience.json"
OUT = ROOT / "artifacts" / "delivery-path-audit" / "v1"
SEED = "delivery-path-audit-v1"


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def wilson(successes: int, total: int) -> list[float]:
    if not total:
        return [0.0, 0.0]
    z = 1.96
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def load() -> list[dict[str, Any]]:
    records = json.loads(SOURCE.read_text(encoding="utf-8"))["records"]
    return [record for record in records if (slot_for_experience(record) or {}).get("slot") == "delivery_path"]


def sample() -> None:
    records = load()
    selected = []
    for kind, count in (("merged", 6), ("request_changes", 4)):
        group = [record for record in records if record["kind"] == kind]
        group.sort(key=lambda record: hashlib.sha256(f"{SEED}:{record['experience_id']}".encode()).hexdigest())
        selected.extend(group[:count])
    rows = []
    for record in selected:
        slot = slot_for_experience(record)
        expected = slot["expected"]
        positions = [
            field for field in ("root_cause", "action", "spec_summary")
            if expected in str(record.get(field) or "")
        ]
        rows.append({
            "experience_id": record["experience_id"],
            "source_task_id": record["source_task_id"],
            "kind": record["kind"],
            "root_cause": record["root_cause"],
            "action": record["action"],
            "expected": expected,
            "expected_positions": positions,
            "expected_verbatim": bool(positions),
        })
    protocol = {
        "seed": SEED,
        "selection": "SHA256(seed:experience_id) ascending within each kind; 6 merged + 4 request_changes",
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "manual_rule": "merged successful delivery path is real; request_changes must itself describe a path/location problem",
        "rows": rows,
    }
    write_json(OUT / "sample.json", protocol)
    print(json.dumps({"sample": str(OUT / "sample.json"), "ids": [row["experience_id"] for row in rows]}, ensure_ascii=False))


def summarize() -> None:
    manual = json.loads((OUT / "manual-review.json").read_text(encoding="utf-8"))
    sampled = {
        row["experience_id"]: row
        for row in json.loads((OUT / "sample.json").read_text(encoding="utf-8"))["rows"]
    }
    rows = [{**sampled[key], **value} for key, value in manual.items()]
    true_count = sum(row["verdict"] == "真路径" for row in rows)
    low, high = wilson(true_count, len(rows))
    records = load()
    machine = []
    for record in records:
        root = str(record["root_cause"] or "")
        path_problem = any(term in root for term in (
            "路径不明", "路径不明确", "未明确路径", "缺少路径", "没有路径", "交付路径",
            "产出物路径", "文件位置", "存放位置", "输出位置", "写到哪里", "落在哪", "落点",
        ))
        verdict = "真路径" if record["kind"] == "merged" or path_problem else "被压成路径"
        machine.append({"experience_id": record["experience_id"], "kind": record["kind"], "verdict": verdict})
    summary = {
        "sample_size": len(rows),
        "sample_true_path": true_count,
        "sample_compressed_path": len(rows) - true_count,
        "sample_wilson_95": [round(low, 4), round(high, 4)],
        "sample_extrapolation_to_36": {
            "true_path_range": [math.floor(low * 36), math.ceil(high * 36)],
            "compressed_path_range": [36 - math.ceil(high * 36), 36 - math.floor(low * 36)],
            "note": "外推，不是剩余26条的人审实测",
        },
        "machine_all_36": {
            "true_path": sum(row["verdict"] == "真路径" for row in machine),
            "compressed_path": sum(row["verdict"] == "被压成路径" for row in machine),
            "rule": "merged=true; request_changes requires explicit path/location problem in root_cause",
            "rows": machine,
        },
        "manual_rows": rows,
    }
    write_json(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["sample", "summarize"])
    args = parser.parse_args()
    if args.command == "sample":
        sample()
    else:
        summarize()


if __name__ == "__main__":
    main()
