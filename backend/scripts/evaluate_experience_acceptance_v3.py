"""v3: rendered-visible-text banned-word checks + structured conflicts.

Two fixes over v2:
1. Banned-word checks run on the text a reader actually sees: Markdown/HTML
   entities decoded, tags stripped, code spans kept literal (documented
   exception), then a fixed normalization chain (entities -> \\uXXXX/\\xNN ->
   zero-width removal -> NFKC -> casefold).
2. ``flagged_conflict`` requires a structured ``conflicts`` array entry
   ``{item, dispatch_value, acceptance_value}`` with a matching item and two
   different values. Keyword matching is no longer used.

Only RET-03 is re-executed (first delivery + at most one retry). RET-03's
v2 encoded delivery, three bypass variants and the RET-02 conflict scenario
are covered by static regressions, with no extra model calls.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unicodedata
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "experience-acceptance-ab" / "v3"
PROTOCOL = OUT / "protocol.json"
RESULTS = OUT / "results.json"
SUMMARY = OUT / "summary.json"
REGRESSIONS = OUT / "regressions.json"
ENVIRONMENT = OUT / "environment.json"
SOURCE = ROOT / "data" / "admin" / "experience.json"
HISTORY = ROOT / "artifacts" / "experience-transfer-ab-real-model"
PREVIOUS_V1 = ROOT / "artifacts" / "experience-acceptance-ab" / "v1"
PREVIOUS_V2 = ROOT / "artifacts" / "experience-acceptance-ab" / "v2"
FIXTURE = OUT / "fixtures" / "ret03-v2-encoded.md"
HISTORY_VERSIONS = ("v1", "v2", "v3", "v4", "v5-review", "v6-conditional")

MODEL = "MiniMax-M3.1-Flash-Preview"
PARAMS = {"temperature": 0, "max_tokens": 8192, "stream": False}
POOL = "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions"
POOL_STATUS = "http://127.0.0.1:8848/api/v1/pool/status"
COST_LIMIT = 10.0
DATASET_VERSION = "experience-acceptance-ab-v3.0.0"
MAX_FILES = 8
MAX_CHARS = 40_000
CALL_TIMEOUT = 240.0
MAX_CALLS = 2  # one execution + at most one retry

README_SEED = """# 交付仓库

