"""预算护栏：调度前预留、结算、巡检收口与阈值事故（budget-guard）。

边界（对照 Paperclip，原生重写）：

* **预留**（`budget-reservations.ts` 的同款执行点）：claim 授予前在同一事务内
  检查 `committed = 已结算 + 在途`；超限即拒（拒绝码在 claim 层），授予即插
  held 行——零额行也拦重复派发（attempt 唯一）。
* **结算**（`run-cost-accounting.ts` 的拿手处）：submit_result 同事务结算；
  harness 上报 usage 则记实际值，**未上报记 NULL——不发明零**（policy 层按
  预留额保守计入）。reconcile 巡检把 abandon/超时/租约丢失后滞留的 held
  行收口。
* **事故**（`budget_incidents` schema）：warning（阈值%）与 exhausted（≥ 限额）
  两种，任务+阈值同时至多一条 open（部分唯一索引）；解决不删行。
* **告警**：open 事故经 alert 队列投递（`source_key=budget:<incident_id>`，
  一次/事故）。未配置 webhook 时显式不入队——重试与投递由
  `python -m app.alerts` 负责，本模块只入队。

不新增事件名（R10 §37.7 白名单不动）：预留/结算/事故都是状态投影；usage 随
submit_result 的 completion 载荷本来就会落进 task_events。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from app.alerts.service import AlertNotifier
from app.budget.policy import (
    BudgetState,
    UsageBuckets,
    admission_blocked,
    classify_observed,
    reservation_amount,
)
from app.config import settings
from app.db.connection import Database
from app.spec import OWNER_STATES

KIND_BUDGET_WARNING = "budget.warning"
KIND_BUDGET_EXHAUSTED = "budget.exhausted"

THRESHOLD_WARNING = "warning"
THRESHOLD_EXHAUSTED = "exhausted"

#: The states in which a current attempt's reservation stays held. Mirrors the
#: claim layer's owner states: the attempt is still the task's current one, so
#: the task cannot start anything else under it. Once the work is submitted the
#: row is already settled (same transaction), so verifying/review/merge never
#: hold a reservation.
_ACTIVE_STATES = tuple(sorted(state.value for state in OWNER_STATES))


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------ read side


def task_budget(conn: sqlite3.Connection, task_id: str) -> int | None:
    """The task's declared token budget, or ``None`` when it is not enforced.

    The budget lives in the plan snapshot content (``tasks[].budget_tokens``,
    R10 §8); ``>0`` enforces, ``0``/absent/missing-snapshot does not
    (backward compatible: every pre-budget plan keeps working unchanged).
    """
    row = conn.execute(
        "SELECT s.content_json FROM tasks t"
        " JOIN plan_snapshots s ON s.snapshot_id = t.plan_snapshot_id"
        " WHERE t.task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None or row["content_json"] is None:
        return None
    try:
        content = json.loads(str(row["content_json"]))
    except json.JSONDecodeError:
        return None
    if not isinstance(content, Mapping):
        return None
    for spec in content.get("tasks", ()) or ():
        if not isinstance(spec, Mapping) or str(spec.get("id") or "") != task_id:
            continue
        raw = spec.get("budget_tokens", 0)
        if isinstance(raw, bool) or not isinstance(raw, int):
            return None
        return raw if raw > 0 else None
    return None


def committed_buckets(conn: sqlite3.Connection, task_id: str) -> UsageBuckets:
    """The three evidence buckets, straight from the reservation rows."""
    row = conn.execute(
        "SELECT"
        " COALESCE(SUM(CASE WHEN state = 'settled' AND actual_tokens IS NOT NULL"
        "                   THEN actual_tokens ELSE 0 END), 0) AS settled,"
        " COALESCE(SUM(CASE WHEN state = 'settled' AND actual_tokens IS NULL"
        "                   THEN amount_tokens ELSE 0 END), 0) AS unreported,"
        " COALESCE(SUM(CASE WHEN state = 'held' THEN amount_tokens ELSE 0 END), 0)"
        "     AS held"
        " FROM budget_reservations WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    return UsageBuckets(
        settled_tokens=int(row["settled"]),
        unreported_tokens=int(row["unreported"]),
        held_tokens=int(row["held"]),
    )


def usage_tokens(completion: Mapping[str, object]) -> int | None:
    """What the harness reported, or ``None`` when it did not.

    Accepted shapes: ``harness_output.usage.total_tokens``, or both
    ``input_tokens`` and ``output_tokens``. A partial report is treated as
    unreported (conservative direction: over-count, never under-count), and
    nothing is ever inferred from a missing field.
    """
    harness_output = completion.get("harness_output")
    if not isinstance(harness_output, Mapping):
        return None
    usage = harness_output.get("usage")
    if not isinstance(usage, Mapping):
        return None

    def valid_int(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    total = valid_int(usage.get("total_tokens"))
    if total is not None:
        return total
    in_tokens = valid_int(usage.get("input_tokens"))
    out_tokens = valid_int(usage.get("output_tokens"))
    if in_tokens is None or out_tokens is None:
        return None
    return in_tokens + out_tokens


# ------------------------------------------------- write side (conn-scoped)


def reserve_blocked(conn: sqlite3.Connection, *, task_id: str, limit: int, amount: int) -> bool:
    """Admission decision for one new attempt (no writes)."""
    buckets = committed_buckets(conn, task_id)
    return admission_blocked(committed=buckets.committed, amount=amount, limit=limit)


def record_reservation(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    attempt_id: str,
    amount: int,
    now: datetime,
) -> None:
    """Insert the held row for a just-granted claim (same transaction)."""
    conn.execute(
        "INSERT INTO budget_reservations(reservation_id, task_id, attempt_id,"
        " amount_tokens, state, actual_tokens, source, note, created_at, settled_at)"
        " VALUES (?, ?, ?, ?, 'held', NULL, 'claim', NULL, ?, NULL)",
        (f"br-{uuid.uuid4().hex}", task_id, attempt_id, amount, now.isoformat()),
    )


def settle_attempt(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    attempt_id: str,
    actual_tokens: int | None,
    now: datetime,
    note: str | None = None,
) -> bool:
    """Close an attempt's hold with what was (or was not) reported.

    Returns True when this call wrote something. An attempt that never held a
    reservation (reclaim/take-over paths create attempts without one --
    declared gap) still gets a settled row **when its task enforces a
    budget**, so its usage is not silently lost; the row carries amount 0.
    Replays are no-ops: only a ``held`` row is ever touched, and a duplicate
    insert is fenced by the attempt-id uniqueness.
    """
    cursor = conn.execute(
        "UPDATE budget_reservations SET state = 'settled', actual_tokens = ?,"
        " settled_at = ?, note = COALESCE(?, note)"
        " WHERE attempt_id = ? AND state = 'held'",
        (actual_tokens, now.isoformat(), note, attempt_id),
    )
    if cursor.rowcount == 1:
        return True
    if task_budget(conn, task_id) is None:
        return False
    existing = conn.execute(
        "SELECT 1 FROM budget_reservations WHERE attempt_id = ?", (attempt_id,)
    ).fetchone()
    if existing is not None:
        return False
    attempt = conn.execute(
        "SELECT 1 FROM task_attempts WHERE attempt_id = ? AND task_id = ?",
        (attempt_id, task_id),
    ).fetchone()
    if attempt is None:
        return False
    conn.execute(
        "INSERT INTO budget_reservations(reservation_id, task_id, attempt_id,"
        " amount_tokens, state, actual_tokens, source, note, created_at, settled_at)"
        " VALUES (?, ?, ?, 0, 'settled', ?, 'attempt.unreserved', ?, ?, ?)",
        (
            f"br-{uuid.uuid4().hex}",
            task_id,
            attempt_id,
            actual_tokens,
            note,
            now.isoformat(),
            now.isoformat(),
        ),
    )
    return True


def release_attempt(conn: sqlite3.Connection, *, attempt_id: str, now: datetime, note: str) -> bool:
    """Prove no provider work happened and drop the hold (human/CLI action)."""
    cursor = conn.execute(
        "UPDATE budget_reservations SET state = 'released', settled_at = ?,"
        " note = COALESCE(?, note) WHERE attempt_id = ? AND state = 'held'",
        (now.isoformat(), note, attempt_id),
    )
    return cursor.rowcount == 1


def open_incident(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    threshold: str,
    observed: int,
    limit: int,
    now: datetime,
) -> str | None:
    """Open the incident if none is open for (task, threshold).

    Returns the new incident id, or ``None`` when one was already open --
    callers count opens, so an ignored insert must not look like a new one.
    """
    incident_id = f"bi-{uuid.uuid4().hex}"
    cursor = conn.execute(
        "INSERT OR IGNORE INTO budget_incidents(incident_id, task_id, threshold,"
        " observed_tokens, limit_tokens, status, note, created_at, resolved_at,"
        " resolved_by) VALUES (?, ?, ?, ?, ?, 'open', NULL, ?, NULL, NULL)",
        (incident_id, task_id, threshold, observed, limit, now.isoformat()),
    )
    return incident_id if cursor.rowcount == 1 else None


def raise_incidents(conn: sqlite3.Connection, *, task_id: str, now: datetime) -> tuple[str, ...]:
    """Open the threshold incident the task's committed tokens call for."""
    limit = task_budget(conn, task_id)
    if limit is None:
        return ()
    buckets = committed_buckets(conn, task_id)
    state = classify_observed(
        observed=buckets.observed,
        limit=limit,
        warn_percent=settings.budget_warn_percent,
    )
    if state is BudgetState.EXHAUSTED:
        incident = open_incident(
            conn,
            task_id=task_id,
            threshold=THRESHOLD_EXHAUSTED,
            observed=buckets.committed,
            limit=limit,
            now=now,
        )
        return () if incident is None else (incident,)
    if state is BudgetState.WARNING:
        incident = open_incident(
            conn,
            task_id=task_id,
            threshold=THRESHOLD_WARNING,
            observed=buckets.committed,
            limit=limit,
            now=now,
        )
        return () if incident is None else (incident,)
    return ()


