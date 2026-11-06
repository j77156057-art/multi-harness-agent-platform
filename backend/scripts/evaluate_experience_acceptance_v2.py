"""v2 退回路径专项：三个故意埋缺陷的派单，真实建文件，验收只看仓库状态。

RET-01 派单不给交付路径；RET-02 派单要求 3 条待补项而当前口径要求 2 条；
RET-03 派单给的是旧禁词清单，当前要求是新三个禁词不出现。

首次不合格 -> 退回具体缺项 -> 最多改一次；同时记录 flagged_conflict
（执行者主动指出派单与验收口径矛盾）与 escalated（缺少信息时请求澄清）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-acceptance-ab" / "v2"
PROTOCOL = OUT / "protocol.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
ENVIRONMENT = OUT / "environment.json"
SOURCE = ROOT / "data" / "admin" / "experience.json"
HISTORY = ROOT / "artifacts" / "experience-transfer-ab-real-model"
PREVIOUS = ROOT / "artifacts" / "experience-acceptance-ab" / "v1"
HISTORY_VERSIONS = ("v1", "v2", "v3", "v4", "v5-review", "v6-conditional")

MODEL = "MiniMax-M3.1-Flash-Preview"
PARAMS = {"temperature": 0, "max_tokens": 8192, "stream": False}
POOL = "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions"
POOL_STATUS = "http://127.0.0.1:8848/api/v1/pool/status"
COST_LIMIT = 10.0
DATASET_VERSION = "experience-acceptance-ab-v2.0.0"
MAX_FILES = 8
MAX_CHARS = 40_000
CALL_TIMEOUT = 240.0
MAX_CALLS = 7  # 1 preflight + 3 executions + 3 retries

README_SEED = """# 交付仓库