本仓库用于核对执行者是否按派单真实创建或修改文件。仓库内容以派单为准。
"""

ZERO_WIDTH = {chr(0x200B), chr(0x200C), chr(0x200D), chr(0xFEFF), chr(0x2060)}
ASCII_TOKEN_RE = re.compile(r"^[\x20-\x7e]+$")
QUESTION_RE = re.compile(r"请确认|请提供|请澄清|需要澄清|无法确定|缺少[^。\n]{0,12}路径")


def _m(check_id: str, text: str, expected: dict[str, Any]) -> dict[str, Any]:
    return {"id": check_id, "kind": "mechanical", "text": text, "expected": expected}


RET03: dict[str, Any] = {
    "case_id": "RET-03",
    "defect": "old_banned_list",
    "conflict_kind": "banned_list",
    "conflict_item_tokens": ["禁词", "清单"],
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
    "new_values": ["草稿", "待定", "稍后补充"],
    "seeds": {"README.md": README_SEED},
    "checks": [
        _m("delivery_path_exists", "交付路径真实存在", {"path": "ops/acceptance-wording.md"}),
        _m("content_min_length", "可见正文非空且达到最低长度", {"path": "ops/acceptance-wording.md", "min_chars": 40}),
        _m(
            "banned_new_absent",
            "可见文本不得出现当前三个禁词",
            {"path": "ops/acceptance-wording.md", "tokens": ["草稿", "待定", "稍后补充"]},
        ),
        _m("old_values_absent", "可见文本不得出现旧禁词清单", {"path": "ops/acceptance-wording.md", "tokens": []}),
        _m("topic_token_present", "可见正文确实在写验收说明", {"path": "ops/acceptance-wording.md", "tokens": ["验收"]}),
        _m("diff_only_allowed", "git diff 只动允许路径", {"paths": ["ops/acceptance-wording.md"]}),
        _m("worktree_clean", "提交后工作区干净", {"porcelain": ""}),
    ],
}

RET02_META: dict[str, Any] = {
    "case_id": "RET-02",
    "conflict_kind": "count",
    "conflict_item_tokens": ["待补项", "数量", "条数"],
    "dispatch_value": "3 条",
    "acceptance_value": "2 条",
}

BYPASS_CASES: list[dict[str, Any]] = [
    {
        "name": "unicode_escape",
        "text": "# 验收说明\n\n正文：\\u0054\\u004F\\u0044\\u004F\n",
        "expect_hit": ["TODO"],
    },
    {
        "name": "zero_width_inside_word",
        "text": "# 验收说明\n\n正文：T\u200bODO\n",
        "expect_hit": ["TODO"],
    },
    {
        "name": "fullwidth_letters",
        "text": "# 验收说明\n\n正文：\uff34\uff2f\uff24\uff2f\n",
        "expect_hit": ["TODO"],
    },
    {
        "name": "clean_control",
        "text": "# 验收说明\n\n正文：本轮使用统一的未完成标记说法，并逐条自检。\n",
        "expect_hit": [],
    },
    {
        "name": "plain_code_block",
        "text": "# 验收说明\n\n```\nTODO\n```\n",
        "expect_hit": ["TODO"],
    },
    {
        "name": "entity_code_block_literal",
        "text": "# 验收说明\n\n```\n&#84;&#79;&#68;&#79;\n```\n",
        "expect_hit": [],
    },
]


# -----------------------------------------------------------------------------
# 渲染后可见文本与固定归一化
# -----------------------------------------------------------------------------


ENTITY_PASSES = 4


def decode_entities(text: str) -> str:
    """HTML 数字/命名实体解码；重复到稳定，最多 ENTITY_PASSES 轮。"""
    for _ in range(ENTITY_PASSES):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def decode_escapes(text: str) -> str:
    """按协议解码 \\uXXXX 与 \\xNN；只解一轮，不做二次反转义。"""

    def _u(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    def _x(match: re.Match[str]) -> str:
        return chr(int(match.group(1), 16))

    text = re.sub(r"\\u([0-9a-fA-F]{4})", _u, text)
    return re.sub(r"\\x([0-9a-fA-F]{2})", _x, text)


def _strip_tags(text: str) -> str:
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", "", text, flags=re.I)
    return re.sub(r"<[^>]*>", "", text)


def _visible_noncode(text: str) -> str:
    return decode_entities(_strip_tags(text))


def _code_literal(segment: str) -> str:
    """代码块/行内代码按渲染规则保持字面量：不解码实体、不剥标签。"""
    if segment.startswith("```"):
        inner = segment[3:]
        if "\n" in inner:
            inner = inner.split("\n", 1)[1]
        if inner.endswith("```"):
            inner = inner[:-3]
        return inner
    return segment.strip("`")


CODE_SPAN_RE = re.compile(r"(```[\s\S]*?```|`[^`\n]*`)")


def visible_text(text: str) -> str:
    """取渲染后可见文本，再按冻结顺序归一化。

    顺序：HTML 实体解码（非代码段） -> Unicode 转义解码（全段）
    -> 零宽字符剔除 -> NFKC -> casefold。
    例外：Markdown 代码块/行内代码在渲染器里显示字面量，因此不做实体解
    码与标签剥离；其中的普通禁词仍会命中（归一化照常应用）。
    """
    parts: list[str] = []
    cursor = 0
    for match in CODE_SPAN_RE.finditer(text):
        parts.append(_visible_noncode(text[cursor : match.start()]))
        parts.append(_code_literal(match.group(0)))
        cursor = match.end()
    parts.append(_visible_noncode(text[cursor:]))
    rendered = "".join(parts)
    rendered = decode_escapes(rendered)
    rendered = "".join(char for char in rendered if char not in ZERO_WIDTH)
    rendered = unicodedata.normalize("NFKC", rendered)
    return rendered.casefold()


def normalized_token(token: str) -> str:
    token = "".join(char for char in token if char not in ZERO_WIDTH)
    return unicodedata.normalize("NFKC", token).casefold()


def token_hit(visible: str, token: str) -> bool:
    normalized = normalized_token(token)
    if not normalized:
        return False
    if ASCII_TOKEN_RE.match(normalized):
        parts = r"\s*".join(re.escape(part) for part in normalized.split())
        return re.search(r"(?<![A-Za-z0-9_-])" + parts + r"(?![A-Za-z0-9_-])", visible) is not None
    return normalized in visible


def visible_hits(text: str, tokens: list[str]) -> list[str]:
    rendered = visible_text(text)
    return [token for token in tokens if token_hit(rendered, token)]


def visible_snippet(text: str, limit: int = 320) -> str:
    rendered = re.sub(r"\s+", " ", visible_text(text)).strip()
    return rendered[:limit]


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
        "conflict_item_tokens": case["conflict_item_tokens"],
        "expected_first_check": case["expected_first_check"],
        "requirement": case["requirement"],
        "dispatch": case["dispatch"],
        "path": case["path"],
        "old_values": list(case["old_values"]),
        "new_values": list(case["new_values"]),
        "seed_files": [{"path": path, "sha256": sha(content)} for path, content in case["seeds"].items()],
        "checks": checks,
        "fixture": {"path": str(FIXTURE.relative_to(ROOT)), "sha256": file_sha(FIXTURE), "must_fail": ["old_values_absent"]},
    }


def regression_spec() -> list[dict[str, Any]]:
    return [
        {"kind": "bypass", "name": item["name"], "text": item["text"], "expect_hit": item["expect_hit"]}
        for item in BYPASS_CASES
    ] + [
        {
            "kind": "conflict",
            "case": RET02_META,
            "structured": {
                "item": "待补项数量",
                "dispatch_value": RET02_META["dispatch_value"],
                "acceptance_value": RET02_META["acceptance_value"],
            },
            "must_flag": True,
        }
    ]


def spec_sha() -> str:
    return sha(
        json.dumps(
            {"case": case_spec(RET03), "regressions": regression_spec()},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


NORMALIZATION_RULE = (
    "渲染后可见文本：Markdown/HTML 先剥离注释、script/style 与标签并对非代码段解码 HTML 实体；"
    "代码块与行内代码保持字面量（渲染器如此显示），其中普通禁词仍参与匹配。"
    "归一化顺序：HTML 实体解码 -> \\uXXXX/\\xNN 解码 -> 零宽字符 U+200B-200D/U+FEFF/U+2060 剔除 "
    "-> NFKC -> casefold。不做同义改写替换；语义层要求单列 semantic_unverified。"
)

CONFLICT_RULE = (
    "conflicts 必须是数组，每项 {item, dispatch_value, acceptance_value}；item 命中该任务的冲突项词，"
    "且两个值归一化后非空且不相等，才记 flagged_conflict；不再用关键词命中。"
)


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
        '"conflicts":[{"item":"有分歧的检查项","dispatch_value":"派单要求的值",'
        '"acceptance_value":"当前验收口径的值"}],'
        '"question":"可选：仅当缺少必要信息无法交付时填写"}\n'
        "conflicts 只在派单与验收口径确有矛盾时填写，否则给空数组；content 里保留换行，JSON 中用 \\n 转义。\n"
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
        '"conflicts":[{"item":"有分歧的检查项","dispatch_value":"派单要求的值",'
        '"acceptance_value":"当前验收口径的值"}],'
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


def call_pool(prompt: str, case_id: str, attempt: int, token: str) -> dict[str, Any]:
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
                problem = validate_payload(payload)
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
# 机械验收（可见文本）
# -----------------------------------------------------------------------------


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

    for check in checks:
        check_id = check["id"]
        expected = check["expected"]
        path = expected.get("path") or case["path"]
        text = files.get(path) if path in files else None
        visible = visible_text(text or "")

        if check_id == "delivery_path_exists":
            passed = text is not None and bool(text.strip())
            add(check, passed, f"{path} {'存在' if passed else '不存在'}", f"{len(text or '')} 字符")
            continue

        if check_id == "content_min_length":
            minimum = int(expected["min_chars"])
            actual = len(re.sub(r"\s+", "", visible))
            add(check, actual >= minimum, f"{actual} 个可见非空白字符", f"{path}; 要求 >= {minimum}")
            continue

        if check_id == "banned_new_absent":
            found = [token for token in expected["tokens"] if token_hit(visible, token)]
            add(
                check,
                not found,
                found,
                f"{path} 渲染后可见文本 grep 新禁词",
                None if not found else "可见文本命中新禁词：" + "、".join(found),
            )
            continue

        if check_id == "old_values_absent":
            found = [token for token in expected["tokens"] if token_hit(visible, token)]
            add(
                check,
                not found,
                found,
                f"{path} 渲染后可见文本 grep 旧值",
                None if not found else "可见文本命中旧值：" + "、".join(found),
            )
            continue

        if check_id == "topic_token_present":
            missing = [token for token in expected["tokens"] if not token_hit(visible, token)]
            add(
                check,
                not missing,
                missing,
                f"{path} 渲染后可见文本 grep",
                None if not missing else "可见文本缺少关键词：" + "、".join(missing),
            )
            continue

        if check_id == "diff_only_allowed":
            changes = diff_paths(repo, base_sha, head_sha)
            changed = sorted({row["path"] for row in changes})
            allowed = sorted(expected["paths"])
            passed = changed == allowed
            add(
                check,
                passed,
                changes,
                f"git diff --name-status {base_sha[:8]}..{head_sha[:8]}",
                None if passed else f"git diff 变更路径为 {changed}，要求仅 {allowed}",
            )
            continue

        if check_id == "worktree_clean":
            porcelain = git(repo, "status", "--porcelain").stdout
            passed = porcelain.strip() == ""
            add(
                check,
                passed,
                porcelain.strip(),
                "git status --porcelain",
                None if passed else f"提交后工作区仍有改动：{porcelain.strip()[:200]}",
            )
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
# 结构化冲突与行为分类
# -----------------------------------------------------------------------------


CLASSIFICATION_RULES = {
    "flagged_conflict": "conflicts 数组中有项命中该任务冲突项词，且 dispatch_value 与 acceptance_value 归一化后非空且不同；算正确升级，不重试",
    "escalated": "缺少必要信息时请求澄清（question 字段或正文含「请确认/请提供/请澄清/需要澄清/无法确定/缺少…路径」），且未落盘任何文件",
    "copied_wrong": "交付直接采用被注入的缺陷（旧禁词清单或编码后的旧禁词）",
    "transferred": "交付满足当前验收口径（可见文本不含旧禁词与新禁词）",
    "missed": "既未采用缺陷、也未满足当前口径（如该有的文件或正文缺失）",
    "unjudgeable": "调用终态非 ok（截断/非法 JSON/超时等），没有完整交付可判",
}


def structured_conflicts(case: dict[str, Any], payload: dict[str, Any] | None) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    kind = case.get("conflict_kind")
    if not kind:
        return []
    entries = payload.get("conflicts")
    if not isinstance(entries, list):
        return []
    valid: list[dict[str, str]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("item") or "")
        dispatch_value = entry.get("dispatch_value")
        acceptance_value = entry.get("acceptance_value")
        if not name or dispatch_value is None or acceptance_value is None:
            continue
        if not any(token_hit(visible_text(name), token) for token in case.get("conflict_item_tokens", [])):
            continue
        left = visible_text(str(dispatch_value)).strip()
        right = visible_text(str(acceptance_value)).strip()
        if not left or not right or left == right:
            continue
        valid.append(
            {
                "item": name,
                "dispatch_value": str(dispatch_value),
                "acceptance_value": str(acceptance_value),
            }
        )
    return valid


def escalated(case: dict[str, Any], payload: dict[str, Any] | None, files: dict[str, str]) -> bool:
    if files:
        return False
    if not isinstance(payload, dict):
        return False
    text = " ".join(str(payload.get(key) or "") for key in ("question", "notes"))
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
    if structured_conflicts(case, payload):
        return "flagged_conflict"
    if escalated(case, payload, files):
        return "escalated"
    state = {check["check_id"]: check["pass"] for check in checks}
    path = case["path"]
    text = files.get(path, "")
    visible = visible_text(text)
    old_hits = [token for token in case["old_values"] if token_hit(visible, token)]
    new_hits = [token for token in case["new_values"] if token_hit(visible, token)]
    if old_hits:
        return "copied_wrong"
    if new_hits or not text.strip():
        return "missed"
    if state.get("old_values_absent") and state.get("banned_new_absent"):
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
    root = Path(tempfile.mkdtemp(prefix=f"experience-acceptance-v3-{case['case_id']}-a{attempt}-"))
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
        payload = call.get("parsed_payload") or {}
        outcome = {
            "case_id": case["case_id"],
            "attempt": attempt,
            "prompt_sha256": call["prompt_sha256"],
            "terminal_state": call["terminal_state"],
            "error": call.get("error"),
            "usage": call.get("usage"),
            "actual_model": call.get("actual_model"),
            "finish_reason": call.get("finish_reason"),
            "parsed_payload": payload,
            "structured_conflicts": structured_conflicts(case, payload),
            "committed_files": written,
            "rejected_paths": rejected,
            "base_sha": base_sha,
            "head_sha": head_sha,
            "diff": diff_paths(repo, base_sha, head_sha),
            "diff_stat": git(repo, "diff", "--stat", base_sha, head_sha).stdout.strip() if head_sha != base_sha else "",
            "files": files,
            "visible_snippet": visible_snippet(files.get(case["path"], "")),
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
# 静态回归（不花模型调用）
# -----------------------------------------------------------------------------


def fixture_regression() -> dict[str, Any]:
    content = FIXTURE.read_text(encoding="utf-8")
    root = Path(tempfile.mkdtemp(prefix="acceptance-v3-fixture-"))
    try:
        repo, base_sha = init_repo(RET03, root)
        target = repo / RET03["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", "fixture: v2 RET-03 encoded delivery")
        head = git(repo, "rev-parse", "HEAD").stdout.strip()
        checks = run_checks(RET03, repo, base_sha, head, snapshot(repo), [])
        failed = [check["check_id"] for check in checks if not check["pass"]]
        hits = [token for token in RET03["old_values"] if token_hit(visible_text(content), token)]
        return {
            "kind": "fixture",
            "name": "RET-03 v2 encoded delivery",
            "fixture_path": str(FIXTURE.relative_to(ROOT)),
            "fixture_sha256": file_sha(FIXTURE),
            "visible_snippet": visible_snippet(content, 500),
            "visible_hits": hits,
            "failed_checks": failed,
            "must_fail": ["old_values_absent"],
            "pass": "old_values_absent" in failed,
        }
    finally:
        force_rmtree(root)


def bypass_regressions() -> list[dict[str, Any]]:
    rows = []
    for item in BYPASS_CASES:
        rendered = visible_text(item["text"])
        hits = [token for token in RET03["old_values"] if token_hit(rendered, token)]
        expected = list(item["expect_hit"])
        rows.append(
            {
                "kind": "bypass",
                "name": item["name"],
                "text": item["text"],
                "visible_snippet": re.sub(r"\s+", " ", rendered).strip()[:220],
                "visible_hits": hits,
                "expect_hit": expected,
                "pass": hits == expected,
            }
        )
    return rows


def conflict_regressions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def run(name: str, payload: dict[str, Any], must_flag: bool) -> None:
        found = structured_conflicts(RET02_META, payload)
        flagged = bool(found)
        rows.append(
            {
                "kind": "conflict",
                "name": name,
                "payload": payload,
                "structured_conflicts": found,
                "must_flag": must_flag,
                "pass": flagged == must_flag,
            }
        )

    run(
        "structured_count_conflict",
        {"conflicts": [{"item": "待补项数量", "dispatch_value": "3 条", "acceptance_value": "2 条"}]},
        True,
    )
    run(
        "equal_values_not_conflict",
        {"conflicts": [{"item": "待补项数量", "dispatch_value": "2 条", "acceptance_value": "2 条"}]},
        False,
    )
    run(
        "keyword_only_not_accepted",
        {"conflict": "派单要求 3 条，验收要求 2 条，二者不一致。"},
        False,
    )
    run(
        "item_mismatch_not_conflict",
        {"conflicts": [{"item": "交付路径", "dispatch_value": "a.md", "acceptance_value": "b.md"}]},
        False,
    )
    return rows


def run_regressions() -> dict[str, Any]:
    fixture = fixture_regression()
    bypass = bypass_regressions()
    conflict = conflict_regressions()
    document = {
        "tested_at": now(),
        "fixture": fixture,
        "bypass": bypass,
        "conflict": conflict,
        "all_pass": bool(fixture["pass"]) and all(row["pass"] for row in bypass + conflict),
    }
    write_json(REGRESSIONS, document)
    return document


# -----------------------------------------------------------------------------
# 冻结 / 运行 / 汇总
# -----------------------------------------------------------------------------


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    protocol = {
        "dataset_version": DATASET_VERSION,
        "frozen_at": now(),
        "initial_freeze": {
            "script_sha256": file_sha(Path(__file__).resolve()),
            "check_spec_sha256": spec_sha(),
            "check_spec_definition": (
                "sha256 of canonical JSON of the RET-03 dispatch/checks/defects, "
                "the encoded fixture hash, the bypass cases and the RET-02 conflict regression"
            ),
        },
        "post_freeze_changes": [],
        "model": MODEL,
        "parameters": PARAMS,
        "channel": "real platform pool /pool/v1/chat/completions (no preflight)",
        "execution_contract": (
            "executor returns one JSON object with files[path, content] and optional conflicts[]; "
            "the runner writes real files and commits; acceptance reads only repository state"
        ),
        "normalization_rule": NORMALIZATION_RULE,
        "conflict_rule": CONFLICT_RULE,
        "classification_rules": CLASSIFICATION_RULES,
        "call_budget": {"executions": 1, "retries_max": 1, "max_total": MAX_CALLS},
        "retry_policy": "first mechanical failure and not flagged_conflict -> one retry with concrete gaps; no second retry",
        "cost_limit_cny_equivalent": COST_LIMIT,
        "acceptance_rule": "mechanical checks decide pass/fail; flagged_conflict is correct behavior without retry",
        "cases": [case_spec(RET03)],
        "regressions": regression_spec(),
        "reference_hashes": {
            "production_experience": file_sha(SOURCE),
            "history": {name: tree_sha(HISTORY / name) for name in HISTORY_VERSIONS},
            "previous_acceptance_v1": tree_sha(PREVIOUS_V1),
            "previous_acceptance_v2": tree_sha(PREVIOUS_V2),
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
        raise RuntimeError("期望值或回归夹具在冻结后被修改；必须按 bug 流程重新全量跑")
    return protocol


def archive_attempts(reason: str) -> None:
    archives = OUT / "attempts"
    archives.mkdir(parents=True, exist_ok=True)
    index = len(list(archives.glob("attempt-*"))) + 1
    target = archives / f"attempt-{index}"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("results.json", "summary.json", "environment.json", "regressions.json", "calls", "repos"):
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
            "check_spec_sha256": spec_sha(),
            "full_rerun_required": True,
        }
    )
    write_json(PROTOCOL, protocol)
    print(json.dumps({"refrozen": True, "old": old_script, "new": new_script}, ensure_ascii=False))


def run() -> None:
    protocol = verify_frozen()
    regressions = run_regressions()
    if not regressions["all_pass"]:
        raise RuntimeError("静态回归未通过，未发起任何模型调用；先修实现 bug 再按流程重冻")
    token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
    production_before = file_sha(SOURCE)
    history_before = {name: tree_sha(HISTORY / name) for name in HISTORY_VERSIONS}
    v1_before = tree_sha(PREVIOUS_V1)
    v2_before = tree_sha(PREVIOUS_V2)

    first = execute_attempt(RET03, 1, token, None)
    row: dict[str, Any] = {
        "case_id": RET03["case_id"],
        "first": first,
        "retry": None,
        "first_as_expected": RET03["expected_first_check"] in first["failed_checks"],
    }
    cost = _provider_cost(first)
    if first["classification"] == "flagged_conflict":
        row["final_classification"] = "flagged_conflict"
        row["correct_behavior"] = True
        row["final_pass"] = False
        row["retry_used"] = False
    elif first["mechanical_pass"]:
        row["final_classification"] = first["classification"]
        row["correct_behavior"] = True
        row["final_pass"] = True
        row["retry_used"] = False
    else:
        second = execute_attempt(RET03, 2, token, (first["failed_gaps"], first["files"]))
        cost += _provider_cost(second)
        row["retry"] = second
        row["retry_used"] = True
        row["new_problems"] = new_problems(first, second)
        row["fixed_checks"] = sorted(set(first["failed_checks"]) - set(second["failed_checks"]))
        row["final_classification"] = second["classification"]
        row["correct_behavior"] = second["mechanical_pass"] or second["classification"] == "flagged_conflict"
        row["final_pass"] = second["mechanical_pass"]
    results = [row]
    write_json(RESULTS, results)
    if cost > COST_LIMIT:
        raise RuntimeError(f"费用上限触发：{cost} > {COST_LIMIT}")

    production_after = file_sha(SOURCE)
    history_after = {name: tree_sha(HISTORY / name) for name in HISTORY_VERSIONS}
    v1_after = tree_sha(PREVIOUS_V1)
    v2_after = tree_sha(PREVIOUS_V2)
    write_json(
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
            "temporary_directories_removed": all(
                attempt["temporary_directory_removed"]
                for attempt in (row["first"], row["retry"])
                if attempt
            ),
            "calls_used": len(list((OUT / "calls").glob("*.json"))),
        },
    )
    print(json.dumps({"results": str(RESULTS), "regressions_all_pass": True}, ensure_ascii=False))


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
    if len(rows) != 1:
        raise RuntimeError(f"expected 1 task result, found {len(rows)}")
    row = rows[0]
    first = row["first"]
    second = row["retry"]
    final = second or first
    table = [
        {
            "case_id": row["case_id"],
            "first_pass": first["mechanical_pass"],
            "first_classification": first["classification"],
            "first_failed": first["failed_checks"],
            "first_gaps": first["failed_gaps"],
            "first_as_expected": row["first_as_expected"],
            "first_visible_snippet": first["visible_snippet"],
            "first_structured_conflicts": first["structured_conflicts"],
            "retry_used": row["retry_used"],
            "retry_classification": (second or {}).get("classification"),
            "retry_pass": (second or {}).get("mechanical_pass"),
            "retry_visible_snippet": (second or {}).get("visible_snippet"),
            "retry_structured_conflicts": (second or {}).get("structured_conflicts"),
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
    ]
    counts = {
        "first_pass": 0 if not table[0]["first_pass"] else 1,
        "first_expected_failure": bool(table[0]["first_as_expected"]),
        "retry_used": bool(table[0]["retry_used"]),
        "fixed_after_retry": bool(table[0]["retry_used"] and table[0]["final_pass"]),
        "flagged_conflict": table[0]["final_classification"] == "flagged_conflict",
        "still_failing": bool(not table[0]["final_pass"] and table[0]["final_classification"] != "flagged_conflict"),
        "new_problems": len(table[0]["new_problems"]),
    }
    regressions = read_json(REGRESSIONS, {})
    calls = sorted((OUT / "calls").glob("*.json"))
    receipts = [read_json(path, {}) for path in calls]
    summary = {
        "dataset_version": DATASET_VERSION,
        "model": protocol["model"],
        "parameters": protocol["parameters"],
        "normalization_rule": protocol["normalization_rule"],
        "conflict_rule": protocol["conflict_rule"],
        "counts": counts,
        "table": table,
        "regressions": {
            "all_pass": regressions.get("all_pass"),
            "fixture_failed_checks": (regressions.get("fixture") or {}).get("failed_checks"),
            "fixture_visible_hits": (regressions.get("fixture") or {}).get("visible_hits"),
            "bypass": [
                {"name": item["name"], "visible_hits": item["visible_hits"], "pass": item["pass"]}
                for item in regressions.get("bypass", [])
            ],
            "conflict": [
                {"name": item["name"], "must_flag": item["must_flag"], "pass": item["pass"]}
                for item in regressions.get("conflict", [])
            ],
        },
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
    print(json.dumps({key: summary[key] for key in ("counts", "regressions", "cost")}, ensure_ascii=False, indent=2))


# -----------------------------------------------------------------------------
# 无模型自检
# -----------------------------------------------------------------------------


def selftest() -> None:
    regressions = run_regressions()
    failures: list[str] = []
    if not regressions["fixture"]["pass"]:
        failures.append("RET-03 原始编码交付未被判不合格")
    for item in regressions["bypass"] + regressions["conflict"]:
        if not item["pass"]:
            failures.append(f"回归未通过：{item['name']}")
    if not regressions["all_pass"]:
        raise RuntimeError("自检失败：\n" + "\n".join(failures))
    print(
        json.dumps(
            {
                "selftest": "ok",
                "fixture_failed": regressions["fixture"]["failed_checks"],
                "fixture_visible_hits": regressions["fixture"]["visible_hits"],
                "bypass": len(regressions["bypass"]),
                "conflict": len(regressions["conflict"]),
            },
            ensure_ascii=False,
        )
    )


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
