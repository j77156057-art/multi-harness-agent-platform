"""v5: 禁词匹配加入"字母数字骨架"层，并对 v1–v4 已留存交付全量重判。

唯一改动：判禁词时，在既有"渲染后可见文本"之外，再把文本压成连续骨架
（剥掉一切非字母数字、非汉字统一表意文字的间隔符），禁止串在两者上各匹
配一次，任一命中即判不合格。

本轮不调用模型；数据源为 v1–v4 已落盘的交付文本。阈值与检查项不变。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend" / "scripts"
OUT = ROOT / "artifacts" / "experience-acceptance-ab" / "v5"
FIXTURES = OUT / "fixtures"
PROTOCOL = OUT / "protocol.json"
RESULTS = OUT / "results.json"
FIXTURE_RESULTS = OUT / "fixtures-result.json"
SUMMARY = OUT / "summary.json"
ENVIRONMENT = OUT / "environment.json"
V1 = ROOT / "artifacts" / "experience-acceptance-ab" / "v1"
V2 = ROOT / "artifacts" / "experience-acceptance-ab" / "v2"
V3 = ROOT / "artifacts" / "experience-acceptance-ab" / "v3"
V4 = ROOT / "artifacts" / "experience-acceptance-ab" / "v4"
SOURCE = ROOT / "data" / "admin" / "experience.json"
HISTORY = ROOT / "artifacts" / "experience-transfer-ab-real-model"
HISTORY_VERSIONS = ("v1", "v2", "v3", "v4", "v5-review", "v6-conditional")
DATASET_VERSION = "experience-acceptance-ab-v5.0.0"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v1 = _load("acc_v1", "evaluate_experience_acceptance_v1.py")
v2 = _load("acc_v2", "evaluate_experience_acceptance_v2.py")
v3 = _load("acc_v3", "evaluate_experience_acceptance_v3.py")

BANNED_TOKENS: dict[str, list[str]] = {}
for case in v1.CASES:
    BANNED_TOKENS[f"v1/{case['case_id']}"] = list(case["old_values"])
for case in v2.CASES:
    BANNED_TOKENS[f"v2/{case['case_id']}"] = list(case["old_values"])
BANNED_TOKENS["v3/RET-03"] = list(v3.RET03["old_values"])
BANNED_TOKENS["v4/RET-03"] = list(v3.RET03["old_values"])

FIXTURE_SPECS = [
    {
        "name": "dunhao_split",
        "text": "# 验收说明\n\n1. T、O、D、O\n2. 待、补、充\n3. 占、位\n4. 示、例、文、本\n",
        "tokens": list(v3.RET03["old_values"]),
    },
    {
        "name": "slash_space_split",
        "text": "# 验收说明\n\n1. T/O/D/O\n2. 待 补 充\n",
        "tokens": list(v3.RET03["old_values"]),
    },
    {
        "name": "zero_width_split",
        "text": "# 验收说明\n\n1. T\u200bO\u200bD\u200bO\n2. 待\u200b补\u200b充\n",
        "tokens": list(v3.RET03["old_values"]),
    },
]

SKELETON_RULE = "可见文本 -> 逐字符保留字母/数字/汉字统一表意文字，丢弃其余全部字符（间隔符） -> 连续骨架"


# -----------------------------------------------------------------------------
# v5 新增层
# -----------------------------------------------------------------------------


def skeleton(text: str) -> str:
    """把已归一化文本压成连续骨架：只留字母数字与汉字表意文字。"""
    return "".join(char for char in text if char.isalnum())


def token_skeleton(token: str) -> str:
    return skeleton(v3.normalized_token(token))


def skeleton_hit(skel: str, token: str) -> tuple[bool, int, str]:
    """骨架上的子串匹配；返回 (命中, 起始下标, 片段)。"""
    wanted = token_skeleton(token)
    if not wanted:
        return False, -1, ""
    index = skel.find(wanted)
    if index < 0:
        return False, -1, ""
    return True, index, skel[index : index + len(wanted)]


def banned_hits(text: str, tokens: list[str]) -> list[dict[str, Any]]:
    """可见文本匹配一次，骨架匹配一次；任一命中即记不合格。"""
    rendered = v3.visible_text(text)
    skel = skeleton(rendered)
    hits: list[dict[str, Any]] = []
    for token in tokens:
        visible_hit = v3.token_hit(rendered, token)
        found, index, fragment = skeleton_hit(skel, token)
        if not visible_hit and not found:
            continue
        hits.append(
            {
                "token": token,
                "visible_hit": visible_hit,
                "skeleton_hit": found,
                "skeleton_index": index,
                "skeleton_fragment": fragment,
                "skeleton_context": skel[max(0, index - 10) : index + len(fragment) + 10] if found else "",
            }
        )
    return hits


def v3_ban_hits(text: str, tokens: list[str]) -> list[str]:
    rendered = v3.visible_text(text)
    return [token for token in tokens if v3.token_hit(rendered, token)]


# -----------------------------------------------------------------------------
# 基础工具
# -----------------------------------------------------------------------------


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_text(encoding="utf-8"))


def tree_sha(path: Path) -> str:
    digest = hashlib.sha256()
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(item.read_bytes())
    return "sha256:" + digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def check_spec() -> dict[str, Any]:
    """冻结对象：新归一化规则 + 每条用例的禁止串 + 夹具哈希 + 阈值声明。"""
    frozen_fixtures = []
    for spec in FIXTURE_SPECS:
        frozen_fixtures.append(
            {
                "name": spec["name"],
                "sha256": sha(spec["text"]),
                "tokens": spec["tokens"],
            }
        )
    return {
        "skeleton_rule": SKELETON_RULE,
        "match_rule": "禁止串在可见文本与骨架上各匹配一次，任一命中即不合格",
        "tokens": BANNED_TOKENS,
        "fixtures": frozen_fixtures,
        "thresholds": {
            "content_min_length": 40,
            "unchanged": True,
        },
    }


def spec_sha() -> str:
    return sha(json.dumps(check_spec(), ensure_ascii=False, sort_keys=True))


def v4_fixture_text() -> str:
    rows = read_json(V4 / "results.json")
    return rows[0]["parsed_payload"]["files"][0]["content"]


def all_texts() -> list[dict[str, Any]]:
    """收集 v1–v4 全部已留存交付，逐条给出文本与原始判定。"""
    rows: list[dict[str, Any]] = []

    # v1：6 条首次样本
    for item in read_json(V1 / "results.json"):
        first = item["first"]
        path = first["checks"][0]["expected"]["path"]
        text = first["files"].get(path, "")
        rows.append(
            {
                "source": "v1",
                "case_id": item["case_id"],
                "attempt": 1,
                "path": path,
                "text": text,
                "old_verdict": "pass" if first["mechanical_pass"] else "fail",
                "old_ban_hits": v3_ban_hits(text, BANNED_TOKENS[f"v1/{item['case_id']}"]) if text else [],
                "other_checks_pass": all(c["pass"] for c in first["checks"]),
            }
        )

    # v2：3 个任务各首次 + 退回
    for item in read_json(V2 / "results.json"):
        for stage in ("first", "retry"):
            record = item.get(stage)
            if not record:
                continue
            tokens = BANNED_TOKENS[f"v2/{item['case_id']}"]
            text, path = delivered_text(record)
            rows.append(
                {
                    "source": "v2",
                    "case_id": item["case_id"],
                    "attempt": record["attempt"],
                    "path": path,
                    "text": text,
                    "old_verdict": "pass" if record["mechanical_pass"] else "fail",
                    "old_ban_hits": v3_ban_hits(text, tokens) if text else [],
                    "other_checks_pass": all(c["pass"] for c in record["checks"]),
                }
            )

    # v3：首次 + 退回（退回截断，无文本）
    for item in read_json(V3 / "results.json"):
        for stage in ("first", "retry"):
            record = item.get(stage)
            if not record:
                continue
            text, path = delivered_text(record)
            rows.append(
                {
                    "source": "v3",
                    "case_id": item["case_id"],
                    "attempt": record.get("attempt", 1 if stage == "first" else 2),
                    "path": path,
                    "text": text,
                    "old_verdict": "fail" if stage == "first" else "unjudgeable",
                    "old_ban_hits": v3_ban_hits(text, BANNED_TOKENS["v3/RET-03"]) if text else [],
                    "other_checks_pass": all(c["pass"] for c in record["checks"]),
                }
            )

    # v4：唯一一次退回
    for record in read_json(V4 / "results.json"):
        text, path = delivered_text(record)
        rows.append(
            {
                "source": "v4",
                "case_id": record["case_id"],
                "attempt": 2,
                "path": path,
                "text": text,
                "old_verdict": record["verdict"],
                "old_ban_hits": v3_ban_hits(text, BANNED_TOKENS["v4/RET-03"]) if text else [],
                "other_checks_pass": all(c["pass"] for c in record["checks"]),
            }
        )
    return rows


def delivered_text(record: dict[str, Any]) -> tuple[str, str]:
    files = record.get("files") or {}
    checks = record.get("checks") or []
    expected_path = checks[0]["expected"]["path"] if checks else ""
    if expected_path in files:
        return files[expected_path], expected_path
    committed = [p for p in (record.get("committed_files") or []) if p in files]
    if committed:
        return "\n".join(files[p] for p in committed), "concat:" + ",".join(committed)
    return "", expected_path


def new_verdict(row: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if not row["text"]:
        return "unjudgeable_no_text", []
    hits = banned_hits(row["text"], BANNED_TOKENS[f"{row['source']}/{row['case_id']}"])
    if hits:
        return "fail", hits
    return ("pass" if row["other_checks_pass"] and row["old_verdict"] in {"pass", "still_bypassing"} else row["old_verdict"]), []


# -----------------------------------------------------------------------------
# 子命令
# -----------------------------------------------------------------------------


def freeze() -> None:
    if PROTOCOL.exists():
        raise SystemExit(f"protocol.json 已存在，拒绝重复冻结：{PROTOCOL}")
    script_path = Path(__file__)
    OUT.mkdir(parents=True, exist_ok=True)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    v4_text = v4_fixture_text()
    fixture_path = FIXTURES / "v4-ret03-split.md"
    fixture_path.write_text(v4_text, encoding="utf-8")
    protocol = {
        "dataset_version": DATASET_VERSION,
        "mode": "no_model_calls_reevaluate_retained_texts",
        "frozen_at": now(),
        "script": {"path": str(script_path.relative_to(ROOT)), "sha256": file_sha(script_path)},
        "check_spec": {"sha256": spec_sha(), "content": check_spec()},
        "v4_fixture": {"path": str(fixture_path.relative_to(ROOT)), "sha256": sha(v4_text)},
        "thresholds_unchanged": True,
        "post_freeze_changes": [],
    }
    write_json(PROTOCOL, protocol)
    print(json.dumps({"frozen_at": protocol["frozen_at"], "script": protocol["script"]["sha256"],
                      "check_spec": protocol["check_spec"]["sha256"],
                      "v4_fixture": protocol["v4_fixture"]["sha256"]}, ensure_ascii=False, indent=2))


def verify_frozen() -> dict[str, Any]:
    protocol = read_json(PROTOCOL)
    if file_sha(Path(__file__)) != protocol["script"]["sha256"]:
        raise SystemExit("脚本 SHA-256 与冻结记录不一致")
    if spec_sha() != protocol["check_spec"]["sha256"]:
        raise SystemExit("检查清单 SHA-256 与冻结记录不一致")
    return protocol


def run() -> None:
    protocol = verify_frozen()
    env_before = snapshot_environment()

    fixture_rows = []
    for spec in FIXTURE_SPECS:
        old_hits = v3_ban_hits(spec["text"], spec["tokens"])
        new_hits = banned_hits(spec["text"], spec["tokens"])
        fixture_rows.append(
            {
                "name": spec["name"],
                "sha256": sha(spec["text"]),
                "old_v3": {"hits": old_hits, "verdict": "pass" if not old_hits else "fail"},
                "new_v5": {
                    "hits": [hit["token"] for hit in new_hits],
                    "evidence": new_hits,
                    "verdict": "pass" if not new_hits else "fail",
                },
                "assert_old_pass": not old_hits,
                "assert_new_fail": bool(new_hits),
            }
        )

    v4_text = (FIXTURES / "v4-ret03-split.md").read_text(encoding="utf-8")
    v4_old = v3_ban_hits(v4_text, v3.RET03["old_values"])
    v4_new = banned_hits(v4_text, v3.RET03["old_values"])
    fixture_rows.append(
        {
            "name": "v4_real_delivery",
            "sha256": sha(v4_text),
            "old_v3": {"hits": v4_old, "verdict": "pass" if not v4_old else "fail"},
            "new_v5": {"hits": [hit["token"] for hit in v4_new], "evidence": v4_new, "verdict": "pass" if not v4_new else "fail"},
            "assert_old_pass": not v4_old,
            "assert_new_fail": bool(v4_new),
        }
    )
    write_json(FIXTURE_RESULTS, fixture_rows)

    rows = []
    for row in all_texts():
        verdict, hits = new_verdict(row)
        rows.append(
            {
                "source": row["source"],
                "case_id": row["case_id"],
                "attempt": row["attempt"],
                "path": row["path"],
                "text_sha256": sha(row["text"]) if row["text"] else None,
                "old_verdict": row["old_verdict"],
                "new_verdict": verdict,
                "changed": row["old_verdict"] != verdict,
                "old_ban_hits": row["old_ban_hits"],
                "new_ban_hits": [hit["token"] for hit in hits],
                "hit_evidence": hits,
            }
        )
    write_json(RESULTS, rows)

    false_positives = [row for row in rows if row["source"] == "v1" and row["old_verdict"] == "pass" and row["new_verdict"] == "fail"]
    missed = [row for row in rows if row["new_verdict"] == "pass" and row["old_ban_hits"]]
    summary = {
        "dataset_version": DATASET_VERSION,
        "model_calls": 0,
        "rows": len(rows),
        "counts": {
            "pass": sum(1 for row in rows if row["new_verdict"] == "pass"),
            "fail": sum(1 for row in rows if row["new_verdict"] == "fail"),
            "unjudgeable_no_text": sum(1 for row in rows if row["new_verdict"] == "unjudgeable_no_text"),
            "changed": sum(1 for row in rows if row["changed"]),
        },
        "v1_false_positives": false_positives,
        "v5_missed_old_hits": missed,
        "fixture_assertions": {
            row["name"]: {"old_pass": row["assert_old_pass"], "new_fail": row["assert_new_fail"]} for row in fixture_rows
        },
        "criteria": {
            "script_sha256": protocol["script"]["sha256"],
            "check_spec_sha256": protocol["check_spec"]["sha256"],
            "post_freeze_changes": protocol.get("post_freeze_changes", []),
        },
        "environment": {
            "production_unchanged": env_before["production_sha256"] == snapshot_environment()["production_sha256"],
            "production_sha256": env_before["production_sha256"],
        },
    }
    write_json(SUMMARY, summary)

    env_after = snapshot_environment()
    write_json(
        ENVIRONMENT,
        {
            **env_before,
            "production_sha256_after": env_after["production_sha256"],
            "production_unchanged": env_before["production_sha256"] == env_after["production_sha256"],
            "history_sha256_after": env_after["history"],
            "history_unchanged": env_before["history"] == env_after["history"],
            "previous_dirs_after": env_after["previous_dirs"],
            "previous_dirs_unchanged": env_before["previous_dirs"] == env_after["previous_dirs"],
        },
    )
    print(json.dumps(summary["counts"], ensure_ascii=False))


def snapshot_environment() -> dict[str, Any]:
    return {
        "production_sha256": file_sha(SOURCE),
        "history": {version: tree_sha(HISTORY / version) for version in HISTORY_VERSIONS},
        "previous_dirs": {name: tree_sha(path) for name, path in (("v1", V1), ("v2", V2), ("v3", V3), ("v4", V4))},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["freeze", "run"])
    args = parser.parse_args()
    if args.command == "freeze":
        freeze()
    else:
        run()


if __name__ == "__main__":
    main()
