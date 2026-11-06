"""v1 交付后验收实验：不注入经验，执行者真建文件，验收只看仓库真实状态。

流程：
1. 每个任务在仓库外的独立临时 git 仓库里初始化并提交种子文件；
2. 执行者（真实池模型）拿到派单，输出严格 JSON 的文件写入计划；
3. 运行器把文件真实写入仓库并提交，然后只对仓库实际状态做机械验收；
4. 不合格则把具体缺项退回一次，重跑验收；
5. 判分脚本与每任务期望值在第一次模型调用前冻结进 protocol.json。

本轮不读取、不注入任何经验。派单 JSON 只作为输入，不作为命中依据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
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
OUT = ROOT / "artifacts" / "experience-acceptance-ab" / "v1"
PROTOCOL = OUT / "protocol.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
ENVIRONMENT = OUT / "environment.json"
SOURCE = ROOT / "data" / "admin" / "experience.json"
V4_FROZEN = ROOT / "artifacts" / "experience-transfer-ab-real-model" / "v4" / "frozen.json"
V6_FROZEN = ROOT / "artifacts" / "experience-transfer-ab-real-model" / "v6-conditional" / "frozen.json"
HISTORY = ("v1", "v2", "v3", "v4", "v5-review", "v6-conditional")

MODEL = "MiniMax-M3.1-Flash-Preview"
PARAMS = {"temperature": 0, "max_tokens": 8192, "stream": False}
POOL = "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions"
POOL_STATUS = "http://127.0.0.1:8848/api/v1/pool/status"
COST_LIMIT = 10.0
DATASET_VERSION = "experience-acceptance-ab-v1.0.0"
MAX_FILES = 8
MAX_CHARS = 40_000
CALL_TIMEOUT = 240.0
MAX_CALLS = 13  # 1 preflight + 6 executions + 6 retries

README_SEED = """# 交付仓库

本仓库用于核对执行者是否按派单真实创建或修改文件。仓库内容以派单为准。
"""

ADAPTER_NOTES_SEED = """# 适配层实现说明

## 1. 注册与调用
适配层通过 register_adapter 注册；调用时按 harness_id 选择具体实现。

## 2. 失败处理
CLI 超时或非零退出时，适配层返回 fail-closed 错误，不返回降级结果。

## 3. 已知限制
流式输出暂不支持断点续传。
"""

RECEIPT_NOTES_SEED = """# 平台回执说明

## 1. 回执结构
回执包含 request_id、status、usage 与 finish_reason 字段。

## 2. 幂等与重试
同一 request_id 的回执只登记一次，重复提交按幂等处理。

