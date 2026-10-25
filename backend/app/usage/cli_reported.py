"""成员 CLI 自报的用量 —— 平台从 CLI 的原始输出里解析出来。

为什么要有这一层：办公室窗口页的「用量」读的是**额度池的用量账**（``pool/usage.jsonl``）
—— 只有走额度池的调用才在里面。成员用**自带模型**跑的活，平台不在数据链路上
（CLI 直接跟 provider 对话），所以那一本账上一个数都没有：人以为「没消耗」，
其实只是没记账。

这一层做的事：**平台自己从 CLI 的原始输出里把用量读出来**。平台的数据目录里就有
每个 attempt 的原始输出（办公室的「它最后说了什么」读的就是它）。

三种形状，各有各的读法（真数据实测）：

* **Anthropic 形状**（Claude Code）：结尾有一行**整场汇总**
  （带 ``total_cost_usd`` / ``duration_api_ms`` / ``num_turns``）—— **取那一个**。
  同一条 usage 在流里会出现好几次（消息本体 + 它的副本），逐条累加会把同一份算上
  好几遍（实测 4 倍）。
* **opencode 形状**：每步一条 ``part.tokens``（``cache.read`` 是这一步读到的缓存）——
  按步 id 去重后累加。
* **OpenAI 形状**（chat / responses）：一次请求一条 —— 取最后一条。

**输入一律含缓存**：Anthropic 的 ``input_tokens`` 不含 ``cache_read_input_tokens``，
opencode 的 ``input`` 也不含 ``cache.read``；归一之后（总输入 = 新输入 + 缓存读 +
缓存写），「命中 ÷ 总输入」在各家之间才是同一个口径，与额度池那本账一致。

**认不出来就是没有**：不写 0，不猜。仓库的老规矩：宁可原样，也不要编一个错的说法。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: 「整场汇总」的标志：顶层对象带这些键，说明它报的是整场而不是一次请求。
_SUMMARY_KEYS = ("total_cost_usd", "duration_api_ms", "num_turns", "duration_ms")

#: 读多大规模的原始输出。再大就不算了 —— 宁可没有，别把内存吃光。
MAX_OUTPUT_BYTES = 32 * 1024 * 1024

#: 「成员自报」那本账的文件名（与额度池那本同目录，不同文件）。
REPORTED_LEDGER_NAME = "usage-reported.jsonl"


@dataclass(frozen=True)
class ReportedUsage:
    """一个 attempt 的用量（成员 CLI 自己报出来的）。"""

    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int
    requests: int
    shape: str

    @property
    def cache_hit_rate(self) -> float | None:
        """命中缓存的 token 占**总输入**的比例（没有输入就没有比例）。"""
        if self.prompt_tokens <= 0:
            return None
        return self.cached_tokens / self.prompt_tokens


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _usage_triplet(usage: dict[str, Any]) -> tuple[int, int, int] | None:
    """一条 usage → (总输入, 缓存读, 输出)。认不出来就是 None。"""
    if "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
        # Anthropic：input_tokens 不含缓存。
        fresh = _int(usage.get("input_tokens"))
        read = _int(usage.get("cache_read_input_tokens"))
        created = _int(usage.get("cache_creation_input_tokens"))
        return fresh + read + created, read, _int(usage.get("output_tokens"))
    if "prompt_tokens" in usage or "prompt_tokens_details" in usage:
        # OpenAI chat：prompt_tokens 已含缓存。
        details = usage.get("prompt_tokens_details")
        cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        return _int(usage.get("prompt_tokens")), cached, _int(usage.get("completion_tokens"))
    if "input_tokens" in usage or "input_tokens_details" in usage:
        # OpenAI responses：input_tokens 已含缓存。
        details = usage.get("input_tokens_details")
        cached = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
        return _int(usage.get("input_tokens")), cached, _int(usage.get("output_tokens"))
    return None


def _opencode_step(tokens: dict[str, Any]) -> tuple[int, int, int] | None:
    """opencode 的一步：``input`` 不含 ``cache.read``。"""
    if "input" not in tokens and "output" not in tokens:
        return None
    cache = tokens.get("cache")
    read = _int(cache.get("read")) if isinstance(cache, dict) else 0
    fresh = _int(tokens.get("input"))
    return fresh + read, read, _int(tokens.get("output"))


def parse_cli_usage(text: str) -> ReportedUsage | None:
    """一段 CLI 的原始输出 → 这个 attempt 的用量（认不出来就是没有）。

    规则见模块头：优先用 CLI 自己报的**整场汇总**；没有汇总就用每步的累加；
    再没有就取最后一条 usage。
    """
    if not text:
        return None
    summary: tuple[int, int, int] | None = None
    last: tuple[int, int, int] | None = None
    steps: dict[str, tuple[int, int, int]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or ("usage" not in line and "tokens" not in line):
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        # opencode：每步一条 part.tokens
        part = obj.get("part")
        if isinstance(part, dict):
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                step = _opencode_step(tokens)
                if step is not None:
                    steps[str(part.get("id") or obj.get("uuid") or "")] = step
        # 顶层 usage：可能是整场汇总，也可能只是一次请求
        usage = obj.get("usage")
        if isinstance(usage, dict):
            triplet = _usage_triplet(usage)
            if triplet is not None:
                last = triplet
                if any(key in obj for key in _SUMMARY_KEYS):
                    summary = triplet
    if summary is not None:
        prompt, cached, completion = summary
        return ReportedUsage(prompt, completion, cached, 1, "summary")
    if steps:
        prompt = sum(step[0] for step in steps.values())
        cached = sum(step[1] for step in steps.values())
        completion = sum(step[2] for step in steps.values())
        return ReportedUsage(prompt, completion, cached, len(steps), "steps")
    if last is not None:
        prompt, cached, completion = last
        return ReportedUsage(prompt, completion, cached, 1, "last")
    return None


def reported_ledger_path(data_dir: Path | str) -> Path:
    """「成员自报」那本账在哪。"""
    return Path(data_dir) / "pool" / REPORTED_LEDGER_NAME


def read_reported_records(data_dir: Path | str) -> list[dict[str, Any]]:
    """读「成员自报」那本账（读不出来就是没有，不报错）。"""
    path = reported_ledger_path(data_dir)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def record_reported_usage(
    *,
    data_dir: Path | str,
    task_id: str,
    attempt_id: str,
    harness_id: str,
    at: str | None = None,
) -> ReportedUsage | None:
    """把这个 attempt 的用量记进「成员自报」那本账。

    认不出来（CLI 没报用量、日志不在、读不动）就是**没有** —— 不写 0。
    """
    from app.adapter import cli_output  # 局部导入：cli_output 不依赖这里

    stdout_path, _ = cli_output.log_paths(data_dir, task_id, attempt_id)
    if not stdout_path.exists():
        return None
    try:
        if stdout_path.stat().st_size > MAX_OUTPUT_BYTES:
            return None
        text = stdout_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    usage = parse_cli_usage(text)
    if usage is None:
        return None
    if not (usage.prompt_tokens or usage.completion_tokens or usage.cached_tokens):
        # 全是 0：CLI 报了用量但没有数 —— 0 的意思是「没数到」，不是「没用」。
        # 记下来会变成一句假话（「这次免费」），所以不记。
        return None
    record = {
        "at": at or time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "model": "cli",
        "upstream": f"cli:{harness_id}",
        "protocol": "cli",
        "status": "reported",
        "duration_ms": 0,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cached_tokens": usage.cached_tokens,
        "usage_reported": True,
        "credits": 0.0,
        "session_id": None,
        "consumer": f"harness/{harness_id}",
        "attempt_id": attempt_id,
        "task_id": task_id,
        "note": "reported by the member CLI",
        "shape": usage.shape,
    }
    path = reported_ledger_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        return usage
    return usage
