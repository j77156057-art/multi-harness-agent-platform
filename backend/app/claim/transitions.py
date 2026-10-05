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


def apply_transition(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    current: TaskStatus,
    event: EventKind,
    expected_epoch: int | None = None,
    extra_sets: dict[str, object] | None = None,
    state_seq: int | None = None,
) -> TaskStatus:
    """Conditionally UPDATE ``tasks.status`` and assert exactly one row changed.

    The WHERE clause restates the expected status (and epoch when given) so a
    concurrent writer that got there first produces ``rowcount == 0`` -- a
    :class:`ConcurrentStateChange` -- rather than a lost update.

    ``state_changed_seq`` is written **only when the status actually changes**.
    R10 §10 requires self loops to leave ``state_changed_seq`` (and
    ``waiting_since``) untouched; passing ``state_seq`` on a self loop is
    therefore ignored rather than silently applied.
    """
    nxt = resolve_next(current, event)

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
    return nxt
