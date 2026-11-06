"""A/B 效果实验：开启验收（acceptance.enabled）到底带来多少净收益。

设计（先冻结、后跑）：

* 3 个模板 × 2 臂 = 6 次执行。两臂派单 JSON **逐字节相同**，唯一差异是
  ``acceptance.enabled`` 的 true/false；
* 每个模板只埋一种真实陷阱：禁词 / 路径 / 数量，其余难度对齐（单文件、
  正文不少于 120 个非空白字符、每轮一次模型调用 + 一次提交）；
* E 臂（enabled）最多一次退回，退回工单由生产
  ``ExperienceAcceptanceGate.retry_goal`` 生成；D 臂（disabled）单轮到底，
  没有任何退回机会；
* **打分独立于验收器**：最终交付一律用 v5 冻结判据
  （``flow_gate.banned_hits`` 的可见文本 + 字母数字骨架）与机械检查项
  （``delivery_path_exists`` / ``content_min_length`` / ``content_min_120`` /
  ``pending_item_count``）离线评，两臂同一把尺子；验收器自己的 verdict
  只用来决定 E 臂是否退回，**不当成绩**。
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-acceptance-effect" / "v1"
PROTOCOL = OUT / "protocol.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
ENVIRONMENT = OUT / "environment.json"
SOURCE = ROOT / "data" / "admin" / "experience.json"
SCRIPTS = ROOT / "backend" / "scripts"

MODEL = "MiniMax-M3.1-Flash-Preview"
PARAMS = {"temperature": 0, "max_tokens": 8192, "stream": False}
POOL = "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions"
POOL_STATUS = "http://127.0.0.1:8848/api/v1/pool/status"
COST_LIMIT = 10.0
DATASET_VERSION = "experience-acceptance-effect-v1.0.0"
MAX_FILES = 8
MAX_CHARS = 40_000
CALL_TIMEOUT = 240.0
ARMS = ("D", "E")  # D=disabled（单轮到底），E=enabled（最多一次退回）
MAX_CALLS = 1 + 6 + 3  # 预检 + 6 次首轮 + E 臂最多 3 次退回
ACCEPTANCE_DESCRIPTION = "交付物存在且满足派单中的验收约束"
MIN_VISIBLE_CHARS_DISPATCH = 120

README_SEED = """# 交付仓库

本仓库用于核对执行者是否按派单真实创建或修改文件。仓库内容以派单为准。
"""

OLD_PENDING_SEED = """# 第一轮待补项（旧版）

1. TODO：补齐失败路径的验收说明
2. 待补充：统一标题强调风格
3. 占位：补充证据链接
4. 示例文本：给出回执样例
"""

ROUND1_NOTES_SEED = """# 第一轮交接说明

本轮完成了适配层注册与回执字段核对，遗留两项待跟进。
"""

FIRST_ROUND_SEED = """# 第一轮遗留待补项

