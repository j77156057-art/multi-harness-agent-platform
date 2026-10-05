"""Single entry point for epoch-guarded writes (R10 §13 / ADR-033).

Every write that depends on the task's fencing epoch goes through
:func:`guarded_write` instead of re-deriving the stale-state check at each
call-site. The pattern is taken from AO's ``sessionguard`` (see
``docs/ao-capability-audit.md`` §3.3):

    so the stale-state check lives in one tested place instead of being
    re-derived per call-site

The guard must run inside an explicit transaction -- the caller owns
``BEGIN IMMEDIATE``, and this module only re-reads and validates.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.spec import TaskStatus


class GuardError(RuntimeError):
    """Base class for guard rejections."""


class TaskNotFound(GuardError):
    """No such task."""


class StaleFencingEpoch(GuardError):
    """The caller's fencing epoch is older than ``tasks.current_epoch``."""


class AttemptMismatch(GuardError):
    """The caller's attempt is not the task's current attempt."""


class StatusNotAllowed(GuardError):
    """The task is not in a status that permits this write."""


@dataclass(frozen=True, slots=True)
class TaskGuard:
    """The validated view of a task row at the moment of a write."""

    task_id: str
    status: TaskStatus
    current_epoch: int
    current_attempt_id: str | None
    owner: str | None


def guarded_write(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    fencing_epoch: int,
    attempt_id: str | None = None,
    allowed_statuses: frozenset[TaskStatus] | None = None,
) -> TaskGuard:
    """Re-read ``tasks`` and validate epoch, attempt and status.

    Raises :class:`GuardError` subclasses on rejection. Never mutates anything.
    """
    if not conn.in_transaction:
        raise GuardError("guarded_write must run inside an explicit transaction")

    row = conn.execute(
        "SELECT task_id, status, current_epoch, current_attempt_id, owner"
        "  FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise TaskNotFound(task_id)

    raw_status = str(row["status"])
    try:
        status = TaskStatus(raw_status)
    except ValueError as exc:
        raise GuardError(f"unknown task status in database: {raw_status!r}") from exc

    guard = TaskGuard(
        task_id=str(row["task_id"]),
        status=status,
        current_epoch=int(row["current_epoch"]),
        current_attempt_id=row["current_attempt_id"],
        owner=row["owner"],
    )

    if fencing_epoch != guard.current_epoch:
        raise StaleFencingEpoch(
            f"fencing epoch {fencing_epoch} is stale for {task_id} "
            f"(current {guard.current_epoch})"
        )
    if attempt_id is not None and attempt_id != guard.current_attempt_id:
        raise AttemptMismatch(f"attempt {attempt_id} is not current for {task_id}")
    if allowed_statuses is not None and status not in allowed_statuses:
        allowed = sorted(item.value for item in allowed_statuses)
        raise StatusNotAllowed(f"{task_id} is {status.value}, expected one of {allowed}")
    return guard
