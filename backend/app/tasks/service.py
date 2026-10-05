"""Scheduler-side operations the REST layer exposes (MVP-08).

Everything here resolves the next status through MVP-03's transition table and
appends an allowlisted R10 §37.7 event; nothing derives a status by hand.

Scope note: this module deliberately stops where a later MVP begins. Verify
(MVP-06), worktree/publish (MVP-05) and merge (MVP-07) are not implemented here.
The one exception is :meth:`SchedulerService.record_verify_passed`, which exists
so the review path can be reached and tested before MVP-06 lands. It performs
the real ``verifying -> awaiting_review`` transition but produces **no
verification receipt**, and R10 §16 requires a receipt before a reviewer may
claim. It is therefore a test/scaffolding hook, is not exposed over HTTP, and
its docstring says so.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.claim.guarded_write import guarded_write
from app.claim.transitions import apply_transition
from app.config import settings
from app.db.connection import Database
from app.db.sequence import next_global_seq
from app.events.store import EventRecord, EventStore
from app.lease.service import release_lease
from app.plan.validator import PlanError, PlanSpec, PlanValidator
from app.spec import EventKind, TaskStatus


class NotFound(LookupError):
    """The addressed resource does not exist."""


class Conflict(RuntimeError):
    """The request is valid but the current state forbids it.

    ``code`` names the specific wire code from docs/prep/MVP-08/error-codes.md;
    without one the API layer falls back to a generic ``request.conflict``.
    """

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class InvalidInput(ValueError):
    """The request body itself is malformed."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanOutcome:
    plan_id: str
    snapshot_id: str
    plan_hash: str
    ready: tuple[str, ...]
    waiting: tuple[str, ...]