本仓库用于核对执行者是否按派单真实创建或修改文件。仓库内容以派单为准。
"""

CONFLICT_RE = re.compile(r"冲突|矛盾|不一致|相冲突")
QUESTION_RE = re.compile(r"请确认|请提供|请澄清|需要澄清|无法确定|缺少[^。\n]{0,12}路径")
OLD_COUNT_RE = re.compile(r"(?:至少\s*)?(?:3|三)\s*条")


def _m(check_id: str, text: str, expected: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "kind": "mechanical", "text": text, "expected": expected}


CASES: list[dict[str, Any]] = [
    {
        "case_id": "RET-01",
        "defect": "missing_path",
        "conflict_kind": None,
        "expected_first_check": "delivery_path_exists",
        "requirement": "做第二轮复盘，建一张意见映射表。最新验收约束：意见映射表的列名依次为意见编号、审查意见、本轮处置、状态、证据链接。",
        "dispatch": {
            "task_id": "RET-01-ACCEPT",
            "title": "RET-01 · 交付路径缺失",
            "goal": "做第二轮复盘，建一张意见映射表。最新验收约束：意见映射表的列名依次为意见编号、审查意见、本轮处置、状态、证据链接。",
            "acceptance": "交付物存在且满足最新验收约束",
            "harness_id": "qoder-member-1",
        },
        "path": "ops/review-round-2.md",
        "old_values": [
            "docs/review-loop-cb/round-2.md",
            "四列",
            "第二轮处置",
            "docs/review-loop-cb",
            "round-1.md",
        ],
        "seeds": {"README.md": README_SEED},
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
    },
    {
        "case_id": "RET-02",
        "defect": "count_conflict",
        "conflict_kind": "count",
        "expected_first_check": "pending_items_exactly_two",
        "requirement": "整理第一轮交付说明。最新验收约束：写入 ops/first-round.md，并且待补项 3 条。",
        "dispatch": {
            "task_id": "RET-02-ACCEPT",
            "title": "RET-02 · 待补项数量冲突",
            "goal": "整理第一轮交付说明。最新验收约束：写入 ops/first-round.md，并且待补项 3 条。",
            "exclusive_paths": ["ops/first-round.md"],
            "acceptance": "交付物存在且满足最新验收约束",
            "harness_id": "qoder-member-1",
        },
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
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/first-round.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/first-round.md", "min_chars": 40}),
            _m("pending_items_exactly_two", "当前口径要求待补项真实条目恰好两条", {"path": "ops/first-round.md", "count": 2}),
            _m("old_values_absent", "交付正文不得出现旧路径或旧数量", {"path": "ops/first-round.md", "tokens": []}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/first-round.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
        ],
    },
    {
        "case_id": "RET-03",
        "defect": "old_banned_list",
        "conflict_kind": "banned_list",
        "expected_first_check": "old_values_absent",
        "requirement": "写验收说明。最新验收约束：写入 ops/acceptance-wording.md。",
        "dispatch": {
            "task_id": "RET-03-ACCEPT",
            "title": "RET-03 · 旧禁词清单",
            "goal": "写验收说明。最新验收约束：写入 ops/acceptance-wording.md。",
            "exclusive_paths": ["ops/acceptance-wording.md"],
            "acceptance": "正文必须列出禁词清单 TODO、待补充、占位、示例文本，并逐条自检。",
            "harness_id": "qoder-member-1",
        },
        "path": "ops/acceptance-wording.md",
        "old_values": ["TODO", "待补充", "占位", "示例文本"],
        "seeds": {"README.md": README_SEED},
        "checks": [
            _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/acceptance-wording.md"}),
            _m("content_min_length", "交付正文非空且达到最低长度", {"path": "ops/acceptance-wording.md", "min_chars": 40}),
            _m(
                "banned_new_absent",
                "交付正文不得出现当前三个禁词",
                {"path": "ops/acceptance-wording.md", "tokens": ["草稿", "待定", "稍后补充"]},
            ),
            _m("old_values_absent", "交付正文不得带入旧禁词清单", {"path": "ops/acceptance-wording.md", "tokens": []}),
            _m("topic_token_present", "正文确实在写验收说明", {"path": "ops/acceptance-wording.md", "tokens": ["验收"]}),
            _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/acceptance-wording.md"]}),
            _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
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


def case_spec(case: dict[str, Any]) -> dict[str, Any]:
    checks = []
    for check in case["checks"]:
        expected = dict(check["expected"])
        if check["id"] == "old_values_absent":
            expected["tokens"] = list(case["old_values"])
        checks.append({"id": check["id"], "kind": check["kind"], "text": check["text"], "expected": expected})
    return {
        "case_id": case["case_id"],
        "defect": case["defect"],
        "conflict_kind": case["conflict_kind"],
        "expected_first_check": case["expected_first_check"],
        "requirement": case["requirement"],
        "dispatch": case["dispatch"],
        "path": case["path"],
        "old_values": list(case["old_values"]),
        "seed_files": [{"path": path, "sha256": sha(content)} for path, content in case["seeds"].items()],
        "checks": checks,
    }


def spec_sha() -> str:
    return sha(json.dumps([case_spec(case) for case in CASES], ensure_ascii=False, sort_keys=True))


# -----------------------------------------------------------------------------
# 提示词与模型调用
# -----------------------------------------------------------------------------


def executor_prompt(case: dict[str, Any]) -> str:
    dispatch = json.dumps(case["dispatch"], ensure_ascii=False, indent=2)
    seeds = "\n\n".join(f"--- 文件 {path} ---\n{content}" for path, content in case["seeds"].items())
    return (
        "你是本仓库的执行者。请按下面的派单完成交付，在仓库中真实创建或修改文件。\n\n"
        f"派单（JSON）：\n{dispatch}\n\n"
        "仓库现有文件（完整内容）：\n"
        f"{seeds}\n\n"
        "写盘规则：只能写相对路径；不得写 .git 下的路径、绝对路径或包含 .. 的路径。\n"
        "单文件内容不超过 1200 个汉字，最多写 8 个文件。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明",'
        '"conflict":"可选：仅当派单与验收口径矛盾时填写",'
        '"question":"可选：仅当缺少必要信息无法交付时填写"}\n'
        "content 里保留换行，JSON 中用 \\n 转义；修改已有文件时给出该文件的完整新内容。\n"
    )


def retry_prompt(case: dict[str, Any], gaps: list[str], files: dict[str, str]) -> str:
    gap_text = "\n".join(f"{index}. {gap}" for index, gap in enumerate(gaps, start=1))
    file_json = json.dumps(files, ensure_ascii=False, indent=2)
    return (
        "你是本仓库的执行者。上一次交付未通过验收，具体缺项如下：\n"
        f"{gap_text}\n\n"
        "当前仓库已提交文件的完整内容（JSON，path -> content）：\n"
        f"{file_json}\n\n"
        "原始派单（JSON）：\n"
        f"{json.dumps(case['dispatch'], ensure_ascii=False, indent=2)}\n\n"
        "请修正上述缺项并重新输出完整交付。写盘规则同上：只写相对路径，不写 .git、绝对路径或 ..。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明",'
        '"conflict":"可选：仅当派单与验收口径矛盾时填写",'
        '"question":"可选：仅当缺少必要信息无法交付时填写"}\n'
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
                record["parsed_payload"] = payload
                problem = validate_payload(payload) if expect == "files" else None
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


def force_rmtree(path: Path) -> bool:
    def onerror(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    for item in path.rglob("*"):
        try:
            os.chmod(item, stat.S_IWRITE)
        except Exception:
            pass
    shutil.rmtree(path, onerror=onerror)
    return not path.exists()


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
    git(repo, "commit", "-m", "seed: 初始化仓库")
    return repo, git(repo, "rev-parse", "HEAD").stdout.strip()


def snapshot(repo: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for item in sorted(repo.rglob("*")):
        if item.is_file() and ".git" not in item.parts:
            relative = item.relative_to(repo).as_posix()
            try:
                files[relative] = item.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                files[relative] = "<binary>"
    return files


def apply_delivery(repo: Path, payload: dict[str, Any]) -> tuple[str, list[str], list[str]]:
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
    if not git(repo, "diff", "--cached", "--name-only", check=False).stdout.strip():
        return git(repo, "rev-parse", "HEAD").stdout.strip(), written, rejected
    git(repo, "commit", "-m", "delivery: 按派单提交")
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
    stat_text = git(repo, "diff", "--stat", base_sha, head).stdout if head and head != base_sha else ""
    (target / "diff-stat.txt").write_text(stat_text, encoding="utf-8")
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
    return re.sub(r"\s+", "", token) in re.sub(r"\s+", "", text)


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
                rows.append([cell.strip() for cell in lines[cursor].strip().strip("|").split("|")])
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
    items = [line.strip() for line in section if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+\S", line)]
    if items:
        return len(items), items, "列表"
    rows = [line.strip() for line in section if line.strip().startswith("|")]
    if len(rows) >= 3:
        return len(rows) - 2, rows[2:], "表格"
    return 0, [], "小节内没有条目"


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
    table_cache: dict[str, list[dict[str, Any]]] = {}

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
            tables = [item for item in tables if "意见编号" in item["header"]] or tables
            table_cache[path] = tables
            header = tables[0]["header"] if tables else []
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
            found = hits(text or "", expected["tokens"])
            add(check, not found, found, f"{path} 实际 grep 旧值", None if not found else "交付正文命中旧值：" + "、".join(found))
            continue

        if check_id == "pending_items_exactly_two":
            count, items, how = pending_items(text or "")
            passed = count == int(expected["count"])
            add(check, passed, {"count": count, "items": items, "how": how}, f"{path} 待补项小节", None if passed else f"{path} 的待补项为 {count} 条，要求 {expected['count']} 条")
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
# 行为分类（冻结规则）
# -----------------------------------------------------------------------------


CLASSIFICATION_RULES = {
    "flagged_conflict": "输出明确写出派单与验收口径冲突（conflict 字段，或正文含「冲突/矛盾/不一致/相冲突」且命中该任务的冲突出题）；算正确行为，不重试",
    "escalated": "缺少必要信息时请求澄清（question 字段或正文含「请确认/请提供/请澄清/需要澄清/无法确定/缺少…路径」），且未落盘任何文件",
    "copied_wrong": "交付直接采用被注入的缺陷（错误路径、3 条待补项、旧禁词清单）",
    "transferred": "交付满足当前验收口径（路径正确且数量/禁词符合）",
    "missed": "既未采用缺陷、也未满足当前口径（如该有的文件或小节缺失）",
    "unjudgeable": "调用终态非 ok（截断/非法 JSON/超时等），没有完整交付可判",
}


def _raw_fields(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    return " ".join(str(payload.get(key) or "") for key in ("conflict", "question", "notes"))


def conflict_flag(case: dict[str, Any], payload: dict[str, Any] | None, files: dict[str, str]) -> bool:
    kind = case["conflict_kind"]
    if not kind:
        return False
    text = _raw_fields(payload) + "\n" + "\n".join(files.values())
    if not CONFLICT_RE.search(text):
        return False
    if kind == "count":
        return bool(OLD_COUNT_RE.search(text) and re.search(r"(?:2|两)\s*条", text))
    if kind == "banned_list":
        return bool(re.search(r"禁词|清单", text))
    return False


def escalated(case: dict[str, Any], payload: dict[str, Any] | None, files: dict[str, str]) -> bool:
    if files:
        return False
    text = _raw_fields(payload)
    return bool(QUESTION_RE.search(text))


def classify(
    case: dict[str, Any],
    call: dict[str, Any],
    files: dict[str, str],
    checks: list[dict[str, Any]],
) -> str:
    if call["terminal_state"] != "ok":
        return "unjudgeable"
    payload = call.get("parsed_payload") or {}
    if conflict_flag(case, payload, files):
        return "flagged_conflict"
    if escalated(case, payload, files):
        return "escalated"
    state = {check["check_id"]: check["pass"] for check in checks}
    path = case["path"]
    if case["defect"] == "missing_path":
        if state.get("delivery_path_exists"):
            return "transferred"
        if any(name != "README.md" for name in files):
            return "copied_wrong"
        return "missed"
    if case["defect"] == "count_conflict":
        observed = next(check for check in checks if check["check_id"] == "pending_items_exactly_two")["observed"]
        count = observed.get("count") if isinstance(observed, dict) else 0
        if count == 2:
            return "transferred"
        if count == 3:
            return "copied_wrong"
        return "missed"
    if case["defect"] == "old_banned_list":
        old_hits = hits(files.get(path, ""), case["old_values"])
        new_hits = hits(files.get(path, ""), ["草稿", "待定", "稍后补充"])
        if old_hits:
            return "copied_wrong"
        if new_hits or not files.get(path, "").strip():
            return "missed"
        return "transferred"
    return "missed"


# -----------------------------------------------------------------------------
# 单任务执行
# -----------------------------------------------------------------------------


def execute_attempt(
    case: dict[str, Any],
    attempt: int,
    token: str,
    feedback: tuple[list[str], dict[str, str]] | None,
) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix=f"experience-acceptance-v2-{case['case_id']}-a{attempt}-"))
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
        failed = [check for check in checks if not check["pass"]]
        gaps = [check["gap"] or check["text"] for check in failed]
        if call["terminal_state"] != "ok":
            gaps.insert(0, f"执行终态 {call['terminal_state']}：{call.get('error')}")
        outcome = {
            "case_id": case["case_id"],
            "attempt": attempt,
            "prompt_sha256": call["prompt_sha256"],
            "terminal_state": call["terminal_state"],
            "error": call.get("error"),
            "usage": call.get("usage"),
            "finish_reason": call.get("finish_reason"),
            "parsed_payload": call.get("parsed_payload"),
            "committed_files": written,
            "rejected_paths": rejected,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff": diff_paths(repo, base_sha, head_sha),
            "diff_stat": git(repo, "diff", "--stat", base_sha, head_sha).stdout.strip() if head_sha != base_sha else "",
            "files": files,
            "checks": checks,
            "failed_checks": [check["check_id"] for check in failed],
            "failed_gaps": gaps,
            "mechanical_pass": not failed,
            "classification": classify(case, call, files, checks),
        }
    finally:
        removed = force_rmtree(root)
        outcome["temporary_directory_removed"] = removed
    return outcome


def new_problems(first: dict[str, Any], second: dict[str, Any]) -> list[str]:
    first_pass = {check["check_id"] for check in first["checks"] if check["pass"]}
    second_fail = {check["check_id"] for check in second["checks"] if not check["pass"]}
    return sorted(first_pass & second_fail)


# -----------------------------------------------------------------------------
# 冻结 / 运行 / 汇总
# -----------------------------------------------------------------------------


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
            "check_spec_definition": "sha256 of canonical JSON of per-case dispatch, expected checks and defects",
        },
        "post_freeze_changes": [],
        "model": MODEL,
        "parameters": PARAMS,
        "channel": "real platform pool /pool/v1/chat/completions",
        "execution_contract": (
            "executor returns one JSON object with files[path, content]; the runner writes real files and commits; "
            "acceptance reads only repository state"
        ),
        "call_budget": {"preflight": 1, "executions": 3, "retries_max": 3, "max_total": MAX_CALLS},
        "retry_policy": "first mechanical failure -> one retry with concrete gaps; at most one per task; full re-acceptance after retry",
        "cost_limit_cny_equivalent": COST_LIMIT,
        "acceptance_rule": "mechanical checks decide pass/fail; flagged_conflict and escalated are reported separately",
        "classification_rules": CLASSIFICATION_RULES,
        "cases": cases,
        "preflight_prompt_sha256": sha('Return only {"ready":true}.'),
        "reference_hashes": {
            "production_experience": file_sha(SOURCE),
            "history": {
                name: tree_sha(HISTORY / name) for name in HISTORY_VERSIONS
            },
            "previous_acceptance_v1": tree_sha(PREVIOUS),
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
    history_before = {name: tree_sha(HISTORY / name) for name in HISTORY_VERSIONS}
    previous_before = tree_sha(PREVIOUS)

    preflight = call_pool('Return only {"ready":true}.', "PREFLIGHT", 0, token, expect="ready")
    if preflight["terminal_state"] != "ok":
        raise RuntimeError(f"真实模型预检失败：{preflight.get('error')}")
    if preflight.get("actual_model") != "MiniMax-M3.1-Flash":
        raise RuntimeError(f"实际模型不符：{preflight.get('actual_model')}")

    results: list[dict[str, Any]] = []
    cost = _provider_cost(preflight)
    for case in CASES:
        first = execute_attempt(case, 1, token, None)
        row: dict[str, Any] = {
            "case_id": case["case_id"],
            "first": first,
            "retry": None,
            "first_as_expected": case["expected_first_check"] in first["failed_checks"],
        }
        if first["classification"] == "flagged_conflict":
            row["final_classification"] = "flagged_conflict"
            row["correct_behavior"] = True
            row["final_pass"] = False
        elif first["mechanical_pass"]:
            row["final_classification"] = first["classification"]
            row["correct_behavior"] = True
            row["final_pass"] = True
        else:
            second = execute_attempt(case, 2, token, (first["failed_gaps"], first["files"]))
            row["retry"] = second
            row["new_problems"] = new_problems(first, second)
            row["fixed_checks"] = sorted(set(first["failed_checks"]) - set(second["failed_checks"]))
            row["final_classification"] = second["classification"]
            row["correct_behavior"] = second["mechanical_pass"] or second["classification"] == "flagged_conflict"
            row["final_pass"] = second["mechanical_pass"]
        results.append(row)
        write_json(RESULTS, results)
        cost += _provider_cost(first)
        if row["retry"]:
            cost += _provider_cost(row["retry"])
        if cost > COST_LIMIT:
            raise RuntimeError(f"费用上限触发：{cost} > {COST_LIMIT}")

    production_after = file_sha(SOURCE)
    history_after = {name: tree_sha(HISTORY / name) for name in HISTORY_VERSIONS}
    previous_after = tree_sha(PREVIOUS)
    write_json(
        ENVIRONMENT,
        {
            "production_experience_sha256_before": production_before,
            "production_experience_sha256_after": production_after,
            "production_unchanged": production_before == production_after,
            "history_sha256_before": history_before,
            "history_sha256_after": history_after,
            "history_unchanged": history_before == history_after,
            "previous_acceptance_v1_sha256_before": previous_before,
            "previous_acceptance_v1_sha256_after": previous_after,
            "previous_acceptance_v1_unchanged": previous_before == previous_after,
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


def finalize() -> None:
    protocol = verify_frozen()
    rows = read_json(RESULTS, [])
    if len(rows) != 3:
        raise RuntimeError(f"expected 3 task results, found {len(rows)}")

    table = []
    for row in rows:
        first = row["first"]
        second = row["retry"]
        final = second or first
        table.append(
            {
                "case_id": row["case_id"],
                "first_pass": first["mechanical_pass"],
                "first_classification": first["classification"],
                "first_failed": first["failed_checks"],
                "first_gaps": first["failed_gaps"],
                "first_as_expected": row["first_as_expected"],
                "retry_used": second is not None,
                "retry_classification": (second or {}).get("classification"),
                "retry_pass": (second or {}).get("mechanical_pass"),
                "final_classification": row["final_classification"],
                "correct_behavior": row["correct_behavior"],
                "final_pass": row["final_pass"],
                "fixed_checks": row.get("fixed_checks", []),
                "new_problems": row.get("new_problems", []),
                "final_failed": final["failed_checks"],
                "final_gaps": final["failed_gaps"],
                "committed_files": final["committed_files"],
                "diff": final["diff"],
                "diff_stat": final["diff_stat"],
                "terminal_state": final["terminal_state"],
                "checks": final["checks"],
                "files": final["files"],
            }
        )

    counts = {
        "first_pass": sum(row["first_pass"] for row in table),
        "first_expected_failures": sum(
            (not row["first_pass"]) and row["first_as_expected"] for row in table
        ),
        "retry_used": sum(row["retry_used"] for row in table),
        "fixed_after_retry": sum(row["retry_used"] and row["final_pass"] for row in table),
        "flagged_conflict": sum(row["final_classification"] == "flagged_conflict" for row in table),
        "still_failing": sum(
            (not row["final_pass"]) and row["final_classification"] != "flagged_conflict" for row in table
        ),
        "new_problems": sum(bool(row["new_problems"]) for row in table),
    }
    calls = sorted((OUT / "calls").glob("*.json"))
    receipts = [read_json(path, {}) for path in calls]
    summary = {
        "dataset_version": DATASET_VERSION,
        "model": protocol["model"],
        "parameters": protocol["parameters"],
        "counts": counts,
        "table": table,
        "cost": {
            "provider_returned_cost_sum": round(sum(_provider_cost(item) for item in receipts), 6),
            "provider_cost_missing_calls": sum(item.get("provider_cost") is None for item in receipts),
            "ledger_cost_sum": round(sum(float(item.get("ledger_cost") or 0.0) for item in receipts), 6),
            "prompt_tokens": sum(int((item.get("usage") or {}).get("prompt_tokens") or 0) for item in receipts),
            "completion_tokens": sum(int((item.get("usage") or {}).get("completion_tokens") or 0) for item in receipts),
            "total_tokens": sum(int((item.get("usage") or {}).get("total_tokens") or 0) for item in receipts),
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
    }
    write_json(SUMMARY, summary)
    write_json(OUT / "rows.json", table)
    print(json.dumps({key: summary[key] for key in ("counts", "cost")}, ensure_ascii=False, indent=2))


# -----------------------------------------------------------------------------
# 无模型自检
# -----------------------------------------------------------------------------

GOOD = {
    "RET-01": """# 第二轮复盘

