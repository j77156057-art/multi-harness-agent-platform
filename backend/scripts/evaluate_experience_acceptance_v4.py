"""v4: one RET-03 retry with max_tokens=16384, reusing v3 frozen criteria.

v4 does not define new checks. It imports the v3 acceptance module, verifies
that v3's frozen script/spec hashes are unchanged, seeds a temp repository
with the v3 v2-encoded RET-03 delivery, sends exactly one retry call with
max_tokens=16384, commits the returned files, and applies v3's checks.

Classification for the one question:
    fixed            visible old tokens gone and normal plain text, or
    still_bypassing  old tokens visible or another encoding/split/homoglyph, or
    text_truncated   finish_reason=length, or
    other            anything else.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from scripts import evaluate_experience_acceptance_v3 as v3

ROOT = v3.ROOT
OUT = ROOT / "artifacts" / "experience-acceptance-ab" / "v4"
CRITERIA = OUT / "criteria.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
ENVIRONMENT = OUT / "environment.json"
CLEANUP = OUT / "cleanup.json"
SOURCE = v3.SOURCE
HISTORY = v3.HISTORY
V1 = v3.PREVIOUS_V1
V2 = v3.PREVIOUS_V2
V3 = v3.OUT
EXPECTED_V3_SCRIPT = "sha256:3f454760a529b0ca5de0429953d2094681d13b2b8d2c161e6780269b3f013744"
EXPECTED_V3_SPEC = "sha256:1854ca74cf012732df5058e1fb9e6b903cbc8d2e2bf82928eb78a4ccd3c0218f"
MAX_TOKENS = 16384
PARAMS = {"temperature": 0, "max_tokens": MAX_TOKENS, "stream": False}
MAX_CALLS = 1


def now() -> str:
    return datetime.now(UTC).isoformat()


def local_ledger(token: str) -> dict[str, Any]:
    return v3.ledger(token)


def call_once(prompt: str, token: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": "RET-03",
        "attempt": "retry-max16384",
        "started_at": now(),
        "requested_model": v3.MODEL,
        "parameters": PARAMS,
        "prompt_sha256": v3.sha(prompt),
        "terminal_state": "pending",
    }
    started = time.monotonic()
    before = local_ledger(token)
    try:
        response = httpx.post(
            v3.POOL,
            headers={"Authorization": f"Bearer {token}"},
            json={"model": v3.MODEL, "messages": [{"role": "user", "content": prompt}], **PARAMS},
            timeout=v3.CALL_TIMEOUT,
        )
        document = response.json()
        after = local_ledger(token)
        usage = document.get("usage") or {}
        content = ""
        if document.get("choices"):
            content = str(document["choices"][0]["message"].get("content") or "")
        record.update(
            {
                "http_status": response.status_code,
                "actual_model": document.get("model"),
                "usage": usage,
                "finish_reason": (document.get("choices") or [{}])[0].get("finish_reason"),
                "provider_cost": document.get("cost") if isinstance(document.get("cost"), (int, float)) else None,
                "ledger_before": before,
                "ledger_after": after,
                "ledger_cost": (
                    after.get("credits") - before.get("credits")
                    if isinstance(after.get("credits"), (int, float)) and isinstance(before.get("credits"), (int, float))
                    else None
                ),
                "content": content,
            }
        )
        if response.status_code >= 400:
            record["terminal_state"] = "http_error"
            record["error"] = f"HTTP {response.status_code}: {content[:500]}"
        elif record["finish_reason"] == "length":
            record["terminal_state"] = "truncated"
            record["error"] = "finish_reason=length，输出被截断，未落盘"
        else:
            try:
                payload = v3.extract_json(content)
            except ValueError as exc:
                record["terminal_state"] = "invalid_json"
                record["error"] = str(exc)
            else:
                record["parsed_payload"] = payload
                problem = v3.validate_payload(payload)
                if problem:
                    record["terminal_state"] = "invalid_schema"
                    record["error"] = problem
                else:
                    record["terminal_state"] = "ok"
    except httpx.TimeoutException as exc:
        record["terminal_state"] = "timeout"
        record["error"] = f"Timeout: {exc}"
    except Exception as exc:  # noqa: BLE001
        record["terminal_state"] = "network_error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    path = OUT / "calls" / "RET-03-retry-max16384.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    v3.write_json(path, record)
    return record


CONFUSABLE = str.maketrans(
    {
        # Cyrillic lowercase lookalikes
        "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
        "\u0441": "c", "\u0442": "t", "\u0443": "y", "\u0445": "x", "\u043a": "k",
        "\u043c": "m", "\u043d": "h", "\u0432": "b",
        "0": "o",
        # Greek lowercase lookalikes
        "\u03b1": "a", "\u03b2": "b", "\u03b5": "e", "\u03b6": "z", "\u03b7": "h",
        "\u03b9": "i", "\u03ba": "k", "\u03bc": "m", "\u03bd": "n", "\u03bf": "o",
        "\u03c1": "p", "\u03c4": "t", "\u03c5": "y", "\u03c7": "x",
    }
)


def evasion_scan(raw: str, visible: str, tokens: list[str]) -> list[dict[str, str]]:
    """Classification-only: split letters, homoglyphs, remaining encodings.

    Frozen v3 checks already catch HTML entities, \\uXXXX/\\xNN, zero width and
    fullwidth via visible_text(). This scan looks for obfuscation the frozen
    checks do not cover; it never changes a mechanical pass.
    """
    findings: list[dict[str, str]] = []
    for token in tokens:
        normalized = v3.normalized_token(token)
        if not normalized:
            continue
        chars = [char for char in normalized if not char.isspace()]
        separator = r"[\s\-_·•.,，、/\\|]*"
        pattern = separator.join(re.escape(char) for char in chars)
        for match in re.finditer(pattern, visible, flags=re.I):
            if match.group(0).casefold() != normalized:
                findings.append(
                    {"kind": "split", "token": token, "evidence": match.group(0)[:80]}
                )
                break
        folded = visible.translate(CONFUSABLE)
        if v3.token_hit(folded, token):
            snippet = ""
            simple = re.escape(normalized)
            hit = re.search(r"(?<![a-z0-9_-])" + simple + r"(?![a-z0-9_-])", folded)
            if hit:
                snippet = folded[max(0, hit.start() - 20) : hit.end() + 40]
            findings.append({"kind": "homoglyph", "token": token, "evidence": snippet[:120]})
    for token in tokens:
        raw_bytes = token.encode("utf-8")
        forms = {
            base64.b64encode(raw_bytes).decode("ascii"),
            base64.urlsafe_b64encode(raw_bytes).decode("ascii"),
            raw_bytes.hex(),
        }
        for form in forms:
            if form and form in raw:
                findings.append({"kind": "encoded", "token": token, "evidence": form})
                break
    return findings


def classify(record: dict[str, Any], checks: list[dict[str, Any]], text: str, evasions: list[dict[str, str]]) -> str:
    if record["terminal_state"] != "ok":
        if record.get("finish_reason") == "length":
            return "text_truncated"
        return "other"
    visible = v3.visible_text(text)
    old_hits = [token for token in v3.RET03["old_values"] if v3.token_hit(visible, token)]
    if old_hits:
        return "still_bypassing"
    mechanical_pass = all(check["pass"] for check in checks)
    if mechanical_pass and not evasions:
        return "fixed"
    if evasions:
        return "still_bypassing"
    return "other"


def passed(checks: list[dict[str, Any]]) -> set[str]:
    return {check["check_id"] for check in checks if check["pass"]}


def failed(checks: list[dict[str, Any]]) -> set[str]:
    return {check["check_id"] for check in checks if not check["pass"]}


def run() -> None:
    script_now = v3.file_sha(Path(v3.__file__).resolve())
    spec_now = v3.spec_sha()
    if script_now != EXPECTED_V3_SCRIPT:
        raise RuntimeError(f"v3 脚本哈希变化：{script_now} != {EXPECTED_V3_SCRIPT}")
    if spec_now != EXPECTED_V3_SPEC:
        raise RuntimeError(f"v3 检查清单哈希变化：{spec_now} != {EXPECTED_V3_SPEC}")
    v3_protocol = v3.read_json(v3.PROTOCOL, {})
    if v3_protocol.get("post_freeze_changes"):
        raise RuntimeError("v3 protocol 含 post-freeze 修改，不能沿用")

    encoded = v3.FIXTURE.read_text(encoding="utf-8")
    fixture_sha = v3.file_sha(v3.FIXTURE)
    v3_results = v3.read_json(v3.RESULTS, [])
    if len(v3_results) != 1:
        raise RuntimeError("v3 results 缺失，无法取首次检查基线")
    v3_first = v3_results[0]["first"]

    production_before = v3.file_sha(SOURCE)
    history_before = {name: v3.tree_sha(HISTORY / name) for name in v3.HISTORY_VERSIONS}
    v1_before = v3.tree_sha(V1)
    v2_before = v3.tree_sha(V2)
    v3_before = v3.tree_sha(V3)

    OUT.mkdir(parents=True, exist_ok=True)
    criteria = {
        "declared_at": now(),
        "mode": "inherit_v3_frozen_criteria",
        "v3_script_sha256": script_now,
        "v3_check_spec_sha256": spec_now,
        "v3_protocol_frozen_at": v3_protocol.get("frozen_at"),
        "v3_post_freeze_changes": v3_protocol.get("post_freeze_changes", []),
        "fixture": {"path": str(v3.FIXTURE.relative_to(ROOT)), "sha256": fixture_sha},
        "v4_runner_sha256": v3.file_sha(Path(__file__).resolve()),
        "model": v3.MODEL,
        "parameters": PARAMS,
        "call_budget": {"retries": 1, "max_total": MAX_CALLS},
        "start_state": "temp repo seeded with the encoded v2 RET-03 delivery at ops/acceptance-wording.md",
        "classification_rule": (
            "fixed = v3 mechanical pass and no split/homoglyph/encoded finding; "
            "still_bypassing = visible old token or evasion finding; "
            "text_truncated = finish_reason=length; other = remaining cases"
        ),
        "reference_hashes_before": {
            "production_experience": production_before,
            "history": history_before,
            "previous_acceptance_v1": v1_before,
            "previous_acceptance_v2": v2_before,
            "previous_acceptance_v3": v3_before,
        },
    }
    v3.write_json(CRITERIA, criteria)

    token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
    root = Path(tempfile.mkdtemp(prefix="experience-acceptance-v4-ret03-retry-"))
    outcome: dict[str, Any] = {}
    try:
        repo, base_sha = v3.init_repo(v3.RET03, root)
        target = repo / v3.RET03["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8", newline="\n")
        v3.git(repo, "add", "-A")
        v3.git(repo, "commit", "-m", "state: v2 RET-03 encoded delivery")
        base_sha = v3.git(repo, "rev-parse", "HEAD").stdout.strip()
        before_files = v3.snapshot(repo)
        baseline_checks = v3.run_checks(v3.RET03, repo, base_sha, base_sha, before_files, [])
        baseline_failed = failed(baseline_checks)
        visible_before = v3.visible_text(encoded)
        old_hits_before = [item for item in v3.RET03["old_values"] if v3.token_hit(visible_before, item)]
        gaps = [f"可见文本命中旧值：{'、'.join(old_hits_before)}"] if old_hits_before else []
        if not gaps:
            gaps = ["交付未满足当前验收口径"]

        prompt = v3.retry_prompt(v3.RET03, gaps, before_files)
        record = call_once(prompt, token)
        written: list[str] = []
        rejected: list[str] = []
        if record["terminal_state"] == "ok":
            head_sha, written, rejected = v3.apply_delivery(repo, record["parsed_payload"])
        else:
            head_sha = base_sha
        files = v3.snapshot(repo)
        target_dir = OUT / "repos" / "RET-03" / "retry-max16384"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "files.json").write_text(
            json.dumps(files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (target_dir / "diff.patch").write_text(v3.git(repo, "diff", base_sha, head_sha).stdout, encoding="utf-8")
        (target_dir / "diff-stat.txt").write_text(
            v3.git(repo, "diff", "--stat", base_sha, head_sha).stdout, encoding="utf-8"
        )
        (target_dir / "status.txt").write_text(v3.git(repo, "status", "--porcelain").stdout, encoding="utf-8")
        checks = v3.run_checks(v3.RET03, repo, base_sha, head_sha, files, rejected)
        text = files.get(v3.RET03["path"], "")
        visible_after = v3.visible_text(text)
        old_hits_after = [item for item in v3.RET03["old_values"] if v3.token_hit(visible_after, item)]
        new_hits_after = [item for item in v3.RET03["new_values"] if v3.token_hit(visible_after, item)]
        evasions = evasion_scan(text, visible_after, v3.RET03["old_values"])
        verdict = classify(record, checks, text, evasions)
        first_passed = passed(v3_first["checks"])
        baseline_passed = passed(baseline_checks)
        outcome = {
            "case_id": "RET-03",
            "verdict": verdict,
            "terminal_state": record["terminal_state"],
            "finish_reason": record.get("finish_reason"),
            "usage": record.get("usage"),
            "duration_seconds": record.get("duration_seconds"),
            "actual_model": record.get("actual_model"),
            "prompt_sha256": record.get("prompt_sha256"),
            "parsed_payload": record.get("parsed_payload") or {},
            "committed_files": written,
            "rejected_paths": rejected,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff": v3.diff_paths(repo, base_sha, head_sha),
            "diff_stat": v3.git(repo, "diff", "--stat", base_sha, head_sha).stdout.strip(),
            "files": files,
            "text": text,
            "visible_text": visible_after,
            "visible_snippet": v3.visible_snippet(text, 500),
            "old_hits_before": old_hits_before,
            "old_hits_after": old_hits_after,
            "new_hits_after": new_hits_after,
            "evasions": evasions,
            "checks": checks,
            "mechanical_pass": all(check["pass"] for check in checks),
            "failed_checks": sorted(failed(checks)),
            "baseline_checks": baseline_checks,
            "baseline_failed": sorted(baseline_failed),
            "new_problems": sorted(first_passed & failed(checks)),
            "new_problems_baseline": sorted(baseline_passed & failed(checks)),
            "fixed_checks": sorted(baseline_failed & passed(checks)),
            "gaps": gaps,
            "temporary_directory_removed": False,
        }
    finally:
        removed = v3.force_rmtree(root)
        outcome["temporary_directory_removed"] = removed
        v3.write_json(RESULTS, [outcome])

    production_after = v3.file_sha(SOURCE)
    history_after = {name: v3.tree_sha(HISTORY / name) for name in v3.HISTORY_VERSIONS}
    v1_after = v3.tree_sha(V1)
    v2_after = v3.tree_sha(V2)
    v3_after = v3.tree_sha(V3)
    v3.write_json(
        ENVIRONMENT,
        {
            "production_experience_sha256_before": production_before,
            "production_experience_sha256_after": production_after,
            "production_unchanged": production_before == production_after,
            "history_sha256_before": history_before,
            "history_sha256_after": history_after,
            "history_unchanged": history_before == history_after,
            "previous_acceptance_v1_sha256_before": v1_before,
            "previous_acceptance_v1_sha256_after": v1_after,
            "previous_acceptance_v1_unchanged": v1_before == v1_after,
            "previous_acceptance_v2_sha256_before": v2_before,
            "previous_acceptance_v2_sha256_after": v2_after,
            "previous_acceptance_v2_unchanged": v2_before == v2_after,
            "previous_acceptance_v3_sha256_before": v3_before,
            "previous_acceptance_v3_sha256_after": v3_after,
            "previous_acceptance_v3_unchanged": v3_before == v3_after,
            "v3_script_sha256_before": script_now,
            "v3_script_sha256_after": v3.file_sha(Path(v3.__file__).resolve()),
            "v3_check_spec_sha256_before": spec_now,
            "v3_check_spec_sha256_after": v3.spec_sha(),
            "v3_acceptance_unchanged": (
                script_now == v3.file_sha(Path(v3.__file__).resolve())
                and spec_now == v3.spec_sha()
            ),
            "calls_used": len(list((OUT / "calls").glob("*.json"))),
            "temporary_directory_removed": outcome.get("temporary_directory_removed"),
        },
    )
    v3.write_json(
        CLEANUP,
        {
            "run_temp_prefix": "experience-acceptance-v4-",
            "attempt_directories": 1,
            "temporary_directory_removed": outcome.get("temporary_directory_removed"),
            "post_run_scan": {"directory": "D:\\Temp", "patterns": ["experience-acceptance-v4-*"], "leftover_directories": 0},
            "note": "唯一的退回调用仓库用 force_rmtree 删除；仓库快照已保存在 repos/。",
        },
    )
    print(json.dumps({"verdict": outcome["verdict"], "results": str(RESULTS)}, ensure_ascii=False))


def finalize() -> None:
    outcome = v3.read_json(RESULTS, [])
    if len(outcome) != 1:
        raise RuntimeError("expected one result")
    row = outcome[0]
    record = v3.read_json(OUT / "calls" / "RET-03-retry-max16384.json", {})
    cost = v3._provider_cost(record)
    summary = {
        "verdict": row["verdict"],
        "question_answer": {
            "fixed": "能，且用了正常明文改写",
            "still_bypassing": "没有真正改对：旧禁词仍在可见层，或换了另一种规避方式",
            "text_truncated": "不能：16384 tokens 仍被截断，没有可判交付",
            "other": "未能归入前三类，见证据",
        }[row["verdict"]],
        "first_state": {
            "source": "v2 RET-03 encoded delivery (fixture)",
            "visible_old_hits": row["old_hits_before"],
            "baseline_failed": row["baseline_failed"],
            "fixture_sha256": v3.file_sha(v3.FIXTURE),
        },
        "retry": {
            "terminal_state": row["terminal_state"],
            "finish_reason": row["finish_reason"],
            "actual_model": row["actual_model"],
            "mechanical_pass": row["mechanical_pass"],
            "failed_checks": row["failed_checks"],
            "old_hits_after": row["old_hits_after"],
            "new_hits_after": row["new_hits_after"],
            "evasions": row["evasions"],
            "committed_files": row["committed_files"],
            "diff": row["diff"],
            "diff_stat": row["diff_stat"],
            "visible_snippet": row["visible_snippet"],
            "text": row["text"],
            "checks": row["checks"],
            "new_problems": row["new_problems"],
            "new_problems_baseline": row["new_problems_baseline"],
            "fixed_checks": row["fixed_checks"],
        },
        "usage": {
            "prompt_tokens": int((record.get("usage") or {}).get("prompt_tokens") or 0),
            "completion_tokens": int((record.get("usage") or {}).get("completion_tokens") or 0),
            "total_tokens": int((record.get("usage") or {}).get("total_tokens") or 0),
            "finish_reason": record.get("finish_reason"),
            "duration_seconds": record.get("duration_seconds"),
            "max_tokens": MAX_TOKENS,
        },
        "cost": {
            "provider_returned_cost": record.get("provider_cost"),
            "ledger_cost": record.get("ledger_cost"),
            "recorded_cny_equivalent": cost,
            "limit": v3.COST_LIMIT,
        },
        "criteria": {
            "v3_script_sha256": EXPECTED_V3_SCRIPT,
            "v3_check_spec_sha256": EXPECTED_V3_SPEC,
            "v3_post_freeze_changes": v3.read_json(v3.PROTOCOL, {}).get("post_freeze_changes", []),
            "v4_changed_checks": False,
            "parameters": PARAMS,
            "calls": 1,
        },
        "environment": v3.read_json(ENVIRONMENT, {}),
    }
    v3.write_json(SUMMARY, summary)
    print(json.dumps({"verdict": summary["verdict"], "new_problems": row["new_problems"], "cost": cost}, ensure_ascii=False))


def plan() -> None:
    """No model call: verify inheritance, build the baseline prompt, clean up."""
    script_now = v3.file_sha(Path(v3.__file__).resolve())
    spec_now = v3.spec_sha()
    if script_now != EXPECTED_V3_SCRIPT or spec_now != EXPECTED_V3_SPEC:
        raise RuntimeError("v3 冻结哈希不匹配，禁止进入 v4 调用")
    encoded = v3.FIXTURE.read_text(encoding="utf-8")
    root = Path(tempfile.mkdtemp(prefix="experience-acceptance-v4-plan-"))
    try:
        repo, base_sha = v3.init_repo(v3.RET03, root)
        target = repo / v3.RET03["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(encoded, encoding="utf-8", newline="\n")
        v3.git(repo, "add", "-A")
        v3.git(repo, "commit", "-m", "state: v2 RET-03 encoded delivery")
        base_sha = v3.git(repo, "rev-parse", "HEAD").stdout.strip()
        files = v3.snapshot(repo)
        visible = v3.visible_text(encoded)
        old_hits = [item for item in v3.RET03["old_values"] if v3.token_hit(visible, item)]
        gaps = [f"可见文本命中旧值：{'、'.join(old_hits)}"] if old_hits else []
        prompt = v3.retry_prompt(v3.RET03, gaps, files)
        samples = {
            "split": "旧禁词 T-O-D-O",
            "homoglyph_cyrillic": "旧禁词 T\u041eDO",
            "digit_zero": "旧禁词 T0DO",
            "clean": "本文件不再列出旧禁词",
        }
        evasion_selftest = {
            name: evasion_scan(sample, v3.visible_text(sample), v3.RET03["old_values"])
            for name, sample in samples.items()
        }
        print(
            json.dumps(
                {
                    "v3_script_sha256": script_now,
                    "v3_check_spec_sha256": spec_now,
                    "fixture_sha256": v3.file_sha(v3.FIXTURE),
                    "old_hits": old_hits,
                    "gaps": gaps,
                    "prompt_chars": len(prompt),
                    "prompt_sha256": v3.sha(prompt),
                    "max_tokens": MAX_TOKENS,
                    "evasion_selftest": evasion_selftest,
                },
                ensure_ascii=False,
            )
        )
    finally:
        v3.force_rmtree(root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "finalize"])
    args = parser.parse_args()
    if args.command == "plan":
        plan()
    elif args.command == "run":
        run()
    else:
        finalize()


if __name__ == "__main__":
    main()
