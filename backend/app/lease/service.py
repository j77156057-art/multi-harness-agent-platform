"""Development lease grant, renewal, release and expiry sweep (R10 §13).

Lease timing uses the scheduler's own clock, never a Harness-reported
timestamp (R10 §13). The double anchor is ``grant_wall + grant_mono +
boot_id``: within one boot the monotonic delta is authoritative, across a boot
change the wall clock applies with a single restart exemption.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.claim.guarded_write import guarded_write
from app.claim.transitions import apply_transition
from app.config import settings
from app.db.connection import Database
from app.events.store import EventStore
from app.human.decisions import open_decision
from app.spec import OWNER_STATES, EventKind, TaskStatus

#: Process-unique boot id for the R10 §13 double anchor.
BOOT_ID = uuid.uuid4().hex

LEASE_KIND_DEVELOPMENT = "development"


class LeaseError(RuntimeError):
    """Base class for lease rejections."""


class LeaseNotFound(LeaseError):
    """No active lease for that (task, attempt)."""


class LeaseTokenMismatch(LeaseError):
    """The presented lease token does not match the stored hash."""


class LeaseExpired(LeaseError):
    """The lease deadline has passed. R10 §13: renewal rejection means the
    owner is treated as out of contact immediately."""


@dataclass(frozen=True, slots=True)
class LeaseGrant:
    lease_id: str
    token: str
    fencing_epoch: int
    lease_until: str


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def parse_iso(raw: str) -> datetime:
    moment = datetime.fromisoformat(raw)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def grant_lease(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    attempt_id: str,
    owner: str,
    fencing_epoch: int,
    seconds: int | None = None,
    now: datetime | None = None,
) -> LeaseGrant:
    """Insert one development lease. The plaintext token is returned once."""
    moment = now or utc_now()
    token = secrets.token_urlsafe(32)
    lease_id = f"lease-{uuid.uuid4().hex}"
    lease_until = iso(moment + timedelta(seconds=seconds or settings.lease_seconds))
    conn.execute(
        "INSERT INTO leases("
        "  lease_id, task_id, attempt_id, kind, owner, token_hash, fencing_epoch,"
        "  lease_until, grant_wall, grant_mono, boot_id, released_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
        (
            lease_id,
            task_id,
            attempt_id,
            LEASE_KIND_DEVELOPMENT,
            owner,
            _hash_token(token),
            fencing_epoch,
            lease_until,
            iso(moment),
            time.monotonic(),
            BOOT_ID,
        ),
    )
    return LeaseGrant(
        lease_id=lease_id,
        token=token,
        fencing_epoch=fencing_epoch,
        lease_until=lease_until,
    )


def reissue_lease_token(conn: sqlite3.Connection, *, lease_id: str) -> str:
    """Mint a fresh token for an active lease and return it once (R10 §13).

    The recovery path for a harness that lost its token: only the hash is
    stored, so the original plaintext cannot be handed back -- instead the
    hash is replaced and the previous token stops working. Callers must have
    checked that the lease is active and not expired; a dead lease has nothing
    to reissue.
    """
    token = secrets.token_urlsafe(32)
    cursor = conn.execute(
        "UPDATE leases SET token_hash = ? WHERE lease_id = ? AND released_at IS NULL",
        (_hash_token(token), lease_id),
    )
    if cursor.rowcount != 1:
        raise LeaseNotFound(f"lease {lease_id} is not active")
    return token


def renew_lease(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    attempt_id: str,
    lease_token: str,
    fencing_epoch: int,
    seconds: int | None = None,
    now: datetime | None = None,
) -> str:
    """Extend an active lease and return the new deadline."""
    moment = now or utc_now()
    guarded_write(
        conn,
        task_id=task_id,
        fencing_epoch=fencing_epoch,
        attempt_id=attempt_id,
        allowed_statuses=OWNER_STATES,
    )
    row = conn.execute(
        "SELECT lease_id, token_hash, lease_until FROM leases"
        " WHERE task_id = ? AND attempt_id = ? AND kind = ? AND released_at IS NULL",
        (task_id, attempt_id, LEASE_KIND_DEVELOPMENT),
    ).fetchone()
    if row is None:
        raise LeaseNotFound(f"no active development lease for {task_id}/{attempt_id}")
    if not secrets.compare_digest(str(row["token_hash"]), _hash_token(lease_token)):
        raise LeaseTokenMismatch(f"lease token mismatch for {task_id}/{attempt_id}")
    if parse_iso(str(row["lease_until"])) <= moment:
        raise LeaseExpired(f"lease for {task_id}/{attempt_id} already expired")
    new_until = iso(moment + timedelta(seconds=seconds or settings.lease_seconds))
    conn.execute(
        "UPDATE leases SET lease_until = ? WHERE lease_id = ?", (new_until, row["lease_id"])
    )
    return new_until


def release_lease(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    attempt_id: str,
    reason: str,
    now: datetime | None = None,
) -> int:
    """Mark the active development lease released. Returns rows touched.

    ``leases`` has no ``release_reason`` column (only ``resource_leases`` does),
    so the reason lands on the matching ``task_claims`` row instead.
    """
    moment = now or utc_now()
    cursor = conn.execute(
        "UPDATE leases SET released_at = ?"
        " WHERE task_id = ? AND attempt_id = ? AND kind = ? AND released_at IS NULL",
        (iso(moment), task_id, attempt_id, LEASE_KIND_DEVELOPMENT),
    )
    conn.execute(
        "UPDATE task_claims SET released_at = ?, release_reason = ?"
        " WHERE task_id = ? AND attempt_id = ? AND released_at IS NULL",
        (iso(moment), reason, task_id, attempt_id),
    )
    return cursor.rowcount


def release_attempt_resources(
    conn: sqlite3.Connection, *, attempt_id: str, reason: str, now: datetime | None = None
) -> int:
    """Release every ``resource_leases`` row held by an attempt."""
    moment = now or utc_now()
    cursor = conn.execute(
        "UPDATE resource_leases SET released_at = ?, release_reason = ?"
        " WHERE attempt_id = ? AND released_at IS NULL",
        (iso(moment), reason, attempt_id),
    )
    return cursor.rowcount


def expire_stale_leases(
    database: Database,
    *,
    now: datetime | None = None,
    escalated_out: list[str] | None = None,
) -> list[str]:
    """Sweep expired development leases — **ask a person before declaring a task lost**.

    R10-fix-14 把这个清扫器改成了两段（原来是"到期即判失联"）：

    1. **到期**：租约一定释放（`_released` 是机械事实 —— 那个进程不能再续期），
       但任务**不**直接转 `stale`，而是走 `lease_uncertain` 停在 `awaiting_human`，
       并在「需要你处理」里留一条：这条活的执行方断线了，等它回来还是现在换人。
       停一下的代价是这条活多等一会儿；判错的代价是**把还在干的活判死**，
       而且人会以为它没了 —— 前者小得多。
    2. **宽限期过**（`lease_uncertain_grace_minutes`）：人一直没答，才走
       `lease_expire` → `stale` 兜底。队列因此不会被无人值守的机器卡死。

    Returns the task ids that actually transitioned by lease expiry（两段都算）。
    Budget escalations are separate state changes; pass ``escalated_out`` to receive
    them too (the scheduler reports them without conflating the two lists).
    """
    moment = now or utc_now()
    cutoff = iso(moment)
    store = EventStore(database)
    transitioned: list[str] = []

    with database.transaction() as conn:
        rows = conn.execute(
            "SELECT task_id, attempt_id, fencing_epoch FROM leases"
            " WHERE kind = ? AND released_at IS NULL AND lease_until <= ?",
            (LEASE_KIND_DEVELOPMENT, cutoff),
        ).fetchall()
        for row in rows:
            task_id = str(row["task_id"])
            attempt_id = row["attempt_id"]
            task = conn.execute(
                "SELECT status, attempt_no, max_attempts, budget_override_by"
                "  FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                continue

            if attempt_id is not None:
                release_attempt_resources(
                    conn, attempt_id=str(attempt_id), reason="lease_expire", now=moment
                )
            release_lease(
                conn,
                task_id=task_id,
                attempt_id=str(attempt_id),
                reason="lease_expire",
                now=moment,
            )

            status = TaskStatus(str(task["status"]))
            if status not in OWNER_STATES:
                # 07 现场：escalated 的这条 task 在这里被 continue 跳过，attempt
                # 永远停在 in_progress（lease 已释放，人却看到「还在跑」）。
                # 非 owner 态的 attempt 也要有终态；close_attempt 幂等，已有终态
                # 时返回 False。
                if attempt_id is not None:
                    from app.tasks.service import close_attempt

                    close_attempt(
                        conn,
                        attempt_id=str(attempt_id),
                        status="failed",
                        reason="lease_expire",
                        now=moment,
                    )
                continue

            # 第一段：到期先问人，不判失联。
            event = store.append(
                event_name=EventKind.LEASE_UNCERTAIN.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="scheduler",
                actor_id="lease-sweeper",
                payload={
                    "previous_status": status.value,
                    "boot_id": BOOT_ID,
                    "grace_minutes": settings.lease_uncertain_grace_minutes,
                },
                connection=conn,
            )
            open_decision(
                conn,
                task_id=task_id,
                attempt_id=attempt_id,
                actor="scheduler",
                question=(
                    f"「{task_id}」的执行方断线了（租约到期没续）。"
                    "要等它回来，还是现在换人？"
                ),
                options=["等它回来", "现在换人", "取消这条活"],
                scope="lease_uncertain",
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=status,
                event=EventKind.LEASE_UNCERTAIN,
                expected_epoch=int(row["fencing_epoch"]),
                extra_sets={"waiting_since": iso(moment)},
                state_seq=event.seq,
            )
            transitioned.append(task_id)

        # 第二段：宽限期过了还没人回答，才按失联兜底。
        cutoff_grace = iso(moment - timedelta(minutes=settings.lease_uncertain_grace_minutes))
        for row in conn.execute(
            "SELECT t.task_id, t.current_epoch, t.current_attempt_id, d.decision_id FROM tasks t"
            "  JOIN task_decisions d ON d.task_id = t.task_id"
            " WHERE t.status = ? AND d.scope = 'lease_uncertain' AND d.answer IS NULL"
            "   AND d.decided_seq IS NULL AND t.waiting_since IS NOT NULL"
            "   AND t.waiting_since <= ?",
            (TaskStatus.AWAITING_HUMAN.value, cutoff_grace),
        ).fetchall():
            task_id = str(row["task_id"])
            event = store.append(
                event_name=EventKind.LEASE_EXPIRE.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="scheduler",
                actor_id="lease-sweeper",
                payload={"reason": "uncertain grace elapsed", "boot_id": BOOT_ID},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=TaskStatus.AWAITING_HUMAN,
                event=EventKind.LEASE_EXPIRE,
                expected_epoch=int(row["current_epoch"]),
                extra_sets={
                    "owner": None,
                    "current_attempt_id": None,
                    "waiting_since": iso(moment),
                },
                state_seq=event.seq,
            )
            conn.execute(
                "UPDATE task_decisions SET answer = ?, decided_seq = ? WHERE decision_id = ?",
                ("超时未确认，平台按失联处理", event.seq, str(row["decision_id"])),
            )
            # 人没回来，owner 阶段到此结束：attempt 不能继续假装在跑。
            if row["current_attempt_id"] is not None:
                from app.tasks.service import close_attempt

                close_attempt(
                    conn,
                    attempt_id=str(row["current_attempt_id"]),
                    status="failed",
                    reason="lease_expire",
                    now=moment,
                )
            transitioned.append(task_id)

        escalated = escalate_budget_exhausted(conn, store=store, now=moment)
        if escalated_out is not None:
            escalated_out.extend(escalated)
    return transitioned


def escalate_budget_exhausted(
    conn: sqlite3.Connection, *, store: EventStore, now: datetime | None = None
) -> list[str]:
    """Move budget-exhausted ``ready``/``queued``/``stale``/``reclaiming`` tasks to ``escalated``.

    R10 §10 defines ``attempt_budget_exhausted`` out of all four states. Running
    from the lease sweep means a task that just lost its owner is escalated in
    the same round, and a task that returns to ``ready``/``queued`` already at
    ``max_attempts`` (release, abandon/reopen, rework, resource deferral) is
    escalated on the next round instead of waiting for the generic aging
    threshold.
    """
    moment = now or utc_now()
    escalated: list[str] = []
    rows = conn.execute(
        "SELECT task_id, status, current_epoch, attempt_no, max_attempts, budget_override_by"
        "  FROM tasks"
        " WHERE status IN (?, ?, ?, ?)"
        "   AND attempt_no >= max_attempts AND budget_override_by IS NULL",
        (
            TaskStatus.READY.value,
            TaskStatus.QUEUED.value,
            TaskStatus.STALE.value,
            TaskStatus.RECLAIMING.value,
        ),
    ).fetchall()
    for row in rows:
        task_id = str(row["task_id"])
        status = TaskStatus(str(row["status"]))
        event = store.append(
            event_name=EventKind.ATTEMPT_BUDGET_EXHAUSTED.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="scheduler",
            actor_id="lease-sweeper",
            payload={"attempt_no": int(row["attempt_no"])},
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=status,
            event=EventKind.ATTEMPT_BUDGET_EXHAUSTED,
            expected_epoch=int(row["current_epoch"]),
            extra_sets={"escalated_at": iso(moment)},
            state_seq=event.seq,
        )
        escalated.append(task_id)
    return escalated
