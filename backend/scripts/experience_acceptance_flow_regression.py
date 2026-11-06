"""接入前后判定一致性回归：v5 冻结判据 vs 接入后的交付验收层。

同一批输入（v1–v4 的 15 条历史交付文本）各判一次，逐条比对 old→new。
任何一条变了就 exit 1 —— 说明接线改到了判定，必须停下来。

用法：
    PYTHONPATH=backend python backend/scripts/experience_acceptance_flow_regression.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.acceptance.flow_gate import TOKENS, frozen_identity, judge_text  # noqa: E402

import evaluate_experience_acceptance_v5 as V5  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "artifacts" / "experience-acceptance-flow" / "v1"


def _hit_key(hit: dict) -> tuple:
    return (
        hit["token"],
        hit["visible_hit"],
        hit["skeleton_hit"],
        hit["skeleton_index"],
        hit["skeleton_fragment"],
    )


def main() -> int:
    rows = V5.all_texts()
    results = []
    for row in rows:
        old_verdict, old_hits = V5.new_verdict(row)
        tokens = V5.BANNED_TOKENS[f"{row['source']}/{row['case_id']}"]
        new_verdict, new_hits = judge_text(
            row["text"],
            tokens=tokens,
            other_checks_pass=row["other_checks_pass"],
            previous_verdict=row["old_verdict"],
        )
        same_verdict = old_verdict == new_verdict
        same_hits = sorted(map(_hit_key, old_hits)) == sorted(map(_hit_key, new_hits))
        results.append(
            {
                "source": row["source"],
                "case": row["case_id"],
                "attempt": row["attempt"],
                "path": row["path"],
                "old_verdict": old_verdict,
                "new_verdict": new_verdict,
                "same_verdict": same_verdict,
                "same_hits": same_hits,
                "old_hits": [hit["token"] for hit in old_hits],
                "new_hits": [hit["token"] for hit in new_hits],
            }
        )

    changed = [item for item in results if not item["same_verdict"] or not item["same_hits"]]
    payload = {
        "frozen": frozen_identity(),
        "tokens": list(TOKENS),
        "rows": results,
        "mismatches": changed,
        "verdict": "consistent" if not changed else "INCONSISTENT",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "regression.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"rows={len(results)} mismatches={len(changed)} verdict={payload['verdict']}")
    for item in results:
        mark = "=" if item["same_verdict"] and item["same_hits"] else "!"
        print(
            f"{mark} {item['source']}/{item['case']}#{item['attempt']} "
            f"{item['path'] or '-'}: {item['old_verdict']} -> {item['new_verdict']}"
        )
    return 0 if not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