def resolve_open_incidents(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    note: str,
    resolved_by: str,
    now: datetime,
) -> int:
    """Close every open incident of a task (human/CLI action, kept as history)."""
    cursor = conn.execute(
        "UPDATE budget_incidents SET status = 'resolved', resolved_at = ?,"
        " resolved_by = ?, note = COALESCE(?, note)"
        " WHERE task_id = ? AND status = 'open'",
        (now.isoformat(), resolved_by, note, task_id),
    )
    return int(cursor.rowcount)


def _candidate_tasks(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT task_id FROM budget_reservations"
        " UNION SELECT task_id FROM budget_incidents WHERE status = 'open'"
        " ORDER BY 1"
    ).fetchall()
    return [str(row["task_id"]) for row in rows]


def _reconcile_held(conn: sqlite3.Connection, now: datetime) -> int:
    """Settle holds whose attempt is no longer active (Paperclip: reservations
    survive restarts until closure is proven -- here closure is the attempt
    leaving the task's active ownership)."""
    placeholders = ", ".join("?" for _ in _ACTIVE_STATES)
    cursor = conn.execute(
        "UPDATE budget_reservations SET state = 'settled', actual_tokens = NULL,"
        " settled_at = ?, note = COALESCE(note, 'reconciled: attempt no longer active')"
        " WHERE state = 'held' AND NOT EXISTS ("
        "   SELECT 1 FROM tasks t JOIN task_attempts a"
        "     ON a.attempt_id = budget_reservations.attempt_id"
        "   WHERE t.task_id = budget_reservations.task_id"
        "     AND t.current_attempt_id = budget_reservations.attempt_id"
        f"     AND t.status IN ({placeholders})"
        ")",
        (now.isoformat(), *_ACTIVE_STATES),
    )
    return int(cursor.rowcount)