## 3. 已知限制
回执不包含供应商计费单价。
"""


def _m(check_id: str, text: str, expected: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "kind": "mechanical", "text": text, "expected": expected}


CASES: list[dict[str, Any]] = [
    {
        "case_id": "TR-01",
        "requirement": (
            "做第二轮复盘。最新验收约束：创建 ops/review-round-2.md；"
            "意见映射表的列名依次为意见编号、审查意见、本轮处置、状态、证据链接。"
        ),
        "path": "ops/review-round-2.md",
        "old_values": [
            "docs/review-loop-cb/round-2.md",
            "四列",
            "第二轮处置",
            "docs/review-loop-cb",
            "round-1.md",
        ],
        "seeds": {"README.md": README_SEED},
        "experiences": ["exp-b0ff85de2be10ae8b8a576ae"],
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/review-round-2.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/review-round-2.md", "min_chars": 40}),
            _m(
                "table_header_exact",
                "意见映射表表头恰好为要求的五列且顺序一致",
                {"path": "ops/review-round-2.md", "columns": ["意见编号", "审查意见", "本轮处置", "状态", "证据链接"]},
            ),
            _m("table_has_rows", "映射表至少有一条数据行", {"path": "ops/review-round-2.md", "min_rows": 1}),
            _m("old_values_absent", "交付正文不得出现旧值", {"path": "ops/review-round-2.md", "tokens": []}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/review-round-2.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
        "semantic_unverified": [
            {"text": "意见逐条引用且不得改写或合并", "source": "exp-b0ff85de2be10ae8b8a576ae"},
            {"text": "待补项必须具体可核验", "source": "exp-b0ff85de2be10ae8b8a576ae"},
            {"text": "交付风格保持一致", "source": "exp-b0ff85de2be10ae8b8a576ae"},
        ],
    },
    {
        "case_id": "TR-02",
        "requirement": "写验收说明。最新验收约束：正文不得出现“草稿”“待定”“稍后补充”这三个词。",
        "path": "ops/acceptance-wording.md",
        "old_values": ["TODO", "待补充", "占位", "示例文本", "portfolio/lanes/codebuddy/retro.md", "retro.md"],
        "seeds": {"README.md": README_SEED},
        "experiences": ["exp-96ed71006d509669a9a7ef02", "exp-45a29c4a3c9e78ea5b2fb3ff"],
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/acceptance-wording.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/acceptance-wording.md", "min_chars": 40}),
            _m(
                "banned_new_absent",
                "交付正文不得复现当前三个禁词",
                {"path": "ops/acceptance-wording.md", "tokens": ["草稿", "待定", "稍后补充"]},
            ),
            _m("old_values_absent", "交付正文不得带入历史禁词或旧路径", {"path": "ops/acceptance-wording.md", "tokens": []}),
            _m("topic_token_present", "正文确实在写验收说明", {"path": "ops/acceptance-wording.md", "tokens": ["验收"]}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/acceptance-wording.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
        "semantic_unverified": [
            {"text": "验收写明逐条/逐项检查新禁词", "source": "exp-96ed71006d509669a9a7ef02"},
            {"text": "复盘只陈述已由证据确认的事实，不预述未来", "source": "exp-96ed71006d509669a9a7ef02"},
            {"text": "全文逐项自检是否实际执行", "source": "exp-45a29c4a3c9e78ea5b2fb3ff"},
        ],
    },
    {
        "case_id": "TR-03",
        "requirement": "整理第一轮交付说明。最新验收约束：写入 ops/first-round.md，并且恰好两条待补项。",
        "path": "ops/first-round.md",
        "old_values": [
            "docs/review-loop/round-1.md",
            "至少 3 条",
            "至少3条",
            "至少三条",
            "docs/review-loop",
            "round-2.md",
        ],
        "seeds": {"README.md": README_SEED},
        "experiences": ["exp-6af29904b03befd4766ef4b6"],
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/first-round.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/first-round.md", "min_chars": 40}),
            _m("pending_items_exactly_two", "待补项真实条目恰好两条", {"path": "ops/first-round.md", "count": 2}),
            _m("old_values_absent", "交付正文不得出现旧路径或旧数量", {"path": "ops/first-round.md", "tokens": []}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/first-round.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
        "semantic_unverified": [
            {"text": "待补项具体可核验", "source": "exp-6af29904b03befd4766ef4b6"},
            {"text": "diff 完整可读、文档结构闭合", "source": "exp-6af29904b03befd4766ef4b6"},
        ],
    },
    {
        "case_id": "TR-04",
        "requirement": "生成 ops/review-checklist.md。最新验收约束：只保留 F1、F2 检查。",
        "path": "ops/review-checklist.md",
        "old_values": ["round-1", "F3", "F4", "docs/review-loop-cb"],
        "seeds": {"README.md": README_SEED},
        "experiences": ["exp-544730dd2d1dcef17bc8d994"],
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/review-checklist.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/review-checklist.md", "min_chars": 40}),
            _m(
                "finding_labels_exact",
                "检查项标签集合恰好为 F1、F2",
                {"path": "ops/review-checklist.md", "present": [1, 2], "absent": [3, 4]},
            ),
            _m("old_values_absent", "交付正文不得出现旧编号或旧路径", {"path": "ops/review-checklist.md", "tokens": []}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/review-checklist.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
        "semantic_unverified": [
            {"text": "按当前派单列出的检查集合逐项复核", "source": "exp-544730dd2d1dcef17bc8d994"},
            {"text": "已核结论只陈述实际核对结果，最终结论由人类验收", "source": "exp-544730dd2d1dcef17bc8d994"},
        ],
    },
    {
        "case_id": "TR-05",
        "requirement": "提交 qoder 的交付说明。最新验收约束：只创建 ops/qoder-lane.md，不得创建其他文件。",
        "path": "ops/qoder-lane.md",
        "old_values": ["docs/formal-multi-harness/qoder-lane.md", "docs/formal-multi-harness"],
        "seeds": {"README.md": README_SEED},
        "experiences": ["exp-1d86f4d6f343d82dac6bcb91"],
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/qoder-lane.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/qoder-lane.md", "min_chars": 40}),
            _m("topic_token_present", "正文确实是 qoder 的交付说明", {"path": "ops/qoder-lane.md", "tokens": ["qoder"]}),
            _m("old_values_absent", "交付正文不得出现旧路径", {"path": "ops/qoder-lane.md", "tokens": []}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/qoder-lane.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
        "semantic_unverified": [
            {"text": "交付范围陈述与实际变更一致", "source": "exp-1d86f4d6f343d82dac6bcb91"},
            {"text": "文档结论与证据一致", "source": "exp-1d86f4d6f343d82dac6bcb91"},
        ],
    },
    {
        "case_id": "TR-06",
        "requirement": "整理证据清单。最新验收约束：只列两个来源文件 ops/adapter-notes.md 与 ops/receipt-notes.md。",
        "path": "ops/evidence-sources.md",
        "old_values": [
            "相关 CLI 实现文档",
            "平台回执实现文档",
            "docs/prep/自循环验收-1020/source-inventory.md",
            "docs/spec/0.3-R10.md",
            "docs/dev-convention.md",
            "CLAUDE.md",
        ],
        "seeds": {
            "README.md": README_SEED,
            "ops/adapter-notes.md": ADAPTER_NOTES_SEED,
            "ops/receipt-notes.md": RECEIPT_NOTES_SEED,
        },
        "experiences": ["exp-e73c4967bdd3f5a4d0ca50c4", "exp-7f5c6ee829094f6e525fcc2a"],
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/evidence-sources.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/evidence-sources.md", "min_chars": 40}),
            _m(
                "evidence_paths_exact",
                "证据清单只含派单点名的两个文件",
                {"path": "ops/evidence-sources.md", "paths": ["ops/adapter-notes.md", "ops/receipt-notes.md"]},
            ),
            _m(
                "seed_sources_exist",
                "被点名的来源文件在仓库中真实存在",
                {"paths": ["ops/adapter-notes.md", "ops/receipt-notes.md"]},
            ),
            _m("old_values_absent", "交付正文不得出现旧泛指名称或旧路径", {"path": "ops/evidence-sources.md", "tokens": []}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/evidence-sources.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
        "semantic_unverified": [
            {"text": "每个来源逐项给出可核查章节或段落、能支持的结论及限制", "source": "exp-e73c4967bdd3f5a4d0ca50c4"},
            {"text": "未确认项明确标注，泛指名称不得替代实际核实", "source": "exp-7f5c6ee829094f6e525fcc2a"},
        ],
    },
]


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


def dispatch_for(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": f"{case['case_id']}-ACCEPT",
        "title": f"{case['case_id']} · 交付验收场景",
        "goal": case["requirement"],
        "exclusive_paths": [case["path"]],
        "acceptance": "交付物存在且满足最新验收约束",
        "harness_id": "qoder-member-1",
    }


def case_spec(case: dict[str, Any]) -> dict[str, Any]:
    expected_checks = []
    for check in case["checks"]:
        expected = dict(check["expected"])
        if check["id"] in {"old_values_absent"}:
            expected["tokens"] = list(case["old_values"])
        expected_checks.append({"id": check["id"], "kind": check["kind"], "text": check["text"], "expected": expected})
    return {
        "case_id": case["case_id"],
        "requirement": case["requirement"],
        "dispatch": dispatch_for(case),
        "path": case["path"],
        "old_values": list(case["old_values"]),
        "seed_files": [{"path": p, "sha256": sha(c)} for p, c in case["seeds"].items()],
        "checks": expected_checks,
        "semantic_unverified": list(case["semantic_unverified"]),
        "experiences": list(case["experiences"]),
    }


def spec_sha() -> str:
    return sha(json.dumps([case_spec(case) for case in CASES], ensure_ascii=False, sort_keys=True))


# -----------------------------------------------------------------------------
# 提示词与模型调用
# -----------------------------------------------------------------------------


def executor_prompt(case: dict[str, Any]) -> str:
    dispatch = json.dumps(dispatch_for(case), ensure_ascii=False, indent=2)
    seeds = "\n\n".join(
        f"--- 文件 {path} ---\n{content}" for path, content in case["seeds"].items()
    )
    return (
        "你是本仓库的执行者。请按下面的派单完成交付，在仓库中真实创建或修改文件。\n\n"
        f"派单（JSON）：\n{dispatch}\n\n"
        "仓库现有文件（完整内容）：\n"
        f"{seeds}\n\n"
        "写盘规则：只能写相对路径；不得写 .git 下的路径、绝对路径或包含 .. 的路径。\n"
        "单文件内容不超过 1200 个汉字，最多写 8 个文件。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明"}\n'
        "content 里保留换行，JSON 中用 \\n 转义；修改已有文件时给出该文件的完整新内容。\n"
    )


def retry_prompt(case: dict[str, Any], gaps: list[str], files: dict[str, str]) -> str:
    file_json = json.dumps(files, ensure_ascii=False, indent=2)
    gap_text = "\n".join(f"{index}. {gap}" for index, gap in enumerate(gaps, start=1))
    return (
        "你是本仓库的执行者。上一次交付未通过验收，具体缺项如下：\n"
        f"{gap_text}\n\n"
        "当前仓库已提交文件的完整内容（JSON，path -> content）：\n"
        f"{file_json}\n\n"
        "原始派单（JSON）：\n"
        f"{json.dumps(dispatch_for(case), ensure_ascii=False, indent=2)}\n\n"
        "请修正上述缺项并重新输出完整交付。写盘规则同上：只写相对路径，不写 .git、绝对路径或 ..。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明"}\n'
    )


def ledger(token: str) -> dict[str, Any]:
    try:
        response = httpx.get(POOL_STATUS, headers={"Authorization": f"Bearer {token}"}, timeout=30.0)
        response.raise_for_status()
        upstreams = (response.json().get("usage") or {}).get("upstreams") or {}
        return dict(upstreams.get("minimax") or {})
    except Exception:
        return {}


def extract_json(content: str) -> Any:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


def call_pool(
    prompt: str, case_id: str, attempt: int, token: str, expect: str = "files"
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": case_id,
        "attempt": attempt,
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
                payload = extract_json(content)
            except ValueError as exc:
                record["terminal_state"] = "invalid_json"
                record["error"] = str(exc)
            else:
                problem = validate_payload(payload) if expect == "files" else None
                record["parsed_payload"] = payload
                if problem:
                    record["terminal_state"] = "invalid_schema"
                    record["error"] = problem
                else:
                    record["terminal_state"] = "ok"
    except httpx.TimeoutException as exc:
        record["terminal_state"] = "timeout"
        record["error"] = f"Timeout: {exc}"
    except Exception as exc:  # noqa: BLE001 - receipt keeps the error verbatim
        record["terminal_state"] = "network_error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    write_json(OUT / "calls" / f"{case_id}-attempt{attempt}.json", record)
    return record


def validate_payload(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "JSON 顶层不是对象"
    files = payload.get("files")
    if not isinstance(files, list):
        return "缺少 files 数组"
    if len(files) > MAX_FILES:
        return f"文件数 {len(files)} 超过 {MAX_FILES}"
    for item in files:
        if not isinstance(item, dict):
            return "files 元素不是对象"
        if not isinstance(item.get("path"), str) or not item["path"].strip():
            return "files 元素缺少 path"
        if not isinstance(item.get("content"), str):
            return "files 元素缺少 content 字符串"
        if len(item["content"]) > MAX_CHARS:
            return f"{item['path']} 内容超过 {MAX_CHARS} 字符"
    return None


def safe_relpath(path: str) -> str | None:
    raw = path.strip().replace("\\", "/")
    pure = Path(raw)
    if not raw or pure.is_absolute() or raw.startswith("/"):
        return None
    parts = [part for part in raw.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    if parts[0] == ".git":
        return None
    return "/".join(parts)


# -----------------------------------------------------------------------------
# 临时仓库
# -----------------------------------------------------------------------------


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and process.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {process.stderr.strip()}")
    return process


def init_repo(case: dict[str, Any], root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "acceptance-executor")
    git(repo, "config", "user.email", "executor@local")
    git(repo, "config", "core.autocrlf", "false")
    for path, content in case["seeds"].items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    commit = git(repo, "commit", "-m", "seed: 初始化仓库")
    base_sha = git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, base_sha


def snapshot(repo: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in sorted(repo.rglob("*")):
        if not item.is_file():
            continue
        if ".git" in item.parts:
            continue
        relative = item.relative_to(repo).as_posix()
        try:
            files[relative] = item.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            files[relative] = "<binary>"
    return files


def apply_delivery(repo: Path, payload: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """返回 (commit_sha, 已写入路径, 被拒绝路径)。"""
    written: list[str] = []
    rejected: list[str] = []
    for item in payload.get("files") or []:
        raw = str(item.get("path") or "")
        relative = safe_relpath(raw)
        if relative is None:
            rejected.append(raw)
            continue
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(item.get("content") or ""), encoding="utf-8", newline="\n")
        written.append(relative)
    if not written:
        return "", written, rejected
    git(repo, "add", "-A")
    staged = git(repo, "diff", "--cached", "--name-only", check=False).stdout.strip()
    if not staged:
        return git(repo, "rev-parse", "HEAD").stdout.strip(), written, rejected
    message = "delivery: 按派单提交"
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD").stdout.strip(), written, rejected


def diff_paths(repo: Path, base_sha: str, head_sha: str) -> list[dict[str, str]]:
    if not base_sha or not head_sha or base_sha == head_sha:
        return []
    output = git(repo, "diff", "--name-status", base_sha, head_sha).stdout
    rows: list[dict[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append({"status": parts[0].strip(), "path": parts[-1].strip()})
    return rows


def store_repo_artifacts(case_id: str, attempt: int, repo: Path, base_sha: str) -> None:
    target = OUT / "repos" / case_id / f"attempt-{attempt}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "files.json").write_text(
        json.dumps(snapshot(repo), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    head = git(repo, "rev-parse", "HEAD", check=False).stdout.strip()
    patch = git(repo, "diff", base_sha, head).stdout if head and head != base_sha else ""
    (target / "diff.patch").write_text(patch, encoding="utf-8")
    stat = git(repo, "diff", "--stat", base_sha, head).stdout if head and head != base_sha else ""
    (target / "diff-stat.txt").write_text(stat, encoding="utf-8")
    (target / "status.txt").write_text(git(repo, "status", "--porcelain").stdout, encoding="utf-8")


# -----------------------------------------------------------------------------
# 机械验收
# -----------------------------------------------------------------------------


ASCII_TOKEN_RE = re.compile(r"^[\x20-\x7e]+$")


def token_hit(text: str, token: str) -> bool:
    if not token:
        return False
    if ASCII_TOKEN_RE.match(token):
        parts = r"\s*".join(re.escape(part) for part in token.split())
        pattern = r"(?<![A-Za-z0-9_-])" + parts + r"(?![A-Za-z0-9_-])"
        return re.search(pattern, text, re.I) is not None
    compact = re.sub(r"\s+", "", text)
    return re.sub(r"\s+", "", token) in compact


def hits(text: str, tokens: list[str]) -> list[str]:
    return [token for token in tokens if token_hit(text, token)]


def markdown_tables(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    tables: list[dict[str, Any]] = []
    index = 0
    while index < len(lines) - 1:
        line = lines[index].strip()
        separator = lines[index + 1].strip()
        if line.startswith("|") and re.fullmatch(r"\|[\s:|-]+\|", separator):
            header = [cell.strip().strip("*` ") for cell in line.strip().strip("|").split("|")]
            rows: list[list[str]] = []
            cursor = index + 2
            while cursor < len(lines) and lines[cursor].strip().startswith("|"):
                raw = lines[cursor].strip().strip("|")
                rows.append([cell.strip() for cell in raw.split("|")])
                cursor += 1
            tables.append({"header": header, "rows": rows})
            index = cursor
            continue
        index += 1
    return tables


def pending_items(text: str) -> tuple[int, list[str], str]:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith("#") and "待补项" in line:
            start = index + 1
            break
    if start is None:
        return 0, [], "没有「待补项」小节"
    section: list[str] = []
    for line in lines[start:]:
        if line.strip().startswith("#"):
            break
        section.append(line)
    list_items = [
        line.strip()
        for line in section
        if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)
    ]
    if list_items:
        return len(list_items), list_items, "列表"
    table_rows = [
        line.strip()
        for line in section
        if line.strip().startswith("|")
    ]
    if len(table_rows) >= 3:
        return len(table_rows) - 2, table_rows[2:], "表格"
    if len(table_rows) == 2:
        return 0, [], "表格只有表头"
    return 0, [], "小节内没有条目"


EVIDENCE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md(?![A-Za-z0-9_.-])")


def evidence_paths(text: str, own_path: str) -> list[str]:
    found = []
    for match in EVIDENCE_PATH_RE.finditer(text):
        value = match.group(0)
        if value == own_path:
            continue
        if value not in found:
            found.append(value)
    return found


def run_checks(
    case: dict[str, Any],
    repo: Path,
    base_sha: str,
    head_sha: str,
    files: dict[str, str],
    rejected: list[str],
) -> list[dict[str, Any]]:
    checks = case_spec(case)["checks"]
    results: list[dict[str, Any]] = []

    def add(check: dict[str, Any], passed: bool, observed: Any, evidence: str, gap: str | None = None):
        results.append(
            {
                "check_id": check["id"],
                "kind": check["kind"],
                "text": check["text"],
                "expected": check["expected"],
                "observed": observed,
                "pass": passed,
                "evidence": evidence,
                "gap": gap,
            }
        )

    table_cache: dict[str, list[dict[str, Any]]] = {}

    for check in checks:
        check_id = check["id"]
        expected = check["expected"]
        path = expected.get("path") or case["path"]
        text = files.get(path) if path in files else None

        if check_id == "delivery_path_exists":
            passed = text is not None and bool(text.strip())
            add(check, passed, f"{path} {'存在' if passed else '不存在'}", f"{len(text or '')} 字符")
            continue

        if check_id == "content_min_length":
            minimum = int(expected["min_chars"])
            actual = len(re.sub(r"\s+", "", text or ""))
            add(check, actual >= minimum, f"{actual} 个非空白字符", f"{path}; 要求 >= {minimum}")
            continue

        if check_id == "table_header_exact":
            tables = markdown_tables(text or "")
            tables = [t for t in tables if "意见编号" in t["header"]] or tables
            header = tables[0]["header"] if tables else []
            tables_cache = tables
            table_cache[path] = tables_cache
            passed = header == expected["columns"]
            add(check, passed, header, f"{path} 的表头单元格", None if passed else f"{path} 的表头为 {header}，要求依次为 {expected['columns']}")
            continue

        if check_id == "table_has_rows":
            tables = table_cache.get(path) or markdown_tables(text or "")
            rows = tables[0]["rows"] if tables else []
            passed = len(rows) >= int(expected["min_rows"])
            add(check, passed, f"{len(rows)} 条数据行", f"{path} 映射表", None if passed else f"{path} 的映射表没有数据行")
            continue

        if check_id == "banned_new_absent":
            found = hits(text or "", expected["tokens"])
            add(check, not found, found, f"{path} 实际 grep 新禁词", None if not found else "交付正文命中新禁词：" + "、".join(found))
            continue

        if check_id == "old_values_absent":
            found = hits(text or "", expected["tokens"] or [])
            add(check, not found, found, f"{path} 实际 grep 旧值", None if not found else "交付正文命中旧值：" + "、".join(found))
            continue

        if check_id == "pending_items_exactly_two":
            count, items, how = pending_items(text or "")
            passed = count == int(expected["count"])
            add(check, passed, {"count": count, "items": items, "how": how}, f"{path} 待补项小节", None if passed else f"{path} 的待补项为 {count} 条，要求 {expected['count']} 条")
            continue

        if check_id == "finding_labels_exact":
            labels = sorted({int(value) for value in re.findall(r"(?<![A-Za-z0-9_-])F(\d+)(?![0-9])", text or "")})
            passed = labels == expected["present"] and not any(value in labels for value in expected["absent"])
            add(check, passed, labels, f"{path} 中的 F 编号集合", None if passed else f"{path} 的检查项标签为 {labels}，要求恰好 F1、F2，且不得出现 F3/F4")
            continue

        if check_id == "evidence_paths_exact":
            found = evidence_paths(text or "", path)
            passed = sorted(found) == sorted(expected["paths"])
            add(check, passed, found, f"{path} 正文中出现的 .md 路径", None if passed else f"{path} 的证据清单为 {found}，要求只含 {expected['paths']}")
            continue

        if check_id == "seed_sources_exist":
            missing = [item for item in expected["paths"] if item not in files]
            add(check, not missing, missing, "仓库实际文件系统", None if not missing else "仓库中缺少来源文件：" + "、".join(missing))
            continue

        if check_id == "topic_token_present":
            missing = [token for token in expected["tokens"] if not token_hit(text or "", token)]
            add(check, not missing, missing, f"{path} 实际 grep", None if not missing else "正文缺少关键词：" + "、".join(missing))
            continue

        if check_id == "diff_only_allowed":
            changes = diff_paths(repo, base_sha, head_sha)
            changed = sorted({row["path"] for row in changes})
            allowed = sorted(expected["paths"])
            passed = changed == allowed
            add(check, passed, changes, f"git diff --name-status {base_sha[:8]}..{head_sha[:8]}", None if passed else f"git diff 变更路径为 {changed}，要求仅 {allowed}")
            continue

        if check_id == "worktree_clean":
            porcelain = git(repo, "status", "--porcelain").stdout
            passed = porcelain.strip() == ""
            add(check, passed, porcelain.strip(), "git status --porcelain", None if passed else f"提交后工作区仍有改动：{porcelain.strip()[:200]}")
            continue

        raise RuntimeError(f"unknown check {check_id}")

    if rejected:
        results.append(
            {
                "check_id": "safe_paths_only",
                "kind": "mechanical",
                "text": "写入路径必须是仓库内相对路径",
                "expected": {"rejected": []},
                "observed": rejected,
                "pass": False,
                "evidence": "运行器拒绝的路径",
                "gap": "以下写入路径不被接受：" + "、".join(rejected),
            }
        )
    return results


# -----------------------------------------------------------------------------
# 单任务执行
# -----------------------------------------------------------------------------


def execute_attempt(
    case: dict[str, Any],
    attempt: int,
    token: str,
    feedback: tuple[list[str], dict[str, str]] | None,
) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix=f"experience-acceptance-{case['case_id']}-a{attempt}-"))
    outcome: dict[str, Any] = {}
    try:
        repo, base_sha = init_repo(case, root)
        prompt = executor_prompt(case) if feedback is None else retry_prompt(case, feedback[0], feedback[1])
        call = call_pool(prompt, case["case_id"], attempt, token)
        written: list[str] = []
        rejected: list[str] = []
        if call["terminal_state"] == "ok":
            head_sha, written, rejected = apply_delivery(repo, call["parsed_payload"])
        else:
            head_sha = base_sha
        files = snapshot(repo)
        store_repo_artifacts(case["case_id"], attempt, repo, base_sha)
        checks = run_checks(case, repo, base_sha, head_sha, files, rejected)
        failed = [check for check in checks if check["kind"] == "mechanical" and not check["pass"]]
        gap_list = [check["gap"] or check["text"] for check in failed]
        if call["terminal_state"] != "ok":
            gap_list.insert(0, f"执行终态 {call['terminal_state']}：{call.get('error')}")
        changes = diff_paths(repo, base_sha, head_sha)
        outcome = {
            "case_id": case["case_id"],
            "attempt": attempt,
            "prompt_sha256": call["prompt_sha256"],
            "terminal_state": call["terminal_state"],
            "error": call.get("error"),
            "usage": call.get("usage"),
            "finish_reason": call.get("finish_reason"),
            "committed_files": written,
            "rejected_paths": rejected,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff": changes,
            "diff_stat": git(repo, "diff", "--stat", base_sha, head_sha).stdout.strip() if head_sha != base_sha else "",
            "files": files,
            "checks": checks,
            "failed_checks": [check["check_id"] for check in failed],
            "failed_gaps": gap_list,
            "mechanical_pass": not failed,
            "temporary_directory_removed": False,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)
        outcome["temporary_directory_removed"] = not root.exists()
    return outcome


def new_problems(first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    first_pass = {check["check_id"] for check in first["checks"] if check["pass"]}
    second_fail = {check["check_id"] for check in second["checks"] if not check["pass"]}
    return sorted(first_pass & second_fail)


# -----------------------------------------------------------------------------
# 冻结 / 运行 / 汇总
# -----------------------------------------------------------------------------


def derived_mapping() -> dict[str, Any]:
    frozen = read_json(V4_FROZEN, {})
    records = {row["experience_id"]: row for row in frozen.get("derived_constraints", [])}
    used: dict[str, dict[str, Any]] = {}
    for case in CASES:
        for experience_id in case["experiences"]:
            record = records.get(experience_id)
            if not record:
                raise RuntimeError(f"missing derived constraint {experience_id}")
            used[experience_id] = {
                "experience_id": experience_id,
                "template_id": record["template_id"],
                "derived_constraint": record["derived_constraint"],
                "case_id": case["case_id"],
                "mechanical_check_ids": [check["id"] for check in case["checks"]],
                "semantic_unverified": [item["text"] for item in case["semantic_unverified"]],
            }
    unused = [
        {
            "experience_id": record["experience_id"],
            "template_id": record["template_id"],
            "reason": "本轮 6 个任务未映射到该条派生约束",
        }
        for experience_id, record in records.items()
        if experience_id not in used
    ]
    if len(records) != 15:
        raise RuntimeError(f"expected 15 derived constraints, got {len(records)}")
    return {"used": list(used.values()), "unused": unused}


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = []
    for case in CASES:
        spec = case_spec(case)
        spec["prompt_sha256"] = sha(executor_prompt(case))
        cases.append(spec)
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "initial_freeze": {
            "script_sha256": file_sha(Path(__file__).resolve()),
            "check_spec_sha256": spec_sha(),
            "check_spec_definition": "sha256 of canonical JSON of per-case dispatch, expected checks, old values and semantic list",
        },
        "post_freeze_changes": [],
        "model": MODEL,
        "parameters": PARAMS,
        "channel": "real platform pool /pool/v1/chat/completions",
        "execution_contract": (
            "executor returns one JSON object with files[path, content]; the runner writes real files and commits; "
            "acceptance reads only repository state"
        ),
        "call_budget": {"preflight": 1, "executions": 6, "retries_max": 6, "max_total": MAX_CALLS},
        "retry_policy": "only on mechanical failure; at most one retry per task; full re-acceptance after retry",
        "cost_limit_cny_equivalent": COST_LIMIT,
        "acceptance_rule": (
            "mechanical checks decide pass/fail; semantic checks are listed as semantic_unverified and counted neither way"
        ),
        "cases": cases,
        "derived_constraint_mapping": derived_mapping(),
        "preflight_prompt_sha256": sha('Return only {"ready":true}.'),
        "reference_hashes": {
            "production_experience": file_sha(SOURCE),
            "v4_derived_constraints": file_sha(V4_FROZEN),
            "v6_cases": file_sha(V6_FROZEN),
            "history": {
                name: tree_sha(ROOT / "artifacts" / "experience-transfer-ab-real-model" / name)
                for name in HISTORY
            },
        },
    }
    write_json(PROTOCOL, protocol)
    print(
        json.dumps(
            {
                "frozen": str(PROTOCOL),
                "frozen_at": protocol["frozen_at"],
                "script_sha256": protocol["initial_freeze"]["script_sha256"],
                "check_spec_sha256": protocol["initial_freeze"]["check_spec_sha256"],
                "cases": len(cases),
                "max_calls": MAX_CALLS,
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
        raise RuntimeError("判分脚本在冻结后被修改，且没有 post_freeze_changes 记录；必须按 bug 流程重新全量跑")
    if spec_sha() != protocol["initial_freeze"]["check_spec_sha256"]:
        raise RuntimeError("每任务期望值在冻结后被修改；必须按 bug 流程重新全量跑")
    return protocol


def archive_attempts(reason: str) -> None:
    protocol = read_json(PROTOCOL, {})
    archives = OUT / "attempts"
    archives.mkdir(parents=True, exist_ok=True)
    index = len(list(archives.glob("attempt-*"))) + 1
    target = archives / f"attempt-{index}"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("results.json", "summary.json", "environment.json", "calls", "repos"):
        source = OUT / name
        if source.exists():
            shutil.move(str(source), str(target / name))
    (target / "reason.txt").write_text(reason, encoding="utf-8")


def refreeze(note: str, evidence: str) -> None:
    """实现 bug 修复后的重冻结：记录证据、归档旧结果、要求全量重跑。"""
    protocol = read_json(PROTOCOL, None)
    if not protocol:
        raise RuntimeError("没有可更新的 protocol.json")
    old_script = protocol["initial_freeze"]["script_sha256"]
    new_script = file_sha(Path(__file__).resolve())
    if old_script == new_script:
        raise RuntimeError("脚本未变化，不需要 refreeze")
    evidence_path = OUT / "bugfix" / "evidence.md"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    evidence_path.write_text(evidence.strip() + "\n", encoding="utf-8")
    archive_attempts(note)
    protocol.setdefault("post_freeze_changes", []).append(
        {
            "changed_at": now(),
            "reason": "implementation_bug",
            "note": note,
            "evidence_file": str(evidence_path.relative_to(ROOT)),
            "old_script_sha256": old_script,
            "new_script_sha256": new_script,
            "full_rerun_required": True,
            "archive": f"attempts/attempt-{len(list((OUT / 'attempts').glob('attempt-*')))}",
        }
    )
    write_json(PROTOCOL, protocol)
    print(json.dumps({"refrozen": True, "old": old_script, "new": new_script}, ensure_ascii=False))


def run() -> None:
    protocol = verify_frozen()
    token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
    production_before = file_sha(SOURCE)
    history_before = {
        name: tree_sha(ROOT / "artifacts" / "experience-transfer-ab-real-model" / name)
        for name in HISTORY
    }

    preflight = call_pool('Return only {"ready":true}.', "PREFLIGHT", 0, token, expect="ready")
    if preflight["terminal_state"] != "ok":
        raise RuntimeError(f"真实模型预检失败：{preflight.get('error')}")
    if preflight.get("actual_model") != "MiniMax-M3.1-Flash":
        raise RuntimeError(f"实际模型不符：{preflight.get('actual_model')}")

    results: list[dict[str, Any]] = []
    cost = _provider_cost(preflight)
    for case in CASES:
        first = execute_attempt(case, 1, token, None)
        row: dict[str, Any] = {"case_id": case["case_id"], "first": first, "retry": None}
        if not first["mechanical_pass"]:
            second = execute_attempt(
                case, 2, token, (first["failed_gaps"], first["files"])
            )
            row["retry"] = second
            row["new_problems"] = new_problems(first, second)
            row["fixed_checks"] = sorted(
                set(first["failed_checks"]) - set(second["failed_checks"])
            )
        row["final_pass"] = bool(
            (row["retry"] or first)["mechanical_pass"]
        )
        row["semantic_unverified"] = case_spec(case)["semantic_unverified"]
        results.append(row)
        write_json(RESULTS, results)
        for attempt in (first, row["retry"]):
            if attempt:
                cost += _provider_cost(attempt)
        if cost > COST_LIMIT:
            raise RuntimeError(f"费用上限触发：{cost} > {COST_LIMIT}")
        calls_used = sum(1 for _ in (OUT / "calls").glob("*.json"))
        if calls_used >= MAX_CALLS:
            break

    production_after = file_sha(SOURCE)
    history_after = {
        name: tree_sha(ROOT / "artifacts" / "experience-transfer-ab-real-model" / name)
        for name in HISTORY
    }
    write_json(
        ENVIRONMENT,
        {
            "production_experience_sha256_before": production_before,
            "production_experience_sha256_after": production_after,
            "production_unchanged": production_before == production_after,
            "history_sha256_before": history_before,
            "history_sha256_after": history_after,
            "history_unchanged": history_before == history_after,
            "temporary_directories_removed": all(
                attempt["temporary_directory_removed"]
                for row in results
                for attempt in (row["first"], row["retry"])
                if attempt
            ),
            "calls_used": len(list((OUT / "calls").glob("*.json"))),
        },
    )
    print(json.dumps({"cases": len(results), "results": str(RESULTS)}, ensure_ascii=False))


def _provider_cost(record: dict[str, Any]) -> float:
    value = record.get("provider_cost")
    if isinstance(value, (int, float)):
        return float(value)
    value = record.get("ledger_cost")
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


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
        raise RuntimeError(f"expected 6 task results, found {len(rows)}")
    cases = {spec["case_id"]: spec for spec in protocol["cases"]}

    table = []
    for row in rows:
        spec = cases[row["case_id"]]
        first = row["first"]
        second = row["retry"]
        final = second or first
        table.append(
            {
                "case_id": row["case_id"],
                "dispatch_path": spec["path"],
                "first_pass": first["mechanical_pass"],
                "first_failed": first["failed_checks"],
                "first_failed_gaps": first["failed_gaps"],
                "retry_used": second is not None,
                "retry_terminal_state": (second or {}).get("terminal_state"),
                "retry_pass": (second or {}).get("mechanical_pass"),
                "final_pass": row["final_pass"],
                "fixed_checks": row.get("fixed_checks", []),
                "new_problems": row.get("new_problems", []),
                "final_failed": final["failed_checks"],
                "final_failed_gaps": final["failed_gaps"],
                "committed_files": final["committed_files"],
                "diff": final["diff"],
                "diff_stat": final["diff_stat"],
                "terminal_state": final["terminal_state"],
                "checks": final["checks"],
                "semantic_unverified": spec["semantic_unverified"],
                "old_value_hits": _old_hits(spec, final["files"]),
                "new_banned_word_hits": _new_banned_hits(spec, final["files"]),
            }
        )

    counts = {
        "first_attempt_pass": sum(row["first_pass"] for row in table),
        "after_retry_pass": sum(row["final_pass"] for row in table),
        "retry_used": sum(row["retry_used"] for row in table),
        "failed": sum(not row["final_pass"] for row in table),
        "new_problems": sum(bool(row["new_problems"]) for row in table),
    }
    calls = sorted((OUT / "calls").glob("*.json"))
    receipts = [read_json(path, {}) for path in calls]
    summary = {
        "dataset_version": DATASET_VERSION,
        "model": protocol["model"],
        "parameters": protocol["parameters"],
        "tasks": len(table),
        "counts": counts,
        "pass_rate_first_attempt": round(counts["first_attempt_pass"] / len(table), 4),
        "pass_rate_after_retry": round(counts["after_retry_pass"] / len(table), 4),
        "wilson_95_first_attempt": _wilson(counts["first_attempt_pass"], len(table)),
        "wilson_95_after_retry": _wilson(counts["after_retry_pass"], len(table)),
        "table": table,
        "cost": {
            "provider_returned_cost_sum": round(sum(_provider_cost(item) for item in receipts), 6),
            "provider_cost_missing_calls": sum(item.get("provider_cost") is None for item in receipts),
            "ledger_cost_sum": round(sum(float(item.get("ledger_cost") or 0.0) for item in receipts), 6),
            "prompt_tokens": sum(int((item.get("usage") or {}).get("prompt_tokens") or 0) for item in receipts),
            "completion_tokens": sum(int((item.get("usage") or {}).get("completion_tokens") or 0) for item in receipts),
            "total_tokens": sum(int((item.get("usage") or {}).get("total_tokens") or 0) for item in receipts),
            "usage_missing_calls": sum(not item.get("usage") for item in receipts),
            "finish_reasons": dict(Counter(str(item.get("finish_reason")) for item in receipts)),
            "calls": len(receipts),
        },
        "environment": read_json(ENVIRONMENT, {}),
        "protocol": {
            "frozen_at": protocol["frozen_at"],
            "script_sha256": protocol["initial_freeze"]["script_sha256"],
            "check_spec_sha256": protocol["initial_freeze"]["check_spec_sha256"],
            "post_freeze_changes": protocol.get("post_freeze_changes", []),
        },
        "reference_hashes": protocol["reference_hashes"],
    }
    write_json(SUMMARY, summary)
    write_json(OUT / "rows.json", table)
    print(json.dumps({key: summary[key] for key in ("counts", "pass_rate_first_attempt", "pass_rate_after_retry", "cost")}, ensure_ascii=False, indent=2))


def _delivery_text(spec: dict[str, Any], files: dict[str, str]) -> str:
    return files.get(spec["path"], "") or ""


def _old_hits(spec: dict[str, Any], files: dict[str, str]) -> list[str]:
    return hits(_delivery_text(spec, files), spec["old_values"])


def _new_banned_hits(spec: dict[str, Any], files: dict[str, str]) -> list[str]:
    tokens: list[str] = []
    for check in spec["checks"]:
        if check["id"] == "banned_new_absent":
            tokens.extend(check["expected"]["tokens"])
    return hits(_delivery_text(spec, files), tokens)


# -----------------------------------------------------------------------------
# 无模型自检
# -----------------------------------------------------------------------------

GOOD = {
    "TR-01": """# 第二轮复盘

