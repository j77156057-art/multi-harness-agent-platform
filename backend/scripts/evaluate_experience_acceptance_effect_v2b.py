"""v2 最小验证：只跑 D 臂 ×3，回答"能不能稳定造出可定位的失败"。

单模板、长任务：6 个产物、跨步骤（条目 -> 清单 -> 校验脚本 -> 说明）、
跨文件引用（清单里的路径必须在磁盘上真实存在，条目里的 artifact 必须指向
清单列出的文件）。判分用冻结的独立判据（v5 banned_hits + 机械检查 + 真跑
校验脚本），**不使用验收器 verdict**。

Windows 清理：先 rmtree 清只读位，删不掉就退回 `cmd /c rmdir /s /q`，
最后逐个复查，残留必须为 0。
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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT_ROOT = ROOT / "artifacts" / "experience-acceptance-effect" / "v2"
SCRIPTS = ROOT / "backend" / "scripts"
SOURCE = ROOT / "data" / "admin" / "experience.json"

MODEL = "MiniMax-M3.1-Flash-Preview"
PARAMS = {"temperature": 0, "max_tokens": 32768, "stream": False}
POOL = "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions"
COST_LIMIT = 10.0
MAX_CALLS = 3
CALL_TIMEOUT = 300.0
RUNS = 3

README_SEED = """# 交接仓库

本仓库用于存放交接材料。本次交付统一放在 `handoff/` 目录下。
"""

FILES = [
    "handoff/manifest.json",
    "handoff/entries/E1.json",
    "handoff/entries/E2.json",
    "handoff/entries/E3.json",
    "handoff/entries/E4.json",
    "handoff/summary.json",
    "handoff/check.py",
    "handoff/README.md",
]
NON_MANIFEST = [path for path in FILES if path != "handoff/manifest.json"]

REQUIREMENT = """在仓库的 handoff/ 目录下交付一个可运行的交接包，恰好 8 个文件：
handoff/manifest.json、handoff/entries/E1.json、handoff/entries/E2.json、
handoff/entries/E3.json、handoff/entries/E4.json、handoff/summary.json、
handoff/check.py、handoff/README.md。

结构要求：
1. manifest.json 为 JSON 对象：{"version": 2, "artifacts": [...], "entry_count": 4}。
   artifacts 必须恰好列出除 manifest.json 之外的 7 个文件（相对仓库根的路径），不多不少。
2. 四个条目文件各为 JSON 对象：
   {"id": "E1"/"E2"/"E3"/"E4", "artifact": "<对应条目文件的相对路径>",
    "predecessor": <前一环节产物的相对路径，E1 为 null>,
    "depends_on": [...], "summary": "<一句话说明>"}。
   E1 的 depends_on 为空数组，predecessor 为 null；
   E2/E3/E4 的 depends_on 依次为 ["E1"]/["E2"]/["E3"]，
   predecessor 依次指向 handoff/entries/E1.json、E2.json、E3.json；
   artifact 必须指向 manifest.json 的 artifacts 里列出的真实文件。
3. summary.json 为 JSON 对象：{"entry_count": 4, "artifact_count": 7,
   "chain": ["E1","E2","E3","E4"], "checker": "handoff/check.py",
   "readme": "handoff/README.md"}。里面的数量必须与 manifest.json 和磁盘上的实际文件一致。
4. check.py 只用标准库：从仓库根目录执行 `python handoff/check.py` 时，
   校验文件是否存在、manifest 的条目数、四个条目的依赖链与 predecessor、
   summary.json 里的数量是否与实际一致；全部通过时打印 OK 并以 0 退出，
   否则打印不一致项并以 1 退出。
5. README.md 不少于 120 个非空白字符，写清运行方式与产物清单（清单要体现 8 个文件），
   运行命令必须原样写成 `python handoff/check.py`。
