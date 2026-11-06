"""v3 最小验证：环境错配轴。只跑 D 臂 ×3，目标失败率 1–2/3。

派单只在需求里写一次目标运行环境（Python -S / 仅标准库 / GBK 默认编码 /
中文+空格工作目录 / GBK 输入 / UTF-8 输出 / 校验和）。执行者沙箱与目标
环境不同：沙箱 UTF-8 默认编码、输入样例是 UTF-8、无 -S。于是"沙箱自测通过"
不等于"目标环境能跑"。

判分全部在目标环境真实执行：python -S tool/run.py，在中文+空格目录里，
GBK 输入、只读、out/ 可写；记录 exit code / stderr / output 是否存在 /
打印的 sha256 与文件实际字节是否一致。**不使用验收器 verdict**。
"""

from __future__ import annotations

import argparse
import ast
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "artifacts" / "experience-acceptance-effect" / "v3"
SCRIPTS = ROOT / "backend" / "scripts"
SOURCE = ROOT / "data" / "admin" / "experience.json"

MODEL = "MiniMax-M3.1-Flash-Preview"
PARAMS = {"temperature": 0, "max_tokens": 32768, "stream": False}
POOL = "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions"
COST_LIMIT = 10.0
MAX_CALLS = 3
CALL_TIMEOUT = 300.0
RUNS = 3

README_SEED = """# 工作区

本仓库用于存放交付物。本次交付统一放在 `tool/` 目录下。
"""

INPUT_ROWS = [("张三", "北京", 1280), ("李四", "上海", 960), ("王五", "深圳", 1500)]
INPUT_TEXT = "".join(f"{name}|{city}|{amount}\n" for name, city, amount in INPUT_ROWS)
EXPECTED_ROWS = [{"name": name, "city": city, "amount": amount} for name, city, amount in INPUT_ROWS]
EXPECTED_TOTAL = sum(amount for _, _, amount in INPUT_ROWS)
INPUT_NAME = "输入 数据.txt"
OUTPUT_REL = "out/结果.json"
FILES = ["tool/run.py", "tool/README.md"]
STDLIB = set(sys.stdlib_module_names) | {"__future__"}
DIGEST_RE = re.compile(r"sha256:([0-9a-f]{64})")

REQUIREMENT = """在仓库里交付一个单文件小工具 tool/run.py，并写 tool/README.md。

目标运行环境（这里写一次，交付必须在该环境成立）：
- Windows 10 x64；Python 3.13；启动命令 `python -S tool/run.py`（-S 表示不加载
  site-packages，只能用标准库，不能 pip 安装任何包）。
- 进程默认编码是 GBK（PYTHONUTF8=0），控制台可以正常显示中文。
- 工作目录是 `C:\\数据 目录\\工作区`（目录名同时含中文和空格）；其中的输入文件
  `输入 数据.txt` 为 GBK 编码、只读。
- 输出必须写到 `out/结果.json`，该文件为 UTF-8 编码；`out/` 已存在且可写。
- 没有网络。

工具行为：
1. 从工作目录读取 `输入 数据.txt`（GBK），每行 `姓名|城市|金额`，金额是十进制整数；
   空行忽略。
2. 生成 JSON 对象写入 `out/结果.json`：{"rows":[{"name","city","amount"}, ...],
   "total": <整数>, "count": <整数>}。rows 顺序与输入一致，total 为金额之和。
3. 在 stdout 打印一行 `sha256:<64 位小写十六进制>`，值必须是 `out/结果.json`
   实际字节的 SHA-256。
4. tool/run.py 只能 import 标准库，不得依赖任何第三方包，不得联网。
5. tool/README.md 不少于 120 个非空白字符，写清运行方式（命令原样写成
   `python -S tool/run.py`）和上面的目标环境约束。
"""


