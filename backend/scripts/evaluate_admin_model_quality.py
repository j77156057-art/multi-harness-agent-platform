"""四通道固定场景评估；只写独立评估产物，不改变产品运行配置。

用法（在 backend/ 下）：
  python -m scripts.evaluate_admin_model_quality freeze
  python -m scripts.evaluate_admin_model_quality run
  python -m scripts.evaluate_admin_model_quality summarize
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.adapter.harnesses.cli import SubprocessRunner
from app.admin.service import AdminHarnessError, AdminService, extract_json_object
from app.auth.tokens import TokenService
from app.config import settings
from tests.merge_support import build_chain, register_harness

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "admin-model-quality" / "v1.0.1"
MANIFEST = OUT / "frozen.json"
PROMPTS = OUT / "prompts.json"
RUNS = OUT / "runs"
SUMMARY = OUT / "summary.json"
GROUP = "G-1"
MEMBER = "claude-code-member-1"
REPEATS = 3
DATASET_VERSION = "admin-quality-v1.0.1"
FALSE_EXECUTOR = ("尚无执行者", "没有执行者", "未有执行者", "无执行者", "无人执行")
CHANNELS = {
    "qoder-auto": {
        "harness_id": "qoder", "provider": "qoder", "model": "Auto", "transport": "cli",
    },
    "codebuddy-hy3": {
        "harness_id": "codebuddy", "provider": "tencent", "model": "hy3", "transport": "cli",
    },
    "opencode-deepseek": {
        "harness_id": "opencode", "provider": "sst",
        "model": "opencode-cli/deepseek-v4.1-flash-free", "transport": "pool",
    },
    "ollama-qwen36": {
        "harness_id": "pi", "provider": "ollama", "model": "qwen3.6:35b-a3b",
        "transport": "ollama",
    },
}
REQUIREMENT = (
    "补全启动文档，写清安装、启动和异常处理。只修改 docs/startup.md，"
    "并交给已登记成员。验收必须是可以实际检查的条目，不能只写“完成目标”。"
)
REVIEW_TASK = {
    "task_id": "REAL-FAILURE-001", "attempt_id": "REAL-FAILURE-001.a1",
    "title": "补启动说明", "status": "awaiting_review",
}
REVIEW_SPEC = {
    "id": "REAL-FAILURE-001", "title": "补启动说明",
    "goal": "在 docs/startup.md 补启动说明，并保留既有入口，不得修改其他文件",
    "exclusive_paths": ["docs/startup.md"],
    "acceptance": "docs/startup.md 含启动步骤；既有入口保留；不得修改其他文件",
}
REVIEW_DIFF = {
    "available": True,
    "patch": (
        "diff --git a/docs/startup.md b/docs/startup.md\n"
        "+1. 安装依赖\n+2. 启动服务\n"
        "diff --git a/office/src/main.js b/office/src/main.js\n"
        "-const existingEntry = 'office'\n"
    ),
}
QUESTION = "现在在哪一步？下一步谁做什么？"


def now() -> str:
    return datetime.now(UTC).isoformat()


def sha(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cli_version(command: str) -> str:
    executable = shutil.which(command) or command
    if os.name == "nt" and not executable.lower().endswith((".exe", ".cmd", ".bat", ".com")):
        for suffix in (".cmd", ".exe", ".bat"):
            if Path(executable + suffix).exists():
                executable += suffix
                break
    try:
        run = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=20, check=False,
        )
        return (run.stdout or run.stderr).strip().splitlines()[0][:300]
    except Exception as exc:  # 版本缺失必须留在协议里，不能猜。
        return f"unavailable: {type(exc).__name__}"


class RecordingRunner:
    def __init__(self) -> None:
        self.inner = SubprocessRunner()
        self.last_stdout = ""
        self.last_stderr = ""

    def run(self, argv, **kwargs):
        try:
            result = self.inner.run(argv, **kwargs)
        except Exception as exc:
            self.last_stdout = str(getattr(exc, "stdout", "") or "")
            self.last_stderr = str(getattr(exc, "stderr", "") or "")
            raise
        self.last_stdout = result.stdout
        self.last_stderr = result.stderr
        return result


class EvalService(AdminService):
    def __init__(self, *args, channel: str, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.channel = channel
        self.calls: list[dict[str, Any]] = []
        self.scenario = ""
        self._cold_pending = channel == "ollama-qwen36"

    def _record(self, prompt: str, started: float, **fields) -> None:
        spec = CHANNELS[self.channel]
        stage = self.scenario
        if self.scenario == "spec":
            stage = "spec_adjustment" if any(
                call["stage"] == "spec_initial" for call in self.calls
            ) else "spec_initial"
        attempt = 1 + sum(call["stage"] == stage for call in self.calls)
        self.calls.append({
            "stage": stage, "attempt": attempt, "started_at": now(),
            "requested_model": spec["model"], "prompt_sha256": sha(prompt),
            "duration_seconds": round(time.monotonic() - started, 3), **fields,
        })

    def _invoke_model(self, prompt: str, route=None, log_key=""):
        if CHANNELS[self.channel]["transport"] == "cli":
            started = time.monotonic()
            try:
                payload, model = super()._invoke_model(prompt, route=route, log_key=log_key)
            except Exception as exc:
                self._record(
                    prompt, started, raw_output=self.runner.last_stdout,
                    stderr=self.runner.last_stderr, parsed_payload=None,
                    actual_model="", usage={}, error=f"{type(exc).__name__}: {exc}",
                )
                raise
            actual, usage = cli_facts(self.runner.last_stdout)
            self._record(
                prompt, started, raw_output=self.runner.last_stdout,
                stderr=self.runner.last_stderr, parsed_payload=payload,
                actual_model=actual, usage=usage, model_receipt=model, error=None,
            )
            return payload, model

        started = time.monotonic()
        cold = self._cold_pending
        self._cold_pending = False
        try:
            raw, actual, usage = self._http(prompt)
            payload = extract_json_object(raw)
        except Exception as exc:
            self._record(
                prompt, started, raw_output="", stderr="", parsed_payload=None,
                actual_model="", usage={}, cold_start=cold,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        model = {
            "adapter": CHANNELS[self.channel]["transport"], "channel": self.channel,
            "duration_seconds": round(time.monotonic() - started, 1),
        }
        self._record(
            prompt, started, raw_output=raw, stderr="", parsed_payload=payload,
            actual_model=actual, usage=usage, cold_start=cold, model_receipt=model, error=None,
        )
        return payload, model

    def _http(self, prompt: str) -> tuple[str, str, dict[str, Any]]:
        transport = CHANNELS[self.channel]["transport"]
        if transport == "pool":
            token = (ROOT / "data" / "platform-member-1.key").read_text(encoding="utf-8").strip()
            response = httpx.post(
                "http://127.0.0.1:8848/api/v1/pool/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": CHANNELS[self.channel]["model"],
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 4096, "stream": False,
                },
                timeout=180.0,
            )
            response.raise_for_status()
            data = response.json()
            return (
                str(data["choices"][0]["message"]["content"] or ""),
                str(data.get("model") or ""), dict(data.get("usage") or {}),
            )
        response = httpx.post(
            "http://127.0.0.1:11434/api/chat",
            json={
                "model": CHANNELS[self.channel]["model"], "think": False,
                "messages": [{"role": "user", "content": prompt}], "stream": False,
                "options": {"num_predict": 4096}, "keep_alive": "10m",
            },
            timeout=240.0,
        )
        response.raise_for_status()
        data = response.json()
        usage = {
            "prompt_tokens": data.get("prompt_eval_count"),
            "completion_tokens": data.get("eval_count"),
            "done_reason": data.get("done_reason"),
        }
        return str(data["message"].get("content") or ""), str(data.get("model") or ""), usage


def cli_facts(raw: str) -> tuple[str, dict[str, Any]]:
    model = ""
    usage: dict[str, Any] = {}
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if not model and isinstance(item.get("model"), str):
            model = item["model"]
        if item.get("type") == "result":
            usage = {
                "input_tokens": (item.get("usage") or {}).get("input_tokens"),
                "output_tokens": (item.get("usage") or {}).get("output_tokens"),
                "total_cost_usd": item.get("total_cost_usd"),
                "credits": item.get("total_credits"),
                "duration_ms": item.get("duration_ms"),
            }
    return model, usage


def make_service(channel: str, chain, scratch: Path) -> EvalService:
    spec = CHANNELS[channel]
    register_harness(chain.database, spec["harness_id"], provider=spec["provider"])
    register_harness(chain.database, MEMBER, provider="anthropic")
    TokenService(chain.database).issue(
        subject_type="harness", subject_id=MEMBER, label="evaluation member"
    )
    return EvalService(
        chain.database, harness_id=spec["harness_id"], channel=channel,
        runner=RecordingRunner(), scratch_dir=scratch, timeout_seconds=240,
    )


def scenario_prompts(service: AdminService) -> dict[str, str]:
    return {
        "spec": service._prompt(
            REQUIREMENT, group_id=GROUP, max_tasks=1, members=[MEMBER], answers=[], experience="",
        ),
        "review": service._review_prompt(
            task=REVIEW_TASK, spec=REVIEW_SPEC, diff=REVIEW_DIFF,
            verification={"status": "passed"}, project_name="质量评估",
        ),
        "answer": service._answer_prompt(QUESTION, group_id=GROUP, asker="office-operator"),
    }


def basic_structure(scenario: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if scenario == "answer":
        return isinstance(payload.get("answer"), str) and bool(payload["answer"].strip())
    if scenario == "review":
        return (
            payload.get("verdict") in {"approve", "request_changes"}
            and isinstance(payload.get("findings"), list)
            and all(isinstance(item, dict) and str(item.get("message") or "").strip()
                    for item in payload["findings"])
        )
    if isinstance(payload.get("questions"), list):
        return all(isinstance(item, str) for item in payload["questions"])
    tasks = payload.get("tasks")
    return isinstance(tasks, list) and bool(tasks) and all(
        isinstance(task, dict)
        and all(isinstance(task.get(key), str) and task[key].strip()
                for key in ("title", "goal", "acceptance", "harness_id"))
        and isinstance(task.get("exclusive_paths"), list)
        for task in tasks
    )


def acceptance_ok(text: str) -> bool:
    normalized = text.casefold()
    return (
        "docs/startup.md" in text
        and all(word in text for word in ("安装", "启动", "异常"))
        and any(word in normalized for word in (
            "标题", "包含", "存在", "不少于", "命令", "步骤", "section", "heading",
            "contains", "exists", "at least", "command", "step", "verify", "reviewer",
            "git diff", "non-empty",
        ))
        and len(text) >= 45
    )


def score_payload(scenario: str, payload: Any) -> dict[str, Any]:
    structured = basic_structure(scenario, payload)
    errors: list[str] = []
    if not structured:
        return {"structured_valid": False, "correct": False, "errors": ["invalid_structure"]}
    if scenario == "spec":
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1:
            errors.append("unexpected_questions_or_task_count")
            return {"structured_valid": True, "correct": False, "errors": errors}
        task = tasks[0]
        if task.get("harness_id") != MEMBER:
            errors.append("wrong_executor")
        if task.get("exclusive_paths") != ["docs/startup.md"]:
            errors.append("wrong_paths")
        if not acceptance_ok(str(task.get("acceptance") or "")):
            errors.append("acceptance_not_observable_or_incomplete")
    elif scenario == "review":
        if payload.get("verdict") != "request_changes":
            errors.append("wrong_verdict")
        if payload.get("failure_class") != "execution":
            errors.append("wrong_failure_class")
        text = canonical(payload)
        if "office/src/main.js" not in text:
            errors.append("missed_out_of_scope_path")
        if "existingEntry" not in text and "既有入口" not in text and "入口" not in text:
            errors.append("missed_deleted_entry")
        if not payload.get("findings"):
            errors.append("empty_findings")
    else:
        answer = payload["answer"]
        if "待验收" not in answer and "awaiting_review" not in answer:
            errors.append("wrong_or_missing_status")
        if "harness-1" not in answer:
            errors.append("wrong_or_missing_executor")
        if not (("审核" in answer or "审阅" in answer) and "裁决" in answer):
            errors.append("wrong_or_missing_next_action")
        if any(claim in answer for claim in FALSE_EXECUTOR):
            errors.append("false_no_executor_claim")
    return {"structured_valid": True, "correct": not errors, "errors": errors}


def score_guarded(scenario: str, result: Any, error: str) -> dict[str, Any]:
    if result is None:
        return {
            "structured_valid": False, "correct": False,
            "errors": [error or "no_guarded_result"],
        }
    if scenario == "spec":
        if result.get("status") == "needs_owner" and isinstance(result.get("questions"), list):
            score = score_payload("spec", {"questions": result["questions"]})
        else:
            score = score_payload("spec", {"tasks": result.get("tasks") or []})
        if result.get("status") != "ok":
            score["correct"] = False
            score["errors"].append("guard_status_not_ok")
        return score
    return score_payload(scenario, result)


def infrastructure_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.HTTPError, TimeoutError, ConnectionError)):
        return True
    text = str(exc).lower()
    return any(word in text for word in ("timed out", "timeout", "unavailable", "exited", "network"))


def execute_scenario(service: EvalService, scenario: str) -> tuple[Any, str, bool]:
    service.scenario = scenario
    for attempt in range(2):
        try:
            if scenario == "spec":
                return service.refine(
                    requirement=REQUIREMENT, group_id=GROUP, max_tasks=1, members=[MEMBER],
                ), "", False
            if scenario == "review":
                return service.review(
                    task=REVIEW_TASK, spec=REVIEW_SPEC, diff=REVIEW_DIFF,
                    verification={"status": "passed"}, project_name="质量评估",
                ), "", False
            return service.answer(question=QUESTION, group_id=GROUP, asker="office-operator"), "", False
        except Exception as exc:
            if attempt == 0 and infrastructure_error(exc):
                continue
            return None, f"{type(exc).__name__}: {exc}", infrastructure_error(exc)
    raise AssertionError("unreachable")


def freeze() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists() or PROMPTS.exists():
        raise SystemExit("frozen protocol already exists; refusing to overwrite it")
    chain = build_chain(OUT / "freeze-chain")
    service = make_service("qoder-auto", chain, OUT / "freeze-scratch")
    assert MEMBER in service.assignable_harnesses(), "evaluation member is not assignable"
    ledger = service._task_ledger(GROUP)
    assert "T-1" in ledger and "harness-1" in ledger and "awaiting_review" in ledger, ledger
    prompts = scenario_prompts(service)
    write_json(PROMPTS, prompts)
    prompt_hashes = {name: sha(text) for name, text in prompts.items()}
    manifest = {
        "dataset_version": DATASET_VERSION, "frozen_at": now(), "repeats": REPEATS,
        "channels": CHANNELS,
        "versions": {
            "qoderclicn": cli_version("qoderclicn"),
            "codebuddy": cli_version("codebuddy"),
            "opencode": cli_version("opencode"),
            "ollama": cli_version("ollama"),
            "python": sys.version.split()[0],
            "admin_service_sha256": sha((ROOT / "backend/app/admin/service.py").read_text(encoding="utf-8")),
            "facts_guard_sha256": sha((ROOT / "backend/app/admin/facts.py").read_text(encoding="utf-8")),
        },
        "initial_prompt_sha256": prompt_hashes,
        "prompts_sha256": sha(PROMPTS.read_text(encoding="utf-8")),
        "dynamic_prompt_policy": (
            "规格二次修订取决于首次模型输出；发生时逐调用记录 prompt_sha256，不预先编造。"
        ),
        "retry_policy": "每阶段仅瞬时网络/超时/崩溃最多重试一次；非法JSON和判断错误不重试。",
        "scenario_rules": {
            "spec": "首次回答单独评分；1个任务、指定成员和唯一路径，验收含安装/启动/异常且可观察。守卫后允许二次修订。",
            "review": "request_changes + execution；指出office/src/main.js越界及existingEntry/既有入口删除。",
            "answer": "待验收 + harness-1 + 审核/审阅裁决；不得声称无执行者。原文与事实守卫后分别评分。",
        },
        "metrics": "36个场景为分母；基础设施失败不进入已完成质量指标，但不从完成率分母删除。",
        "inputs": {
            "requirement": REQUIREMENT, "review_task": REVIEW_TASK,
            "review_spec": REVIEW_SPEC, "review_diff": REVIEW_DIFF, "question": QUESTION,
        },
    }
    manifest["protocol_sha256"] = sha(canonical(manifest))
    write_json(MANIFEST, manifest)
    print(json.dumps({"frozen": str(MANIFEST), "protocol_sha256": manifest["protocol_sha256"],
                      "prompt_hashes": prompt_hashes}, ensure_ascii=False, indent=2))


def verify_frozen() -> dict[str, Any]:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    claimed = manifest.pop("protocol_sha256")
    assert sha(canonical(manifest)) == claimed, "frozen protocol hash mismatch"
    manifest["protocol_sha256"] = claimed
    assert sha(PROMPTS.read_text(encoding="utf-8")) == manifest["prompts_sha256"], "prompt hash mismatch"
    return manifest


def run() -> None:
    manifest = verify_frozen()
    RUNS.mkdir(parents=True, exist_ok=True)
    expected = {(channel, scenario, repeat)
                for channel in CHANNELS for scenario in ("spec", "review", "answer")
                for repeat in range(1, REPEATS + 1)}
    # 同一冻结账本只读提供事实；各模型调用不修改任务状态。共享它可避免重复搭建
    # Git 验证链的 Windows 文件锁，已完成的同协议记录直接跳过，不重复花费额度。
    chain = build_chain(RUNS / "shared-chain")
    for channel in CHANNELS:
        service = make_service(channel, chain, RUNS / "scratch" / channel)
        if channel == "ollama-qwen36":
            # 只影响本地运行时驻留，不改配置；第一次正式调用单独标为冷启动。
            httpx.post("http://127.0.0.1:11434/api/generate",
                       json={"model": CHANNELS[channel]["model"], "keep_alive": 0}, timeout=30)
        for repeat in range(1, REPEATS + 1):
            for scenario in ("spec", "review", "answer"):
                path = RUNS / f"{channel}--{scenario}--{repeat}.json"
                if path.exists():
                    previous = json.loads(path.read_text(encoding="utf-8"))
                    assert previous["protocol_sha256"] == manifest["protocol_sha256"]
                    continue
                before = len(service.calls)
                result, error, infra_failed = execute_scenario(service, scenario)
                calls = service.calls[before:]
                initial = next((call for call in calls if call["error"] is None), None)
                raw_score = (
                    score_payload(scenario, initial["parsed_payload"])
                    if initial else {"structured_valid": False, "correct": False,
                                     "errors": ["no_successful_model_call"]}
                )
                record = {
                    "dataset_version": DATASET_VERSION,
                    "protocol_sha256": manifest["protocol_sha256"],
                    "channel": channel, "scenario": scenario, "repeat": repeat,
                    "requested_model": CHANNELS[channel]["model"],
                    "actual_models": sorted({call["actual_model"] for call in calls
                                             if call["actual_model"]}),
                    "status": "infrastructure_failed" if infra_failed else "completed",
                    "error": error, "raw": raw_score, "guarded": score_guarded(scenario, result, error),
                    "call_count": len(calls), "calls": calls,
                }
                write_json(RUNS / f"{channel}--{scenario}--{repeat}.json", record)
                print(json.dumps({key: record[key] for key in (
                    "channel", "scenario", "repeat", "status", "call_count", "error"
                )}, ensure_ascii=False), flush=True)
    assert {path.stem for path in RUNS.glob("*.json")} == {
        f"{channel}--{scenario}--{repeat}" for channel, scenario, repeat in expected
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = int(position), min(int(position) + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 3)


def effective_usage(call: dict[str, Any]) -> dict[str, Any]:
    if call["usage"]:
        return call["usage"]
    # CLI 已返回结果但平台解析/校验失败时，额度仍已发生；从原始回执补记账。
    if call["raw_output"] and CHANNELS[next(
        name for name, spec in CHANNELS.items() if spec["model"] == call["requested_model"]
    )]["transport"] == "cli":
        return cli_facts(call["raw_output"])[1]
    return call["usage"]


def summarize() -> None:
    manifest = verify_frozen()
    records = [json.loads(path.read_text(encoding="utf-8")) for path in RUNS.glob("*.json")]
    assert len(records) == 36, f"expected 36 records, got {len(records)}"
    for row in records:
        successful = [call for call in row["calls"] if call["error"] is None]
        if successful:
            row["raw"] = score_payload(row["scenario"], successful[0]["parsed_payload"])
        if row["scenario"] == "spec" and not row["error"] and successful:
            final_payload = successful[-1]["parsed_payload"]
            row["guarded"] = score_payload("spec", final_payload)
            if isinstance(final_payload.get("questions"), list):
                row["guarded"]["errors"].append("guard_status_not_ok")
        write_json(RUNS / f"{row['channel']}--{row['scenario']}--{row['repeat']}.json", row)
    output: dict[str, Any] = {
        "dataset_version": DATASET_VERSION, "protocol_sha256": manifest["protocol_sha256"],
        "evaluator_sha256": sha(Path(__file__).read_text(encoding="utf-8")),
        "scenario_count": len(records), "channels": {},
    }
    for channel in CHANNELS:
        subset = [row for row in records if row["channel"] == channel]
        completed = [row for row in subset if row["status"] == "completed"]
        calls = [call for row in subset for call in row["calls"]]
        durations = [call["duration_seconds"] for call in calls]
        call_usage = [effective_usage(call) for call in calls]
        usage = {
            "input_tokens": sum((item.get("input_tokens") or item.get("prompt_tokens") or 0)
                                for item in call_usage),
            "output_tokens": sum((item.get("output_tokens") or item.get("completion_tokens") or 0)
                                 for item in call_usage),
            "credits": sum((item.get("credits") or 0) for item in call_usage),
            "total_cost_usd": sum((item.get("total_cost_usd") or 0) for item in call_usage),
            "token_usage_reported": any(
                (item.get("input_tokens") or item.get("prompt_tokens") or 0)
                or (item.get("output_tokens") or item.get("completion_tokens") or 0)
                for item in call_usage
            ),
        }
        actual_models = {model for row in subset for model in row["actual_models"]}
        if CHANNELS[channel]["transport"] == "cli":
            actual_models.update(cli_facts(call["raw_output"])[0] for call in calls)
        actual_models.discard("")
        raw_errors: dict[str, int] = {}
        for row in completed:
            for error in row["raw"]["errors"]:
                raw_errors[error] = raw_errors.get(error, 0) + 1
        output["channels"][channel] = {
            "requested_model": CHANNELS[channel]["model"],
            "actual_models": sorted(actual_models),
            "scenario_count": len(subset), "completed": len(completed),
            "completion_rate": len(completed) / len(subset),
            "infrastructure_failure_rate": (len(subset) - len(completed)) / len(subset),
            "call_count": len(calls),
            "raw_correct_rate": sum(row["raw"]["correct"] for row in completed) / len(completed)
                if completed else None,
            "guarded_correct_rate": sum(row["guarded"]["correct"] for row in completed) / len(completed)
                if completed else None,
            "raw_structured_rate": sum(row["raw"]["structured_valid"] for row in completed) / len(completed)
                if completed else None,
            "guarded_structured_rate": sum(row["guarded"]["structured_valid"] for row in completed) / len(completed)
                if completed else None,
            "duration_seconds": {
                "median": statistics.median(durations) if durations else None,
                "max": max(durations) if durations else None,
                "p95": percentile(durations, 0.95),
            },
            "cold_calls": [{"scenario": row["scenario"], "repeat": row["repeat"],
                            "duration_seconds": call["duration_seconds"]}
                           for row in subset for call in row["calls"] if call.get("cold_start")],
            "usage": usage, "raw_errors": raw_errors,
            "by_scenario": {
                scenario: {
                    "raw_correct": sum(row["raw"]["correct"] for row in completed
                                       if row["scenario"] == scenario),
                    "guarded_correct": sum(row["guarded"]["correct"] for row in completed
                                           if row["scenario"] == scenario),
                    "completed": sum(row["scenario"] == scenario for row in completed),
                } for scenario in ("spec", "review", "answer")
            },
        }
    output["total_calls"] = sum(row["call_count"] for row in records)
    write_json(SUMMARY, output)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"freeze", "run", "summarize"}:
        raise SystemExit("usage: freeze|run|summarize")
    {"freeze": freeze, "run": run, "summarize": summarize}[sys.argv[1]]()
