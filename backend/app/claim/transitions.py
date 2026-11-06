"""Apply R10 §10 transitions to the ``tasks`` row (MVP-04).

Lives under ``app/claim/`` because MVP-04's allowed paths are ``app/claim/**``
and ``app/lease/**``; the natural home would be ``app/state/``, which belongs
to MVP-03.

The next status is never hard-coded here. It is resolved through MVP-03's
transition table, so an event that R10 does not allow from the current status
raises :class:`IllegalTransition` instead of quietly writing a new status.
"""

from __future__ import annotations

import sqlite3

from app.spec import EventKind, TaskStatus
from app.state.machine import TransitionTable, default_transition_table

_TABLE: TransitionTable = default_transition_table()


class IllegalTransition(RuntimeError):
    """R10 §10 defines no edge for this (status, event)."""


class ConcurrentStateChange(RuntimeError):
    """The conditional UPDATE matched 0 rows -- someone else moved the task."""


def resolve_next(current: TaskStatus, event: EventKind) -> TaskStatus:
    """Return the single next status for ``(current, event)`` per R10 §10."""
    transition = _TABLE.resolve(current, event)
    nxt = transition.next
    if not isinstance(nxt, TaskStatus):
        raise IllegalTransition(f"{event.value} yields pseudo state {nxt!r}, not a task status")
    return nxt


def is_self_loop(current: TaskStatus, event: EventKind) -> bool:
    """True when the edge keeps the task in the same status (R10 §10 self loop)."""
    return resolve_next(current, event) is current


#: 任务落到这些状态，当前 attempt 就不可能再被继续使用：交付、取消、放弃、
#: 升级、退回重做、失联回收、放回队列。这是**唯一**的收口清单 —— 各条路径
#: 不再各自调 close_attempt，否则总会漏掉一条（2026-11-05 的 escalation /
#: verify_passed 两条就是这么漏的）。
_ATTEMPT_CLOSED_AS: dict[TaskStatus, str] = {
    TaskStatus.MERGED: "merged",
    TaskStatus.CANCELLED: "cancelled",
    TaskStatus.ABANDONED: "abandoned",
    TaskStatus.ESCALATED: "escalated",
    TaskStatus.CHANGES_REQUESTED: "failed",
    TaskStatus.STALE: "failed",
    TaskStatus.RECLAIMING: "failed",
    TaskStatus.READY: "released",
}


def _attempt_close_status(current: TaskStatus, nxt: TaskStatus) -> str | None:
    """Return the attempt status to close with, or ``None`` to keep it open."""
    status = _ATTEMPT_CLOSED_AS.get(nxt)
    if status is not None:
        return status
    # verify_passed 是 2026-11-05 那 8 条「verifying 且 closed_at 为空」的根因：
    # 任务进了 awaiting_review，attempt 却没有任何人收口。其余离开 verifying
    # 的边（env 重排、drift、awaiting_human）都还能回到同一个 attempt，不能关。
    if current is TaskStatus.VERIFYING and nxt is TaskStatus.AWAITING_REVIEW:
        return "released"
    return None


def apply_transition(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    current: TaskStatus,
    event: EventKind,
    expected_epoch: int | None = None,
    extra_sets: dict[str, object] | None = None,
    state_seq: int | None = None,
    attempt_close_reason: str | None = None,
) -> TaskStatus:
    """Conditionally UPDATE ``tasks.status`` and assert exactly one row changed.

    The WHERE clause restates the expected status (and epoch when given) so a
    concurrent writer that got there first produces ``rowcount == 0`` -- a
    :class:`ConcurrentStateChange` -- rather than a lost update.

    ``state_changed_seq`` is written **only when the status actually changes**.
    R10 §10 requires self loops to leave ``state_changed_seq`` (and
    ``waiting_since``) untouched; passing ``state_seq`` on a self loop is
    therefore ignored rather than silently applied.

    This is also the single point where an attempt is closed: whenever the new
    status means the current attempt can never be used again, the attempt gets
    ``closed_at`` / ``close_reason`` here (``attempt_close_reason`` carries a
    human's own words for release/abandon; otherwise the event name is written,
    so the closing edge stays visible afterwards).
    """
    nxt = resolve_next(current, event)

    # Read **before** the UPDATE: several edges (release / reopen / expiry)
    # clear ``current_attempt_id`` in the same statement.
    attempt_id: str | None = None
    if nxt is not current:
        row = conn.execute(
            "SELECT current_attempt_id FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        attempt_id = row[0] if row is not None else None

    assignments = ["status = ?"]
    params: list[object] = [nxt.value]
    for column, value in (extra_sets or {}).items():
        assignments.append(f"{column} = ?")
        params.append(value)
    if state_seq is not None and nxt is not current:
        assignments.append("state_changed_seq = ?")
        params.append(state_seq)

    where = "task_id = ? AND status = ?"
    params.extend([task_id, current.value])
    if expected_epoch is not None:
        where += " AND current_epoch = ?"
        params.append(expected_epoch)

    cursor = conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE {where}", params)
    if cursor.rowcount != 1:
        raise ConcurrentStateChange(
            f"conditional update for {task_id} on {event.value} touched {cursor.rowcount} rows"
        )

    closing = _attempt_close_status(current, nxt)
    if closing is not None:
        # Local import: ``app.tasks.service`` imports this module at load time.
        from app.tasks.service import finish_attempt

        finish_attempt(
            conn,
            attempt_id=attempt_id,
            status=closing,
            reason=attempt_close_reason or event.value,
        )
    return nxt
