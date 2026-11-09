"""Rebuild the SQLite projections from the append-only event log.

Why there is a checkpoint
-------------------------
The event stream predates the richer payloads the rebuild needs: the 2,216
events in ``data/state.db`` carry no task creation event, no ``from``/``to``
status and no attempt close timestamp.  Rewriting history is impossible (the
``task_events`` triggers reject UPDATE), so the operator backfills **one
checkpoint event per row** using the allowlisted observational event
``schema_migration_applied``.  Replay is then:

    latest checkpoint per row  +  later lifecycle events

``schema_version=1`` is kept because the event envelope does not change; the
payload grows a ``kind``/``table``/``row`` trio that the reducer understands.

Rebuildable vs. not
-------------------
``REBUILDABLE`` lists the tables a checkpoint carries in full, so a rebuilt
database is byte-for-byte identical for them.  ``GAPS`` lists what cannot be
rebuilt and the concrete missing fact -- see the module docstring of
``app/tools/rebuild.py`` for the operator-facing wording.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.db.connection import Database
from app.events.store import EventRecord, EventStore
from app.spec import TaskStatus
from app.state.machine import default_transition_table

#: Allowlisted R10 observational event used as the projection checkpoint.
#: R10 §37.7 freezes the 108 names; adding a dedicated ``projection.snapshot``
#: would need a spec amendment, so the existing migration/observation event is
#: used for a data-migration checkpoint (payload says what it actually is).
CHECKPOINT_EVENT = "schema_migration_applied"
CHECKPOINT_KIND = "projection.checkpoint.v1"

#: Parents before children so the rebuilt database satisfies its FKs.
REBUILDABLE: dict[str, str] = {
    "projects": "project_id",
    "groups": "group_id",
    "plans": "plan_id",
    "plan_snapshots": "snapshot_id",
    "tasks": "task_id",
    "task_attempts": "attempt_id",
    "claim_requests": "claim_request_id",
    "task_claims": "claim_id",
    "leases": "lease_id",
    "verification_receipts": "receipt_id",
    "verify_jobs": "job_id",
    "reviews": "review_id",
    "task_decisions": "decision_id",
    "acceptance_receipts": "receipt_id",
    "workspaces": "workspace_id",
}

#: Tables that objectively cannot be rebuilt from the event stream today.
#: Value = the concrete missing fact, not a "not supported" hand-wave.
GAPS: dict[str, str] = {
    "auth_tokens": (
        "只存 token 哈希；明文与哈希都不写事件流（安全要求），事件里没有任何可重放的事实"
    ),
    "task_messages": (
        "消息正文与定向 refs 从不写事件流（003 迁移明确不让消息进 task_events），缺正文"
    ),
    "artifacts": ("承载外部文件（uri/hash/size）；事件流没有文件系统观测，无法还原文件是否存在"),
    "workspaces": ("行元数据（branch/path）可随任务快照恢复，但路径上的真实目录/分支不在事件流里"),
    "capability_probes": "纯运行时探测结果，无创建/变更事件",
    "harness_registrations": "注册表变更没有事件种类，只有 created_seq 字段",
    "harness_presence": "在线状态是运行时心跳投影，无事件",
    "integration_health": "集成头状态由合并动作隐式改变，没有承载它的字段级事件",
    "side_effect_intents": "副作用意图是运行时幂等表，事件只描述结果不描述意图",
    "command_idempotency": "命令幂等键没有事件，只在表里",
    "sweep_progress": "巡检进度是调度器内部状态，无事件",
    "budget_reservations": "额度预留/结算没有事件种类（011 迁移选择只随 submit_result 落 usage）",
    "budget_incidents": "额度事故由运行时计算得出，无事件种类",
    "message_deliveries": "消息投递状态没有事件",
    "circulation_reviews": "A 类事件只有 review_id/round_no/material_hash，评审正文缺失",
    "circulation_rounds": "轮次骨架可推，评审内容与截止时间缺失",
    "allocations": "A 类事件只有 ID 与轮次，分配内容缺失",
    "allocation_decisions": "decision 事件含选择结果但不含完整比较材料",
    "allocation_rounds": "轮次骨架可推，材料缺失",
    "applications": "application.submitted 只有 harness_id/revision/task_ids，申请材料缺失",
    "application_tasks": "关联表没有事件",
    "application_materials": "材料正文缺失",
}

#: task_events is copied verbatim, so it is included in the diff even though it
#: is not produced by a reducer.
COPIED_TABLES: dict[str, str] = {"task_events": "seq"}


def _row_dicts(conn: sqlite3.Connection, table: str, pk: str) -> list[dict[str, Any]]:
    cursor = conn.execute(f"SELECT * FROM {table} ORDER BY {pk}")
    return [dict(row) for row in cursor.fetchall()]


def _jsonable(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    return value


def _jsonable_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _jsonable(value) for key, value in row.items()}


def append_checkpoints(
    database: Database,
    *,
    actor_type: str = "scheduler",
    actor_id: str = "projection-checkpoint",
) -> int:
    """Append one checkpoint event per row of every rebuildable table.

    Idempotent in effect: the reducer keeps the **last** checkpoint seen per
    row, so running this twice only adds events.  Run it before a rebuild, or
    on a schedule, so the log carries a recent full image.
    """
    store = EventStore(database)
    written = 0
    with database.transaction() as conn:
        for table, pk in REBUILDABLE.items():
            for row in _row_dicts(conn, table, pk):
                store.append(
                    event_name=CHECKPOINT_EVENT,
                    aggregate_type=table,
                    aggregate_id=str(row[pk]),
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload={
                        "kind": CHECKPOINT_KIND,
                        "table": table,
                        "row": _jsonable_row(row),
                    },
                    connection=conn,
                )
                written += 1
    return written


def _is_checkpoint(event: EventRecord) -> bool:
    return (
        event.event_name == CHECKPOINT_EVENT
        and event.payload.get("kind") == CHECKPOINT_KIND
        and isinstance(event.payload.get("table"), str)
        and isinstance(event.payload.get("row"), dict)
    )


def _upsert(
    projection: dict[str, dict[str, dict[str, Any]]], table: str, row: dict[str, Any]
) -> None:
    pk = REBUILDABLE[table]
    key = str(row[pk])
    projection[table][key] = row


def fold(
    events: Iterable[EventRecord],
) -> tuple[dict[str, dict[str, dict[str, Any]]], int]:
    """Fold the event log into ``{table: {pk: row}}``.

    Checkpoints set full rows.  Lifecycle events that carry an explicit
    ``state``/``attempt`` snapshot overwrite the matching row, and a plain
    status event advances ``tasks.status`` through the R10 transition table.
    Returns the projection and the seq of the last checkpoint seen.
    """
    projection: dict[str, dict[str, dict[str, Any]]] = {table: {} for table in REBUILDABLE}
    checkpoint_seq = 0
    for event in events:
        if _is_checkpoint(event):
            table = str(event.payload["table"])
            if table in projection:
                _upsert(projection, table, dict(event.payload["row"]))
                checkpoint_seq = max(checkpoint_seq, event.seq)
            continue
        if event.aggregate_type != "task":
            continue
        state = event.payload.get("state")
        if isinstance(state, dict) and "task_id" in state:
            _upsert(projection, "tasks", dict(state))
        attempt = event.payload.get("attempt")
        if isinstance(attempt, dict) and "attempt_id" in attempt:
            _upsert(projection, "task_attempts", dict(attempt))
        _advance_status(projection, event)
    return projection, checkpoint_seq


def _advance_status(projection: dict[str, dict[str, dict[str, Any]]], event: EventRecord) -> None:
    """Advance a checkpointed task through a later lifecycle event.

    This is the delta half of the rebuild.  It only touches status and
    ``state_changed_seq``; other columns changed by that transaction need the
    next checkpoint, which ``diff`` reports explicitly.
    """
    task = projection["tasks"].get(event.aggregate_id)
    if task is None:
        return
    try:
        current = TaskStatus(str(task["status"]))
        nxt = default_transition_table().resolve(current, _event_kind(event.event_name)).next
    except Exception:
        return
    if not isinstance(nxt, TaskStatus):
        return
    if nxt is not current:
        task["status"] = nxt.value
        task["state_changed_seq"] = event.seq


def _event_kind(name: str) -> Any:
    from app.spec import EventKind

    return EventKind(name)


@dataclass(frozen=True, slots=True)
class TableDiff:
    table: str
    source_rows: int
    target_rows: int
    missing_in_target: list[str] = field(default_factory=list)
    extra_in_target: list[str] = field(default_factory=list)
    changed: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return (
            not self.missing_in_target
            and not self.extra_in_target
            and not self.changed
            and self.source_rows == self.target_rows
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "source_rows": self.source_rows,
            "target_rows": self.target_rows,
            "missing_in_target": self.missing_in_target[:20],
            "extra_in_target": self.extra_in_target[:20],
            "changed": dict(list(self.changed.items())[:20]),
            "ok": self.ok,
        }


@dataclass(frozen=True, slots=True)
class RebuildResult:
    target: Path
    events: int
    checkpoint_events: int
    checkpoint_seq: int
    rows: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": str(self.target),
            "events": self.events,
            "checkpoint_events": self.checkpoint_events,
            "checkpoint_seq": self.checkpoint_seq,
            "rows": self.rows,
        }


def _open_readonly(path: Path) -> sqlite3.Connection:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rebuild(source: Path | str, target: Path | str, *, force: bool = False) -> RebuildResult:
    """Replay ``source``'s event log into a fresh database at ``target``.

    The new database is built beside the target (``<target>.tmp``) and moved
    into place with :func:`os.replace` only after ``foreign_key_check`` passes,
    so a failed run never leaves a half-written projection behind.
    """
    source_path = Path(source)
    target_path = Path(target)
    if not source_path.exists():
        raise FileNotFoundError(f"source database not found: {source_path}")
    if target_path.exists() and not force:
        raise FileExistsError(f"target exists; pass --force to replace: {target_path}")
    temp_path = target_path.with_name(target_path.name + ".tmp")
    if temp_path.exists():
        temp_path.unlink()

    events: list[EventRecord] = []
    source_conn = _open_readonly(source_path)
    try:
        store = EventStore(Database(source_path))
        events = list(store.iter_since(0))
    finally:
        source_conn.close()

    projection, checkpoint_seq = fold(events)
    checkpoint_events = sum(1 for event in events if _is_checkpoint(event))

    database = Database(temp_path)
    database.initialize()
    try:
        with database.connect() as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for event in events:
                    conn.execute(
                        "INSERT INTO task_events(seq, event_name, aggregate_type,"
                        " aggregate_id, actor_type, actor_id, payload_json,"
                        " payload_hash, schema_version, created_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            event.seq,
                            event.event_name,
                            event.aggregate_type,
                            event.aggregate_id,
                            event.actor_type,
                            event.actor_id,
                            json.dumps(
                                event.payload,
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            event.payload_hash,
                            event.schema_version,
                            event.created_at,
                        ),
                    )
                for table in REBUILDABLE:
                    for row in projection[table].values():
                        columns = list(row)
                        placeholders = ", ".join(["?"] * len(columns))
                        quoted = ", ".join(f'"{column}"' for column in columns)
                        conn.execute(
                            f'INSERT OR REPLACE INTO "{table}" ({quoted})'
                            f" VALUES ({placeholders})",
                            [row[column] for column in columns],
                        )
                conn.execute(
                    "UPDATE global_seq SET seq = (SELECT MAX(value) FROM ("
                    " SELECT COALESCE(MAX(seq), 0) AS value FROM task_events"
                    " UNION ALL SELECT COALESCE(MAX(created_seq), 0) FROM task_messages"
                    "))"
                )
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"foreign key check failed: {violations[:5]}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except BaseException:
        if temp_path.exists():
            temp_path.unlink()
        raise

    import os

    os.replace(temp_path, target_path)
    rows = {table: len(projection[table]) for table in REBUILDABLE}
    return RebuildResult(
        target=target_path,
        events=len(events),
        checkpoint_events=checkpoint_events,
        checkpoint_seq=checkpoint_seq,
        rows=rows,
    )


def diff(
    source: Path | str, target: Path | str, *, tables: Iterable[str] | None = None
) -> dict[str, TableDiff]:
    """Compare every rebuildable table (plus the copied event log) row by row."""
    source_conn = _open_readonly(Path(source))
    target_conn = _open_readonly(Path(target))
    try:
        wanted = dict(REBUILDABLE)
        wanted.update(COPIED_TABLES)
        selected = list(tables) if tables is not None else list(wanted)
        result: dict[str, TableDiff] = {}
        for table in selected:
            pk = wanted[table]
            source_rows = {
                str(row[pk]): dict(row) for row in source_conn.execute(f"SELECT * FROM {table}")
            }
            target_rows = {
                str(row[pk]): dict(row) for row in target_conn.execute(f"SELECT * FROM {table}")
            }
            missing = sorted(set(source_rows) - set(target_rows))
            extra = sorted(set(target_rows) - set(source_rows))
            changed: dict[str, list[str]] = {}
            for key in sorted(set(source_rows) & set(target_rows)):
                differing = [
                    column
                    for column in source_rows[key]
                    if source_rows[key][column] != target_rows[key].get(column)
                ]
                if differing:
                    changed[key] = differing
            result[table] = TableDiff(
                table=table,
                source_rows=len(source_rows),
                target_rows=len(target_rows),
                missing_in_target=missing,
                extra_in_target=extra,
                changed=changed,
            )
        return result
    finally:
        source_conn.close()
        target_conn.close()


def gap_report(source: Path | str) -> dict[str, dict[str, Any]]:
    """Return row counts plus the concrete missing fact for every gap table."""
    conn = _open_readonly(Path(source))
    try:
        existing = {
            str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        report: dict[str, dict[str, Any]] = {}
        for table, reason in GAPS.items():
            rows = (
                int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if table in existing
                else 0
            )
            report[table] = {"rows": rows, "gap": reason}
        return report
    finally:
        conn.close()