def _load_v1():
    spec = importlib.util.spec_from_file_location(
        "effect_v3_helpers", SCRIPTS / "evaluate_experience_acceptance_v1.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["effect_v3_helpers"] = module
    spec.loader.exec_module(module)
    return module


V1 = _load_v1()
V1.MAX_FILES = len(FILES)
sys.path.insert(0, str(ROOT / "backend"))
from app.acceptance.flow_gate import TOKENS, banned_hits, frozen_identity  # noqa: E402


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str:
    return sha(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def remove_tree(path: Path) -> bool:
    """Windows：git 对象只读，rmtree 会失败；清只读位后再退回 rmdir /s /q。"""
    def _onerror(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    for _ in range(3):
        shutil.rmtree(path, ignore_errors=True, onerror=_onerror)
        if not path.exists():
            return True
        subprocess.run(
            ["cmd", "/c", "rmdir", "/s", "/q", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if not path.exists():
            return True
        time.sleep(0.2)
    return not path.exists()


def dispatch_payload() -> dict[str, Any]:
    return {
        "task_id": "AE3-TOOL",
        "title": "TOOL · 小工具交付场景",
        "goal": REQUIREMENT,
        "exclusive_paths": list(FILES),
        "acceptance": {
            "enabled": False,
            "profile": "experience-v5",
            "description": "交付物存在且满足派单中的验收约束",
        },
        "harness_id": "qoder-member-1",
    }


def executor_prompt() -> str:
    return (
        "你是本仓库的执行者。请按下面的派单完成交付，在仓库中真实创建文件。\n\n"
        f"派单（JSON）：\n{json.dumps(dispatch_payload(), ensure_ascii=False, indent=2)}\n\n"
        "仓库现有文件（完整内容）：\n"
        f"--- 文件 README.md ---\n{README_SEED}\n\n"
        "写盘规则：只能写相对路径；不得写 .git 下的路径、绝对路径或包含 .. 的路径。\n"
        "单文件内容不超过 1200 个汉字，最多写 2 个文件。\n"
        "只输出一个 JSON 对象，不要输出 Markdown 代码块，不要解释。格式：\n"
        '{"files":[{"path":"相对路径","content":"完整文件内容"}],'
        '"commit_message":"提交说明"}\n'
        "content 里保留换行，JSON 中用 \\n 转义。\n"
    )


# -----------------------------------------------------------------------------
# 独立判据（先冻结、后跑）
# -----------------------------------------------------------------------------


def rule_definitions() -> dict[str, Any]:
    return {
        "deliverables_exist": {"kind": "mechanical", "expected": "tool/run.py 与 tool/README.md 存在且非空"},
        "readme_command": {"kind": "mechanical", "expected": "README 含 `python -S tool/run.py`，去空白 >= 120 字符"},
        "stdlib_only": {"kind": "mechanical", "expected": "tool/run.py 的 import 全部来自标准库"},
        "target_exit_zero": {"kind": "target-run", "expected": "目标环境 python -S tool/run.py 退出码 0"},
        "output_file_exists": {"kind": "target-run", "expected": "out/结果.json 在目标环境生成且非空"},
        "output_format": {"kind": "target-run", "expected": "out/结果.json 为 UTF-8，rows 顺序/字段/total/count 与输入一致"},
        "digest_matches": {"kind": "target-run", "expected": "stdout 的 sha256 等于 out/结果.json 实际字节的 SHA-256"},
        "banned_hits_absent": {
            "kind": "frozen-v5",
            "source": "flow_gate.banned_hits（可见文本 + 字母数字骨架）",
            "tokens": list(TOKENS),
            "expected": "2 个产物里任一命中即不合格",
        },
    }


def ast_imports(content: str) -> set[str]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def check_output(target: dict[str, Any]) -> tuple[bool, str]:
    data = target.get("output_bytes")
    if not data:
        return False, "out/结果.json 不存在或为空"
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False, f"out/结果.json 不是 UTF-8（实际字节前 8 位 {data[:8]!r}，大小 {len(data)}）"
    try:
        doc = json.loads(text)
    except Exception as exc:  # noqa: BLE001
        return False, f"JSON 解析失败：{type(exc).__name__}: {exc}"
    if not isinstance(doc, dict):
        return False, "输出不是 JSON 对象"
    rows = doc.get("rows")
    if not isinstance(rows, list):
        return False, "rows 不是数组"
    problems = []
    if len(rows) != len(EXPECTED_ROWS):
        problems.append(f"rows 数量 {len(rows)} != {len(EXPECTED_ROWS)}")
    for index, expected in enumerate(EXPECTED_ROWS):
        if index >= len(rows):
            break
        actual = rows[index]
        if not isinstance(actual, dict):
            problems.append(f"rows[{index}] 不是对象")
            continue
        if {key: actual.get(key) for key in expected} != expected:
            problems.append(f"rows[{index}]={ {key: actual.get(key) for key in expected} } != {expected}")
    if doc.get("count") != len(EXPECTED_ROWS):
        problems.append(f"count={doc.get('count')!r} != {len(EXPECTED_ROWS)}")
    if doc.get("total") != EXPECTED_TOTAL:
        problems.append(f"total={doc.get('total')!r} != {EXPECTED_TOTAL}")
    return (not problems), ("；".join(problems) if problems else "UTF-8 JSON，3 行顺序与金额/合计一致")


def check_digest(target: dict[str, Any]) -> tuple[bool, str]:
    data = target.get("output_bytes")
    printed = target.get("printed_digest")
    if not data:
        return False, "无 output 文件可比对"
    actual = sha256_bytes(data)
    if not printed:
        return False, f"stdout 没有 sha256:<64hex>；实际 {actual}"
    if printed != actual:
        return False, f"打印 {printed} != 实际 {actual}"
    return True, f"打印值与文件字节一致：{actual}"


CATEGORY_MARKERS = [
    ("依赖缺失", ("ModuleNotFoundError", "ImportError", "No module named")),
    ("权限", ("PermissionError", "Access is denied", "WinError 5")),
    ("路径", ("FileNotFoundError", "No such file or directory", "WinError 2", "WinError 3")),
    ("编码", ("UnicodeDecodeError", "UnicodeEncodeError", "codec can't")),
]


def failure_category(failed_rules: list[str], target: dict[str, Any]) -> str:
    rules = set(failed_rules)
    if "stdlib_only" in rules:
        return "依赖缺失"
    stderr = str(target.get("stderr") or "")
    for name, marks in CATEGORY_MARKERS:
        if any(mark in stderr for mark in marks):
            return name
    data = target.get("output_bytes")
    if data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "编码"
    if "output_format" in rules:
        return "输出格式"
    if "digest_matches" in rules:
        return "校验和对不上"
    if "target_exit_zero" in rules or "output_file_exists" in rules:
        return "路径"
    return "交付缺失"


def predictability(failed_rules: list[str], sandbox: dict[str, Any]) -> str:
    rules = set(failed_rules)
    if rules & {"deliverables_exist", "readme_command", "stdlib_only"}:
        return "漏读（写清了却漏读）"
    return "自查不可能（环境限制）" if sandbox.get("pass") else "漏读（写清了却漏读）"


def score_delivery(
    files: dict[str, str], target: dict[str, Any], sandbox: dict[str, Any]
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    missing = [path for path in FILES if not str(files.get(path) or "").strip()]
    items.append(
        {
            "rule": "deliverables_exist",
            "pass": not missing,
            "detail": "缺失或为空：" + (", ".join(missing) if missing else "无"),
        }
    )

    readme = str(files.get("tool/README.md") or "")
    chars = len(re.sub(r"\s", "", readme))
    has_cmd = "python -S tool/run.py" in readme
    items.append(
        {
            "rule": "readme_command",
            "pass": has_cmd and chars >= 120,
            "detail": f"含命令 {has_cmd}；去空白 {chars} 字（阈值 120）",
        }
    )

    imported = ast_imports(str(files.get("tool/run.py") or ""))
    extra = sorted(name for name in imported if name not in STDLIB)
    items.append(
        {
            "rule": "stdlib_only",
            "pass": not extra,
            "detail": ("非标准库 import：" + ", ".join(extra)) if extra else f"仅标准库：{', '.join(sorted(imported))}",
        }
    )

    items.append(
        {
            "rule": "target_exit_zero",
            "pass": target.get("returncode") == 0,
            "detail": f"exit={target.get('returncode')}；stderr 尾部={str(target.get('stderr') or '')[-300:]!r}",
        }
    )
    items.append(
        {
            "rule": "output_file_exists",
            "pass": bool(target.get("output_exists")),
            "detail": f"out/结果.json 存在={bool(target.get('output_exists'))} 大小={target.get('output_size')}",
        }
    )

    fmt_ok, fmt_detail = check_output(target)
    items.append({"rule": "output_format", "pass": fmt_ok, "detail": fmt_detail})

    digest_ok, digest_detail = check_digest(target)
    items.append({"rule": "digest_matches", "pass": digest_ok, "detail": digest_detail})

    hits: list[str] = []
    for path in FILES:
        hits.extend(f"{path}:{token}" for token in banned_hits(str(files.get(path) or ""), TOKENS))
    items.append(
        {
            "rule": "banned_hits_absent",
            "pass": not hits,
            "detail": ("命中：" + ", ".join(hits)) if hits else "无命中",
        }
    )

    failed = [item["rule"] for item in items if not item["pass"]]
    result: dict[str, Any] = {"items": items, "failed_rules": failed, "fail": bool(failed)}
    if failed:
        result["failure_category"] = failure_category(failed, target)
        result["predictability"] = predictability(failed, sandbox)
    return result


# -----------------------------------------------------------------------------
# 双环境执行
# -----------------------------------------------------------------------------


def _write_tree(work: Path, files: dict[str, str], input_bytes: bytes, read_only: bool) -> None:
    for rel, content in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
    (work / "out").mkdir(parents=True, exist_ok=True)
    source = work / INPUT_NAME
    source.write_bytes(input_bytes)
    if read_only:
        os.chmod(source, stat.S_IREAD)


def _run_tool(work: Path, *, isolated: bool, utf8_mode: bool, tag: str) -> dict[str, Any]:
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONIOENCODING"):
        env.pop(key, None)
    env["PYTHONUTF8"] = "1" if utf8_mode else "0"
    argv = [sys.executable] + (["-S"] if isolated else []) + ["tool/run.py"]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=work,
            capture_output=True,
            timeout=60,
            env=env,
        )
        stdout_raw = proc.stdout
        stderr_raw = proc.stderr
        code: int | None = proc.returncode
    except subprocess.TimeoutExpired as exc:
        stdout_raw = exc.stdout or b""
        stderr_raw = exc.stderr or b""
        code = None
    stdout = stdout_raw.decode("utf-8", errors="replace")
    stderr = stderr_raw.decode("utf-8", errors="replace")
    out_path = work / OUTPUT_REL
    exists = out_path.is_file() and out_path.stat().st_size > 0
    data = out_path.read_bytes() if exists else None
    match = DIGEST_RE.search(stdout)
    return {
        "tag": tag,
        "argv": argv[1:],
        "cwd": str(work),
        "env": {"PYTHONUTF8": env["PYTHONUTF8"], "isolated": isolated},
        "returncode": code,
        "stdout": stdout[-2000:],
        "stderr": stderr[-3000:],
        "output_exists": exists,
        "output_size": len(data) if data else 0,
        "output_bytes": data,
        "printed_digest": match.group(1) if match else None,
        "duration_seconds": round(time.monotonic() - started, 3),
    }


def run_sandbox(root: Path, files: dict[str, str]) -> dict[str, Any]:
    """执行者沙箱：UTF-8 默认编码、输入样例是 UTF-8、无 -S。"""
    work = root / "sandbox"
    work.mkdir(parents=True, exist_ok=True)
    _write_tree(work, files, INPUT_TEXT.encode("utf-8"), read_only=False)
    record = _run_tool(work, isolated=False, utf8_mode=True, tag="sandbox")
    needs = bool(record.get("output_bytes"))
    ok = record["returncode"] == 0 and needs
    if ok:
        try:
            json.loads((work / OUTPUT_REL).read_bytes().decode("utf-8"))
        except Exception:  # noqa: BLE001
            ok = False
    record["output_parsed"] = needs
    record["pass"] = ok
    return record


def run_target(root: Path, files: dict[str, str]) -> dict[str, Any]:
    """目标环境：中文+空格目录、GBK 输入（只读）、-S、PYTHONUTF8=0。"""
    work = root / "数据 目录" / "工作区"
    work.mkdir(parents=True, exist_ok=True)
    _write_tree(work, files, INPUT_TEXT.encode("gbk"), read_only=True)
    return _run_tool(work, isolated=True, utf8_mode=False, tag="target")


# -----------------------------------------------------------------------------
# 模型调用与运行
# -----------------------------------------------------------------------------


def call_pool(prompt: str, tag: str, token: str) -> dict[str, Any]:
    record: dict[str, Any] = {
        "tag": tag,
        "started_at": now(),
        "requested_model": MODEL,
        "prompt_sha256": sha(prompt),
    }
    started = time.monotonic()
    try:
        response = httpx.post(
            POOL,
            headers={"Authorization": f"Bearer {token}"},
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], **PARAMS},
            timeout=CALL_TIMEOUT,
        )
        document = response.json()
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
                "content": content,
            }
        )
        if response.status_code >= 400:
            record["terminal_state"] = "http_error"
        elif record["finish_reason"] == "length":
            record["terminal_state"] = "truncated"
        else:
            try:
                payload = V1.extract_json(content)
                problem = V1.validate_payload(payload)
                record["parsed_payload"] = payload
                record["terminal_state"] = "invalid_schema" if problem else "ok"
                if problem:
                    record["error"] = problem
            except ValueError as exc:
                record["terminal_state"] = "invalid_json"
                record["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        record["terminal_state"] = "network_error"
        record["error"] = f"{type(exc).__name__}: {exc}"
    record["duration_seconds"] = round(time.monotonic() - started, 3)
    return record


def init_repo(root: Path) -> tuple[Path, str]:
    repo = root / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    V1.git(repo, "init", "-b", "main")
    V1.git(repo, "config", "user.name", "ae3-executor")
    V1.git(repo, "config", "user.email", "ae3@local")
    V1.git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text(README_SEED, encoding="utf-8", newline="\n")
    V1.git(repo, "add", "-A")
    V1.git(repo, "commit", "-m", "seed")
    return repo, V1.git(repo, "rev-parse", "HEAD").stdout.strip()


def run_once(index: int, token: str, out_dir: Path) -> dict[str, Any]:
    tag = f"D-r{index}"
    root = Path(tempfile.mkdtemp(prefix=f"ae3-{tag}-"))
    row: dict[str, Any] = {"run": index, "template": "TOOL", "arm": "D"}
    try:
        repo, base_sha = init_repo(root)
        call = call_pool(executor_prompt(), tag, token)
        write_json(out_dir / "calls" / f"{tag}.json", call)
        if call["terminal_state"] == "ok":
            head_sha, written, rejected = V1.apply_delivery(repo, call["parsed_payload"])
        else:
            head_sha, written, rejected = base_sha, [], []
        files = V1.snapshot(repo)

        sandbox = run_sandbox(root, files)
        target = run_target(root, files)
        score = score_delivery(files, target, sandbox)

        row.update(
            {
                "prompt_sha256": call["prompt_sha256"],
                "terminal_state": call["terminal_state"],
                "finish_reason": call.get("finish_reason"),
                "actual_model": call.get("actual_model"),
                "usage": call.get("usage"),
                "committed_files": written,
                "rejected_paths": rejected,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "files": files,
                "sandbox": _evidence(sandbox, root / "sandbox"),
                "target": _evidence(target, root / "数据 目录" / "工作区"),
                "score": score,
            }
        )
        store = out_dir / "repos" / tag
        store.mkdir(parents=True, exist_ok=True)
        (store / "files.json").write_text(
            json.dumps(files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        patch = V1.git(repo, "diff", base_sha, head_sha).stdout if head_sha != base_sha else ""
        (store / "diff.patch").write_text(patch, encoding="utf-8")
        if target.get("output_bytes"):
            (store / "target-output.bin").write_bytes(target["output_bytes"])
    finally:
        row["temporary_directory_removed"] = remove_tree(root)
    return row


def _evidence(record: dict[str, Any], work: Path) -> dict[str, Any]:
    data = record.pop("output_bytes", None)
    keep = {key: value for key, value in record.items() if key not in {"output_bytes"}}
    keep["output_sha256"] = sha256_bytes(data) if data else None
    keep["output_preview"] = (
        data.decode("utf-8", errors="replace")[:400] if data else None
    )
    keep["input_kind"] = "gbk" if "数据 目录" in str(work) else "utf-8"
    keep["input_readonly"] = bool(
        (work / INPUT_NAME).exists() and not os.access(work / INPUT_NAME, os.W_OK)
    )
    return keep


def protocol_path(out_dir: Path) -> Path:
    return out_dir / "protocol.json"


def template_spec() -> dict[str, Any]:
    return {
        "template": "TOOL",
        "files": FILES,
        "requirements": REQUIREMENT,
        "input_rows": INPUT_ROWS,
        "input_sha256_gbk": sha(INPUT_TEXT),
        "seed_files": [{"path": "README.md", "sha256": sha(README_SEED)}],
        "sandbox_environment": {
            "python": "venv 解释器，无 -S",
            "default_encoding": "UTF-8（PYTHONUTF8=1）",
            "fixture_encoding": "UTF-8",
            "cwd": "纯 ASCII 临时目录",
            "input_readonly": False,
        },
        "target_environment": {
            "python": "同一解释器，-S（无 site-packages，仅标准库）",
            "default_encoding": "GBK（PYTHONUTF8=0）",
            "fixture_encoding": "GBK",
            "cwd": "数据 目录/工作区（中文 + 空格）",
            "input_readonly": True,
            "output_encoding": "UTF-8",
        },
        "dispatch": dispatch_payload(),
        "prompt_sha256": sha(executor_prompt()),
    }


def templates_sha() -> str:
    return sha(json.dumps(template_spec(), ensure_ascii=False, sort_keys=True))


def scoring_sha() -> str:
    return sha(
        json.dumps(
            {
                "rules": rule_definitions(),
                "tokens": list(TOKENS),
                "files": FILES,
                "expected_rows": EXPECTED_ROWS,
                "expected_total": EXPECTED_TOTAL,
                "categories": [name for name, _ in CATEGORY_MARKERS],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def freeze(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    protocol = {
        "dataset_version": "experience-acceptance-effect-v3.0.0",
        "frozen_at": now(),
        "initial_freeze": {
            "script_sha256": file_sha(Path(__file__).resolve()),
            "templates_sha256": templates_sha(),
            "scoring_sha256": scoring_sha(),
        },
        "post_freeze_changes": [],
        "model": MODEL,
        "parameters": PARAMS,
        "arm": "D only（acceptance.enabled=false，单轮，无退回）",
        "runs": RUNS,
        "scoring_rules": rule_definitions(),
        "frozen_identity": frozen_identity(),
        "templates": template_spec(),
        "reference_hashes": {"production_experience": file_sha(SOURCE)},
    }
    write_json(protocol_path(out_dir), protocol)
    print(
        json.dumps(
            {
                "dir": str(out_dir),
                "frozen_at": protocol["frozen_at"],
                "script_sha256": protocol["initial_freeze"]["script_sha256"],
                "templates_sha256": protocol["initial_freeze"]["templates_sha256"],
                "scoring_sha256": protocol["initial_freeze"]["scoring_sha256"],
            },
            ensure_ascii=False,
        )
    )


def verify_frozen(out_dir: Path) -> dict[str, Any]:
    protocol = read_json(protocol_path(out_dir), None)
    if not protocol:
        raise RuntimeError("先 freeze")
    allowed = [protocol["initial_freeze"]["script_sha256"]] + [
        entry["new_script_sha256"] for entry in protocol.get("post_freeze_changes", [])
    ]
    if file_sha(Path(__file__).resolve()) not in allowed:
        raise RuntimeError("脚本冻结后被改")
    if templates_sha() != protocol["initial_freeze"]["templates_sha256"]:
        raise RuntimeError("模板集冻结后被改")
    if scoring_sha() != protocol["initial_freeze"]["scoring_sha256"]:
        raise RuntimeError("判据冻结后被改")
    if frozen_identity() != protocol["frozen_identity"]:
        raise RuntimeError("v5 三件套身份变了")
    return protocol


def amend(out_dir: Path, reason: str) -> None:
    """post-freeze 实现 bug 的唯一入口：留痕 + 全量重跑。"""
    protocol = read_json(protocol_path(out_dir), None)
    if not protocol:
        raise RuntimeError("先 freeze")
    current = file_sha(Path(__file__).resolve())
    previous = (
        protocol["post_freeze_changes"][-1]["new_script_sha256"]
        if protocol.get("post_freeze_changes")
        else protocol["initial_freeze"]["script_sha256"]
    )
    if current == previous:
        raise RuntimeError("脚本没有变化，无需 amend")
    protocol.setdefault("post_freeze_changes", []).append(
        {
            "changed_at": now(),
            "reason": reason,
            "old_script_sha256": previous,
            "new_script_sha256": current,
            "scope": "只改运行参数/实现 bug；模板集与判据 SHA 未变；必须全量重跑",
            "parameters_after": PARAMS,
        }
    )
    protocol["parameters"] = PARAMS
    write_json(protocol_path(out_dir), protocol)
    print(json.dumps(protocol["post_freeze_changes"][-1], ensure_ascii=False))


def run(out_dir: Path) -> None:
    verify_frozen(out_dir)
    token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
    before = file_sha(SOURCE)
    rows: list[dict[str, Any]] = []
    for index in range(1, RUNS + 1):
        row = run_once(index, token, out_dir)
        rows.append(row)
        write_json(out_dir / "rows.json", rows)
        print(
            json.dumps(
                {
                    "run": index,
                    "terminal_state": row["terminal_state"],
                    "fail": row["score"]["fail"],
                    "failed_rules": row["score"]["failed_rules"],
                    "category": row["score"].get("failure_category"),
                    "predictability": row["score"].get("predictability"),
                    "usage": row.get("usage"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    tally = {"漏读（写清了却漏读）": 0, "自查不可能（环境限制）": 0, "判据自身过严": 0}
    for row in rows:
        label = row["score"].get("predictability")
        if label in tally:
            tally[label] += 1
    summary = {
        "frozen_at": read_json(protocol_path(out_dir), {})["frozen_at"],
        "model": MODEL,
        "runs": len(rows),
        "fail_count": sum(1 for row in rows if row["score"]["fail"]),
        "ok_count": sum(1 for row in rows if not row["score"]["fail"]),
        "fail_rules": [row["score"]["failed_rules"] for row in rows],
        "categories": [row["score"].get("failure_category") for row in rows],
        "predictability_tally": tally,
        "usage": {
            "calls": len(rows),
            "prompt_tokens": sum(int((row.get("usage") or {}).get("prompt_tokens") or 0) for row in rows),
            "completion_tokens": sum(
                int((row.get("usage") or {}).get("completion_tokens") or 0) for row in rows
            ),
            "total_tokens": sum(int((row.get("usage") or {}).get("total_tokens") or 0) for row in rows),
        },
        "cleanup": {
            "temporary_directories_removed": all(row["temporary_directory_removed"] for row in rows),
            "leftover_dirs": len(list(Path(tempfile.gettempdir()).glob("ae3-*"))),
        },
        "production_experience": {
            "before": before,
            "after": file_sha(SOURCE),
            "unchanged": before == file_sha(SOURCE),
        },
        "frozen": read_json(protocol_path(out_dir), {})["initial_freeze"],
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# -----------------------------------------------------------------------------
# 自检（无模型）：真跑两个环境
# -----------------------------------------------------------------------------

GOOD_RUN = """import hashlib
import json
import pathlib

base = pathlib.Path(__file__).resolve().parents[1]
rows = []
for line in (base / "输入 数据.txt").read_text(encoding="gbk").splitlines():
    if not line.strip():
        continue
    name, city, amount = line.split("|")
    rows.append({"name": name, "city": city, "amount": int(amount)})
doc = {"rows": rows, "total": sum(row["amount"] for row in rows), "count": len(rows)}
out = base / "out" / "结果.json"
out.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print("sha256:" + hashlib.sha256(out.read_bytes()).hexdigest())
"""

GOOD_README = """# 工具说明

运行方式：把工作目录切到 `C:\\数据 目录\\工作区`，执行 `python -S tool/run.py`。

目标环境约束：Python 3.13，`-S` 启动，仅有标准库，没有第三方包；进程默认编码
是 GBK，输入文件 `输入 数据.txt` 为 GBK 编码且只读；输出写入 `out/结果.json`，
该文件必须是 UTF-8 编码；程序不联网。

工具会按行解析 `姓名|城市|金额`，生成 rows/total/count 三个字段的 JSON，
并在标准输出打印结果文件字节的 sha256 校验和，便于交接时核对。
"""

BAD_RUN_NON_STDLIB = """import yaml
print("ok")
"""


def selftest(out_dir: Path) -> None:
    files = {"README.md": README_SEED, "tool/run.py": GOOD_RUN, "tool/README.md": GOOD_README}
    root = Path(tempfile.mkdtemp(prefix="ae3-selftest-"))
    cases: list[tuple[str, str]] = []
    try:
        sandbox = run_sandbox(root, files)
        target = run_target(root, files)
        score = score_delivery(files, target, sandbox)
        if score["fail"]:
            raise AssertionError(f"合格样例不应失败：{score['failed_rules']} {score['items']}")
        if not sandbox["pass"]:
            # 沙箱是 UTF-8：显式按 GBK 读的合格样例在沙箱里必然读崩，这是设计内的不对称
            print("info: 合格样例在沙箱自测未通过（UTF-8 fixture vs GBK 读取），符合环境错配设计")
        cases.append(("合格样例", "通过"))

        base = dict(target)
        fake = {
            "依赖缺失": dict(base, returncode=1, stderr="ModuleNotFoundError: No module named 'yaml'"),
            "编码": dict(base, returncode=0, output_bytes="中文".encode("gbk"), output_exists=True),
            "校验和对不上": dict(base, returncode=0, printed_digest="0" * 64),
            "输出格式": dict(base, returncode=0, output_bytes=b'{"rows":[],"total":0,"count":0}'),
            "路径": dict(base, returncode=1, output_bytes=None, output_exists=False,
                        stderr="FileNotFoundError: [Errno 2] No such file or directory: '输入 数据.txt'"),
            "权限": dict(base, returncode=1, output_bytes=None, output_exists=False,
                        stderr="PermissionError: [Errno 13] Permission denied: 'out/结果.json'"),
        }
        for name, target_case in fake.items():
            scored = score_delivery(files, target_case, sandbox)
            got = scored.get("failure_category")
            if got != name:
                raise AssertionError(f"坏例 {name} 被归为 {got}：{scored['failed_rules']}")
            cases.append((name, got))

        static = score_delivery({**files, "tool/run.py": BAD_RUN_NON_STDLIB}, target, sandbox)
        if "stdlib_only" not in static["failed_rules"] or static.get("failure_category") != "依赖缺失":
            raise AssertionError(f"非标准库样例判定错误：{static}")
        cases.append(("非标准库 import", "依赖缺失"))

        no_readme = score_delivery({"README.md": README_SEED, "tool/run.py": GOOD_RUN}, target, sandbox)
        if "readme_command" not in no_readme["failed_rules"]:
            raise AssertionError(f"缺 README 未被判失败：{no_readme['failed_rules']}")
        cases.append(("缺 README", "交付缺失"))
    finally:
        removed = remove_tree(root)
    assert removed, "自检临时目录未清理"
    print(json.dumps({"selftest": "ok", "cases": cases, "cleanup": True}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["selftest", "freeze", "amend", "run"])
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    out_dir = OUT_ROOT / "rev1"
    if args.command == "selftest":
        selftest(out_dir)
    elif args.command == "freeze":
        freeze(out_dir)
    elif args.command == "amend":
        if not args.reason:
            raise SystemExit("amend 需要 --reason")
        amend(out_dir, args.reason)
    else:
        run(out_dir)


if __name__ == "__main__":
    main()