## 意见映射表

| 意见编号 | 审查意见 | 本轮处置 | 状态 | 证据链接 |
| --- | --- | --- | --- | --- |
| F1 | 上次表格列名不完整 | 已改为本轮要求的五列 | 已补 | ops/review-round-1.md |
| F2 | 待补项不够具体 | 已改写为可核验事项 | 已补 | ops/review-round-1.md |

结论：本轮按新的五列结构交付。
""",
    "TR-02": """# 验收说明

1. 检查交付文件是否存在。
2. 检查正文是否出现表示未完成或留白的三个词。
3. 逐条核对上述两项后，交由人类验收。
""",
    "TR-03": """# 第一轮交付说明

## 待补项

1. 补齐启动命令失败路径的验收说明。
2. 统一标题强调风格，与正文其余部分一致。
""",
    "TR-04": """# 检查清单

- F1：交付路径 ops/review-checklist.md 真实存在，且内容可读。
- F2：禁词清单与当前要求一致，不由历史清单替代。

本轮只复核上述两项检查，其余历史检查项不纳入本次交付范围。
""",
    "TR-05": """# qoder 交付说明

本轮只创建 ops/qoder-lane.md 这一个文件，说明 qoder 侧的交付内容与结论。
""",
    "TR-06": """# 证据清单