"""


def _load_v1():
    spec = importlib.util.spec_from_file_location(
        "effect_v2_helpers", SCRIPTS / "evaluate_experience_acceptance_v1.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["effect_v2_helpers"] = module
    spec.loader.exec_module(module)
    return module


V1 = _load_v1()
sys.path.insert(0, str(ROOT / "backend"))
from app.acceptance.flow_gate import (  # noqa: E402
    TOKENS,
    banned_hits,
    frozen_identity,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        "task_id": "AE2-HANDOFF",
        "title": "HANDOFF · 交接包场景",
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
        "单文件内容不超过 1200 个汉字，最多写 8 个文件。\n"
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
        "all_files_exist": {"kind": "mechanical", "expected": "8 个产物文件都存在且非空"},
        "manifest_artifacts": {
            "kind": "mechanical",
            "expected": "manifest.json 可解析，artifacts 恰好列出另外 7 个真实文件",
        },
        "entries_chain": {
            "kind": "mechanical",
            "expected": "E1..E4 的 id/artifact/predecessor/depends_on 与四段链一致，artifact 均真实存在",
        },
        "summary_consistent": {
            "kind": "mechanical",
            "expected": "summary.json 的 entry_count=4、artifact_count=7、chain=E1..E4 与实际一致",
        },
        "checker_runs": {
            "kind": "mechanical",
            "expected": "在仓库根执行 python handoff/check.py，30s 内退出码 0 且 stdout 含 OK",
        },
        "readme_command": {
            "kind": "mechanical",
            "expected": "README 含 `python handoff/check.py`，且去空白后 >= 120 字符",
        },
        "banned_hits_absent": {
            "kind": "frozen-v5",
            "source": "flow_gate.banned_hits（可见文本 + 字母数字骨架）",
            "tokens": list(TOKENS),
            "expected": "6 个产物里任一命中即不合格",
        },
    }


def score_delivery(files: dict[str, str]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    missing = [path for path in FILES if not str(files.get(path, "")).strip()]
    items.append(
        {
            "rule": "all_files_exist",
            "pass": not missing,
            "detail": "缺失或为空：" + (", ".join(missing) if missing else "无"),
        }
    )

    manifest = None
    detail = ""
    try:
        manifest = json.loads(files.get("handoff/manifest.json", ""))
    except Exception as exc:  # noqa: BLE001
        detail = f"manifest.json 解析失败：{type(exc).__name__}: {exc}"
    listed: list[str] = []
    if manifest is not None:
        artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
        if not isinstance(artifacts, list) or not all(isinstance(x, str) for x in artifacts):
            detail = "manifest.artifacts 不是字符串数组"
        else:
            listed = [str(item).replace("\\", "/") for item in artifacts]
            expected = set(NON_MANIFEST)
            extra = sorted(set(listed) - expected)
            absent = sorted(expected - set(listed))
            on_disk = [path for path in listed if str(files.get(path, "")).strip()]
            problems = []
            if extra:
                problems.append("多列：" + ", ".join(extra))
            if absent:
                problems.append("漏列：" + ", ".join(absent))
            if len(listed) != 7:
                problems.append(f"条目数 {len(listed)} != 7")
            unreadable = sorted(set(listed) - set(on_disk))
            if unreadable:
                problems.append("列了但不存在：" + ", ".join(unreadable))
            if manifest.get("entry_count") != 4:
                problems.append(f"entry_count={manifest.get('entry_count')!r} != 4")
            detail = "；".join(problems) if problems else "artifacts 与磁盘一致（7 条）"
    items.append({"rule": "manifest_artifacts", "pass": manifest is not None and detail.startswith("artifacts"), "detail": detail})

    chain_detail: list[str] = []
    chain_ok = True
    expected_chain = {
        "E1": ([], None),
        "E2": (["E1"], "handoff/entries/E1.json"),
        "E3": (["E2"], "handoff/entries/E2.json"),
        "E4": (["E3"], "handoff/entries/E3.json"),
    }
    for entry_id, (expected_dep, expected_prev) in expected_chain.items():
        path = f"handoff/entries/{entry_id}.json"
        raw = files.get(path, "")
        if not raw.strip():
            chain_ok = False
            chain_detail.append(f"{path} 缺失")
            continue
        try:
            entry = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            chain_ok = False
            chain_detail.append(f"{path} 解析失败：{type(exc).__name__}")
            continue
        if not isinstance(entry, dict):
            chain_ok = False
            chain_detail.append(f"{path} 不是对象")
            continue
        if entry.get("id") != entry_id:
            chain_ok = False
            chain_detail.append(f"{path} id={entry.get('id')!r} != {entry_id}")
        if entry.get("depends_on") != expected_dep:
            chain_ok = False
            chain_detail.append(f"{path} depends_on={entry.get('depends_on')!r} != {expected_dep}")
        predecessor = entry.get("predecessor")
        if expected_prev is None:
            if predecessor is not None:
                chain_ok = False
                chain_detail.append(f"{path} predecessor={predecessor!r} != null")
        elif str(predecessor or "").replace("\\", "/") != expected_prev:
            chain_ok = False
            chain_detail.append(f"{path} predecessor={predecessor!r} != {expected_prev}")
        artifact = str(entry.get("artifact") or "").replace("\\", "/")
        if artifact not in NON_MANIFEST:
            chain_ok = False
            chain_detail.append(f"{path} artifact={artifact!r} 不在产物清单")
        elif not str(files.get(artifact, "")).strip():
            chain_ok = False
            chain_detail.append(f"{path} artifact={artifact!r} 磁盘上不存在")
        if not str(entry.get("summary") or "").strip():
            chain_ok = False
            chain_detail.append(f"{path} summary 为空")
    items.append(
        {
            "rule": "entries_chain",
            "pass": chain_ok,
            "detail": "；".join(chain_detail) if chain_detail else "E1<-E2<-E3<-E4 链与 predecessor/artifact 引用一致",
        }
    )

    summary_detail: list[str] = []
    try:
        summary = json.loads(files.get("handoff/summary.json", ""))
    except Exception as exc:  # noqa: BLE001
        summary = None
        summary_detail.append(f"summary.json 解析失败：{type(exc).__name__}: {exc}")
    if isinstance(summary, dict):
        if summary.get("entry_count") != 4:
            summary_detail.append(f"entry_count={summary.get('entry_count')!r} != 4")
        if summary.get("artifact_count") != 7:
            summary_detail.append(f"artifact_count={summary.get('artifact_count')!r} != 7")
        if summary.get("chain") != ["E1", "E2", "E3", "E4"]:
            summary_detail.append(f"chain={summary.get('chain')!r} != ['E1','E2','E3','E4']")
        if str(summary.get("checker") or "").replace("\\", "/") != "handoff/check.py":
            summary_detail.append(f"checker={summary.get('checker')!r}")
        if not str(files.get(str(summary.get("readme") or "").replace("\\", "/"), "")).strip():
            summary_detail.append(f"readme={summary.get('readme')!r} 不存在")
    elif summary is not None:
        summary_detail.append("summary.json 不是对象")
    items.append(
        {
            "rule": "summary_consistent",
            "pass": not summary_detail,
            "detail": "；".join(summary_detail) if summary_detail else "summary 的数量与链和实际一致",
        }
    )

    readme = files.get("handoff/README.md", "")
    readme_ok = "python handoff/check.py" in readme and len(re.sub(r"\s+", "", readme)) >= 120
    items.append(
        {
            "rule": "readme_command",
            "pass": readme_ok,
            "detail": (
                f"含命令={'python handoff/check.py' in readme}；"
                f"去空白字符={len(re.sub(r'\s+', '', readme))}"
            ),
        }
    )

    hit_rows: list[dict[str, Any]] = []
    for path in FILES:
        for hit in banned_hits(str(files.get(path, "")), list(TOKENS)):
            hit_rows.append(
                {"path": path, "token": hit["token"], "visible": hit["visible_hit"], "skeleton": hit["skeleton_hit"]}
            )
    items.append(
        {
            "rule": "banned_hits_absent",
            "pass": not hit_rows,
            "detail": "命中：" + json.dumps(hit_rows, ensure_ascii=False) if hit_rows else "无命中",
        }
    )

    failed = [item["rule"] for item in items if not item["pass"]]
    return {"fail": bool(failed), "failed_rules": failed, "items": items}


def checker_run(files: dict[str, str], tag: str, out_dir: Path) -> dict[str, Any]:
    """真跑模型写的 check.py：独立临时仓库里执行，30s 超时。"""
    root = Path(tempfile.mkdtemp(prefix=f"ae2-check-{tag}-"))
    try:
        for path, content in files.items():
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "handoff/check.py"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                encoding="utf-8",
                errors="replace",
            )
            record = {
                "returncode": proc.returncode,
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
                "pass": proc.returncode == 0 and "OK" in proc.stdout,
            }
        except subprocess.TimeoutExpired:
            record = {"returncode": None, "stdout": "", "stderr": "", "pass": False, "timeout": True}
        write_json(out_dir / f"checker-{tag}.json", record)
        return record
    finally:
        record_cleanup = remove_tree(root)
        if not record_cleanup:
            print(f"WARN: 临时目录未删除：{root}")


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
    V1.git(repo, "config", "user.name", "ae2-executor")
    V1.git(repo, "config", "user.email", "ae2@local")
    V1.git(repo, "config", "core.autocrlf", "false")
    (repo / "README.md").write_text(README_SEED, encoding="utf-8", newline="\n")
    V1.git(repo, "add", "-A")
    V1.git(repo, "commit", "-m", "seed")
    return repo, V1.git(repo, "rev-parse", "HEAD").stdout.strip()


def run_once(index: int, token: str, out_dir: Path) -> dict[str, Any]:
    tag = f"D-r{index}"
    root = Path(tempfile.mkdtemp(prefix=f"ae2-{tag}-"))
    row: dict[str, Any] = {"run": index, "template": "HANDOFF", "arm": "D"}
    try:
        repo, base_sha = init_repo(root)
        prompt = executor_prompt()
        call = call_pool(prompt, tag, token)
        write_json(out_dir / "calls" / f"{tag}.json", call)
        if call["terminal_state"] == "ok":
            head_sha, written, rejected = V1.apply_delivery(repo, call["parsed_payload"])
        else:
            head_sha, written, rejected = base_sha, [], []
        files = V1.snapshot(repo)
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
                "checker": checker_run(files, tag, out_dir / "checks"),
                "score": score_delivery(files),
            }
        )
        store = out_dir / "repos" / tag
        store.mkdir(parents=True, exist_ok=True)
        (store / "files.json").write_text(
            json.dumps(files, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        patch = V1.git(repo, "diff", base_sha, head_sha).stdout if head_sha != base_sha else ""
        (store / "diff.patch").write_text(patch, encoding="utf-8")
        # checker 的结论并进 fail 判定：脚本跑不起来也算不合格
        if not row["checker"]["pass"] and "checker_runs" not in row["score"]["failed_rules"]:
            row["score"]["failed_rules"].append("checker_runs")
            row["score"]["items"].append(
                {
                    "rule": "checker_runs",
                    "pass": False,
                    "detail": f"exit={row['checker'].get('returncode')} stdout={row['checker'].get('stdout','')[:200]!r}",
                }
            )
            row["score"]["fail"] = True
    finally:
        row["temporary_directory_removed"] = remove_tree(root)
    return row


def protocol_path(out_dir: Path) -> Path:
    return out_dir / "protocol.json"


def template_spec() -> dict[str, Any]:
    return {
        "template": "HANDOFF",
        "files": FILES,
        "requirement": REQUIREMENT,
        "seed_files": [{"path": "README.md", "sha256": sha(README_SEED)}],
        "dispatch": dispatch_payload(),
        "prompt_sha256": sha(executor_prompt()),
    }


def templates_sha() -> str:
    return sha(json.dumps(template_spec(), ensure_ascii=False, sort_keys=True))


def scoring_sha() -> str:
    return sha(
        json.dumps(
            {"rules": rule_definitions(), "tokens": list(TOKENS), "files": FILES},
            ensure_ascii=False,
            sort_keys=True,
        )
    )


def freeze(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    protocol = {
        "dataset_version": f"experience-acceptance-effect-v2.{out_dir.name}",
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
            "scope": "只改运行参数/实现 bug；模板集与判据 SHA 未变；已全量重跑",
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
        write_json(out_dir / "rows.json", rows)  # 每跑完一次立刻落盘
        print(
            json.dumps(
                {
                    "run": index,
                    "terminal_state": row["terminal_state"],
                    "fail": row["score"]["fail"],
                    "failed_rules": row["score"]["failed_rules"],
                    "usage": row.get("usage"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    summary = {
        "frozen_at": read_json(protocol_path(out_dir), {})["frozen_at"],
        "model": MODEL,
        "runs": len(rows),
        "fail_count": sum(1 for row in rows if row["score"]["fail"]),
        "ok_count": sum(1 for row in rows if not row["score"]["fail"]),
        "fail_rules": [row["score"]["failed_rules"] for row in rows],
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
            "leftover_dirs": len(list(Path(tempfile.gettempdir()).glob("ae2-*"))),
        },
        "production_experience": {"before": before, "after": file_sha(SOURCE), "unchanged": before == file_sha(SOURCE)},
        "frozen": read_json(protocol_path(out_dir), {})["initial_freeze"],
    }
    write_json(out_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


# -----------------------------------------------------------------------------
# 自检（无模型）
# -----------------------------------------------------------------------------

GOOD_CHECK = """import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parents[1]
manifest = json.loads((root / "handoff" / "manifest.json").read_text(encoding="utf-8"))
summary = json.loads((root / "handoff" / "summary.json").read_text(encoding="utf-8"))
missing = [p for p in manifest.get("artifacts", []) if not (root / p).exists()]
entries = []
for i in (1, 2, 3, 4):
    entries.append(json.loads((root / "handoff" / "entries" / f"E{i}.json").read_text(encoding="utf-8")))
