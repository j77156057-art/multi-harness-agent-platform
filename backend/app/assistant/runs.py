"""助手这一轮做了什么 —— 给人看的那份摘要。

引擎把每一步都写进了 ``<data_dir>/assistant/trace/run-*.jsonl``：思考、调了哪个
工具、看到了什么。那是**服务器本地的文件**，界面上一个字都没有 —— 于是「它能自己
推理」这件事，用户只信一半。

这个模块把那些 trace 读成**摘要**：过程给用户看，原始日志仍旧只在服务器上。
一条 observation 可能是 256KB 的取证材料，摘要里只留个头（多少字节、头几十个字），
不把整份材料搬进界面。

只读：这个模块不写、不删、不改任何东西。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: 一次读多少个 run。
MAX_RUNS = 5

#: observation / thought 摘要的长度上限。
MAX_OBSERVATION_CHARS = 320
MAX_THOUGHT_CHARS = 240

#: 一次读多少个 trace 文件（按修改时间倒序取）。
MAX_FILES = 20


def trace_dir(data_dir: Path | str) -> Path:
    """助手 trace 的落点（与 assistant worker 写的是同一个约定）。"""
    return Path(data_dir) / "assistant" / "trace"


def _summarise_observation(observation: Any) -> str:
    """观察的摘要：先说它有多大，再说它开头是什么。"""
    if observation is None:
        return ""
    if isinstance(observation, dict):
        text = json.dumps(observation, ensure_ascii=False)
    else:
        text = str(observation)
    if not text:
        return ""
    head = text if len(text) <= MAX_OBSERVATION_CHARS else text[:MAX_OBSERVATION_CHARS] + "…"
    return f"{len(text)} 字符 · {head}"


def read_runs(data_dir: Path | str, *, limit: int = MAX_RUNS) -> list[dict[str, Any]]:
    """最近几轮助手的步骤摘要（新的在前）。"""
    directory = trace_dir(data_dir)
    if not directory.is_dir():
        return []
    files = sorted(
        (path for path in directory.glob("run-*.jsonl")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:MAX_FILES]

    runs: list[dict[str, Any]] = []
    for path in files:
        summary = _read_one(path)
        if summary is not None:
            runs.append(summary)
        if len(runs) >= limit:
            break
    return runs


def _read_one(path: Path) -> dict[str, Any] | None:
    """读一个 trace 文件：步骤 + 收尾那一行。"""
    steps: list[dict[str, Any]] = []
    tail: dict[str, Any] = {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                if "run_id" in row:
                    tail = row
                    continue
                thought = str(row.get("thought") or "")
                steps.append(
                    {
                        "no": row.get("no"),
                        "kind": row.get("kind"),
                        "tool": row.get("tool"),
                        "thought": thought
                        if len(thought) <= MAX_THOUGHT_CHARS
                        else thought[:MAX_THOUGHT_CHARS] + "…",
                        "observation": _summarise_observation(row.get("observation")),
                        "taken_at": row.get("taken_at"),
                    }
                )
    except OSError:
        return None
    if not steps and not tail:
        return None
    return {
        "run_id": tail.get("run_id") or path.stem,
        "finished_at": tail.get("finished_at"),
        "status": tail.get("status"),
        "model": tail.get("model"),
        "self_check": tail.get("self_check"),
        "calls_used": tail.get("calls_used"),
        "steps": steps,
    }