class SchedulerService:
    """Project/group/plan/task lifecycle, work transitions and group chat."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.store = EventStore(database)

    # ------------------------------------------------------------- creation

    def create_project(self, *, project_id: str, name: str) -> dict[str, object]:
        now = _now()
        with self.database.transaction() as conn:
            exists = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if exists is not None:
                raise Conflict(f"project {project_id} already exists")
            conn.execute(
                "INSERT INTO projects(project_id, name, created_at) VALUES (?, ?, ?)",
                (project_id, name, now),
            )
        return {"project_id": project_id, "name": name, "created_at": now}

    def create_group(self, *, group_id: str, project_id: str, name: str) -> dict[str, object]:
        now = _now()
        with self.database.transaction() as conn:
            parent = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            if parent is None:
                raise NotFound(f"unknown project: {project_id}")
            exists = conn.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,)).fetchone()
            if exists is not None:
                raise Conflict(f"group {group_id} already exists")
            conn.execute(
                "INSERT INTO groups(group_id, project_id, name, created_at) VALUES (?, ?, ?, ?)",
                (group_id, project_id, name, now),
            )
        return {"group_id": group_id, "project_id": project_id, "name": name}

    def create_plan(
        self, *, plan_id: str, group_id: str, snapshot: dict[str, object]
    ) -> PlanOutcome:
        """Store a plan draft. Tasks materialise only on human approval."""
        with self.database.transaction() as conn:
            group = conn.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,)).fetchone()
            if group is None:
                raise NotFound(f"unknown group: {group_id}")
            exists = conn.execute("SELECT 1 FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if exists is not None:
                raise Conflict(f"plan {plan_id} already exists")

            raw = dict(snapshot)
            raw.setdefault("plan_id", plan_id)
            try:
                spec = PlanSpec.from_mapping(raw)
            except (TypeError, ValueError) as exc:
                raise InvalidInput(f"plan snapshot is malformed: {exc}") from exc
            validation = PlanValidator().validate(spec)
            if not validation.ok:
                raise InvalidInput("; ".join(validation.errors))

            snapshot_id = f"snap-{uuid.uuid4().hex}"
            plan_hash = _hash(raw)
            now = _now()
            conn.execute(
                "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
                " VALUES (?, ?, 'draft', ?, ?)",
                (plan_id, group_id, plan_hash, now),
            )
            conn.execute(
                "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash,"
                " content_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (snapshot_id, plan_id, plan_hash, _canonical(raw), now),
            )
            # Tasks exist from the moment the plan is published, in `draft`.
            # R10 §10 gives `draft` both an approve and a reject edge, so a plan
            # rejected before approval still has state to move.
            for task in spec.tasks:
                self._materialise_task(
                    conn,
                    task_id=task.task_id,
                    group_id=group_id,
                    snapshot_id=snapshot_id,
                    depends_on=list(task.depends_on),
                )
        return PlanOutcome(
            plan_id=plan_id,
            snapshot_id=snapshot_id,
            plan_hash=plan_hash,
            ready=(),
            waiting=(),
        )

    # ---------------------------------------------------------------- plans

    def approve_plan(self, *, plan_id: str, decided_by: str) -> PlanOutcome:
        """Human approval. Tasks move to ready, or ready_waiting_deps.

        The server picks the edge: R10 §10 has separate events for "approved with
        everything merged" and "approved with an unmet upstream", so clients must
        not choose one.
        """
        with self.database.transaction() as conn:
            plan = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if plan is None:
                raise NotFound(f"unknown plan: {plan_id}")
            if str(plan["status"]) != "draft":
                raise Conflict(f"plan {plan_id} is {plan['status']}, not draft")
            snapshot = conn.execute(
                "SELECT * FROM plan_snapshots WHERE plan_id = ? ORDER BY created_at DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
            if snapshot is None:
                raise Conflict(f"plan {plan_id} has no snapshot")
            raw = json.loads(str(snapshot["content_json"]))

            ready: list[str] = []
            waiting: list[str] = []
            for item in raw.get("tasks", ()):
                task_id = str(item.get("id", "")).strip()
                if not task_id:
                    raise InvalidInput("every task needs an id")
                depends_on = [str(dep) for dep in item.get("depends_on", ())]
                row = conn.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise Conflict(f"task {task_id} was never materialised from its plan")
                if str(row["status"]) != TaskStatus.DRAFT.value:
                    raise Conflict(f"task {task_id} is {row['status']}, not draft")
                event = self._approve_one(
                    conn, task_id=task_id, depends_on=depends_on, decided_by=decided_by
                )
                if event is EventKind.PLAN_APPROVE:
                    ready.append(task_id)
                else:
                    waiting.append(task_id)

            conn.execute("UPDATE plans SET status = 'approved' WHERE plan_id = ?", (plan_id,))
        return PlanOutcome(
            plan_id=plan_id,
            snapshot_id=str(snapshot["snapshot_id"]),
            plan_hash=str(plan["plan_hash"]),
            ready=tuple(ready),
            waiting=tuple(waiting),
        )

    def _materialise_task(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        group_id: str,
        snapshot_id: str,
        depends_on: list[str],
    ) -> None:
        now = _now()
        existing = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO tasks(task_id, group_id, plan_snapshot_id, status,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, group_id, snapshot_id, TaskStatus.DRAFT.value, now, now),
            )
        for upstream in depends_on:
            parent = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (upstream,)).fetchone()
            if parent is None:
                raise InvalidInput(f"task {task_id} depends on unknown task {upstream}")
            conn.execute(
                "INSERT OR IGNORE INTO task_dependencies(parent_task_id, child_task_id, relation)"
                " VALUES (?, ?, 'depends_on')",
                (upstream, task_id),
            )

    def _approve_one(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        depends_on: list[str],
        decided_by: str,
    ) -> EventKind:
        unmet: list[str] = []
        for upstream in depends_on:
            row = conn.execute("SELECT status FROM tasks WHERE task_id = ?", (upstream,)).fetchone()
            if row is None or str(row["status"]) != TaskStatus.MERGED.value:
                unmet.append(upstream)
        event = EventKind.PLAN_APPROVE_DEPENDENCY_PENDING if unmet else EventKind.PLAN_APPROVE
        record = self.store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="human",
            actor_id=decided_by,
            payload={"plan_dependencies_unmet": unmet},
            connection=conn,
        )
        apply_transition(
            conn,
            task_id=task_id,
            current=TaskStatus.DRAFT,
            event=event,
            extra_sets={"waiting_since": _now() if unmet else None},
            state_seq=record.seq,
        )
        return event

    def reject_plan(self, *, plan_id: str, decided_by: str, reason: str) -> list[str]:
        with self.database.transaction() as conn:
            plan = conn.execute("SELECT * FROM plans WHERE plan_id = ?", (plan_id,)).fetchone()
            if plan is None:
                raise NotFound(f"unknown plan: {plan_id}")
            snapshot = conn.execute(
                "SELECT content_json FROM plan_snapshots WHERE plan_id = ?"
                " ORDER BY created_at DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
            if snapshot is None:
                raise Conflict(f"plan {plan_id} has no snapshot")
            raw = json.loads(str(snapshot["content_json"]))
            rejected: list[str] = []
            for item in raw.get("tasks", ()):
                task_id = str(item.get("id", "")).strip()
                row = conn.execute(
                    "SELECT status FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None or str(row["status"]) != TaskStatus.DRAFT.value:
                    continue
                record = self.store.append(
                    event_name=EventKind.PLAN_REJECT.value,
                    aggregate_type="task",
                    aggregate_id=task_id,
                    actor_type="human",
                    actor_id=decided_by,
                    payload={"reason": reason},
                    connection=conn,
                )
                apply_transition(
                    conn,
                    task_id=task_id,
                    current=TaskStatus.DRAFT,
                    event=EventKind.PLAN_REJECT,
                    state_seq=record.seq,
                )
                rejected.append(task_id)
            conn.execute("UPDATE plans SET status = 'rejected' WHERE plan_id = ?", (plan_id,))
        return rejected

    # ------------------------------------------------------------ work paths

    def submit_result(
        self,
        *,
        task_id: str,
        attempt_id: str,
        fencing_epoch: int,
        completion: dict[str, object],
    ) -> EventRecord:
        """``in_progress -> verifying`` and release the owner lease (R10 §13)."""
        with self.database.transaction() as conn:
            guard = self._guard_owner(conn, task_id, attempt_id, fencing_epoch)
            if guard.status is not TaskStatus.IN_PROGRESS:
                raise Conflict(
                    f"{task_id} is {guard.status.value}; submit_result needs in_progress"
                )
            if not completion.get("commit"):
                raise InvalidInput("completion.commit is required")
            record = self.store.append(
                event_name=EventKind.SUBMIT_RESULT.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=guard.owner or "",
                payload={"attempt_id": attempt_id, "completion": completion},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=guard.status,
                event=EventKind.SUBMIT_RESULT,
                expected_epoch=fencing_epoch,
                extra_sets={"owner": None, "current_attempt_id": None},
                state_seq=record.seq,
            )
            release_lease(conn, task_id=task_id, attempt_id=attempt_id, reason="submit_result")
            conn.execute(
                "UPDATE task_attempts SET status = ?, head_commit = ? WHERE attempt_id = ?",
                (TaskStatus.VERIFYING.value, str(completion["commit"]), attempt_id),
            )
            return record

    def request_help(
        self, *, task_id: str, attempt_id: str, fencing_epoch: int, reason: str
    ) -> dict[str, object]:
        return self._owner_transition(
            task_id=task_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            event=EventKind.REQUEST_HELP,
            allowed=TaskStatus.IN_PROGRESS,
            payload={"reason": reason},
            keep_owner=True,
        )

    def report_blocked(
        self, *, task_id: str, attempt_id: str, fencing_epoch: int, blocker_ref: str
    ) -> dict[str, object]:
        return self._owner_transition(
            task_id=task_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            event=EventKind.DEPENDENCY_MISSING,
            allowed=TaskStatus.IN_PROGRESS,
            payload={"blocker_ref": blocker_ref},
            keep_owner=True,
        )

    def release(
        self, *, task_id: str, attempt_id: str, fencing_epoch: int, reason: str
    ) -> dict[str, object]:
        return self._owner_transition(
            task_id=task_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            event=EventKind.RELEASE,
            allowed=None,
            payload={"reason": reason},
            keep_owner=False,
            release_lease_too=True,
        )

    def abandon(
        self, *, task_id: str, attempt_id: str, fencing_epoch: int, reason: str
    ) -> dict[str, object]:
        return self._owner_transition(
            task_id=task_id,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            event=EventKind.ABANDON,
            allowed=None,
            payload={"reason": reason},
            keep_owner=False,
            release_lease_too=True,
        )

    def _owner_transition(
        self,
        *,
        task_id: str,
        attempt_id: str,
        fencing_epoch: int,
        event: EventKind,
        allowed: TaskStatus | None,
        payload: dict[str, object],
        keep_owner: bool,
        release_lease_too: bool = False,
    ) -> dict[str, object]:
        with self.database.transaction() as conn:
            guard = self._guard_owner(conn, task_id, attempt_id, fencing_epoch)
            if allowed is not None and guard.status is not allowed:
                raise Conflict(
                    f"{task_id} is {guard.status.value}; {event.value} needs {allowed.value}"
                )
            record = self.store.append(
                event_name=event.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=guard.owner or "",
                payload=dict(payload, attempt_id=attempt_id),
                connection=conn,
            )
            # needs_help / blocked keep both owner and current attempt: R10 §10
            # moves only the status on those edges. (There is no tasks.attempt_id
            # column to write either.)
            extra: dict[str, object] = (
                {} if keep_owner else {"owner": None, "current_attempt_id": None}
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=guard.status,
                event=event,
                expected_epoch=fencing_epoch,
                extra_sets=extra,
                state_seq=record.seq,
            )
            if release_lease_too:
                release_lease(conn, task_id=task_id, attempt_id=attempt_id, reason=event.value)
            return {"task_id": task_id, "event": event.value, "seq": record.seq}

    # ---------------------------------------------------------------- review

    def submit_review(
        self,
        *,
        task_id: str,
        attempt_id: str,
        reviewer_harness_id: str,
        verdict: str,
        reviewed_commit: str,
        review_revision: int,
        findings: list[dict[str, object]],
    ) -> dict[str, object]:
        """Record a reviewer verdict.

        Self-review is refused here as well as in the API layer (R10 §21): the
        reviewer must not be the attempt owner and must not share its harness.
        Receipt cross-checks belong to MVP-06/07 and are not performed yet.
        """
        if verdict not in {"approve", "request_changes"}:
            raise InvalidInput("verdict must be approve or request_changes")
        with self.database.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise NotFound(f"unknown task: {task_id}")
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.AWAITING_REVIEW:
                raise Conflict(
                    f"{task_id} is {status.value}; review needs awaiting_review",
                    code="review.requires_verify_receipt",
                )
            attempt = conn.execute(
                "SELECT owner FROM task_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if attempt is not None and str(attempt["owner"]) == reviewer_harness_id:
                raise Conflict(
                    "a reviewer must not review its own attempt",
                    code="review.self_review_forbidden",
                )

            existing = conn.execute(
                "SELECT 1 FROM reviews WHERE task_id = ? AND attempt_id = ?"
                " AND reviewer_harness_id = ? AND review_revision = ?",
                (task_id, attempt_id, reviewer_harness_id, review_revision),
            ).fetchone()
            if existing is not None:
                raise Conflict(
                    "this review revision is already recorded", code="review.round_exhausted"
                )

            review_id = f"rv-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO reviews(review_id, task_id, attempt_id, reviewer_harness_id,"
                " reviewer_provider, status, fencing_epoch, reviewed_commit, verdict,"
                " findings_hash, review_revision, created_seq)"
                " VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?, NULL)",
                (
                    review_id,
                    task_id,
                    attempt_id,
                    reviewer_harness_id,
                    "unknown",
                    int(task["current_epoch"]),
                    reviewed_commit,
                    verdict,
                    _hash(findings),
                    review_revision,
                ),
            )
            event = EventKind.APPROVE if verdict == "approve" else EventKind.REQUEST_CHANGES
            if event is EventKind.REQUEST_CHANGES:
                round_no = int(task["review_round"]) + 1
                if round_no > settings.max_review_rounds:
                    raise Conflict(
                        f"review rounds exhausted for {task_id} "
                        f"(max {settings.max_review_rounds})",
                        code="review.round_exhausted",
                    )
                extra: dict[str, object] = {"review_round": round_no}
            else:
                extra = {"last_review_id": review_id}
            record = self.store.append(
                event_name=event.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=reviewer_harness_id,
                payload={
                    "review_id": review_id,
                    "reviewed_commit": reviewed_commit,
                    "review_revision": review_revision,
                    "findings_hash": _hash(findings),
                },
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=status,
                event=event,
                expected_epoch=int(task["current_epoch"]),
                extra_sets=extra,
                state_seq=record.seq,
            )
            return {
                "review_id": review_id,
                "task_id": task_id,
                "verdict": verdict,
                "event": event.value,
                "seq": record.seq,
            }

    def start_task(self, *, task_id: str, attempt_id: str, fencing_epoch: int) -> int:
        """``claimed -> in_progress``.

        R10 §10 gates this edge on "worktree ready and lease valid", and worktree
        provisioning is MVP-05. This performs the real transition for the lease
        holder but creates no worktree, so it is scaffolding for tests and for the
        API contract, not a substitute for MVP-05. Kept out of the HTTP surface
        for that reason.
        """
        with self.database.transaction() as conn:
            guard = self._guard_owner(conn, task_id, attempt_id, fencing_epoch)
            if guard.status is not TaskStatus.CLAIMED:
                raise Conflict(f"{task_id} is {guard.status.value}; start needs claimed")
            record = self.store.append(
                event_name=EventKind.START.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=guard.owner or "",
                payload={"attempt_id": attempt_id},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=guard.status,
                event=EventKind.START,
                expected_epoch=fencing_epoch,
                state_seq=record.seq,
            )
            conn.execute(
                "UPDATE task_attempts SET status = ? WHERE attempt_id = ?",
                (TaskStatus.IN_PROGRESS.value, attempt_id),
            )
            return record.seq

    def record_verify_passed(self, *, task_id: str, commit: str, actor_id: str) -> int:
        """Scaffolding for the review path before MVP-06 exists.

        Performs the real ``verifying -> awaiting_review`` transition. It does
        **not** run tests and does **not** write a ``verification_receipts`` row,
        which R10 §16 requires before a reviewer may claim. Not exposed over
        HTTP. Tests use it to reach awaiting_review; production must wait for
        MVP-06's Verify Worker.
        """
        with self.database.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise NotFound(f"unknown task: {task_id}")
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.VERIFYING:
                raise Conflict(f"{task_id} is {status.value}; need verifying")
            record = self.store.append(
                event_name=EventKind.VERIFY_PASSED.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="verify_worker",
                actor_id=actor_id,
                payload={"commit": commit},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=status,
                event=EventKind.VERIFY_PASSED,
                expected_epoch=int(task["current_epoch"]),
                state_seq=record.seq,
            )
            return record.seq

    # -------------------------------------------------------------- messages

    def post_message(
        self,
        *,
        group_id: str,
        author_kind: str,
        author_id: str,
        body: str,
        mentions: list[str],
        task_id: str | None = None,
        refs: list[str] | None = None,
    ) -> dict[str, object]:
        if not body.strip():
            raise InvalidInput("body must not be empty")
        with self.database.transaction() as conn:
            group = conn.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,)).fetchone()
            if group is None:
                raise NotFound(f"unknown group: {group_id}")
            if task_id is not None:
                task = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
                if task is None:
                    raise NotFound(f"unknown task: {task_id}")
            # Messages share the global seq space with task_events (R10 §15/§19,
            # MVP-08 prep openapi): one ordering authority for read_context.
            seq = next_global_seq(conn)
            message_id = f"msg-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO task_messages(message_id, group_id, task_id, sender_type,"
                " sender_id, body, created_seq, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, group_id, task_id, author_kind, author_id, body, seq, _now()),
            )
            for recipient in mentions:
                conn.execute(
                    "INSERT INTO message_deliveries(delivery_id, message_id, recipient_id,"
                    " status) VALUES (?, ?, ?, 'pending')",
                    (f"dl-{uuid.uuid4().hex}", message_id, recipient),
                )
        return {
            "message_id": message_id,
            "group_id": group_id,
            "task_id": task_id,
            "seq": seq,
            "mentions": list(mentions),
            "refs": list(refs or ()),
        }

    def list_messages(self, group_id: str, *, since_seq: int = 0, limit: int = 100) -> list[dict]:
        rows = self.database.read(
            "SELECT * FROM task_messages WHERE group_id = ? AND created_seq > ?"
            " ORDER BY created_seq ASC LIMIT ?",
            (group_id, since_seq, limit),
        )
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- reads

    def get_task(self, task_id: str) -> dict[str, object]:
        rows = self.database.read("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        if not rows:
            raise NotFound(f"unknown task: {task_id}")
        return dict(rows[0])

    def list_tasks(self, group_id: str, *, status: str | None = None) -> list[dict]:
        if status is None:
            rows = self.database.read(
                "SELECT * FROM tasks WHERE group_id = ? ORDER BY task_id", (group_id,)
            )
        else:
            rows = self.database.read(
                "SELECT * FROM tasks WHERE group_id = ? AND status = ? ORDER BY task_id",
                (group_id, status),
            )
        return [dict(row) for row in rows]

    def list_events(
        self, *, since_seq: int = 0, limit: int = 200, task_id: str | None = None
    ) -> list[EventRecord]:
        if task_id is None:
            events = list(self.store.iter_since(since_seq))
        else:
            events = [
                event for event in self.store.iter_since(since_seq) if event.aggregate_id == task_id
            ]
        return events[:limit]

    # --------------------------------------------------------------- helpers

    def _guard_owner(
        self, conn: sqlite3.Connection, task_id: str, attempt_id: str, fencing_epoch: int
    ):
        """Delegate to the single fencing entry point.

        Guard failures are deliberately not re-wrapped: ``errors._from_exception``
        maps each subclass to its own wire code, and collapsing them into a
        generic conflict would erase the difference between a stale epoch and a
        mismatched attempt.
        """
        return guarded_write(
            conn, task_id=task_id, fencing_epoch=fencing_epoch, attempt_id=attempt_id
        )


__all__ = [
    "Conflict",
    "InvalidInput",
    "NotFound",
    "PlanError",
    "PlanOutcome",
    "SchedulerService",
]
