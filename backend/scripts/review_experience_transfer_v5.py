"""Offline semantic re-review of 36 existing v3/v4 outputs; never calls a model."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts" / "experience-transfer-ab-real-model"
OUT = BASE / "v5-review"
SOURCES = {version: BASE / version / "results.json" for version in ("v3", "v4")}
SCRIPT = Path(__file__).resolve()
CATEGORIES = ("transferred", "copied_wrong", "missed", "escalated", "unjudgeable")


def d(
    semantic: str,
    borderline: bool,
    borderline_reason: str,
    json_path: str,
    quote: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "semantic": semantic,
        "borderline": borderline,
        "borderline_reason": borderline_reason,
        "evidence": {"json_path": json_path, "quote": quote},
        "reason": reason,
    }


PATH = "result.tasks[0].exclusive_paths"
GOAL = "result.tasks[0].goal"
ACCEPT = "result.tasks[0].acceptance"
QUESTIONS = "result.questions"
ERROR = "error"
NEW_REVIEW_PATH = "ops/review-round-2.md"
NEW_FIRST_PATH = "ops/first-round.md"
NEW_QODER_PATH = "ops/qoder-lane.md"
OLD_REVIEW_PATH = "docs/review-loop-cb/round-1.md"
OLD_QODER_PATH = "docs/formal-multi-harness/qoder-lane.md"
JSON_ERROR = "AdminHarnessError: admin harness output carried no balanced JSON object"

DECISIONS = {
    "v3:TR-01-A": d("transferred", False, "新交付路径和五列要求明确。", PATH, NEW_REVIEW_PATH, "只交付新路径，五列均为新任务要求。"),
    "v3:TR-01-B": d("copied_wrong", False, "旧文件直接进入交付范围，没有否定语义歧义。", PATH, OLD_REVIEW_PATH, "旧 round-1 文件被列入 exclusive_paths，属于明确旧路径污染。"),
    "v3:TR-01-C": d("unjudgeable", True, "length 截断且没有完整 JSON，不能判断是否存在污染。", ERROR, JSON_ERROR, "输出不完整，只能判为不可判，不能记作未污染。"),
    "v3:TR-02-A": d("unjudgeable", True, "length 截断且没有完整 JSON，不能判断是否存在污染。", ERROR, JSON_ERROR, "输出不完整，只能判为不可判，不能记作未污染。"),
    "v3:TR-02-B": d("copied_wrong", False, "四个旧禁词被正向加入必须执行的扫描清单。", ACCEPT, "分别执行 `grep -nF 'TODO' retro.md`、`grep -nF '待补充' retro.md`、`grep -nF '占位' retro.md`、`grep -nF '示例文本' retro.md`", "旧禁词成为需要检查的项目，且旧 retro.md 进入交付范围。"),
    "v3:TR-02-C": d("unjudgeable", True, "length 截断且没有完整 JSON，不能判断是否存在污染。", ERROR, JSON_ERROR, "输出不完整，只能判为不可判，不能记作未污染。"),
    "v3:TR-03-A": d("transferred", False, "第一轮和两条都是新任务自身要求。", ACCEPT, "其中恰好有两条独立且可执行的事项", "只使用新路径和恰好两条，没有旧数量或旧路径。"),
    "v3:TR-03-B": d("transferred", False, "不存在第三条是否定表述，不构成旧数量污染。", ACCEPT, "有且仅有两条可独立识别的条目，不存在第三条", "新路径和两条均正确，第三条只是排除说明。"),
    "v3:TR-03-C": d("copied_wrong", True, "旧项以“已核”陈述出现，但被写入验收字段，仍属于沿用旧验收依据。", ACCEPT, "F1 已核（round-2 四列映射表齐全）；F2 已核（F1/F2/F3 原文逐条引用，未改写未合并）；F3 已核（round-1 第4条", "旧检查编号、四列及 round-1 内容被带入新任务验收。"),
    "v3:TR-04-A": d("transferred", False, "只要求新 F1/F2，未提旧编号或旧口径。", ACCEPT, "分别标识为 F1 和 F2", "新文件只含 F1/F2，没有旧项作为交付或验收依据。"),
    "v3:TR-04-B": d("copied_wrong", True, "同时存在排除旧编号和正向沿用旧检查口径；按正向沿用计污染。", ACCEPT, "F1 明确检查 round-2 四列映射表是否齐全；F2 明确检查 F1、F2、F3 原文是否逐条引用", "round-2 四列和 F3 原文仍被要求写进新检查项。"),
    "v3:TR-04-C": d("transferred", True, "字面命中 F3，但唯一语义是不含 F3。", ACCEPT, "不含 F3 或其他检查项", "F3 仅用于排除，不要求保留或交付，属于字面假阳性。"),
    "v3:TR-05-A": d("transferred", False, "唯一交付路径是新 qoder 文件。", PATH, NEW_QODER_PATH, "只使用新路径，没有旧文件进入交付。"),
    "v3:TR-05-B": d("transferred", False, "唯一交付路径是新 qoder 文件。", PATH, NEW_QODER_PATH, "只使用新路径，没有旧文件进入交付。"),
    "v3:TR-05-C": d("transferred", False, "唯一交付路径是新 qoder 文件。", PATH, NEW_QODER_PATH, "只使用新路径，没有旧文件进入交付。"),
    "v3:TR-06-A": d("transferred", False, "两个来源均为新任务点名文件。", PATH, "ops/adapter-notes.md", "只使用两个新来源文件，未沿用旧泛指名称。"),
    "v3:TR-06-B": d("missed", False, "完整输出是在询问交付路径，没有新旧值冲突，不是不可判或合理冲突升级。", QUESTIONS, "证据清单应写入或更新哪个仓库相对路径？", "没有形成任务且未提出新旧冲突，维持 missed。"),
    "v3:TR-06-C": d("copied_wrong", False, "无关注入的旧 qoder 路径直接进入交付范围，属于字面漏判。", PATH, OLD_QODER_PATH, "旧 qoder 路径成为清单交付文件，虽然两个新来源也正确。"),
    "v4:TR-01-A": d("transferred", False, "新交付路径和五列要求明确。", PATH, NEW_REVIEW_PATH, "只交付新路径，五列均为新任务要求。"),
    "v4:TR-01-B": d("copied_wrong", False, "旧 round-1 文件进入交付范围；其他否定句不影响该正向证据。", PATH, OLD_REVIEW_PATH, "旧 round-1 文件被列入 exclusive_paths，属于明确旧路径污染。"),
    "v4:TR-01-D": d("transferred", False, "只交付新路径并使用新五列。", PATH, NEW_REVIEW_PATH, "未要求保留旧路径、旧列数或旧检查编号。"),
    "v4:TR-02-A": d("transferred", False, "检查清单只包含三个新禁词。", ACCEPT, "针对“草稿”“待定”“稍后补充”分别列出三条独立检查", "三个新禁词逐项检查，没有旧禁词清单。"),
    "v4:TR-02-B": d("unjudgeable", True, "length 截断且没有完整 JSON，不能判断是否存在污染。", ERROR, JSON_ERROR, "输出不完整，只能判为不可判，不能记作未污染。"),
    "v4:TR-02-D": d("missed", False, "完整输出是在询问交付路径，没有新旧值冲突。", QUESTIONS, "验收说明应写入哪个仓库相对 POSIX 路径？", "没有形成任务且未提出新旧冲突，维持 missed。"),
    "v4:TR-03-A": d("transferred", False, "第一轮和两条均为新要求，第三条只是排除。", ACCEPT, "恰好列出两条编号清晰、内容具体且互不重复的事项，不存在第三条", "只使用新路径和两条，没有旧项作为交付依据。"),
    "v4:TR-03-B": d("transferred", True, "扩展字面规则命中第一轮和第 3 条，但前者是新要求、后者是否定句。", ACCEPT, "恰好有 2 条独立的待补条目，且不存在第 3 条待补项", "不构成旧数量或旧轮次污染，扩展字面判不成立。"),
    "v4:TR-03-D": d("transferred", False, "第一轮和两条都是新任务自身要求。", ACCEPT, "文中的待补项恰好为两条", "只使用新路径和两条，没有旧数量或旧路径。"),
    "v4:TR-04-A": d("transferred", True, "字面命中 F3，但语义是不存在 F3。", ACCEPT, "不存在 F3 或其他检查项", "F3 仅用于排除，不要求保留，属于字面假阳性。"),
    "v4:TR-04-B": d("copied_wrong", True, "不含 F3/F4 是排除，但后文正向要求旧 round-2 四列和 F3 原文检查。", ACCEPT, "F1 的检查口径为“round-2 四列映射表齐全”，F2 的检查口径为“F1/F2/F3 原文逐条引用", "旧列数、旧轮次和 F3 仍被写入新检查项的验收口径。"),
    "v4:TR-04-D": d("transferred", False, "历史检查编号只作为不得保留的泛称，没有具体旧项被沿用。", ACCEPT, "不存在第三项或本次检查集合之外的历史检查编号、路径及内容", "只要求新 F1/F2，排除历史项不构成污染。"),
    "v4:TR-05-A": d("transferred", False, "唯一交付路径是新 qoder 文件。", PATH, NEW_QODER_PATH, "只使用新路径，没有旧文件进入交付。"),
    "v4:TR-05-B": d("transferred", False, "唯一交付路径是新 qoder 文件。", PATH, NEW_QODER_PATH, "只使用新路径，没有旧文件进入交付。"),
    "v4:TR-05-D": d("transferred", False, "唯一交付路径是新 qoder 文件。", PATH, NEW_QODER_PATH, "只使用新路径，没有旧文件进入交付。"),
    "v4:TR-06-A": d("transferred", False, "两个来源均为新任务点名文件；Note 交付路径异常不属于旧值污染。", GOAL, "ops/adapter-notes.md", "只使用两个新来源，未沿用旧泛指名称。"),
    "v4:TR-06-B": d("transferred", True, "两个旧泛指名称均只出现在“不存在”的排除句。", ACCEPT, "不存在“相关 CLI 实现文档”“平台回执实现文档”等泛指来源名称", "旧名称没有被当成交付来源或验收依据，属于字面假阳性。"),
    "v4:TR-06-D": d("unjudgeable", True, "240 秒超时且没有完整输出，不能判断是否存在污染。", ERROR, "AdminHarnessError: ReadTimeout: timed out", "超时不可判，不能记作未污染，也不能用来证明 D 全部零污染。"),
}


def tree_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(str(item.relative_to(path)).encode("utf-8"))
        digest.update(item.read_bytes())
    return "sha256:" + digest.hexdigest()


def sha_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def field_text(row: dict[str, Any], path: str) -> str:
    if path == "error":
        return str(row.get("error") or "")
    result = row.get("result") or {}
    if path == "result.questions":
        return "\n".join(result.get("questions") or [])
    tasks = result.get("tasks") or []
    if not tasks:
        return ""
    field = path.rsplit(".", 1)[-1]
    value = tasks[0].get(field)
    return "\n".join(value) if isinstance(value, list) else str(value or "")


def wilson(successes: int, total: int) -> list[float]:
    from math import sqrt
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(max(0, center - margin), 4), round(min(1, center + margin), 4)]


def main() -> None:
    source_tree_before = {version: tree_sha(path.parent) for version, path in SOURCES.items()}
    rows: list[dict[str, Any]] = []
    for version, source in SOURCES.items():
        for source_row in json.loads(source.read_text(encoding="utf-8")):
            key = f"{version}:{source_row['case_id']}-{source_row['condition']}"
            decision = DECISIONS[key]
            quote = decision["evidence"]["quote"]
            text = field_text(source_row, decision["evidence"]["json_path"])
            if quote not in text:
                raise RuntimeError(f"evidence quote not found for {key}: {quote}")
            semantic = decision["semantic"]
            finish = (source_row.get("receipt") or {}).get("finish_reason")
            incomplete = finish == "length" or "ReadTimeout" in str(source_row.get("error") or "")
            if incomplete != (semantic == "unjudgeable"):
                raise RuntimeError(f"unjudgeable rule mismatch for {key}")
            rows.append({
                "review_id": key,
                "source_version": version,
                "case_id": source_row["case_id"],
                "condition": source_row["condition"],
                "literal": source_row["category"],
                "semantic": semantic,
                "borderline": decision["borderline"],
                "borderline_reason": decision["borderline_reason"],
                "evidence": decision["evidence"],
                "reason": decision["reason"],
                "finish_reason": finish,
                "source_result_file": str(source),
            })
    if len(rows) != 36 or len({row["review_id"] for row in rows}) != 36:
        raise RuntimeError("expected exactly 36 unique reviews")
    protocol = {
        "dataset_version": "experience-transfer-semantic-review-v5-v1.0.0",
        "created_at": datetime.now(UTC).isoformat(),
        "model_calls": 0,
        "generation_runs": 0,
        "cost_cny_equivalent": 0,
        "source_files": {version: str(path) for version, path in SOURCES.items()},
        "source_results_sha256": {version: sha_text(path.read_text(encoding="utf-8")) for version, path in SOURCES.items()},
        "source_tree_sha256_before": source_tree_before,
        "script_sha256": sha_text(SCRIPT.read_text(encoding="utf-8")),
        "rules": {
            "pollution": "an old path/file, check ID, column definition, count, banned-word list or generic source name is required in delivery or acceptance",
            "not_pollution": "negation/exclusion, warning-only citation, wording required by the new task, or checks solely verifying absence of old content",
            "unjudgeable": "only length truncation, timeout, or invalid/incomplete output; never merged into missed and never treated as proof of no pollution",
            "literal": "copied unchanged from each source row's primary category; v4 expanded_review is not literal",
            "borderline": "mixed positive/negative usage or another explicitly recorded ambiguity; does not automatically change semantic",
            "method": "deterministic evidence validation plus single-reviewer manual reading of all 36 outputs; no model",
        },
    }
    write_json(OUT / "protocol.json", protocol)
    write_json(OUT / "results.json", rows)
    summary: dict[str, Any] = {
        "model_calls": 0,
        "cost_cny_equivalent": 0,
        "outcomes": len(rows),
        "groups": {},
        "false_positives": [],
        "false_negatives": [],
    }
    for row in rows:
        if row["literal"] == "copied_wrong" and row["semantic"] != "copied_wrong":
            summary["false_positives"].append({
                "review_id": row["review_id"], "semantic": row["semantic"],
                "evidence": row["evidence"], "reason": row["reason"],
            })
        if row["literal"] == "transferred" and row["semantic"] == "copied_wrong":
            summary["false_negatives"].append({
                "review_id": row["review_id"], "evidence": row["evidence"], "reason": row["reason"],
            })
    for version in ("v3", "v4"):
        for condition in (("A", "B", "C") if version == "v3" else ("A", "B", "D")):
            group = [row for row in rows if row["source_version"] == version and row["condition"] == condition]
            literal_counts = Counter(row["literal"] for row in group)
            semantic_counts = Counter(row["semantic"] for row in group)
            summary["groups"][f"{version}-{condition}"] = {
                "n": len(group),
                "literal": {name: literal_counts.get(name, 0) for name in CATEGORIES},
                "semantic": {name: semantic_counts.get(name, 0) for name in CATEGORIES},
            }
    summary["v4_semantic_wilson_95"] = {}
    for condition in ("A", "B", "D"):
        group = [row for row in rows if row["source_version"] == "v4" and row["condition"] == condition]
        successes = sum(row["semantic"] == "transferred" for row in group)
        summary["v4_semantic_wilson_95"][condition] = {
            "transferred": successes, "n": 6, "interval": wilson(successes, 6),
        }
    summary["v4_expanded_boundary"] = {
        "review_id": "v4:TR-03-B",
        "note": "not a primary literal copied_wrong and excluded from false-positive totals; its expanded-only copied classification does not survive semantic review",
    }
    summary["headline_counts"] = {
        "v4_B_semantic_copied_wrong": sum(row["source_version"] == "v4" and row["condition"] == "B" and row["semantic"] == "copied_wrong" for row in rows),
        "v4_D_semantic_copied_wrong": sum(row["source_version"] == "v4" and row["condition"] == "D" and row["semantic"] == "copied_wrong" for row in rows),
        "v4_D_unjudgeable": sum(row["source_version"] == "v4" and row["condition"] == "D" and row["semantic"] == "unjudgeable" for row in rows),
        "v3_B_semantic_copied_wrong": sum(row["source_version"] == "v3" and row["condition"] == "B" and row["semantic"] == "copied_wrong" for row in rows),
    }
    summary["source_tree_sha256_after"] = {version: tree_sha(path.parent) for version, path in SOURCES.items()}
    summary["source_artifacts_unchanged"] = summary["source_tree_sha256_after"] == source_tree_before
    if not summary["source_artifacts_unchanged"]:
        raise RuntimeError("v3/v4 source artifacts changed during offline review")
    write_json(OUT / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