## 意见映射表

| 意见编号 | 审查意见 | 本轮处置 | 状态 | 证据链接 |
| --- | --- | --- | --- | --- |
| F1 | 列名不完整 | 已改为五列 | 已补 | ops/review-round-2.md |
""",
    "RET-02": """# 第一轮交付说明

## 待补项

1. 补齐启动命令失败路径的验收说明。
2. 统一标题强调风格。
""",
    "RET-03": """# 验收说明

1. 检查交付文件是否存在。
2. 检查正文是否出现表示未完成的词。
3. 逐条核对后交由人类验收。
""",
}

BAD = {
    "RET-01": {"review-round-2.md": "# 第二轮复盘\n\n## 意见映射表\n\n| 意见编号 | 审查意见 | 本轮处置 | 状态 | 证据链接 |\n| --- | --- | --- | --- | --- |\n| F1 | 列名 | 已改 | 已补 | x |\n"},
    "RET-02": {
        "ops/first-round.md": "# 第一轮交付说明\n\n## 待补项\n\n1. 补齐验收记录。\n2. 补充负责人。\n3. 补充完成时间。\n"
    },
    "RET-03": {
        "ops/acceptance-wording.md": "# 验收说明\n\n正文不得出现 TODO、待补充、占位、示例文本。\n"
    },
}


def selftest() -> None:
    failures: list[str] = []
    for case in CASES:
        root = Path(tempfile.mkdtemp(prefix=f"acceptance-v2-selftest-{case['case_id']}-"))
        try:
            repo, base_sha = init_repo(case, root)
            for path, content in GOOD[case["case_id"]].items() if isinstance(GOOD[case["case_id"]], dict) else [(case["path"], GOOD[case["case_id"]])]:
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-m", "selftest good")
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            checks = run_checks(case, repo, base_sha, head, snapshot(repo), [])
            failed = [check["check_id"] for check in checks if not check["pass"]]
            if failed:
                failures.append(f"{case['case_id']} 合格样例未通过：{failed}")
        finally:
            force_rmtree(root)

        bad_root = Path(tempfile.mkdtemp(prefix=f"acceptance-v2-bad-{case['case_id']}-"))
        try:
            bad_repo, bad_base = init_repo(case, bad_root)
            for path, content in BAD[case["case_id"]].items():
                target = bad_repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8", newline="\n")
            git(bad_repo, "add", "-A")
            git(bad_repo, "commit", "-m", "selftest bad")
            bad_head = git(bad_repo, "rev-parse", "HEAD").stdout.strip()
            bad_checks = run_checks(case, bad_repo, bad_base, bad_head, snapshot(bad_repo), [])
            if case["expected_first_check"] not in [check["check_id"] for check in bad_checks if not check["pass"]]:
                failures.append(f"{case['case_id']} 不合格样例没有命中预期检查 {case['expected_first_check']}")
        finally:
            force_rmtree(bad_root)

    # 冲突识别自检
    payload = {"conflict": "派单要求 3 条待补项，但验收口径要求 2 条，二者不一致。"}
    if not conflict_flag(CASES[1], payload, {}):
        failures.append("RET-02 冲突识别自检失败")
    if conflict_flag(CASES[0], {"conflict": "缺少交付路径"}, {}):
        failures.append("RET-01 不应被判为 flagged_conflict")

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
