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
from datetime import UTC, datetime, timedelta

from app.claim.guarded_write import guarded_write
from app.claim.transitions import apply_transition
from app.config import settings
from app.db.connection import Database
from app.db.sequence import next_global_seq
from app.events.store import EventRecord, EventStore
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.lease.service import iso, parse_iso, release_lease
from app.plan.validator import PlanError, PlanSpec, PlanValidator
from app.security.scan import MESSAGE_CODE, SECURITY_CODE, SecretScanBlocked, scan_for_block
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


def _normalise_refs(refs: list[str] | None) -> list[str]:
    """Validate the message refs array (openapi post_message / Message schema).

    The HTTP layer enforces ``list[str]`` via pydantic, but the service is also
    called directly by tests and future in-process writers, so the shape is
    checked here as well. A reference names something, so blank entries are
    rejected rather than stored. Order and content are otherwise preserved.
    """
    if refs is None:
        return []
    if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
        raise InvalidInput("refs must be an array of strings")
    if any(not ref.strip() for ref in refs):
        raise InvalidInput("refs entries must not be blank")
    return list(refs)


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
            # R10 §21 makes integration_health the merge queue's stop-the-line
            # signal. Without a row, every merge on an API-created project is
            # refused as "no integration_health row" -- the fake harness found
            # this on its first full run.
            conn.execute(
                "INSERT OR IGNORE INTO integration_health(project_id, status,"
                " integration_commit, updated_seq) VALUES (?, 'green', NULL, NULL)",
                (project_id,),
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

    def _attempt_owner(self, attempt_id: str) -> str:
        rows = self.database.read(
            "SELECT owner FROM task_attempts WHERE attempt_id = ?", (attempt_id,)
        )
        return str(rows[0]["owner"]) if rows and rows[0]["owner"] else "unknown"

    def _record_secret_scan_blocked(
        self,
        *,
        surface: str,
        scan_id: str,
        findings: list,
        aggregate_type: str,
        aggregate_id: str,
        actor_type: str,
        actor_id: str,
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> int:
        """Record the §17 security event. Never carries the matched value."""
        payload: dict[str, object] = {
            "scan_id": scan_id,
            "surface": surface,
            "findings": [finding.as_json() for finding in findings],
        }
        if task_id is not None:
            payload["task_id"] = task_id
        if attempt_id is not None:
            payload["attempt_id"] = attempt_id
        record = self.store.append(
            event_name="secret_scan_blocked",
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
        )
        return record.seq

    def submit_result(
        self,
        *,
        task_id: str,
        attempt_id: str,
        fencing_epoch: int,
        completion: dict[str, object],
    ) -> EventRecord:
        """``in_progress -> verifying`` and release the owner lease (R10 §13)."""
        blocked = scan_for_block(completion, surface="completion")
        if blocked is not None:
            scan_id, findings = blocked
            seq = self._record_secret_scan_blocked(
                surface="completion",
                scan_id=scan_id,
                findings=findings,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=self._attempt_owner(attempt_id),
                task_id=task_id,
                attempt_id=attempt_id,
            )
            raise SecretScanBlocked(
                code=SECURITY_CODE,
                scan_id=scan_id,
                rule_name=findings[0].rule_name,
                event_seq=seq,
                task_id=task_id,
            )
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
                # R10 §13 releases only the *lease* on submit. The attempt stays
                # current: verify, review and the Merge Gate all key off it
                # (`receipt.attempt_id`), so clearing it would orphan the very
                # artefacts the rest of the chain is about.
                extra_sets={"owner": None},
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
                # Ownerless states (changes_requested) have a legal release
                # edge in R10 §10 but no owner to name; v1 has no auth layer,
                # so the actor is recorded as unknown rather than empty.
                actor_id=guard.owner or "unknown",
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

    def review_claim(
        self,
        *,
        task_id: str,
        attempt_id: str,
        reviewer_harness_id: str,
        review_id: str | None = None,
        lease_seconds: int | None = None,
    ) -> dict[str, object]:
        """R10 §21 ``review.claim``: take the review lease for one attempt.

        The review lease is independent of the development lease (R10 §13): the
        developer's lease is released at submit, and the reviewer holds its own
        for 20 minutes by default. Claiming twice for the same reviewer returns
        the existing lease instead of minting a second one, so a retried claim
        cannot leave two reviewers believing they own the same review.
        """
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
            if attempt is None:
                raise NotFound(f"unknown attempt: {attempt_id}")
            owner = str(attempt["owner"])
            # In MVP the development owner *is* the harness id (MVP-04 sets
            # tasks.owner to harness_id), so this one comparison covers both
            # "the owner reviews itself" and "the same harness reviews itself".
            if reviewer_harness_id == owner:
                raise Conflict(
                    "a reviewer must not review its own attempt",
                    code="review.self_review_forbidden",
                )

            existing = conn.execute(
                "SELECT * FROM reviews WHERE task_id = ? AND attempt_id = ?"
                " AND reviewer_harness_id = ? AND status = 'claimed'",
                (task_id, attempt_id, reviewer_harness_id),
            ).fetchone()
            if existing is not None:
                return {
                    "review_id": str(existing["review_id"]),
                    "reviewed_commit": existing["reviewed_commit"],
                    "lease_until": existing["lease_until"],
                    "reused": True,
                }

            receipt = conn.execute(
                'SELECT "commit" FROM verification_receipts WHERE task_id = ? AND attempt_id = ?'
                " AND failure_reason IS NULL ORDER BY rowid DESC LIMIT 1",
                (task_id, attempt_id),
            ).fetchone()
            if receipt is None:
                raise Conflict(
                    "no passing receipt to review",
                    code="review.requires_verify_receipt",
                )
            moment = datetime.now(UTC)
            reviewed_commit = str(receipt["commit"])
            new_id = review_id or f"rv-{uuid.uuid4().hex}"
            lease_until = iso(
                moment + timedelta(seconds=lease_seconds or settings.review_lease_seconds)
            )
            conn.execute(
                "INSERT INTO reviews(review_id, task_id, attempt_id, reviewer_harness_id,"
                " reviewer_provider, status, lease_until, fencing_epoch, reviewed_commit,"
                " verdict, findings_hash, review_revision, created_seq)"
                " VALUES (?, ?, ?, ?, 'unknown', 'claimed', ?, ?, ?, NULL, NULL, 1, NULL)",
                (
                    new_id,
                    task_id,
                    attempt_id,
                    reviewer_harness_id,
                    lease_until,
                    int(task["current_epoch"]),
                    reviewed_commit,
                ),
            )
            return {
                "review_id": new_id,
                "reviewed_commit": reviewed_commit,
                "lease_until": lease_until,
                "reused": False,
            }

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
        review_id: str | None = None,
    ) -> dict[str, object]:
        """Record a reviewer verdict.

        Self-review is refused here as well as in the API layer (R10 §21): the
        reviewer must not be the attempt owner and must not share its harness.
        An expired review lease is refused rather than silently accepted.
        ``review_id`` is optional: when given, the caller must hold a live
        lease from ``review_claim``; when omitted the verdict is recorded
        unleased. R10 §21's flow is claim-then-submit, so the unleased path is
        an MVP simplification rather than the intended one.

        The full Merge Gate cross-assertions live in ``app.merge.gate``; this
        method records the verdict and applies its two R10 §10 edges.
        """
        if verdict not in {"approve", "request_changes"}:
            raise InvalidInput("verdict must be approve or request_changes")
        blocked = scan_for_block(findings, surface="findings")
        if blocked is not None:
            scan_id, scan_findings = blocked
            seq = self._record_secret_scan_blocked(
                surface="findings",
                scan_id=scan_id,
                findings=scan_findings,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=reviewer_harness_id or "unknown",
                task_id=task_id,
                attempt_id=attempt_id,
            )
            raise SecretScanBlocked(
                code=SECURITY_CODE,
                scan_id=scan_id,
                rule_name=scan_findings[0].rule_name,
                event_seq=seq,
                task_id=task_id,
            )
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
            if attempt is None:
                raise NotFound(f"unknown attempt: {attempt_id}")
            if str(attempt["owner"]) == reviewer_harness_id:
                raise Conflict(
                    "a reviewer must not review its own attempt",
                    code="review.self_review_forbidden",
                )
            held = (
                conn.execute(
                    "SELECT * FROM reviews WHERE review_id = ? AND reviewer_harness_id = ?",
                    (review_id, reviewer_harness_id),
                ).fetchone()
                if review_id is not None
                else None
            )
            if held is not None and held["status"] == "claimed":
                deadline = held["lease_until"]
                if deadline is not None and parse_iso(str(deadline)) <= datetime.now(UTC):
                    raise Conflict("the review lease has expired", code="review.lease_expired")

            # R10 §21's flow is claim-then-submit. ``review_claim`` already
            # created the row in status 'claimed', so submitting the verdict
            # completes that row rather than inserting a second one -- the
            # unique key (task, attempt, reviewer, revision) cannot tell a
            # claimed row awaiting its verdict from a finished review, so a
            # second insert would look like a duplicate revision.
            existing = conn.execute(
                "SELECT * FROM reviews WHERE task_id = ? AND attempt_id = ?"
                " AND reviewer_harness_id = ? AND review_revision = ?",
                (task_id, attempt_id, reviewer_harness_id, review_revision),
            ).fetchone()
            if existing is not None and str(existing["status"]) == "completed":
                return {
                    "review_id": str(existing["review_id"]),
                    "task_id": task_id,
                    "verdict": str(existing["verdict"]),
                    "event": (
                        EventKind.APPROVE.value
                        if str(existing["verdict"]) == "approve"
                        else EventKind.REQUEST_CHANGES.value
                    ),
                    "seq": existing["completed_seq"],
                    "replayed": True,
                }

            if existing is not None:
                review_id = str(existing["review_id"])
                conn.execute(
                    "UPDATE reviews SET status = 'completed', verdict = ?, findings_hash = ?,"
                    " reviewed_commit = ?, fencing_epoch = ? WHERE review_id = ?",
                    (
                        verdict,
                        _hash(findings),
                        reviewed_commit,
                        int(task["current_epoch"]),
                        review_id,
                    ),
                )
            else:
                review_id = review_id or f"rv-{uuid.uuid4().hex}"
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

    def start_task(
        self,
        *,
        task_id: str,
        attempt_id: str,
        fencing_epoch: int,
        group_short: str | None = None,
        worktrees: WorktreeManager | None = None,
    ) -> dict[str, object]:
        """``claimed -> in_progress``, provisioning the attempt worktree first.

        R10 §10 gates this edge on "worktree ready and lease valid". Until MVP-05
        this method only performed the transition, which is why the MVP-08 card
        called it scaffolding; now the worktree is actually created here, so the
        guard describes something real.

        ``group_short`` is a parameter rather than a lookup because the schema
        has no ``groups.group_short`` column (see the MVP-05 card's known
        issues); it defaults to the group id.
        """
        manager = worktrees or WorktreeManager(self.database, CanonicalRepo.from_settings())
        with self.database.transaction() as conn:
            guard = self._guard_owner(conn, task_id, attempt_id, fencing_epoch)
            if guard.status is not TaskStatus.CLAIMED:
                raise Conflict(f"{task_id} is {guard.status.value}; start needs claimed")
            task = conn.execute(
                "SELECT group_id, base_commit FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            base_commit = task["base_commit"] or self._integration_head(conn, task_id)

        worktree = manager.provision(
            task_id=task_id,
            attempt_id=attempt_id,
            group_short=group_short or str(task["group_id"]),
            harness=guard.owner or "unknown",
            base_commit=base_commit or self.repo_head(manager),
        )

        with self.database.transaction() as conn:
            record = self.store.append(
                event_name=EventKind.START.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=guard.owner or "",
                payload={
                    "attempt_id": attempt_id,
                    "branch": worktree.branch,
                    "workspace_id": worktree.workspace_id,
                },
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
            return {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "seq": record.seq,
                "branch": worktree.branch,
                "workspace_id": worktree.workspace_id,
                "worktree_path": str(worktree.path),
            }

    @staticmethod
    def repo_head(manager: WorktreeManager) -> str:
        """The canonical integration HEAD, used when a task froze no base."""
        head = manager.repo.ref_commit(manager.repo.integration_ref)
        if head is None:
            raise Conflict("the canonical repository has no integration HEAD")
        return head

    def declare_differential_not_applicable(
        self,
        *,
        task_id: str,
        attempt_id: str,
        fencing_epoch: int,
        reason: str,
        static_evidence: list[str],
    ) -> dict[str, object]:
        """R10 §16 step 5 / §10: ``verifying -> awaiting_human``.

        A pure refactor, doc-only or deletion task may declare that a baseline
        differential does not apply, but it must carry a reason and static
        evidence and it goes to a human. This is the forward edge R10-fix-03
        added; :meth:`resume_after_differential_review` is its reverse.
        """
        if not reason.strip():
            raise InvalidInput("differential: not_applicable requires a reason")
        if not static_evidence:
            raise InvalidInput("differential: not_applicable requires static evidence")
        with self.database.transaction() as conn:
            task = conn.execute(
                "SELECT status, current_epoch FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise NotFound(f"unknown task: {task_id}")
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.VERIFYING:
                raise Conflict(f"{task_id} is {status.value}; need verifying")
            if fencing_epoch != int(task["current_epoch"]):
                raise Conflict("fencing epoch is stale", code="fencing.epoch_stale")
            record = self.store.append(
                event_name=EventKind.DIFFERENTIAL_NOT_APPLICABLE.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=attempt_id,
                payload={"reason": reason, "static_evidence": list(static_evidence)},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=status,
                event=EventKind.DIFFERENTIAL_NOT_APPLICABLE,
                expected_epoch=fencing_epoch,
                state_seq=record.seq,
            )
            return {"task_id": task_id, "event": record.event_name, "seq": record.seq}

    def resume_after_differential_review(
        self, *, task_id: str, decided_by: str
    ) -> dict[str, object]:
        """``awaiting_human -> verifying`` once a human confirmed the declaration.

        Without this edge an ``awaiting_human`` task has no way back into
        verification, and the declaration becomes a dead end.
        """
        with self.database.transaction() as conn:
            task = conn.execute(
                "SELECT status, current_epoch FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise NotFound(f"unknown task: {task_id}")
            status = TaskStatus(str(task["status"]))
            if status is not TaskStatus.AWAITING_HUMAN:
                raise Conflict(f"{task_id} is {status.value}; need awaiting_human")
            record = self.store.append(
                event_name=EventKind.HUMAN_RESUME_VERIFY_NOT_APPLICABLE.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="human",
                actor_id=decided_by,
                payload={"decision": "confirmed differential:not_applicable"},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=status,
                event=EventKind.HUMAN_RESUME_VERIFY_NOT_APPLICABLE,
                expected_epoch=int(task["current_epoch"]),
                state_seq=record.seq,
            )
            return {"task_id": task_id, "event": record.event_name, "seq": record.seq}

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
        blocked = scan_for_block(body, surface="message")
        if blocked is not None:
            scan_id, scan_findings = blocked
            seq = self._record_secret_scan_blocked(
                surface="message",
                scan_id=scan_id,
                findings=scan_findings,
                aggregate_type="group",
                aggregate_id=group_id,
                actor_type=author_kind or "unknown",
                actor_id=author_id or "unknown",
                task_id=task_id,
            )
            raise SecretScanBlocked(
                code=MESSAGE_CODE,
                scan_id=scan_id,
                rule_name=scan_findings[0].rule_name,
                event_seq=seq,
            )
        normalised_refs = _normalise_refs(refs)
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
                " sender_id, body, created_seq, created_at, refs)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    message_id,
                    group_id,
                    task_id,
                    author_kind,
                    author_id,
                    body,
                    seq,
                    _now(),
                    _canonical(normalised_refs),
                ),
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
            "refs": normalised_refs,
        }

    def list_messages(self, group_id: str, *, since_seq: int = 0, limit: int = 100) -> list[dict]:
        rows = self.database.read(
            "SELECT * FROM task_messages WHERE group_id = ? AND created_seq > ?"
            " ORDER BY created_seq ASC LIMIT ?",
            (group_id, since_seq, limit),
        )
        messages = [dict(row) for row in rows]
        for message in messages:
            # refs is a JSON TEXT column (004); the wire contract wants an array,
            # and pre-004 rows default to '[]', never null.
            message["refs"] = json.loads(str(message["refs"]))
        return messages

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

    def _integration_head(self, conn: sqlite3.Connection, task_id: str) -> str | None:
        """The frozen integration commit recorded for this task's project."""
        row = conn.execute(
            "SELECT ih.integration_commit"
            "  FROM tasks t"
            "  JOIN groups g ON g.group_id = t.group_id"
            "  LEFT JOIN integration_health ih ON ih.project_id = g.project_id"
            " WHERE t.task_id = ?",
            (task_id,),
        ).fetchone()
        return None if row is None else row["integration_commit"]

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