1. 补齐失败路径的验收说明。
2. 统一标题强调风格。
3. 补充证据链接。
4. 给出回执样例。
5. 标注未确认项的限制。
"""


# -----------------------------------------------------------------------------
# 模板：每个只埋一种陷阱，其它难度对齐
# -----------------------------------------------------------------------------

CASES: list[dict[str, Any]] = [
    {
        "template": "BANNED",
        "trap": "banned",
        "path": "ops/round-2-pending.md",
        "requirement": (
            "整理第二轮复盘的待补项说明，写入 ops/round-2-pending.md。"
            "正文不少于 120 个非空白字符，写清本轮已经修掉的项与仍需跟进的项。"
            "旧版说明用 TODO、待补充、占位、示例文本 这四个旧标记表示未完成项；"
            "本轮改用具体、可核验的中文描述，交付正文里不得再出现这四个旧标记。"
        ),
        "seeds": {"README.md": README_SEED, "ops/round-1-pending.md": OLD_PENDING_SEED},
    },
    {
        "template": "PATH",
        "trap": "path",
        "path": "ops/handoff/round-2/notes.md",
        "requirement": (
            "整理第二轮交接说明，写入 ops/handoff/round-2/notes.md"
            "（注意是三级目录，层级照写）。正文不少于 120 个非空白字符，"
            "说明本轮完成项与待跟进项。"
        ),
        "seeds": {
            "README.md": README_SEED,
            "ops/handoff/round-1/notes.md": ROUND1_NOTES_SEED,
        },
    },
    {
        "template": "COUNT",
        "trap": "count",
        "path": "ops/first-round-pending.md",
        "count": 3,
        "requirement": (
            "整理第一轮遗留的待补项，写入 ops/first-round-pending.md。"
            "正文不少于 120 个非空白字符；其中「待补项」小节必须恰好 3 条。"
            "上一轮记录过 5 条，本轮只保留其中 3 条。"
        ),
        "seeds": {"README.md": README_SEED, "ops/first-round.md": FIRST_ROUND_SEED},
    },
]


# -----------------------------------------------------------------------------
# 生产代码：验收器与冻结判据（只读引用，不改动）
# -----------------------------------------------------------------------------


def _load_v1_helpers():
    spec = importlib.util.spec_from_file_location(
        "acceptance_effect_v1_helpers", SCRIPTS / "evaluate_experience_acceptance_v1.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["acceptance_effect_v1_helpers"] = module
    spec.loader.exec_module(module)
    return module


V1 = _load_v1_helpers()
sys.path.insert(0, str(ROOT / "backend"))
from app.acceptance.flow_gate import (  # noqa: E402
    CONTENT_MIN_LENGTH,
    MAX_RETRIES,
    TOKENS,
    ExperienceAcceptanceGate,
    banned_hits,
    frozen_identity,
    visible_text,
)


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


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return V1.git(repo, *args, check=check)


def remove_tree(path: Path) -> bool:
    """Windows 上 git 对象是只读的，直接 rmtree 会留下残留；这里清掉只读位再删。"""
    for _ in range(5):
        shutil.rmtree(
            path,
            ignore_errors=True,
            onerror=lambda func, target, _exc: (
                os.chmod(target, stat.S_IWRITE),
                func(target),
            ),
        )
        if not path.exists():
            return True
        time.sleep(0.2)
    return not path.exists()


# -----------------------------------------------------------------------------
# 派单与提示词
# -----------------------------------------------------------------------------


def dispatch_payload(case: dict[str, Any], arm: str) -> dict[str, Any]:
    """两臂唯一的差异就是 acceptance.enabled 这个布尔值。"""
    return {
        "task_id": f"AE-{case['template']}",
        "title": f"{case['template']} · 交付场景",
        "goal": case["requirement"],
        "exclusive_paths": [case["path"]],
        "acceptance": {
            "enabled": arm == "E",
            "profile": "experience-v5",
            "description": ACCEPTANCE_DESCRIPTION,
        },
        "harness_id": "qoder-member-1",
    }


def dispatch_text(case: dict[str, Any], arm: str) -> str:
    return json.dumps(dispatch_payload(case, arm), ensure_ascii=False, indent=2)


def executor_prompt(case: dict[str, Any], arm: str) -> str:
    seeds = "\n\n".join(
        f"--- 文件 {path} ---\n{content}" for path, content in case["seeds"].items()
    )
    return (
        "你是本仓库的执行者。请按下面的派单完成交付，在仓库中真实创建或修改文件。\n\n"
        f"派单（JSON）：\n{dispatch_text(case, arm)}\n\n"
        "仓库现有文件（完整内容）：\n"
        f"{seeds}\n\n"
        "写盘规则：只能写相对路径；不得写 .git 下的路径、绝对路径或包含 .. 的路径。\n"
        "单文件内容不超过 1200 个汉字，最多写 8 个文件。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明"}\n'
        "content 里保留换行，JSON 中用 \\n 转义；修改已有文件时给出该文件的完整新内容。\n"
    )


def retry_prompt(case: dict[str, Any], arm: str, retry_goal: str, files: dict[str, str]) -> str:
    dispatch = dispatch_payload(case, arm)
    dispatch["goal"] = retry_goal
    return (
        "你是本仓库的执行者。上一次交付没有通过验收，按下面的退回工单逐项修正。\n\n"
        "退回后的派单（JSON）：\n"
        f"{json.dumps(dispatch, ensure_ascii=False, indent=2)}\n\n"
        "当前仓库已提交文件的完整内容（JSON，path -> content）：\n"
        f"{json.dumps(files, ensure_ascii=False, indent=2)}\n\n"
        "写盘规则同上：只写相对路径，不写 .git、绝对路径或 ..。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明"}\n'
    )


# -----------------------------------------------------------------------------
# 离线判分尺子（与验收器独立，冻结后不许改）
# -----------------------------------------------------------------------------


def rule_definitions() -> dict[str, Any]:
    return {
        "delivery_path_exists": {
            "kind": "mechanical",
            "source": "flow_gate.delivery_path_exists",
            "expected": "派单 exclusive_paths 指定的文件在最终提交里存在且非空",
        },
        "content_min_length": {
            "kind": "mechanical",
            "source": "flow_gate.CONTENT_MIN_LENGTH（v5 冻结阈值）",
            "expected": f"可见文本长度 >= {CONTENT_MIN_LENGTH}",
        },
        "banned_hits_absent": {
            "kind": "frozen-v5",
            "source": "flow_gate.banned_hits：可见文本 + 字母数字骨架各匹配一次",
            "tokens": list(TOKENS),
            "expected": "任一命中即不合格",
        },
        "content_min_120": {
            "kind": "mechanical",
            "source": "本轮补齐项：派单明写的 120 个非空白字符",
            "expected": f"可见文本去空白后 >= {MIN_VISIBLE_CHARS_DISPATCH}",
        },
        "pending_item_count": {
            "kind": "mechanical",
            "source": "pending_items_robust（v1 pending_items 口径 + 同名 H1 跳过）",
            "applies_to": "COUNT 模板（其余模板 N/A，不计入 fail）",
            "expected": "「待补项」小节的条目数 == 3",
        },
    }


def _non_ws(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


def pending_items_robust(text: str) -> tuple[int, list[str], str]:
    """数「待补项」小节的条目；H1 也含该词时不会被误当小节而数成 0。

    与 v1 的口径一致（列表项优先、其次表格行），只多一条：候选小节里挑
    条目最多的那个（标题层级不同但同名的写法很常见）。
    """
    lines = text.splitlines()
    sections: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line.strip())
        if not match or "待补项" not in match.group(2):
            continue
        level = len(match.group(1))
        body: list[str] = []
        for follow in lines[index + 1 :]:
            next_heading = HEADING_RE.match(follow.strip())
            if next_heading and len(next_heading.group(1)) <= level:
                break
            body.append(follow)
        sections.append((level, body))
    best: tuple[int, list[str], str] = (0, [], "没有「待补项」小节")
    for _level, body in sections:
        list_items = [
            line.strip() for line in body if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
        ]
        table_rows = [line.strip() for line in body if line.strip().startswith("|")]
        if list_items:
            candidate = (len(list_items), list_items, "列表")
        elif len(table_rows) >= 2:
            candidate = (max(len(table_rows) - 2, 0), table_rows[2:], "表格")
        else:
            candidate = (0, [], "小节内没有条目")
        if candidate[0] > best[0]:
            best = candidate
    return best


def score_delivery(case: dict[str, Any], files: dict[str, str]) -> dict[str, Any]:
    """用同一把尺子打分；只用最终提交里的文件，不看验收器 verdict。"""
    path = case["path"]
    text = files.get(path, "") if path in files else ""
    rendered = visible_text(text)
    items: list[dict[str, Any]] = []

    items.append(
        {
            "rule": "delivery_path_exists",
            "applicable": True,
            "pass": bool(text.strip()),
            "observed": {"path_in_files": path in files, "chars": len(text)},
            "evidence": path,
        }
    )
    items.append(
        {
            "rule": "content_min_length",
            "applicable": True,
            "pass": len(rendered) >= CONTENT_MIN_LENGTH,
            "observed": {"visible_chars": len(rendered), "min": CONTENT_MIN_LENGTH},
            "evidence": "flow_gate.visible_text",
        }
    )
    hits = banned_hits(text, list(TOKENS))
    items.append(
        {
            "rule": "banned_hits_absent",
            "applicable": True,
            "pass": not hits,
            "observed": {
                "hits": [
                    {
                        "token": hit["token"],
                        "visible_hit": hit["visible_hit"],
                        "skeleton_hit": hit["skeleton_hit"],
                        "skeleton_index": hit.get("skeleton_index"),
                        "skeleton_context": hit.get("skeleton_context"),
                    }
                    for hit in hits
                ]
            },
            "evidence": "flow_gate.banned_hits",
        }
    )
    items.append(
        {
            "rule": "content_min_120",
            "applicable": True,
            "pass": _non_ws(rendered) >= MIN_VISIBLE_CHARS_DISPATCH,
            "observed": {"non_ws_chars": _non_ws(rendered), "min": MIN_VISIBLE_CHARS_DISPATCH},
            "evidence": "派单正文要求",
        }
    )
    if case["trap"] == "count":
        count, rows, how = pending_items_robust(text)
        items.append(
            {
                "rule": "pending_item_count",
                "applicable": True,
                "pass": count == int(case["count"]),
                "observed": {"count": count, "expected": case["count"], "how": how, "items": rows},
                "evidence": "「待补项」小节",
            }
        )
    else:
        items.append(
            {
                "rule": "pending_item_count",
                "applicable": False,
                "pass": None,
                "observed": {"reason": "该模板没有数量要求"},
                "evidence": "N/A",
            }
        )

    failed = [item["rule"] for item in items if item["applicable"] and not item["pass"]]
    return {"fail": bool(failed), "failed_rules": failed, "items": items}


# -----------------------------------------------------------------------------
# 单轮执行
# -----------------------------------------------------------------------------


def ledger(token: str) -> dict[str, Any]:
    return V1.ledger(token)


def call_pool(prompt: str, tag: str, token: str, expect_files: bool = True) -> dict[str, Any]:
    record: dict[str, Any] = {
        "tag": tag,
        "started_at": now(),
        "requested_model": MODEL,
        "parameters": PARAMS,
        "prompt_sha256": sha(prompt),
        "terminal_state": "pending",
    }
    started = time.monotonic()
    before = ledger(token)
    try:
        response = httpx.post(
            POOL,
            headers={"Authorization": f"Bearer {token}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], **PARAMS},
            timeout=CALL_TIMEOUT,
        )
        document = response.json()
        after = ledger(token)
        usage = document.get("usage") or {}
        cost = document.get("cost", usage.get("cost"))
        content = ""
        if document.get("choices"):
            content = str(document["choices"][0]["message"].get("content") or "")
        record.update(
            {
                "http_status": response.status_code,
                "actual_model": document.get("model"),
                "usage": usage,
                "finish_reason": (document.get("choices") or [{}])[0].get("finish_reason"),
                "provider_cost": cost if isinstance(cost, (int, float)) else None,
                "ledger_before": before,
                "ledger_after": after,
                "ledger_cost": (
                    after.get("credits") - before.get("credits")
                    if isinstance(after.get("credits"), (int, float))
                    and isinstance(before.get("credits"), (int, float))
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
                payload = V1.extract_json(content)
            except ValueError as exc:
                record["terminal_state"] = "invalid_json"
                record["error"] = str(exc)
            else:
                problem = V1.validate_payload(payload) if expect_files else None
                record["parsed_payload"] = payload
                if problem:
                    record["terminal_state"] = "invalid_schema"
                    record["error"] = problem
                else:
                    record["terminal_state"] = "ok"
    except httpx.TimeoutException as exc:
        record["terminal_state"] = "timeout"
        record["error"] = f"Timeout: {exc}"
    except Exception as exc:  # noqa: BLE001 - 回执保留原始错误
        record["terminal_state"] = "network_error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    write_json(OUT / "calls" / f"{tag}.json", record)
    return record


def init_repo(case: dict[str, Any], root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "acceptance-effect-executor")
    git(repo, "config", "user.email", "executor@local")
    git(repo, "config", "core.autocrlf", "false")
    for path, content in case["seeds"].items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "seed: 初始化仓库")
    return repo, git(repo, "rev-parse", "HEAD").stdout.strip()


def store_repo(case_id: str, tag: str, repo: Path, base_sha: str) -> None:
    target = OUT / "repos" / case_id / tag
    target.mkdir(parents=True, exist_ok=True)
    (target / "files.json").write_text(
        json.dumps(V1.snapshot(repo), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    head = git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
    patch = git(repo, "diff", base_sha, head).stdout if head and head != base_sha else ""
    (target / "diff.patch").write_text(patch, encoding="utf-8")
    (target / "status.txt").write_text(git(repo, "status", "--porcelain").stdout, encoding="utf-8")


def run_round(
    case: dict[str, Any],
    arm: str,
    attempt: int,
    token: str,
    feedback: tuple[str, dict[str, str]] | None,
) -> dict[str, Any]:
    tag = f"{case['template']}-{arm}-a{attempt}"
    root = Path(tempfile.mkdtemp(prefix=f"accept-effect-{case['template']}-{arm}-a{attempt}-"))
    outcome: dict[str, Any] = {"template": case["template"], "arm": arm, "attempt": attempt}
    try:
        repo, base_sha = init_repo(case, root)
        if feedback is None:
            prompt = executor_prompt(case, arm)
        else:
            prompt = retry_prompt(case, arm, feedback[0], feedback[1])
        call = call_pool(prompt, tag, token)
        written: list[str] = []
        rejected: list[str] = []
        if call["terminal_state"] == "ok":
            head_sha, written, rejected = V1.apply_delivery(repo, call["parsed_payload"])
        else:
            head_sha = base_sha
        files = V1.snapshot(repo)
        changed = [row["path"] for row in V1.diff_paths(repo, base_sha, head_sha)]
        store_repo(f"{case['template']}-{arm}", f"attempt-{attempt}", repo, base_sha)
        outcome.update(
            {
                "prompt_sha256": call["prompt_sha256"],
                "terminal_state": call["terminal_state"],
                "error": call.get("error"),
                "usage": call.get("usage"),
                "finish_reason": call.get("finish_reason"),
                "actual_model": call.get("actual_model"),
                "provider_cost": call.get("provider_cost"),
                "ledger_cost": call.get("ledger_cost"),
                "committed_files": written,
                "rejected_paths": rejected,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "changed_paths": changed,
                "files": files,
                "score": score_delivery(case, files),
                "temporary_directory_removed": False,
            }
        )
    finally:
        outcome["temporary_directory_removed"] = remove_tree(root)
    return outcome


# -----------------------------------------------------------------------------
# 单臂执行（臂之间的差异只有：E 臂跑验收器 + 最多一次退回）
# -----------------------------------------------------------------------------


def run_arm(case: dict[str, Any], arm: str, token: str) -> dict[str, Any]:
    first = run_round(case, arm, 1, token, None)
    row: dict[str, Any] = {
        "template": case["template"],
        "arm": arm,
        "first": first,
        "retry": None,
        "gate": None,
    }
    if arm != "E":
        row["final"] = first
        return row

    gate = ExperienceAcceptanceGate(
        task_id=f"AE-{case['template']}-E",
        expected_paths=[case["path"]],
    )
    repo_tag = f"{case['template']}-{arm}"
    # 验收器作用于真实提交；本函数只读仓库，不调用 persist（不写生产 data/acceptance）。
    record1 = _gate_check(gate, first, case)
    row["gate"] = {"rounds": [record1], "retry_used": False}
    if record1["verdict"] == "pass":
        row["first"]["gate_verdict"] = record1["verdict"]
        row["final"] = first
        return row
    if not gate.retry_available():
        row["final"] = first
        return row
    retry_goal = gate.retry_goal(case["requirement"], record1)
    second = run_round(case, arm, 2, token, (retry_goal, first["files"]))
    record2 = _gate_check(gate, second, case)
    row["retry"] = second
    row["gate"]["rounds"].append(record2)
    row["gate"]["retry_used"] = True
    row["final"] = second
    row["final"]["gate_verdict"] = record2["verdict"]
    row["first"]["gate_verdict"] = record1["verdict"]
    return row


def _gate_check(gate: ExperienceAcceptanceGate, round_out: dict[str, Any], case: dict[str, Any]):
    """把一次真实提交喂给生产验收器。仓库已删除，因此用提交快照重建只读仓库。"""
    root = Path(tempfile.mkdtemp(prefix=f"accept-effect-gate-{case['template']}-"))
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    try:
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.name", "acceptance-effect-gate")
        git(repo, "config", "user.email", "gate@local")
        git(repo, "config", "core.autocrlf", "false")
        for path, content in round_out["files"].items():
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "replay: 提交快照回放")
        commit = git(repo, "rev-parse", "HEAD").stdout.strip()
        return gate.run(
            commit=commit,
            worktree=repo,
            changed=list(round_out["changed_paths"]) or [],
        )
    finally:
        remove_tree(root)


# -----------------------------------------------------------------------------
# 冻结 / 运行 / 汇总
# -----------------------------------------------------------------------------


RULE_SHA_PAYLOAD_VERSION = "effect-rules-1"


def template_spec() -> list[dict[str, Any]]:
    specs = []
    for case in CASES:
        specs.append(
            {
                "template": case["template"],
                "trap": case["trap"],
                "path": case["path"],
                "count": case.get("count"),
                "requirement": case["requirement"],
                "seed_files": [{"path": p, "sha256": sha(c)} for p, c in case["seeds"].items()],
                "prompt_sha256": {arm: sha(executor_prompt(case, arm)) for arm in ARMS},
                "dispatch_sha256": {arm: sha(dispatch_text(case, arm)) for arm in ARMS},
                "dispatch_byte_identical_except_flag": dispatch_text(case, "E").replace(
                    '"enabled": true', '"enabled": false'
                )
                == dispatch_text(case, "D"),
                "prompt_byte_identical_except_flag": executor_prompt(case, "E").replace(
                    '"enabled": true', '"enabled": false'
                )
                == executor_prompt(case, "D"),
            }
        )
    return specs


def templates_sha() -> str:
    return sha(json.dumps(template_spec(), ensure_ascii=False, sort_keys=True))


def scoring_sha() -> str:
    return sha(
        json.dumps(
            {
                "version": RULE_SHA_PAYLOAD_VERSION,
                "rules": rule_definitions(),
                "frozen_tokens": list(TOKENS),
                "content_min_length": CONTENT_MIN_LENGTH,
                "content_min_120": MIN_VISIBLE_CHARS_DISPATCH,
                "count_expected": {c["template"]: c.get("count") for c in CASES},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    identity = frozen_identity()
    for spec in template_spec():
        if not spec["dispatch_byte_identical_except_flag"]:
            raise RuntimeError(f"{spec['template']} 两臂派单不是逐字节仅差开关")
        if not spec["prompt_byte_identical_except_flag"]:
            raise RuntimeError(f"{spec['template']} 两臂提示词不是逐字节仅差开关")
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "initial_freeze": {
            "script_sha256": file_sha(Path(__file__).resolve()),
            "templates_sha256": templates_sha(),
            "scoring_sha256": scoring_sha(),
            "templates_definition": "3 个模板的定义、种子文件、两臂派单/提示词 SHA 与逐字节一致性断言",
            "scoring_definition": "规则清单 + 冻结禁词 + 两个长度阈值 + 数量期望值 的规范化 JSON",
        },
        "post_freeze_changes": [],
        "model": MODEL,
        "parameters": PARAMS,
        "channel": "real platform pool /pool/v1/chat/completions",
        "execution_contract": (
            "执行者返回一个 JSON 写盘计划；运行器在独立临时 git 仓库里真实写文件并提交；"
            "验收器与离线尺子都只读提交快照"
        ),
        "arms": {
            "D": "acceptance.enabled=false：单轮到底，没有任何退回机会",
            "E": "acceptance.enabled=true：最多一次退回（退回工单由 ExperienceAcceptanceGate.retry_goal 生成）",
        },
        "retry_policy": f"E 臂最多 {MAX_RETRIES} 次退回；D 臂 0 次",
        "scoring_rules": rule_definitions(),
        "frozen_identity": identity,
        "call_budget": {"preflight": 1, "executions": 6, "retries_max": 3, "max_total": MAX_CALLS},
        "cost_limit_cny_equivalent": COST_LIMIT,
        "templates": template_spec(),
        "preflight_prompt_sha256": sha('Return only {"ready":true}.'),
        "reference_hashes": {"production_experience": file_sha(SOURCE)},
        "order": [
            "BANNED-D", "BANNED-E",
            "PATH-E", "PATH-D",
            "COUNT-D", "COUNT-E",
        ],
    }
    write_json(PROTOCOL, protocol)
    print(
        json.dumps(
            {
                "frozen": str(PROTOCOL),
                "frozen_at": protocol["frozen_at"],
                "script_sha256": protocol["initial_freeze"]["script_sha256"],
                "templates_sha256": protocol["initial_freeze"]["templates_sha256"],
                "scoring_sha256": protocol["initial_freeze"]["scoring_sha256"],
                "frozen_identity": identity,
            },
            ensure_ascii=False,
        )
    )


def verify_frozen() -> dict[str, Any]:
    protocol = read_json(PROTOCOL, None)
    if not protocol:
        raise RuntimeError("先执行 freeze，再跑 run")
    allowed = [protocol["initial_freeze"]["script_sha256"]] + [
        entry["new_script_sha256"] for entry in protocol.get("post_freeze_changes", [])
    ]
    current = file_sha(Path(__file__).resolve())
    if current not in allowed:
        raise RuntimeError("脚本在冻结后被修改，且没有 post_freeze_changes 记录")
    if templates_sha() != protocol["initial_freeze"]["templates_sha256"]:
        raise RuntimeError("模板集在冻结后被修改；必须按 bug 流程全量重跑")
    if scoring_sha() != protocol["initial_freeze"]["scoring_sha256"]:
        raise RuntimeError("评分规则在冻结后被修改；必须按 bug 流程全量重跑")
    if frozen_identity() != protocol["frozen_identity"]:
        raise RuntimeError("v5 三件套冻结身份发生变化")
    return protocol


def _provider_cost(record: dict[str, Any]) -> float:
    value = record.get("provider_cost")
    if isinstance(value, (int, float)):
        return float(value)
    value = record.get("ledger_cost")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def run() -> None:
    protocol = verify_frozen()
    token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
    production_before = file_sha(SOURCE)

    preflight = call_pool('Return only {"ready":true}.', "PREFLIGHT", token, expect_files=False)
    if preflight["terminal_state"] != "ok":
        raise RuntimeError(f"真实模型预检失败：{preflight.get('error')}")
    if preflight.get("actual_model") != "MiniMax-M3.1-Flash":
        raise RuntimeError(f"实际模型不符：{preflight.get('actual_model')}")

    by_key = {(c["template"], arm): c for c in CASES for arm in ARMS}
    results: list[dict[str, Any]] = []
    cost = _provider_cost(preflight)
    for key in protocol["order"]:
        template, arm = key.rsplit("-", 1)
        case = by_key[(template, arm)]
        row = run_arm(case, arm, token)
        results.append(row)
        write_json(RESULTS, results)
        cost += _provider_cost({"provider_cost": None, **row["first"]})
        if row["retry"]:
            cost += _provider_cost({"provider_cost": None, **row["retry"]})
        if cost > COST_LIMIT:
            raise RuntimeError(f"费用上限触发：{cost} > {COST_LIMIT}")
        calls_used = len(list((OUT / "calls").glob("*.json")))
        if calls_used > MAX_CALLS:
            raise RuntimeError(f"调用数超过上限：{calls_used} > {MAX_CALLS}")

    write_json(
        ENVIRONMENT,
        {
            "production_experience_sha256_before": production_before,
            "production_experience_sha256_after": file_sha(SOURCE),
            "production_experience_unchanged": production_before == file_sha(SOURCE),
            "frozen_identity": frozen_identity(),
            "temporary_directories_removed": all(
                attempt["temporary_directory_removed"]
                for row in results
                for attempt in (row["first"], row["retry"])
                if attempt
            ),
            "calls_used": len(list((OUT / "calls").glob("*.json"))),
            "model": MODEL,
        },
    )
    print(json.dumps({"rows": len(results), "results": str(RESULTS)}, ensure_ascii=False))


def _wilson(success: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.96
    p = success / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    spread = z * ((p * (1 - p) / total + z * z / (4 * total * total)) ** 0.5) / denominator
    return [round(max(0.0, center - spread), 4), round(min(1.0, center + spread), 4)]


def finalize() -> None:
    protocol = verify_frozen()
    rows = read_json(RESULTS, [])
    if len(rows) != 6:
        raise RuntimeError(f"expected 6 rows, found {len(rows)}")

    table = []
    for row in rows:
        first, second = row["first"], row["retry"]
        final = row["final"]
        gate_rounds = (row.get("gate") or {}).get("rounds") or []
        table.append(
            {
                "template": row["template"],
                "arm": row["arm"],
                "trap": next(c["trap"] for c in CASES if c["template"] == row["template"]),
                "first_fail": first["score"]["fail"],
                "first_failed_rules": first["score"]["failed_rules"],
                "gate_first_verdict": gate_rounds[0]["verdict"] if gate_rounds else None,
                "retried": second is not None,
                "retry_terminal_state": (second or {}).get("terminal_state"),
                "final_fail": final["score"]["fail"],
                "final_failed_rules": final["score"]["failed_rules"],
                "gate_final_verdict": gate_rounds[-1]["verdict"] if gate_rounds else None,
                "fixed_rules": sorted(
                    set(first["score"]["failed_rules"]) - set(final["score"]["failed_rules"])
                )
                if second is not None
                else [],
                "still_failing_rules": final["score"]["failed_rules"]
                if second is not None
                else [],
                "committed_files": final["committed_files"],
                "terminal_state": final["terminal_state"],
                "prompt_sha256": final["prompt_sha256"],
                "score": final["score"],
                "tokens": {
                    "first": int(((first.get("usage") or {}).get("total_tokens") or 0)),
                    "retry": int((((second or {}).get("usage") or {}).get("total_tokens") or 0)),
                },
            }
        )

    def arm_rows(arm: str) -> list[dict[str, Any]]:
        return [row for row in table if row["arm"] == arm]

    counts = {
        "first_round_fail": {
            arm: sum(row["first_fail"] for row in arm_rows(arm)) for arm in ARMS
        },
        "final_fail": {arm: sum(row["final_fail"] for row in arm_rows(arm)) for arm in ARMS},
        "retried": {arm: sum(row["retried"] for row in arm_rows(arm)) for arm in ARMS},
    }
    fixed = [row for row in arm_rows("E") if row["retried"]]
    retry_stats = {
        "retried_templates": [row["template"] for row in fixed],
        "fixed_all_failed_rules": sum(not row["final_fail"] for row in fixed),
        "still_failing": sum(row["final_fail"] for row in fixed),
        "fixed_rules_total": sum(len(row["fixed_rules"]) for row in fixed),
        "still_failing_rules_total": sum(len(row["still_failing_rules"]) for row in fixed),
        "new_fail_introduced": sum(
            bool(set(row["still_failing_rules"]) - set(row["first_failed_rules"])) for row in fixed
        ),
    }
    gate_vs_ruler = {
        "gate_fail_rounds": sum(
            1
            for row in rows
            for record in ((row.get("gate") or {}).get("rounds") or [])
            if record["verdict"] != "pass"
        ),
        "note": "验收器 verdict 只决定 E 臂是否退回，不进成绩；这里只做一致性记录",
    }

    calls = sorted((OUT / "calls").glob("*.json"))
    receipts = [read_json(path, {}) for path in calls]
    by_arm_calls = Counter()
    for receipt in receipts:
        tag = str(receipt.get("tag") or "")
        for arm in ARMS:
            if f"-{arm}-" in tag:
                by_arm_calls[arm] += 1
    summary = {
        "dataset_version": DATASET_VERSION,
        "model": protocol["model"],
        "parameters": protocol["parameters"],
        "counts": counts,
        "retry_stats": retry_stats,
        "gate_vs_ruler": gate_vs_ruler,
        "wilson_95_first_round_fail": {
            arm: _wilson(counts["first_round_fail"][arm], 3) for arm in ARMS
        },
        "wilson_95_final_fail": {arm: _wilson(counts["final_fail"][arm], 3) for arm in ARMS},
        "rows": table,
        "cost": {
            "total_calls": len(receipts),
            "calls_by_arm": dict(by_arm_calls),
            "prompt_tokens": sum(int((item.get("usage") or {}).get("prompt_tokens") or 0) for item in receipts),
            "completion_tokens": sum(
                int((item.get("usage") or {}).get("completion_tokens") or 0) for item in receipts
            ),
            "total_tokens": sum(int((item.get("usage") or {}).get("total_tokens") or 0) for item in receipts),
            "total_tokens_by_arm": {
                arm: sum(
                    int((item.get("usage") or {}).get("total_tokens") or 0)
                    for item in receipts
                    if f"-{arm}-" in str(item.get("tag") or "")
                )
                for arm in ARMS
            },
            "provider_cost_sum": round(sum(_provider_cost(item) for item in receipts), 6),
            "provider_cost_missing_calls": sum(item.get("provider_cost") is None for item in receipts),
            "ledger_cost_sum": round(sum(float(item.get("ledger_cost") or 0.0) for item in receipts), 6),
            "finish_reasons": dict(Counter(str(item.get("finish_reason")) for item in receipts)),
        },
        "environment": read_json(ENVIRONMENT, {}),
        "protocol": {
            "frozen_at": protocol["frozen_at"],
            "script_sha256": protocol["initial_freeze"]["script_sha256"],
            "templates_sha256": protocol["initial_freeze"]["templates_sha256"],
            "scoring_sha256": protocol["initial_freeze"]["scoring_sha256"],
            "frozen_identity": protocol["frozen_identity"],
            "post_freeze_changes": protocol.get("post_freeze_changes", []),
            "order": protocol["order"],
        },
        "byte_identity": [
            {
                "template": spec["template"],
                "dispatch_byte_identical_except_flag": spec["dispatch_byte_identical_except_flag"],
                "prompt_byte_identical_except_flag": spec["prompt_byte_identical_except_flag"],
            }
            for spec in protocol["templates"]
        ],
    }
    write_json(SUMMARY, summary)
    write_json(OUT / "rows.json", table)
    print(json.dumps({key: summary[key] for key in ("counts", "retry_stats", "cost")}, ensure_ascii=False, indent=2))


# -----------------------------------------------------------------------------
# 无模型自检
# -----------------------------------------------------------------------------


def selftest() -> None:
    failures: list[str] = []
    good = {
        "BANNED": (
            "ops/round-2-pending.md",
            "# 第二轮待补项\n\n## 已修掉\n\n- 失败路径的验收说明已补齐，写清命令、返回码与判定条件。\n"
            "- 标题强调风格已统一为正文一致的层级结构，不再出现两种写法。\n\n"
            "## 仍需跟进\n\n- 证据链接尚未核实，下一轮逐条确认后补入。\n"
            "- 回执样例只覆盖成功路径，失败路径样例还需补。\n",
        ),
        "PATH": (
            "ops/handoff/round-2/notes.md",
            "# 第二轮交接说明\n\n## 完成项\n\n- 适配层注册流程已按当前接口核对，调用链与失败处理都已验证，"
            "注册版本随回执一起记录，便于以后查对。\n"
            "- 回执字段核对完成，幂等与重试的行为与文档一致，重复提交只登记一次。\n\n"
            "## 待跟进\n\n- 流式输出的断点续传仍未支持，下一轮给出方案并补验收说明。\n",
        ),
        "COUNT": (
            "ops/first-round-pending.md",
            "# 第一轮交接跟进\n\n## 待补项\n\n"
            "1. 补齐失败路径的验收说明，写清返回码与判定条件，避免只写成功路径。\n"
            "2. 统一标题强调风格，与正文其余部分保持一致，不新增独立的样式体系。\n"
            "3. 补充证据链接，逐条给出可核查的章节位置，泛称不得替代实际文件。\n\n"
            "本轮只保留上述三条，其余两条已合并处理，不再单列。\n",
        ),
    }
    bad = {
        "BANNED": (
            "ops/round-2-pending.md",
            "# 第二轮待补项\n\n旧版用 TODO 标记未完成项，待补充的项已经统一改写；"
            "占位与示例文本两个旧标记本轮不再使用。其余说明按当前口径补齐，"
            "已经修掉失败路径的验收说明并核对了回执字段。\n",
        ),
        "PATH": (
            "ops/handoff/round-2-notes.md",
            "# 第二轮交接说明\n\n本轮完成了适配层注册流程与回执字段核对，调用链与失败处理都已验证。\n"
            "遗留待跟进项是流式输出的断点续传，下一轮给出方案。\n",
        ),
        "COUNT": (
            "ops/first-round-pending.md",
            "# 第一轮遗留待补项\n\n## 待补项\n\n"
            "1. 补齐失败路径的验收说明。\n2. 统一标题强调风格。\n3. 补充证据链接。\n"
            "4. 给出回执样例。\n5. 标注未确认项的限制。\n\n本轮沿用上一轮的五条记录。\n",
        ),
        "BANNED_SKELETON": (
            "ops/round-2-pending.md",
            "# 第二轮待补项\n\n旧版用 T、O、D、O 标记未完成项，待、补、充 表示同类状态；"
            "占、位 与 示、例、文、本 两个旧标记本轮不再使用。其余说明按当前口径补齐，"
            "已经修掉失败路径的验收说明并核对了回执字段，逐条给出可核查的位置。\n",
        ),
    }

    for case in CASES:
        template = case["template"]
        files = {**case["seeds"]}
        path, text = good[template]
        files[path] = text
        score = score_delivery(case, files)
        if score["fail"]:
            failures.append(f"{template} 合格样例被判不合格：{score['failed_rules']}")

        bad_path, bad_text = bad[template]
        bad_files = {**case["seeds"], bad_path: bad_text}
        if bad_path != case["path"]:
            bad_files.pop(case["path"], None)
        score = score_delivery(case, bad_files)
        if not score["fail"]:
            failures.append(f"{template} 不合格样例被判合格")

    # 禁词骨架层单独验一次（可见文本无连续旧串，骨架命中）
    case = next(item for item in CASES if item["template"] == "BANNED")
    path, text = bad["BANNED_SKELETON"]
    skel = score_delivery(case, {**case["seeds"], path: text})
    if not skel["fail"] or "banned_hits_absent" not in skel["failed_rules"]:
        failures.append("骨架拆字样例没被判不合格")

    if failures:
        raise RuntimeError("自检失败：\n" + "\n".join(failures))
    print(json.dumps({"selftest": "ok", "templates": len(CASES)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["selftest", "freeze", "run", "finalize"])
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
    elif args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    else:
        finalize()


if __name__ == "__main__":
    main()
