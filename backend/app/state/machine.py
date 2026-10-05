"""Pure R10 state-machine projection for MVP-03.

The database event store owns facts; this module only validates an event against
the frozen transition table and computes the next projection. It deliberately
does not issue SQL or mutate task rows, keeping H-6 enforceable.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from app.spec import PSEUDO_STATES, EventKind, TaskStatus

StateName = TaskStatus | str


class InvalidTransition(ValueError):
    """Raised when an event is not a legal transition for the current state."""


@dataclass(frozen=True, slots=True)
class Transition:
    current: frozenset[TaskStatus]
    event: EventKind
    next: StateName
    guard: str
    side_effects: tuple[str, ...]

    def accepts(self, status: TaskStatus, event: EventKind) -> bool:
        return status in self.current and event is self.event


@dataclass(frozen=True, slots=True)
class TaskProjection:
    """The minimal task projection required by MVP-03."""

    task_id: str
    status: TaskStatus = TaskStatus.DRAFT
    seq: int = 0
    plan_snapshot_id: str | None = None
    parent_task_id: str | None = None
    last_event: EventKind | None = None
    last_payload: dict[str, Any] | None = None

    def apply(
        self,
        event: EventKind | str,
        *,
        seq: int = 0,
        payload: dict[str, Any] | None = None,
        table: TransitionTable | None = None,
    ) -> TaskProjection:
        transition_table = table or default_transition_table()
        event_kind = event if isinstance(event, EventKind) else EventKind(event)
        transition = transition_table.resolve(self.status, event_kind)
        if transition.next not in PSEUDO_STATES and not isinstance(transition.next, TaskStatus):
            raise InvalidTransition(f"unknown next state: {transition.next}")
        next_status = self.status if transition.next in PSEUDO_STATES else transition.next
        return replace(
            self,
            status=next_status,
            seq=max(self.seq, seq),
            last_event=event_kind,
            last_payload=dict(payload or {}),
        )


class TransitionTable:
    """Immutable-style transition registry with uniqueness checks."""

    def __init__(self, transitions: Iterable[Transition]) -> None:
        self._transitions = tuple(transitions)
        self._by_key: dict[tuple[TaskStatus, EventKind], Transition] = {}
        for transition in self._transitions:
            for current in transition.current:
                key = (current, transition.event)
                if key in self._by_key:
                    raise ValueError(
                        f"duplicate transition: {current.value}|{transition.event.value}"
                    )
                self._by_key[key] = transition

    @property
    def transitions(self) -> tuple[Transition, ...]:
        return self._transitions

    def resolve(self, status: TaskStatus, event: EventKind) -> Transition:
        try:
            return self._by_key[(status, event)]
        except KeyError as exc:
            raise InvalidTransition(
                f"event {event.value!r} is not allowed from {status.value!r}"
            ) from exc

    def outgoing(self, status: TaskStatus) -> tuple[Transition, ...]:
        return tuple(t for t in self._transitions if status in t.current)

    def events(self) -> frozenset[EventKind]:
        return frozenset(t.event for t in self._transitions)

    def validate(self) -> list[str]:
        """Return structural diagnostics used by DOC-STATE-001 style tests."""
        errors: list[str] = []
        for status in TaskStatus:
            if not status.is_terminal and not self.outgoing(status):
                errors.append(f"non-terminal state has no outgoing edge: {status.value}")
        for transition in self._transitions:
            if not transition.guard.strip():
                errors.append(f"empty guard: {transition.event.value}")
            if not transition.side_effects:
                errors.append(f"empty side effects: {transition.event.value}")
        return errors


def _t(
    current: TaskStatus | Iterable[TaskStatus],
    event: EventKind,
    next_state: StateName,
    guard: str,
    *side_effects: str,
) -> Transition:
    states = frozenset({current}) if isinstance(current, TaskStatus) else frozenset(current)
    return Transition(states, event, next_state, guard, tuple(side_effects))


def default_transition_table() -> TransitionTable:
    """Build the MVP projection of the R10 migration table."""
    owner = (
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_HELP,
        TaskStatus.BLOCKED,
        TaskStatus.PLAN_DRIFT,
        TaskStatus.IMPL_DRIFT,
    )
    cancellable = (
        TaskStatus.DRAFT,
        TaskStatus.DRAFT_REJECTED,
        TaskStatus.READY_WAITING_DEPS,
        TaskStatus.READY,
        TaskStatus.QUEUED,
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_HELP,
        TaskStatus.BLOCKED,
        TaskStatus.CHANGES_REQUESTED,
        TaskStatus.PLAN_DRIFT,
        TaskStatus.IMPL_DRIFT,
        TaskStatus.AWAITING_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.MERGING,
        TaskStatus.STALE,
        TaskStatus.RECLAIMING,
        TaskStatus.ESCALATED,
        TaskStatus.AWAITING_HUMAN,
    )
    transitions = [
        _t(
            TaskStatus.DRAFT,
            EventKind.PLAN_APPROVE,
            TaskStatus.READY,
            "plan linter and human approval",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.DRAFT,
            EventKind.PLAN_APPROVE_DEPENDENCY_PENDING,
            TaskStatus.READY_WAITING_DEPS,
            "plan approved with an unmet upstream",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.DRAFT,
            EventKind.DEPENDENCIES_PENDING,
            TaskStatus.READY_WAITING_DEPS,
            "upstream is not merged",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.DRAFT,
            EventKind.PLAN_REJECT,
            TaskStatus.DRAFT_REJECTED,
            "human recorded rejection",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.DRAFT_REJECTED,
            EventKind.PLAN_REVISE,
            TaskStatus.DRAFT,
            "new plan snapshot is bound",
            "event",
            "state_changed",
        ),
        _t(
            (TaskStatus.DRAFT, TaskStatus.DRAFT_REJECTED),
            EventKind.PLAN_SUPERSEDE,
            TaskStatus.CANCELLED,
            "new plan supersedes this snapshot",
            "event",
            "downstream_policy",
        ),
        _t(
            TaskStatus.READY_WAITING_DEPS,
            EventKind.DEPENDENCIES_SATISFIED,
            TaskStatus.READY,
            "all upstream outputs are merged and compatible",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.READY_WAITING_DEPS,
            EventKind.BLOCK_TIMEOUT,
            TaskStatus.ESCALATED,
            "dependency wait exceeded policy",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.READY_WAITING_DEPS,
            EventKind.ORPHAN_DETECTED,
            TaskStatus.ESCALATED,
            "all upstreams are terminal without output",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.READY,
            EventKind.CLAIM,
            TaskStatus.CLAIMED,
            "dependencies and resources pass",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            TaskStatus.READY,
            EventKind.CLAIM_DEFERRED_RESOURCE,
            TaskStatus.QUEUED,
            "resource capacity is unavailable",
            "event",
            "queue_recorded",
        ),
        _t(
            TaskStatus.READY,
            EventKind.AGING_TIMEOUT,
            TaskStatus.ESCALATED,
            "ready aging threshold exceeded",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.QUEUED,
            EventKind.RESOURCE_AVAILABLE,
            TaskStatus.CLAIMED,
            "queued capacity is available",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            TaskStatus.QUEUED,
            EventKind.QUEUE_CANCEL,
            TaskStatus.CANCELLED,
            "human or plan cancellation",
            "event",
            "queue_released",
        ),
        _t(
            owner,
            EventKind.LEASE_EXPIRE,
            TaskStatus.STALE,
            "owner lease expired",
            "event",
            "lease_released",
            "stale_scan",
        ),
        _t(
            TaskStatus.CLAIMED,
            EventKind.START,
            TaskStatus.IN_PROGRESS,
            "worktree ready and lease valid",
            "event",
            "work_started",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.SUBMIT_RESULT,
            TaskStatus.VERIFYING,
            "commit and completion evidence are present",
            "event",
            "verify_job_created",
            "lease_released",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.SUBMIT_REJECTED_VERIFY_CAPACITY,
            TaskStatus.IN_PROGRESS,
            "verify queue is full",
            "event",
            "reminder",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.REQUEST_HELP,
            TaskStatus.NEEDS_HELP,
            "owner lease is valid and help request exists",
            "event",
            "help_created",
        ),
        _t(
            TaskStatus.NEEDS_HELP,
            EventKind.HELP_REQUESTED,
            TaskStatus.NEEDS_HELP,
            "structured help request recorded",
            "event",
            "help_created",
        ),
        _t(
            TaskStatus.NEEDS_HELP,
            EventKind.HELP_ACCEPTED,
            TaskStatus.NEEDS_HELP,
            "collaborator accepted with scoped context",
            "event",
            "help_lease_created",
        ),
        _t(
            TaskStatus.NEEDS_HELP,
            EventKind.HELP_REJECTED,
            TaskStatus.NEEDS_HELP,
            "collaborator rejected or timed out",
            "event",
            "reminder",
        ),
        _t(
            TaskStatus.NEEDS_HELP,
            EventKind.HELP_RESOLVED,
            TaskStatus.IN_PROGRESS,
            "reviewed help artifact and owner lease valid",
            "event",
            "help_attached",
        ),
        _t(
            (TaskStatus.IN_PROGRESS, TaskStatus.NEEDS_HELP),
            EventKind.DEPENDENCY_MISSING,
            TaskStatus.BLOCKED,
            "blocker reference is registered",
            "event",
            "blocked_scan",
        ),
        _t(
            TaskStatus.BLOCKED,
            EventKind.UNBLOCK,
            TaskStatus.IN_PROGRESS,
            "independent scanner confirms blocker release",
            "event",
            "lease_reissued",
        ),
        _t(
            (
                TaskStatus.IN_PROGRESS,
                TaskStatus.NEEDS_HELP,
                TaskStatus.BLOCKED,
                TaskStatus.VERIFYING,
            ),
            EventKind.STUCK_THRESHOLD_REACHED,
            TaskStatus.ESCALATED,
            "three scans show no progress",
            "event",
            "human_todo",
        ),
        _t(
            (
                TaskStatus.IN_PROGRESS,
                TaskStatus.NEEDS_HELP,
                TaskStatus.BLOCKED,
                TaskStatus.VERIFYING,
            ),
            EventKind.STUCK_HUMAN_THRESHOLD,
            TaskStatus.AWAITING_HUMAN,
            "five scans show no progress",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.HUMAN_QUESTION,
            TaskStatus.AWAITING_HUMAN,
            "structured human question recorded",
            "decision_created",
            "lease_paused",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.SPLIT_PARENT_BLOCKED,
            TaskStatus.BLOCKED,
            "child drafts exist and parent remains incomplete",
            "event",
            "children_created",
        ),
        _t(
            (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
            EventKind.SPLIT,
            "child_created",
            "split is authorized and idempotent",
            "event",
            "children_created",
        ),
        _t(
            TaskStatus.BLOCKED,
            EventKind.ALL_CHILDREN_MERGED,
            TaskStatus.READY,
            "all children are merged",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_CODE_FAILED,
            TaskStatus.CHANGES_REQUESTED,
            "baseline/head or test assertion failed",
            "event",
            "verify_released",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_ENV_FAILED,
            TaskStatus.VERIFYING,
            "environment failure below retry limit",
            "event",
            "verify_rescheduled",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_PASSED,
            TaskStatus.AWAITING_REVIEW,
            "receipt exactly matches head commit",
            "event",
            "review_lease_requested",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_LEASE_EXPIRE,
            TaskStatus.VERIFYING,
            "verify process is dead after lease expiry",
            "event",
            "verify_rescheduled",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_EXECUTION_TIMEOUT,
            TaskStatus.ESCALATED,
            "verify profile timeout exceeded",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_QUEUE_TIMEOUT,
            TaskStatus.ESCALATED,
            "verify job exceeded queue SLA",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.DIFFERENTIAL_NOT_APPLICABLE,
            TaskStatus.AWAITING_HUMAN,
            "human confirmation is required",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.REQUEST_CHANGES,
            TaskStatus.CHANGES_REQUESTED,
            "review findings and budget allow revision",
            "event",
            "attempt_frozen",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.REVIEW_ROUND_EXHAUSTED,
            TaskStatus.ESCALATED,
            "review round limit reached",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.APPROVE,
            TaskStatus.APPROVED,
            "required reviews and receipt match",
            "event",
            "branch_frozen",
            "merge_intent_created",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.REVIEW_LEASE_EXPIRE,
            TaskStatus.AWAITING_REVIEW,
            "review intent is not running",
            "event",
            "review_requeued",
        ),
        _t(
            TaskStatus.APPROVED,
            EventKind.MERGE_START,
            TaskStatus.MERGING,
            "all merge gates pass",
            "event",
            "merge_lease_created",
        ),
        _t(
            TaskStatus.APPROVED,
            EventKind.REOPEN_FOR_CHANGES,
            TaskStatus.CHANGES_REQUESTED,
            "approved branch moved or human reopened",
            "event",
            "branch_unfrozen",
        ),
        _t(
            TaskStatus.APPROVED,
            EventKind.MERGE_LEASE_EXPIRE,
            TaskStatus.APPROVED,
            "merge intent is not running",
            "event",
            "merge_requeued",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_SUCCESS,
            TaskStatus.MERGED,
            "integration tests pass",
            "event",
            "integration_ref_updated",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_CONFLICT,
            TaskStatus.APPROVED,
            "no integration commit was written",
            "event",
            "merge_requeued",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_CONFLICT_NEEDS_OWNER,
            TaskStatus.CHANGES_REQUESTED,
            "conflict requires owner repair",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_POST_COMMIT_FAILURE,
            TaskStatus.APPROVED,
            "post-commit failure has a recoverable path",
            "event",
            "revert_intent_created",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED,
            TaskStatus.ESCALATED,
            "post-commit failure requires human decision",
            "event",
            "revert_intent_created",
            "human_todo",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_TIMEOUT,
            TaskStatus.ESCALATED,
            "integration test timeout exceeded",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_LEASE_EXPIRE,
            TaskStatus.APPROVED,
            "merge intent is not running",
            "event",
            "merge_requeued",
        ),
        _t(
            (TaskStatus.CHANGES_REQUESTED, TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.CLAIM_FOR_REVISION,
            TaskStatus.CLAIMED,
            "revision is confirmed and resources pass",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            TaskStatus.CHANGES_REQUESTED,
            EventKind.RESUBMIT,
            TaskStatus.AWAITING_REVIEW,
            "new attempt has verified evidence",
            "event",
            "review_lease_requested",
        ),
        _t(
            TaskStatus.CHANGES_REQUESTED,
            EventKind.REASSIGN,
            TaskStatus.CLAIMED,
            "human or scheduler assigns a new owner",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            TaskStatus.CHANGES_REQUESTED,
            EventKind.REVIEW_WAIT_TIMEOUT,
            TaskStatus.ESCALATED,
            "revision wait exceeded SLA",
            "event",
            "human_todo",
        ),
        _t(
            (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.HUMAN_RESUME,
            TaskStatus.IN_PROGRESS,
            "human confirms drift handling and owner exists",
            "event",
            "lease_reissued",
        ),
        _t(
            (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.HUMAN_REOPEN,
            TaskStatus.READY,
            "human requests a fresh claim",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.STALE,
            EventKind.RECLAIM,
            TaskStatus.RECLAIMING,
            "grace elapsed and budget remains",
            "event",
            "reclaim_window_opened",
        ),
        _t(
            TaskStatus.STALE,
            EventKind.ATTEMPT_BUDGET_EXHAUSTED,
            TaskStatus.ESCALATED,
            "attempt budget is exhausted",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.RECLAIMING,
            EventKind.TAKE_OVER,
            TaskStatus.CLAIMED,
            "new owner and epoch are created",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            TaskStatus.RECLAIMING,
            EventKind.NOBODY_TAKES,
            TaskStatus.READY,
            "takeover window expired",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.RECLAIMING,
            EventKind.ATTEMPT_BUDGET_EXHAUSTED,
            TaskStatus.ESCALATED,
            "attempt budget is exhausted",
            "event",
            "human_todo",
        ),
        _t(
            (
                TaskStatus.CLAIMED,
                TaskStatus.IN_PROGRESS,
                TaskStatus.NEEDS_HELP,
                TaskStatus.BLOCKED,
                TaskStatus.CHANGES_REQUESTED,
            ),
            EventKind.RELEASE,
            TaskStatus.READY,
            "owner releases without uncommitted output",
            "lease_released",
            "resource_released",
        ),
        _t(
            (
                TaskStatus.NEEDS_HELP,
                TaskStatus.BLOCKED,
                TaskStatus.PLAN_DRIFT,
                TaskStatus.IMPL_DRIFT,
            ),
            EventKind.ABANDON,
            TaskStatus.ABANDONED,
            "owner records reason and handoff",
            "artifact_recorded",
            "lease_released",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.ABANDON,
            TaskStatus.ABANDONED,
            "owner records reason and handoff",
            "artifact_recorded",
            "lease_released",
        ),
        _t(
            TaskStatus.ABANDONED,
            EventKind.REOPEN,
            TaskStatus.READY,
            "human or scheduler approves reopen",
            "event",
            "state_changed",
        ),
        _t(
            TaskStatus.ESCALATED,
            EventKind.HUMAN_RELEASE,
            TaskStatus.READY,
            "human release is recorded",
            "decision_created",
            "state_changed",
        ),
        _t(
            TaskStatus.ESCALATED,
            EventKind.HUMAN_ADJUDICATE,
            TaskStatus.AWAITING_HUMAN,
            "structured adjudication is required",
            "decision_created",
            "human_todo",
        ),
        _t(
            (TaskStatus.AWAITING_HUMAN, TaskStatus.ESCALATED),
            EventKind.ESCALATION_REMINDER,
            TaskStatus.ESCALATED,
            "reminder backoff is due",
            "reminder",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_RESUME,
            TaskStatus.IN_PROGRESS,
            "structured decision and lease reissue",
            "decision_consumed",
            "lease_reissued",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_REOPEN,
            TaskStatus.READY,
            "human requests a fresh claim",
            "decision_consumed",
            "state_changed",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_REOPEN_NO_OWNER,
            TaskStatus.READY,
            "human resumes without an owner",
            "decision_consumed",
            "state_changed",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_RESUME_VERIFY_NOT_APPLICABLE,
            TaskStatus.VERIFYING,
            "human confirms the not-applicable profile",
            "decision_consumed",
            "verify_reused",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_WAIT_TIMEOUT,
            TaskStatus.AWAITING_HUMAN,
            "reminder only; never auto-release",
            "reminder",
        ),
        _t(
            cancellable,
            EventKind.CANCEL,
            TaskStatus.CANCELLED,
            "human or plan cancellation with downstream policy",
            "decision_created",
            "downstream_policy",
        ),
        _t(
            (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
            EventKind.PLAN_HASH_CHANGED,
            TaskStatus.PLAN_DRIFT,
            "bound plan snapshot hash changed",
            "event",
            "attempt_frozen",
        ),
        _t(
            (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
            EventKind.IMPL_HASH_CHANGED,
            TaskStatus.IMPL_DRIFT,
            "dependency output hash changed",
            "event",
            "attempt_frozen",
        ),
    ]
    return TransitionTable(transitions)