- ops/adapter-notes.md：第 1 节「注册与调用」说明适配层注册流程。
- ops/receipt-notes.md：第 1 节「回执结构」说明回执字段。
""",
}


def selftest() -> None:
    failures: list[str] = []
    for case in CASES:
        good = GOOD[case["case_id"]]
        root = Path(tempfile.mkdtemp(prefix=f"acceptance-selftest-{case['case_id']}-"))
        try:
            repo, base_sha = init_repo(case, root)
            target = repo / case["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(good, encoding="utf-8", newline="\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-m", "selftest good")
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            files = snapshot(repo)
            checks = run_checks(case, repo, base_sha, head, files, [])
            failed = [check["check_id"] for check in checks if not check["pass"]]
            if failed:
                failures.append(f"{case['case_id']} 合格样例未通过：{failed}")

            bad_root = Path(tempfile.mkdtemp(prefix=f"acceptance-selftest-bad-{case['case_id']}-"))
            try:
                bad_repo, bad_base = init_repo(case, bad_root)
                if case["case_id"] == "TR-05":
                    for path in ("ops/qoder-lane.md", "ops/extra.md"):
                        target = bad_repo / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("qoder 交付说明。" * 10, encoding="utf-8", newline="\n")
                else:
                    target = bad_repo / case["path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text("旧内容", encoding="utf-8", newline="\n")
                git(bad_repo, "add", "-A")
                git(bad_repo, "commit", "-m", "selftest bad")
                bad_head = git(bad_repo, "rev-parse", "HEAD").stdout.strip()
                bad_checks = run_checks(
                    case, bad_repo, bad_base, bad_head, snapshot(bad_repo), []
                )
                if all(check["pass"] for check in bad_checks):
                    failures.append(f"{case['case_id']} 不合格样例被误判为通过")
            finally:
                shutil.rmtree(bad_root, ignore_errors=True)
        finally:
            shutil.rmtree(root, ignore_errors=True)
    if failures:
        raise RuntimeError("自检失败：\n" + "\n".join(failures))
    print(json.dumps({"selftest": "ok", "cases": len(CASES)}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["selftest", "freeze", "run", "finalize", "refreeze"])
    parser.add_argument("--note", default="")
    parser.add_argument("--evidence", default="")
    args = parser.parse_args()
    if args.command == "selftest":
        selftest()
    elif args.command == "freeze":
        freeze()
    elif args.command == "run":
        run()
    elif args.command == "finalize":
        finalize()
    else:
        refreeze(args.note, args.evidence)


if __name__ == "__main__":
    main()
