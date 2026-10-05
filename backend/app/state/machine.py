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
    """Build the R10 section 10 transition table.

    The rows below follow the row order of the R10 section 10 migration table
    one-for-one. backend/tests/test_transition_mirror.py mechanically compares
    the expanded (state, event, next) triples against the spec table, so this
    list is a full projection rather than a hand-picked MVP subset.
    """
    # R10 L421: cancel allowlist (14 states).
    cancellable = (
        TaskStatus.DRAFT,
        TaskStatus.READY,
        TaskStatus.QUEUED,
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
    )
    # R10 L430: states whose owner lease may expire.
    owner = (
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_HELP,
        TaskStatus.BLOCKED,
        TaskStatus.PLAN_DRIFT,
        TaskStatus.IMPL_DRIFT,
    )
    # R10 L459/L460: states covered by the two stuck thresholds.
    stuck = (
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_HELP,
        TaskStatus.BLOCKED,
        TaskStatus.VERIFYING,
    )
    # R10 L468-L471: states from which hash drift is detected.
    drift_dispatch = (
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.CHANGES_REQUESTED,
        TaskStatus.AWAITING_REVIEW,
    )
    drift_verify = (
        TaskStatus.VERIFYING,
        TaskStatus.APPROVED,
        TaskStatus.MERGING,
    )
    # R10 L442: review/merge/rework wait SLA.
    review_wait = (
        TaskStatus.AWAITING_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.MERGING,
        TaskStatus.CHANGES_REQUESTED,
    )
    # R10 L448: review round budget exhausted.
    review_round = (
        TaskStatus.AWAITING_REVIEW,
        TaskStatus.CHANGES_REQUESTED,
    )
    transitions = [
        # --- plan lifecycle (R10 L415-L424) ---
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
            cancellable,
            EventKind.CANCEL,
            TaskStatus.CANCELLED,
            "human or plan cancellation with downstream policy",
            "decision_created",
            "downstream_policy",
            "lease_released",
            "resource_released",
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
            "dependency wait exceeded twice the upstream expected duration",
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
        # --- dispatch / claim (R10 L425-L430) ---
        _t(
            TaskStatus.READY,
            EventKind.CLAIM,
            TaskStatus.CLAIMED,
            "dependencies, capability and concurrency checks pass",
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
            TaskStatus.QUEUED,
            EventKind.RESOURCE_AVAILABLE,
            TaskStatus.CLAIMED,
            "queued capacity is available and resources reserved",
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
            TaskStatus.CLAIMED,
            EventKind.START,
            TaskStatus.IN_PROGRESS,
            "worktree ready and development lease valid",
            "event",
            "work_started",
        ),
        _t(
            owner,
            EventKind.LEASE_EXPIRE,
            TaskStatus.STALE,
            "owner lease_until passed",
            "event",
            "lease_released",
            "stale_scan",
        ),
        # --- verify (R10 L431-L439) ---
        _t(
            TaskStatus.VERIFYING,
            EventKind.DIFFERENTIAL_NOT_APPLICABLE,
            TaskStatus.AWAITING_HUMAN,
            "declared not-applicable with registered rationale and static evidence",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_CODE_FAILED,
            TaskStatus.CHANGES_REQUESTED,
            "baseline/head differential failed, verify_cmd non-zero or assertions failed",
            "event",
            "verify_released",
            "attempt_frozen",
            "worktree_archived",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_ENV_FAILED,
            TaskStatus.VERIFYING,
            "non-code environment failure below retry limit",
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
            "verify lease expired and intent reconciliation confirms the process died",
            "event",
            "verify_rescheduled",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_EXECUTION_TIMEOUT,
            TaskStatus.ESCALATED,
            "verify command exceeded profile SLA",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.VERIFYING,
            EventKind.VERIFY_QUEUE_TIMEOUT,
            TaskStatus.ESCALATED,
            "verify job exceeded verify_queue_sla_minutes",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_RESUME_VERIFY_NOT_APPLICABLE,
            TaskStatus.VERIFYING,
            "human confirmed differential:not_applicable",
            "decision_consumed",
            "verify_reused",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.VERIFY_CODE_FAILED,
            TaskStatus.CHANGES_REQUESTED,
            "legacy worker: invalid receipt, differential failed or assertions failed",
            "event",
            "verify_released",
            "attempt_frozen",
            "worktree_archived",
        ),
        # --- review / approve / merge (R10 L440-L451) ---
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.APPROVE,
            TaskStatus.APPROVED,
            "required reviews satisfied, receipt matches head and epoch is current",
            "event",
            "branch_frozen",
            "merge_intent_created",
        ),
        _t(
            TaskStatus.APPROVED,
            EventKind.REOPEN_FOR_CHANGES,
            TaskStatus.CHANGES_REQUESTED,
            "human adjudication or post-approval branch head movement",
            "event",
            "branch_unfrozen",
        ),
        _t(
            review_wait,
            EventKind.REVIEW_WAIT_TIMEOUT,
            TaskStatus.ESCALATED,
            "review, merge or rework wait exceeded SLA",
            "event",
            "human_todo",
        ),
        # --- human / escalation loops (R10 L443-L444) ---
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_WAIT_TIMEOUT,
            TaskStatus.AWAITING_HUMAN,
            "wait exceeded SLA; reminder only, never auto-release",
            "reminder",
        ),
        _t(
            TaskStatus.ESCALATED,
            EventKind.ESCALATION_REMINDER,
            TaskStatus.ESCALATED,
            "human adjudication still pending",
            "reminder",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.SUBMIT_RESULT,
            TaskStatus.VERIFYING,
            "commit, completion revision and branch present within verify queue cap",
            "event",
            "verify_job_created",
            "lease_released",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.SUBMIT_REJECTED_VERIFY_CAPACITY,
            TaskStatus.IN_PROGRESS,
            "verify job queue is at capacity (32)",
            "event",
            "reminder",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.REQUEST_CHANGES,
            TaskStatus.CHANGES_REQUESTED,
            "reviewer findings logged and review_round below max",
            "event",
            "attempt_frozen",
        ),
        _t(
            review_round,
            EventKind.REVIEW_ROUND_EXHAUSTED,
            TaskStatus.ESCALATED,
            "review_round reached max_review_rounds",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.CHANGES_REQUESTED,
            EventKind.CLAIM_FOR_REVISION,
            TaskStatus.CLAIMED,
            "original or new owner passes dependency, resource and capability checks",
            "event",
            "attempt_created",
            "lease_created",
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
            EventKind.RESUBMIT,
            TaskStatus.AWAITING_REVIEW,
            "new attempt with valid development lease and complete evidence",
            "event",
            "review_lease_requested",
        ),
        # --- help / blocked / stuck / split (R10 L452-L464) ---
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.REQUEST_HELP,
            TaskStatus.NEEDS_HELP,
            "owner lease valid and help_id created",
            "event",
            "help_created",
        ),
        _t(
            TaskStatus.NEEDS_HELP,
            EventKind.HELP_REQUESTED,
            TaskStatus.NEEDS_HELP,
            "owner published a structured help request",
            "event",
            "help_created",
        ),
        _t(
            TaskStatus.NEEDS_HELP,
            EventKind.HELP_ACCEPTED,
            TaskStatus.NEEDS_HELP,
            "collaborator accepted with scoped task context",
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
            "independent artifact attached and owner lease valid",
            "event",
            "help_attached",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.DEPENDENCY_MISSING,
            TaskStatus.BLOCKED,
            "scanner or report_blocked logged a blocker reference",
            "event",
            "blocked_scan",
        ),
        _t(
            TaskStatus.BLOCKED,
            EventKind.UNBLOCK,
            TaskStatus.IN_PROGRESS,
            "independent scanner confirms blocker release and owner lease valid",
            "event",
            "lease_reissued",
        ),
        _t(
            stuck,
            EventKind.STUCK_THRESHOLD_REACHED,
            TaskStatus.ESCALATED,
            "three consecutive scans show no progress",
            "event",
            "human_todo",
        ),
        _t(
            stuck,
            EventKind.STUCK_HUMAN_THRESHOLD,
            TaskStatus.AWAITING_HUMAN,
            "five consecutive scans show no progress",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.HUMAN_QUESTION,
            TaskStatus.AWAITING_HUMAN,
            "structured human question recorded; owner lease clock paused",
            "decision_created",
            "lease_paused",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.SPLIT_PARENT_BLOCKED,
            TaskStatus.BLOCKED,
            "child drafts exist, parent incomplete and split idempotency key valid",
            "event",
            "children_created",
        ),
        _t(
            TaskStatus.BLOCKED,
            EventKind.ALL_CHILDREN_MERGED,
            TaskStatus.READY,
            "all children merged while the parent remains incomplete",
            "event",
            "state_changed",
        ),
        _t(
            (TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED),
            EventKind.SPLIT,
            "child_created",
            "human or owner initiated, budget sufficient and idempotency key valid",
            "event",
            "children_created",
        ),
        # --- awaiting_human actions (R10 L465-L467) ---
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_RESUME,
            TaskStatus.IN_PROGRESS,
            "structured decision recorded and development lease (re)issued",
            "decision_consumed",
            "lease_reissued",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_REOPEN,
            TaskStatus.READY,
            "human requires a fresh claim",
            "decision_consumed",
            "state_changed",
        ),
        _t(
            TaskStatus.AWAITING_HUMAN,
            EventKind.HUMAN_CANCEL,
            TaskStatus.CANCELLED,
            "human cancellation",
            "decision_created",
            "state_changed",
        ),
        # --- drift (R10 L468-L475) ---
        _t(
            drift_dispatch,
            EventKind.PLAN_HASH_CHANGED,
            TaskStatus.PLAN_DRIFT,
            "bound plan snapshot hash differs from the task binding",
            "event",
            "attempt_frozen",
        ),
        _t(
            drift_dispatch,
            EventKind.IMPL_HASH_CHANGED,
            TaskStatus.IMPL_DRIFT,
            "implementation or dependency output hash violates the contract",
            "event",
            "attempt_frozen",
        ),
        _t(
            drift_verify,
            EventKind.PLAN_HASH_CHANGED,
            TaskStatus.PLAN_DRIFT,
            "bound plan snapshot hash differs from the task binding",
            "event",
            "attempt_frozen",
        ),
        _t(
            drift_verify,
            EventKind.IMPL_HASH_CHANGED,
            TaskStatus.IMPL_DRIFT,
            "implementation or dependency output hash violates the contract",
            "event",
            "attempt_frozen",
        ),
        _t(
            (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.CLAIM_FOR_REVISION,
            TaskStatus.CLAIMED,
            "human confirms drift handled or plan rolled back to the bound version",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.HUMAN_RESUME,
            TaskStatus.IN_PROGRESS,
            "human confirms continuation with a valid owner and development lease",
            "event",
            "lease_reissued",
        ),
        _t(
            (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.HUMAN_REOPEN_NO_OWNER,
            TaskStatus.READY,
            "human confirms continuation but no valid owner exists; fresh claim needed",
            "decision_created",
            "state_changed",
        ),
        _t(
            (TaskStatus.PLAN_DRIFT, TaskStatus.IMPL_DRIFT),
            EventKind.HUMAN_REOPEN,
            TaskStatus.READY,
            "human requests a fresh claim",
            "decision_created",
            "state_changed",
        ),
        # --- stale / reclaim (R10 L476-L482) ---
        _t(
            TaskStatus.STALE,
            EventKind.RECLAIM,
            TaskStatus.RECLAIMING,
            "grace elapsed, takeover allowed and attempt budget remains",
            "event",
            "reclaim_window_opened",
        ),
        _t(
            TaskStatus.STALE,
            EventKind.ATTEMPT_BUDGET_EXHAUSTED,
            TaskStatus.ESCALATED,
            "attempt_no reached max_attempts without budget override",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.STALE,
            EventKind.HUMAN_CANCEL,
            TaskStatus.CANCELLED,
            "human cancellation",
            "decision_created",
            "state_changed",
        ),
        _t(
            TaskStatus.RECLAIMING,
            EventKind.TAKE_OVER,
            TaskStatus.CLAIMED,
            "new harness receives a new epoch, attempt and resources within budget",
            "event",
            "attempt_created",
            "lease_created",
        ),
        _t(
            TaskStatus.RECLAIMING,
            EventKind.ATTEMPT_BUDGET_EXHAUSTED,
            TaskStatus.ESCALATED,
            "attempt_no reached max_attempts without budget override",
            "event",
            "human_todo",
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
            EventKind.HUMAN_CANCEL,
            TaskStatus.CANCELLED,
            "human cancellation",
            "decision_created",
            "state_changed",
        ),
        # --- release / abandon / reopen (R10 L483-L486) ---
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
            "owner yields voluntarily with no uncommitted output",
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
            "owner records reason, output and takeover info",
            "artifact_recorded",
            "lease_released",
        ),
        _t(
            TaskStatus.IN_PROGRESS,
            EventKind.ABANDON,
            TaskStatus.ABANDONED,
            "owner submits an abandonment record",
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
        # --- timers, leases, merge edges (R10 L487-L500) ---
        _t(
            (TaskStatus.READY, TaskStatus.QUEUED),
            EventKind.AGING_TIMEOUT,
            TaskStatus.ESCALATED,
            "wait exceeded the aging threshold",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.AWAITING_REVIEW,
            EventKind.REVIEW_LEASE_EXPIRE,
            TaskStatus.AWAITING_REVIEW,
            "review lease expired with no running review intent",
            "event",
            "review_requeued",
        ),
        _t(
            TaskStatus.APPROVED,
            EventKind.MERGE_LEASE_EXPIRE,
            TaskStatus.APPROVED,
            "merge lease expired with no running merge intent",
            "event",
            "merge_requeued",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_LEASE_EXPIRE,
            TaskStatus.APPROVED,
            "merge lease expired with no running merge intent",
            "event",
            "merge_requeued",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_TIMEOUT,
            TaskStatus.ESCALATED,
            "integration test exceeded integration_test_timeout_minutes",
            "event",
            "human_todo",
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
            "structured human adjudication is required",
            "decision_created",
            "human_todo",
        ),
        _t(
            TaskStatus.APPROVED,
            EventKind.MERGE_START,
            TaskStatus.MERGING,
            "all gates pass and integration_health is green",
            "event",
            "merge_lease_created",
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
            "pre-commit conflict, nothing written, merge worker can resolve",
            "event",
            "merge_requeued",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_CONFLICT_NEEDS_OWNER,
            TaskStatus.CHANGES_REQUESTED,
            "pre-commit conflict requires the task owner to produce new code",
            "event",
            "human_todo",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_POST_COMMIT_FAILURE,
            TaskStatus.APPROVED,
            "post-commit failure with successful revert recovery and re-run",
            "event",
            "revert_intent_created",
        ),
        _t(
            TaskStatus.MERGING,
            EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED,
            TaskStatus.ESCALATED,
            "post-commit failure with failed or invalid revert recovery",
            "event",
            "revert_intent_created",
            "human_todo",
        ),
        _t(
            TaskStatus.BLOCKED,
            EventKind.BLOCK_TIMEOUT,
            TaskStatus.ESCALATED,
            "blocking exceeded the block SLA",
            "event",
            "human_todo",
        ),
    ]
    return TransitionTable(transitions)
