"""验收记录落盘：状态机字段 + 评审 receipt 的唯一来源。

执行器（worker）把验收判定交回平台时，平台不重算判据，只把这份记录
**归档**并在两个地方公开出来：

1. ``tasks.acceptance_state`` / ``acceptance_retries`` / ``acceptance_receipt_id``
   —— 看板直接读任务详情就能看到，不用去翻交付轨迹；
2. ``acceptance_receipts`` 行 —— 完整缺项与每一轮判定，评审 receipt
   （``verification_receipts.receipt_json``）里会挂一份摘要。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from app.db.connection import Database


def receipt_id_for(task_id: str, attempt_id: str, commit: str) -> str:
    digest = hashlib.sha256(f"{task_id}:{attempt_id}:{commit}".encode("utf-8")).hexdigest()
    return "ar-" + digest[:20]


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def record_acceptance(
    conn,
    *,
    task_id: str,
    attempt_id: str,
    commit: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """写入一行验收记录（同一 task/attempt/commit 重复提交是幂等的）。

    ``conn`` 必须是调用方已开启的事务连接：验收记录与状态迁移要么一起落下，
    要么一起回滚。
    """
    receipt_id = receipt_id_for(task_id, attempt_id, commit)
    existing = conn.execute(
        "SELECT * FROM acceptance_receipts WHERE task_id = ? AND attempt_id = ? AND head_commit = ?",
        (task_id, attempt_id, commit),
    ).fetchone()
    if existing:
        summary = _summary(dict(existing))
        _project_task(conn, task_id, summary)
        return summary

    rounds = record.get("rounds") or []
    retries = int(record.get("retries") if record.get("retries") is not None else max(len(rounds) - 1, 0))
    attempt_state = str(record.get("attempt_state") or _state_from(record.get("state")))
    retry_state = record.get("retry_state")
    profile = str(record.get("profile") or "")
    frozen = record.get("frozen") or {}
    finish_reason = record.get("finish_reason")
    created_at = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO acceptance_receipts(receipt_id, task_id, attempt_id, head_commit,"
        " profile, enabled, retries, attempt_state, retry_state, gaps_json, rounds_json,"
        " frozen_json, finish_reason, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            receipt_id,
            task_id,
            attempt_id,
            commit,
            profile,
            1,
            retries,
            attempt_state,
            retry_state,
            _dumps(record.get("gaps") or []),
            _dumps(rounds),
            _dumps(frozen),
            finish_reason,
            created_at,
        ),
    )
    row = conn.execute(
        "SELECT * FROM acceptance_receipts WHERE receipt_id = ?", (receipt_id,)
    ).fetchone()
    summary = _summary(dict(row))
    _project_task(conn, task_id, summary)
    return summary


def _state_from(state: Any) -> str:
    text = str(state or "")
    if text in {"passed", "retry_fixed"}:
        return text
    if text.startswith("unjudgeable"):
        return "unjudgeable"
    return "abandoned"


def _project_task(conn, task_id: str, summary: dict[str, Any]) -> None:
    conn.execute(
        "UPDATE tasks SET acceptance_state = ?, acceptance_retries = ?,"
        " acceptance_receipt_id = ? WHERE task_id = ?",
        (
            summary["attempt_state"],
            summary["retries"],
            summary["receipt_id"],
            task_id,
        ),
    )


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "receipt_id": row["receipt_id"],
        "task_id": row["task_id"],
        "attempt_id": row["attempt_id"],
        "commit": row["head_commit"],
        "profile": row["profile"],
        "enabled": bool(row["enabled"]),
        "retries": int(row["retries"]),
        "attempt_state": row["attempt_state"],
        "retry_state": row["retry_state"],
        "finish_reason": row["finish_reason"],
        "gaps": json.loads(row["gaps_json"]),
        "rounds": json.loads(row["rounds_json"]),
        "frozen": json.loads(row["frozen_json"]),
        "created_at": row["created_at"],
    }


def acceptance_for(
    database: Database, *, task_id: str, attempt_id: str, commit: str
) -> dict[str, Any] | None:
    rows = database.read(
        "SELECT * FROM acceptance_receipts WHERE task_id = ? AND attempt_id = ?"
        " AND head_commit = ? ORDER BY rowid DESC LIMIT 1",
        (task_id, attempt_id, commit),
    )
    return _summary(dict(rows[0])) if rows else None


def acceptance_history(database: Database, *, task_id: str, limit: int = 10) -> list[dict[str, Any]]:
    rows = database.read(
        "SELECT * FROM acceptance_receipts WHERE task_id = ? ORDER BY rowid DESC LIMIT ?",
        (task_id, limit),
    )
    return [_summary(dict(row)) for row in rows]
