"""写一条待决事项（`task_decisions`）—— 平台与 harness 共用同一张表。

单独一个模块，是为了不制造循环依赖：`human.service` 依赖 `lease.service`
（发租约、放租约），而清扫器在租约到期时又要写决策。放进 `human.service` 会让
两边互相 import；放进这里，两边都只依赖它。

提问方有两种，形状一样：
* harness 在干活中途卡住 → `HumanOpsService.ask_human`（走 human.question 边）；
* 平台自己发现要人定夺（租约到期、合并冲突要谁返修）→ 直接写一行 + 由调用方
  走各自的状态边。平台提问也必须有记录，否则「谁让它停的」事后查不到。
"""

from __future__ import annotations

import json
import sqlite3
import uuid

__all__ = ["open_decision"]


def open_decision(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    attempt_id: object,
    actor: str,
    question: str,
    options: list[str],
    scope: str,
) -> str:
    """插入一条待决事项，返回 decision_id。"""
    decision_id = f"dec-{uuid.uuid4().hex}"
    conn.execute(
        "INSERT INTO task_decisions(decision_id, task_id, attempt_id, actor, question,"
        " options_json, answer, scope, expires_at, decided_seq, superseded_by)"
        " VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, NULL)",
        (
            decision_id,
            task_id,
            None if attempt_id is None else str(attempt_id),
            actor,
            question,
            json.dumps(options, ensure_ascii=False),
            scope,
        ),
    )
    return decision_id