def _auto_resolve(conn: sqlite3.Connection, now: datetime) -> int:
    """Resolve open incidents whose condition no longer holds (e.g. after a
    human raised the budget or released an unknown hold)."""
    resolved = 0
    rows = conn.execute(
        "SELECT incident_id, task_id, threshold FROM budget_incidents"
        " WHERE status = 'open' ORDER BY incident_id"
    ).fetchall()
    for row in rows:
        task_id = str(row["task_id"])
        threshold = str(row["threshold"])
        limit = task_budget(conn, task_id)
        still_open = False
        if limit is not None:
            buckets = committed_buckets(conn, task_id)
            state = classify_observed(
                observed=buckets.observed,
                limit=limit,
                warn_percent=settings.budget_warn_percent,
            )
            if threshold == THRESHOLD_EXHAUSTED:
                still_open = state is BudgetState.EXHAUSTED
            else:
                still_open = state is BudgetState.WARNING
        if still_open:
            continue
        conn.execute(
            "UPDATE budget_incidents SET status = 'resolved', resolved_at = ?,"
            " resolved_by = 'budget-scan', note = COALESCE(note, 'auto: below threshold')"
            " WHERE incident_id = ? AND status = 'open'",
            (now.isoformat(), str(row["incident_id"])),
        )
        resolved += 1
    return resolved


# ------------------------------------------------------------- orchestration


@dataclass(frozen=True, slots=True)
class BudgetScanReport:
    held_reconciled: int
    incidents_opened: int
    incidents_resolved: int
    alerts_queued: int
    open_incidents: int

    def as_json(self) -> dict[str, object]:
        return {
            "held_reconciled": self.held_reconciled,
            "incidents_opened": self.incidents_opened,
            "incidents_resolved": self.incidents_resolved,
            "alerts_queued": self.alerts_queued,
            "open_incidents": self.open_incidents,
        }


@dataclass(frozen=True, slots=True)
class BudgetOverview:
    task_id: str
    limit_tokens: int
    settled_tokens: int
    unreported_tokens: int
    held_tokens: int
    committed_tokens: int
    state: str
    open_incidents: tuple[str, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "limit_tokens": self.limit_tokens,
            "settled_tokens": self.settled_tokens,
            "unreported_tokens": self.unreported_tokens,
            "held_tokens": self.held_tokens,
            "committed_tokens": self.committed_tokens,
            "state": self.state,
            "open_incidents": list(self.open_incidents),
        }