if missing or len(entries) != 4:
    print("MISSING", missing)
    sys.exit(1)
chain = {e["id"]: e.get("depends_on", []) for e in entries}
if chain != {"E1": [], "E2": ["E1"], "E3": ["E2"], "E4": ["E3"]}:
    print("CHAIN", chain)
    sys.exit(1)
if summary.get("entry_count") != 4 or summary.get("artifact_count") != 7:
    print("SUMMARY", summary)
    sys.exit(1)
print("OK")
"""

GOOD_FILES = {
    "handoff/manifest.json": json.dumps(
        {"version": 2, "artifacts": list(NON_MANIFEST), "entry_count": 4}, ensure_ascii=False, indent=2
    ),
    "handoff/entries/E1.json": json.dumps(
        {"id": "E1", "artifact": "handoff/entries/E1.json", "predecessor": None,
         "depends_on": [], "summary": "整理第一轮材料"},
        ensure_ascii=False,
        indent=2,
    ),
    "handoff/entries/E2.json": json.dumps(
        {"id": "E2", "artifact": "handoff/entries/E2.json",
         "predecessor": "handoff/entries/E1.json", "depends_on": ["E1"],
         "summary": "在 E1 基础上补充证据"},
        ensure_ascii=False,
        indent=2,
    ),
    "handoff/entries/E3.json": json.dumps(
        {"id": "E3", "artifact": "handoff/entries/E3.json",
         "predecessor": "handoff/entries/E2.json", "depends_on": ["E2"],
         "summary": "汇总前两条并给出结论"},
        ensure_ascii=False,
        indent=2,
    ),
    "handoff/entries/E4.json": json.dumps(
        {"id": "E4", "artifact": "handoff/entries/E4.json",
         "predecessor": "handoff/entries/E3.json", "depends_on": ["E3"],
         "summary": "复核结论并给出交接清单"},
        ensure_ascii=False,
        indent=2,
    ),
    "handoff/summary.json": json.dumps(
        {"entry_count": 4, "artifact_count": 7, "chain": ["E1", "E2", "E3", "E4"],
         "checker": "handoff/check.py", "readme": "handoff/README.md"},
        ensure_ascii=False,
        indent=2,
    ),
    "handoff/check.py": GOOD_CHECK,
    "handoff/README.md": (
        "# 交接包说明\n\n运行方式：在仓库根目录执行 `python handoff/check.py`，"
        "脚本会读取 handoff/manifest.json、四个条目文件与 handoff/summary.json，"
        "校验文件存在、条目数量、依赖链与 predecessor，以及 summary 里的数量是否一致，"
        "通过时打印 OK 并以 0 退出，失败时打印不一致项并以 1 退出。\n\n"
        "产物清单共 8 个文件：manifest.json、entries/E1.json、entries/E2.json、"
        "entries/E3.json、entries/E4.json、summary.json、check.py 与本说明。\n"
    ),
}


def selftest(out_dir: Path) -> None:
    problems: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="ae2-selftest-"))
    files = {"README.md": README_SEED, **GOOD_FILES}
    base = score_delivery(files)
    if base["fail"]:
        problems.append(f"合格样例被判不合格：{base['failed_rules']}")
    run_result = checker_run(files, "selftest", scratch)
    if not run_result["pass"]:
        problems.append(f"合格样例的 check.py 没跑通：{run_result}")

    bad_cases = {
        "缺文件": {k: v for k, v in files.items() if k != "handoff/check.py"},
        "清单漏列": {**files, "handoff/manifest.json": json.dumps(
            {"version": 1, "artifacts": NON_MANIFEST[:4], "entry_count": 3}, ensure_ascii=False
        )},
        "依赖链错": {**files, "handoff/entries/E3.json": json.dumps(
            {"id": "E3", "artifact": "handoff/entries/E3.json",
             "predecessor": "handoff/entries/E2.json", "depends_on": ["E1"], "summary": "x"},
            ensure_ascii=False,
        )},
        "summary 不一致": {**files, "handoff/summary.json": json.dumps(
            {"entry_count": 3, "artifact_count": 7, "chain": ["E1", "E2", "E3", "E4"],
             "checker": "handoff/check.py", "readme": "handoff/README.md"},
            ensure_ascii=False,
        )},
        "脚本退出1": {**files, "handoff/check.py": "import sys\nprint('MISSING')\nsys.exit(1)\n"},
    }
    expected = {
        "缺文件": "all_files_exist",
        "清单漏列": "manifest_artifacts",
        "依赖链错": "entries_chain",
        "summary 不一致": "summary_consistent",
        "脚本退出1": "checker_runs",
    }
    for name, case in bad_cases.items():
        score = score_delivery(case)
        wants_checker = expected[name] == "checker_runs"
        if wants_checker:
            result = checker_run(case, "selftest-bad", scratch)
            if not result["pass"]:
                score["fail"] = True
                score["failed_rules"].append("checker_runs")
        if not score["fail"]:
            problems.append(f"{name} 没被判不合格")
        if expected[name] not in score["failed_rules"]:
            problems.append(f"{name} 的失败项不是 {expected[name]}：{score['failed_rules']}")

    if not remove_tree(scratch):
        problems.append(f"自检临时目录没删掉：{scratch}")

    if problems:
        raise RuntimeError("自检失败：\n" + "\n".join(problems))
    print(json.dumps({"selftest": "ok", "cases": len(bad_cases) + 1}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["selftest", "freeze", "run", "amend"])
    parser.add_argument("--rev", default="rev2")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()
    out_dir = OUT_ROOT / args.rev
    if args.command == "selftest":
        selftest(out_dir)
    elif args.command == "freeze":
        freeze(out_dir)
    elif args.command == "amend":
        amend(out_dir, args.reason)
    else:
        run(out_dir)


if __name__ == "__main__":
    main()
