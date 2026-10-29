"""The R10 §18 human decision surface (human-ops).

Six operations, each on the R10 §10 edge registered for it:

=========== =================================  ==================================
operation   admitted from                     event → target
=========== =================================  ==================================
ask_human   in_progress                       ``human.question`` → awaiting_human
resume      awaiting_human                    ``human.resume`` → in_progress
            plan_drift/impl_drift with owner  ``human.resume`` → in_progress
            plan_drift/impl_drift, no owner   ``human.reopen_no_owner`` → ready
reopen      awaiting_human / drift            ``human.reopen`` → ready
            abandoned                         ``reopen`` → ready
release     escalated                         ``human.release`` → ready
adjudicate  escalated                         ``human.adjudicate`` → awaiting_human
cancel      queued                            ``queue_cancel`` → cancelled
            stale/reclaiming/awaiting_human   ``human.cancel`` → cancelled
            the table's other cancel states   ``cancel`` → cancelled
=========== =================================  ==================================

``release`` of a task whose ``attempt_no`` has reached ``max_attempts`` must
carry the R10 §8 ``attempt_budget_override``; a plain release is refused so the
task cannot loop back to ``ready`` and fail the same claim guard. The release
event records the override; the decision row keeps the rationale.

Lease semantics are the §18 literal: entering ``awaiting_human`` releases the
development lease (the owner clock pauses); ``resume`` re-issues one for the
*same* attempt and epoch -- no new attempt, no epoch bump. Drift states may
still hold an unreleased lease (the drift sweep only freezes the attempt), so
resume releases any survivor before granting, keeping the one-active-lease
invariant.

Decisions: ``ask_human`` and ``adjudicate`` open a ``task_decisions`` row;
``resume``/``reopen`` close every open row for the task by writing ``answer``
and the seq of the consuming event. The table has no ``decided_by`` column,
so authorship lives in that event's payload (``decided_by``) -- the decision
row's ``decided_seq`` is the pointer to it; the answer itself is stored raw.

Identity is an explicit parameter, like ``capabilities()``: v1 has no
authentication layer, so §18's pinned-decision rules and the harness-token
403 semantics are recorded gaps, not faked.

``cancel`` is scoped to this slice's side effects: development and resource
leases released, claimed review rows expired. The bare verdict on the other
independent leases (review/verify/merge kinds) and in-flight intent
reconciliation belong to the verify/merge lines, downstream propagation to
MERGE-REVERT-002, and worktree recycling to the operator (needs the archive
root) -- declared gaps, per the card.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from app.claim.guarded_write import guarded_write
from app.claim.transitions import apply_transition
from app.db.connection import Database
from app.events.store import EventRecord, EventStore
from app.lease.service import grant_lease, release_attempt_resources, release_lease
from app.spec import OWNER_STATES, EventKind, TaskStatus
from app.tasks.service import Conflict, NotFound

#: R10 L421's plain ``cancel`` allowlist minus ``queued``: that state has its
#: own, more specific edge (``queue_cancel``), and the three states below have
#: the human-specific one. Chosen by current status in ``cancel``.
_CANCEL_STATES = frozenset(
    {
        TaskStatus.DRAFT,
        TaskStatus.READY,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.VERIFYING,
        TaskStatus.NEEDS_HELP,
        TaskStatus.BLOCKED,
        TaskStatus.CHANGES_REQUESTED,
        TaskStatus.PLAN_DRIFT,
        TaskStatus.IMPL_DRIFT,
        TaskStatus.AWAITING_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.MERGING,
    }
)
_HUMAN_CANCEL_STATES = frozenset(
    {TaskStatus.STALE, TaskStatus.RECLAIMING, TaskStatus.AWAITING_HUMAN}
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class HumanOpsService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.store = EventStore(database)

    # ------------------------------------------------------------------- ask

    def ask_human(
        self,
        *,
        task_id: str,
        question: str,
        options: list[str] | None = None,
        actor: str = "harness",
        scope: str = "task",
        attempt_id: str | None = None,
        fencing_epoch: int | None = None,
    ) -> dict[str, object]:
        """The §7 ``ask_human`` verb: park the task for a human decision.

        ``fencing_epoch`` (with ``attempt_id``) turns on the §13 owner guard the
        harness routes use, so a stale owner cannot park a task someone else now
        holds; the human routes call without it.
        """
        if not question.strip():
            raise Conflict("question must not be empty")
        with self.database.transaction() as conn:
            if fencing_epoch is not None:
                guarded_write(
                    conn,
                    task_id=task_id,
                    fencing_epoch=fencing_epoch,
                    attempt_id=attempt_id,
                    allowed_statuses=OWNER_STATES,
                )
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.IN_PROGRESS:
                raise Conflict(f"{task_id} is {status.value}; human.question needs in_progress")
            decision_id = self._open_decision(
                conn,
                task_id=task_id,
                attempt_id=task["current_attempt_id"],
                actor=actor,
                question=question,
                options=options or [],
                scope=scope,
            )
            record, _ = self._transition(
                conn,
                task,
                task_id=task_id,
                event=EventKind.HUMAN_QUESTION,
                actor_type="harness",
                actor_id=actor,
                payload={"decision_id": decision_id, "question": question, "scope": scope},
                extra_sets={"waiting_since": _now(), "next_reminder_at": None},
            )
            if task["current_attempt_id"] is not None:
                # §18: the owner lease clock pauses on entry; resume re-issues.
                release_lease(
                    conn,
                    task_id=task_id,
                    attempt_id=str(task["current_attempt_id"]),
                    reason="human_question",
                )
            return {"task_id": task_id, "decision_id": decision_id, "seq": record.seq}

    # ------------------------------------------------------------ human ops

    def resume(
        self, *, task_id: str, decided_by: str, answer: str | None = None
    ) -> dict[str, object]:
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is TaskStatus.AWAITING_HUMAN:
                event = EventKind.HUMAN_RESUME
                reissue_lease = True
            elif status in (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT):
                if task["owner"] is None:
                    event = EventKind.HUMAN_REOPEN_NO_OWNER
                    reissue_lease = False
                else:
                    event = EventKind.HUMAN_RESUME
                    reissue_lease = True
            else:
                raise Conflict(
                    f"{task_id} is {status.value}; resume needs awaiting_human or a drift state"
                )
            extra: dict[str, object] = {"waiting_since": None, "next_reminder_at": None}
            if not reissue_lease:
                # ``human.reopen_no_owner`` lands on ``ready``: the next claim
                # builds a fresh attempt, and claim requires these two cleared.
                extra.update({"owner": None, "current_attempt_id": None})
            record, nxt = self._transition(
                conn,
                task,
                task_id=task_id,
                event=event,
                actor_type="human",
                actor_id=decided_by,
                payload={"decided_by": decided_by},
                extra_sets=extra,
            )
            attempt_id = task["current_attempt_id"]
            owner = task["owner"]
            if reissue_lease and attempt_id is not None and owner is not None:
                # §18: same attempt, same epoch -- a resume is not a new attempt.
                # A drift state may have left its lease unreleased; drop it so
                # exactly one development lease stays active.
                release_lease(
                    conn, task_id=task_id, attempt_id=str(attempt_id), reason="human_resume"
                )
                grant_lease(
                    conn,
                    task_id=task_id,
                    attempt_id=str(attempt_id),
                    owner=str(owner),
                    fencing_epoch=int(task["current_epoch"]),
                )
            self._answer_decisions(conn, task_id=task_id, answer=answer, decided_seq=record.seq)
            return {
                "task_id": task_id,
                "event": record.event_name,
                "seq": record.seq,
                "status": nxt.value,
            }

    def reopen(
        self, *, task_id: str, decided_by: str, answer: str | None = None
    ) -> dict[str, object]:
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is TaskStatus.ABANDONED:
                event = EventKind.REOPEN
            elif status in (
                TaskStatus.AWAITING_HUMAN,
                TaskStatus.PLAN_DRIFT,
                TaskStatus.IMPL_DRIFT,
            ):
                event = EventKind.HUMAN_REOPEN
            else:
                raise Conflict(
                    f"{task_id} is {status.value}; reopen needs awaiting_human, a drift state or abandoned"
                )
            record, nxt = self._transition(
                conn,
                task,
                task_id=task_id,
                event=event,
                actor_type="human",
                actor_id=decided_by,
                payload={"decided_by": decided_by},
                extra_sets={
                    "waiting_since": None,
                    "next_reminder_at": None,
                    "owner": None,
                    "current_attempt_id": None,
                },
            )
            self._answer_decisions(conn, task_id=task_id, answer=answer, decided_seq=record.seq)
            return {
                "task_id": task_id,
                "event": record.event_name,
                "seq": record.seq,
                "status": nxt.value,
            }

    def grant_attempt_budget_override(
        self,
        *,
        task_id: str,
        decided_by: str,
        reason: str = "",
    ) -> dict[str, object]:
        """Let a task whose attempt budget is spent be claimed again.

        R10 §8: once ``attempt_no`` has reached ``max_attempts`` a plain
        release would just send the task back to ``ready`` where the next claim
        is refused by the same budget guard.  The human therefore carries the
        override.

        This is the human's own move -- the platform does not grant it to
        itself, and the administrator harness does not either.  It changes **no
        task state** (the task stays where it is; only the *claim guard* learns
        that the attempt counter no longer caps this task), so it needs no new
        state edge and no new event name: the decision row is the audit trail,
        and the next claim records the override in its own payload.

        What the override actually does (2026-10-29 澄清过一次)：it **removes the
        cap**, it does not add a single extra attempt.  The claim guard is
        ``attempt_no < max_attempts OR budget_override_by IS NOT NULL``, so once a
        human has overridden, that task's attempt counter stops being a limit
        (a real run reached attempt 8 against a cap of 3).  The wording in the
        office says "不再限制尝试次数" for exactly this reason -- promising "one
        more chance" would be a promise the platform does not keep.
        """
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            exhausted = int(task["attempt_no"]) >= int(task["max_attempts"])
            if not exhausted:
                raise Conflict(
                    f"{task_id} has used {task['attempt_no']} of {task['max_attempts']} attempts; "
                    "an attempt budget override only applies after the budget is spent"
                )
            if task["budget_override_by"] is not None:
                raise Conflict(
                    f"{task_id} already carries an attempt budget override "
                    f"({task['budget_override_by']})"
                )
            conn.execute(
                "UPDATE tasks SET budget_override_by = ? WHERE task_id = ?",
                (decided_by, task_id),
            )
            # 决定行要锚在一个事件序号上。这条动作**不新造事件名**（规范里
            # 的事件集是闭集），所以锚在该任务最后一个事件上：读历史的人能看到
            # "在这个时间点上，人给了一次机会"。
            anchor = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM task_events WHERE aggregate_id = ?",
                (task_id,),
            ).fetchone()
            self._record_budget_override(
                conn,
                task_id=task_id,
                attempt_id=task["current_attempt_id"],
                decided_by=decided_by,
                reason=reason,
                decided_seq=int(anchor["seq"] or 0),
            )
        return {
            "task_id": task_id,
            "attempt_budget_override": True,
            "budget_override_by": decided_by,
            "attempt_no": int(task["attempt_no"]),
            "max_attempts": int(task["max_attempts"]),
        }

    def grant_review_same_provider(
        self,
        *,
        task_id: str,
        decided_by: str,
        reason: str = "",
    ) -> dict[str, object]:
        """Open the same-provider review channel for **one** task.

        ``review_policy`` stays ``cross_provider``. What is authorized here is
        narrower than a policy change: for this task, a reviewer whose provider
        equals the owner's may claim and submit the review -- and the review row
        records who opened the channel (``provider_confirmed_by``) together with
        the decision it points at (``provider_override_decision_id``), so a
        same-provider approve can never be read as a cross-provider one.

        Why the human and not the reviewer: a reviewer able to waive its own
        provider check turns ``cross_provider`` into a suggestion (R10 §6 --
        self-report is not authorization). The platform does not grant it to
        itself either, and neither does the administrator harness.

        Only while a review is actually pending (``awaiting_review``): handing
        the waiver out earlier would let a task be pre-authorized before anyone
        knows which windows are available, and the default would stop meaning
        anything. Like the attempt-budget override this creates **no new event
        name** -- the decision row is the audit trail, anchored on the task's
        last event seq, and the review itself records the reference.
        """
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.AWAITING_REVIEW:
                raise Conflict(
                    f"{task_id} is {status.value}; the same-provider review channel"
                    " only applies while a review is pending",
                    code="review.same_provider_not_pending",
                )
            already = conn.execute(
                "SELECT decision_id, actor FROM task_decisions"
                " WHERE task_id = ? AND scope = 'review_same_provider'"
                "   AND superseded_by IS NULL ORDER BY rowid DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if already is not None:
                raise Conflict(
                    f"{task_id} already has the same-provider review channel open"
                    f" (opened by {already['actor']}, decision {already['decision_id']})",
                    code="review.same_provider_already_open",
                )
            anchor = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS seq FROM task_events WHERE aggregate_id = ?",
                (task_id,),
            ).fetchone()
            decision_id = f"dec-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO task_decisions(decision_id, task_id, attempt_id, actor, question,"
                " options_json, answer, scope, expires_at, decided_seq, superseded_by)"
                " VALUES (?, ?, ?, ?, ?, '[]', ?, 'review_same_provider', NULL, ?, NULL)",
                (
                    decision_id,
                    task_id,
                    task["current_attempt_id"],
                    decided_by,
                    "same-provider review channel",
                    reason or "approved",
                    int(anchor["seq"] or 0),
                ),
            )
        return {
            "task_id": task_id,
            "same_provider_review": True,
            "decision_id": decision_id,
            "confirmed_by": decided_by,
            "review_policy": str(task["review_policy"]),
            "attempt_id": task["current_attempt_id"],
        }

    def release(
        self,
        *,
        task_id: str,
        decided_by: str,
        budget_override: bool = False,
        reason: str = "",
    ) -> dict[str, object]:
        """``escalated -> ready``; a budget-exhausted task needs the override.

        R10 §8 / R8-08 is deliberately strict: once ``attempt_no`` has reached
        ``max_attempts`` a plain release would just send the task back to
        ``ready`` where the next claim is refused by the same budget guard.
        The human must therefore carry ``attempt_budget_override``; the release
        event records it and the decision row keeps the rationale.
        """
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.ESCALATED:
                raise Conflict(f"{task_id} is {status.value}; release needs escalated")
            exhausted = int(task["attempt_no"]) >= int(task["max_attempts"])
            existing_override = task["budget_override_by"]
            if budget_override and not exhausted:
                raise Conflict(
                    f"{task_id} has used {task['attempt_no']} of {task['max_attempts']} attempts;"
                    " budget_override only applies after the attempt budget is exhausted"
                )
            if exhausted and existing_override is None and not budget_override:
                raise Conflict(
                    f"{task_id} is over its attempt budget; release requires budget_override"
                    " (R10 §8 attempt_budget_override)"
                )
            payload: dict[str, object] = {"decided_by": decided_by}
            if reason:
                payload["reason"] = reason
            if budget_override:
                payload["attempt_budget_override"] = True
                payload["budget_override_by"] = existing_override or decided_by
            record, nxt = self._transition(
                conn,
                task,
                task_id=task_id,
                event=EventKind.HUMAN_RELEASE,
                actor_type="human",
                actor_id=decided_by,
                payload=payload,
                extra_sets={
                    "waiting_since": None,
                    "next_reminder_at": None,
                    "escalated_at": None,
                    # 放回队列 = 没有主人、也没有当前 attempt。留着那个死掉的
                    # attempt 的话，这条任务看着是 ready，却**没人能领**
                    # （可领列表会跳过还挂着 attempt 的任务）——一个隐形僵尸。
                    "owner": None,
                    "current_attempt_id": None,
                    "queue_reason": None,
                },
            )
            if task["current_attempt_id"] is not None:
                release_attempt_resources(
                    conn, attempt_id=str(task["current_attempt_id"]), reason="release"
                )
                release_lease(
                    conn,
                    task_id=task_id,
                    attempt_id=str(task["current_attempt_id"]),
                    reason="release",
                )
            override_by = existing_override
            if budget_override:
                if existing_override is None:
                    conn.execute(
                        "UPDATE tasks SET budget_override_by = ? WHERE task_id = ?",
                        (decided_by, task_id),
                    )
                    override_by = decided_by
                self._record_budget_override(
                    conn,
                    task_id=task_id,
                    attempt_id=task["current_attempt_id"],
                    decided_by=decided_by,
                    reason=reason,
                    decided_seq=record.seq,
                )
            return {
                "task_id": task_id,
                "event": record.event_name,
                "seq": record.seq,
                "status": nxt.value,
                "budget_override_by": override_by,
            }

    def adjudicate(
        self,
        *,
        task_id: str,
        decided_by: str,
        question: str,
        options: list[str] | None = None,
    ) -> dict[str, object]:
        if not question.strip():
            raise Conflict("question must not be empty")
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.ESCALATED:
                raise Conflict(f"{task_id} is {status.value}; adjudicate needs escalated")
            decision_id = self._open_decision(
                conn,
                task_id=task_id,
                attempt_id=task["current_attempt_id"],
                actor=decided_by,
                question=question,
                options=options or [],
                scope="escalation",
            )
            record, _ = self._transition(
                conn,
                task,
                task_id=task_id,
                event=EventKind.HUMAN_ADJUDICATE,
                actor_type="human",
                actor_id=decided_by,
                payload={"decision_id": decision_id, "decided_by": decided_by},
                extra_sets={
                    "waiting_since": _now(),
                    "next_reminder_at": None,
                    "escalated_at": None,
                },
            )
            return {"task_id": task_id, "decision_id": decision_id, "seq": record.seq}

    def cancel(
        self,
        *,
        task_id: str,
        decided_by: str,
        reason: str,
        actor_type: str = "human",
    ) -> dict[str, object]:
        """Cancel on whichever edge the current status has.

        ``actor_type`` is the *real* subject type. The human route leaves it at
        the default; the administrator harness route passes ``harness`` so the
        audit event does not claim a human cancelled a task the admin harness
        cancelled.
        """
        with self.database.transaction() as conn:
            task = self._task(conn, task_id)
            status = TaskStatus(str(task["status"]))
            if status is TaskStatus.QUEUED:
                event = EventKind.QUEUE_CANCEL
            elif status in _HUMAN_CANCEL_STATES:
                event = EventKind.HUMAN_CANCEL
            elif status in _CANCEL_STATES:
                event = EventKind.CANCEL
            else:
                raise Conflict(f"{task_id} is {status.value}; not on any cancel edge")
            attempt_id = task["current_attempt_id"]
            record, nxt = self._transition(
                conn,
                task,
                task_id=task_id,
                event=event,
                actor_type=actor_type,
                actor_id=decided_by,
                payload={"decided_by": decided_by, "reason": reason},
                extra_sets={
                    "waiting_since": None,
                    "next_reminder_at": None,
                    "escalated_at": None,
                    "owner": None,
                    "current_attempt_id": None,
                    "queue_reason": None,
                },
            )
            if attempt_id is not None:
                release_attempt_resources(conn, attempt_id=str(attempt_id), reason="cancel")
                release_lease(conn, task_id=task_id, attempt_id=str(attempt_id), reason="cancel")
            conn.execute(
                "UPDATE reviews SET status = 'expired'" " WHERE task_id = ? AND status = 'claimed'",
                (task_id,),
            )
            return {
                "task_id": task_id,
                "event": record.event_name,
                "seq": record.seq,
                "status": nxt.value,
            }

    # -------------------------------------------------------------- internals

    @staticmethod
    def _task(conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            raise NotFound(f"unknown task: {task_id}")
        return task

    def _transition(
        self,
        conn: sqlite3.Connection,
        task: sqlite3.Row,
        *,
        task_id: str,
        event: EventKind,
        actor_type: str,
        actor_id: str,
        payload: dict[str, object],
        extra_sets: dict[str, object],
    ) -> tuple[EventRecord, TaskStatus]:
        record = self.store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            connection=conn,
        )
        nxt = apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus(str(task["status"])),
            event=event,
            expected_epoch=int(task["current_epoch"]),
            extra_sets=extra_sets,
            state_seq=record.seq,
        )
        return record, nxt

    @staticmethod
    def _record_budget_override(
        conn: sqlite3.Connection,
        *,
        task_id: str,
        attempt_id: object,
        decided_by: str,
        reason: str,
        decided_seq: int,
    ) -> None:
        """Write the override decision once; ``human.release`` is the event audit."""
        already = conn.execute(
            "SELECT 1 FROM task_decisions WHERE task_id = ? AND scope = 'budget_override'",
            (task_id,),
        ).fetchone()
        if already is not None:
            return
        conn.execute(
            "INSERT INTO task_decisions(decision_id, task_id, attempt_id, actor, question,"
            " options_json, answer, scope, expires_at, decided_seq, superseded_by)"
            " VALUES (?, ?, ?, ?, ?, '[]', ?, 'budget_override', NULL, ?, NULL)",
            (
                f"dec-{uuid.uuid4().hex}",
                task_id,
                None if attempt_id is None else str(attempt_id),
                decided_by,
                "attempt budget override",
                reason or "approved",
                decided_seq,
            ),
        )

    def _open_decision(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        attempt_id: object,
        actor: str,
        question: str,
        options: list[str],
        scope: str,
    ) -> str:
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

    @staticmethod
    def _answer_decisions(
        conn: sqlite3.Connection,
        *,
        task_id: str,
        answer: str | None,
        decided_seq: int,
    ) -> None:
        """Close every open decision row for the task; a verdict without an
        answer leaves them open (the card's "未决行保持 NULL")."""
        if answer is None:
            return
        conn.execute(
            "UPDATE task_decisions SET answer = ?, decided_seq = ?"
            " WHERE task_id = ? AND answer IS NULL",
            (answer, decided_seq, task_id),
        )