class BudgetGuard:
    def __init__(self, database: Database, *, notifier: AlertNotifier | None = None) -> None:
        self.database = database
        self.notifier = notifier

    # ----------------------------------------------------------------- scan

    def scan(self, *, now: datetime | None = None, enqueue: bool = True) -> BudgetScanReport:
        moment = now or datetime.now(UTC)
        with self.database.transaction() as conn:
            reconciled = _reconcile_held(conn, moment)
            opened = 0
            for task_id in _candidate_tasks(conn):
                opened += len(raise_incidents(conn, task_id=task_id, now=moment))
            resolved = _auto_resolve(conn, moment)

        queued = 0
        if enqueue and self.notifier is not None and self.notifier.webhook_url is not None:
            for incident in self.open_incidents():
                if self.notifier.enqueue(
                    kind=(
                        KIND_BUDGET_EXHAUSTED
                        if incident["threshold"] == THRESHOLD_EXHAUSTED
                        else KIND_BUDGET_WARNING
                    ),
                    task_id=str(incident["task_id"]),
                    payload={
                        "incident_id": incident["incident_id"],
                        "threshold": incident["threshold"],
                        "observed_tokens": incident["observed_tokens"],
                        "limit_tokens": incident["limit_tokens"],
                    },
                    source_key=f"budget:{incident['incident_id']}",
                ):
                    queued += 1
        return BudgetScanReport(
            held_reconciled=reconciled,
            incidents_opened=opened,
            incidents_resolved=resolved,
            alerts_queued=queued,
            open_incidents=len(self.open_incidents()),
        )

    def open_incidents(self) -> list[sqlite3.Row]:
        return self.database.read(
            "SELECT incident_id, task_id, threshold, observed_tokens, limit_tokens"
            " FROM budget_incidents WHERE status = 'open'"
            " ORDER BY created_at, incident_id"
        )

    # ------------------------------------------------------------- overview

    def overview(self) -> list[BudgetOverview]:
        rows: list[BudgetOverview] = []
        with self.database.transaction() as conn:
            open_by_task: dict[str, list[str]] = {}
            for incident in conn.execute(
                "SELECT task_id, threshold FROM budget_incidents"
                " WHERE status = 'open' ORDER BY task_id, threshold"
            ):
                open_by_task.setdefault(str(incident["task_id"]), []).append(
                    str(incident["threshold"])
                )
            for candidate in conn.execute(
                "SELECT task_id FROM budget_reservations"
                " UNION SELECT task_id FROM budget_incidents WHERE status = 'open'"
                " ORDER BY 1"
            ):
                tid = str(candidate["task_id"])
                limit = task_budget(conn, tid)
                buckets = committed_buckets(conn, tid)
                if limit is None:
                    continue
                state = classify_observed(
                    observed=buckets.observed,
                    limit=limit,
                    warn_percent=settings.budget_warn_percent,
                )
                rows.append(
                    BudgetOverview(
                        task_id=tid,
                        limit_tokens=limit,
                        settled_tokens=buckets.settled_tokens,
                        unreported_tokens=buckets.unreported_tokens,
                        held_tokens=buckets.held_tokens,
                        committed_tokens=buckets.committed,
                        state=state.value,
                        open_incidents=tuple(sorted(open_by_task.get(tid, ()))),
                    )
                )
        return rows

    # --------------------------------------------------- human/CLI actions

    def settle(self, attempt_id: str, tokens: int, note: str | None = None) -> bool:
        """Manual settlement with a human-supplied actual (unreported hold)."""
        moment = datetime.now(UTC)
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT task_id FROM budget_reservations WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return False
            written = settle_attempt(
                conn,
                task_id=str(row["task_id"]),
                attempt_id=attempt_id,
                actual_tokens=tokens,
                now=moment,
                note=note,
            )
            if written:
                raise_incidents(conn, task_id=str(row["task_id"]), now=moment)
        return written

    def release(self, attempt_id: str, note: str) -> bool:
        """Manual release: proven no provider work happened."""
        with self.database.transaction() as conn:
            return release_attempt(conn, attempt_id=attempt_id, now=datetime.now(UTC), note=note)

    def resolve(self, task_id: str, note: str, resolved_by: str = "cli") -> int:
        with self.database.transaction() as conn:
            return resolve_open_incidents(
                conn,
                task_id=task_id,
                note=note,
                resolved_by=resolved_by,
                now=datetime.now(UTC),
            )


def reservation_for_claim(limit: int) -> int:
    """The hold one attempt places, capped at the task's own budget."""
    return reservation_amount(settings.budget_reservation_tokens, limit)
