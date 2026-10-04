"""R10 规范常量的**代码侧镜像**。

为什么需要这个文件：
  R10 §10 / §37.7 是状态机与事件名的唯一真相源（纯文档）。但业务代码需要可 import 的
  枚举。两处各写一份必然漂移 —— 这正是 R9/R10 复审发现的那类"两份真相源"问题。

规则：
1. 本文件只做**镜像**，不新增、不改写。任何新增状态/事件必须先改 R10 正文并升
   schema_version，再改这里，且两者必须在同一次提交内完成。
2. `tools/docstate/docstate.py` 会校验 R10 正文与本文件的对应关系（R11 起生效）。
3. 状态集合、事件集合、迁移边数量必须与 R10 一致，改动会让 docstate 失败。
"""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    """R10 §10 的 23 个正式状态（+1 伪状态）。"""

    DRAFT = "draft"
    DRAFT_REJECTED = "draft_rejected"
    READY_WAITING_DEPS = "ready_waiting_deps"
    READY = "ready"
    QUEUED = "queued"
    VERIFYING = "verifying"
    CLAIMED = "claimed"
    IN_PROGRESS = "in_progress"
    NEEDS_HELP = "needs_help"
    BLOCKED = "blocked"
    CHANGES_REQUESTED = "changes_requested"
    PLAN_DRIFT = "plan_drift"
    IMPL_DRIFT = "impl_drift"
    AWAITING_REVIEW = "awaiting_review"
    APPROVED = "approved"
    MERGING = "merging"
    STALE = "stale"
    RECLAIMING = "reclaiming"
    ABANDONED = "abandoned"
    ESCALATED = "escalated"
    AWAITING_HUMAN = "awaiting_human"
    MERGED = "merged"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


#: 终态：没有出边（R10 §10 迁移表）
_TERMINAL = frozenset({TaskStatus.MERGED, TaskStatus.CANCELLED})

#: 持有开发 Lease 的 owner 状态：必须有独立于 owner 的 Lease 过期出口（R10 §10 硬规则 / ADR-033）
OWNER_STATES = frozenset(
    {
        TaskStatus.CLAIMED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.NEEDS_HELP,
        TaskStatus.BLOCKED,
        TaskStatus.PLAN_DRIFT,
        TaskStatus.IMPL_DRIFT,
    }
)

#: 持有独立 worker Lease 的等待状态（R10 §13）
WORKER_LEASE_STATES = frozenset(
    {
        TaskStatus.VERIFYING,
        TaskStatus.AWAITING_REVIEW,
        TaskStatus.APPROVED,
        TaskStatus.MERGING,
    }
)

#: 人工等待状态：超时只提醒，不自动放行（R10 §18 / ADR-035）
HUMAN_WAIT_STATES = frozenset(
    {
        TaskStatus.AWAITING_HUMAN,
        TaskStatus.ESCALATED,
    }
)

#: 伪状态：只作为 `split` 边的 next 出现，不参与可达性检查（R10 §10 补充说明表）
PSEUDO_STATES = frozenset({"child_created"})

#: R10 §13：参与 queue_reason 的资源容量原因。verify_capacity 只存在于 verify_jobs
QUEUE_REASONS = frozenset({"resource", "worktree_capacity", "merge_capacity"})

#: R10 §16：verify failure_reason 枚举。混用会导致环境失败被当代码失败返工
VERIFY_FAILURE_REASONS = frozenset({"code_failed", "env_failed", "timeout", "aborted"})

#: R10 §15：side_effect_intents.type 枚举
SIDE_EFFECT_INTENT_TYPES = frozenset(
    {
        "worktree_add",
        "merge",
        "revert_merge",
        "push",
        "archive",
        "verify_run",
        "patch_apply",  # R9-05 / R10-11
    }
)


class EventKind(StrEnum):
    """R10 §37.7 M 集合的迁移事件**子集** —— MVP 阶段实际会用到的。

    完整集合有 74 项（M）。这里只列 MVP 会触发的，其余由 MVP-03 状态机按需补充。

    ⚠️禁止凭印象新增：新增前必须先确认该事件名在 R10 §37.7 的 M 集合内，
    否则 `docstate` 的封闭性断言会失败。
    """

    # plan
    PLAN_APPROVE = "plan.approve"
    PLAN_APPROVE_DEPENDENCY_PENDING = "plan.approve_dependency_pending"
    PLAN_REJECT = "plan.reject"
    PLAN_REVISE = "plan.revise"
    PLAN_SUPERSEDE = "plan.supersede"

    # claim
    CLAIM = "claim"
    CLAIM_DEFERRED_RESOURCE = "claim_deferred_resource"  # R10-01
    CLAIM_FOR_REVISION = "claim_for_revision"
    REASSIGN = "reassign"
    RESUBMIT = "resubmit"
    RELEASE = "release"
    ABANDON = "abandon"
    REOPEN = "reopen"  # R10-02：abandoned 的唯一恢复出口

    # deps
    DEPENDENCIES_PENDING = "dependencies_pending"
    DEPENDENCIES_SATISFIED = "dependencies_satisfied"
    BLOCK_TIMEOUT = "block_timeout"
    ORPHAN_DETECTED = "orphan_detected"  # R10-05

    # queue
    RESOURCE_AVAILABLE = "resource_available"
    QUEUE_CANCEL = "queue_cancel"
    AGING_TIMEOUT = "aging_timeout"

    # work
    START = "start"
    SUBMIT_RESULT = "submit_result"
    SUBMIT_REJECTED_VERIFY_CAPACITY = "submit_rejected_verify_capacity"
    LEASE_EXPIRE = "lease_expire"

    # verify
    VERIFY_PASSED = "verify_passed"
    VERIFY_CODE_FAILED = "verify_code_failed"
    VERIFY_ENV_FAILED = "verify_env_failed"
    VERIFY_LEASE_EXPIRE = "verify_lease_expire"
    VERIFY_EXECUTION_TIMEOUT = "verify_execution_timeout"
    VERIFY_QUEUE_TIMEOUT = "verify_queue_timeout"
    DIFFERENTIAL_NOT_APPLICABLE = "differential_not_applicable"  # R10-fix-03

    # review
    REQUEST_CHANGES = "request_changes"
    REVIEW_ROUND_EXHAUSTED = "review_round_exhausted"
    REVIEW_WAIT_TIMEOUT = "review_wait_timeout"
    REVIEW_LEASE_EXPIRE = "review_lease_expire"
    APPROVE = "approve"
    REOPEN_FOR_CHANGES = "reopen_for_changes"

    # merge
    MERGE_START = "merge_start"
    MERGE_SUCCESS = "merge_success"
    MERGE_TIMEOUT = "merge_timeout"
    MERGE_LEASE_EXPIRE = "merge_lease_expire"
    MERGE_CONFLICT = "merge_conflict"
    MERGE_CONFLICT_NEEDS_OWNER = "merge_conflict_needs_owner"  # R10-06
    MERGE_POST_COMMIT_FAILURE = "merge_post_commit_failure"
    MERGE_POST_COMMIT_FAILURE_ESCALATED = "merge_post_commit_failure_escalated"  # R10-06

    # help
    REQUEST_HELP = "request_help"
    HELP_REQUESTED = "help_requested"
    HELP_ACCEPTED = "help_accepted"
    HELP_REJECTED = "help_rejected"
    HELP_RESOLVED = "help_resolved"

    # blocked / stuck
    DEPENDENCY_MISSING = "dependency_missing"
    UNBLOCK = "unblock"
    STUCK_THRESHOLD_REACHED = "stuck_threshold_reached"
    STUCK_HUMAN_THRESHOLD = "stuck_human_threshold"

    # drift
    PLAN_HASH_CHANGED = "plan_hash_changed"
    IMPL_HASH_CHANGED = "impl_hash_changed"

    # stale / reclaim
    RECLAIM = "reclaim"
    TAKE_OVER = "take_over"
    NOBODY_TAKES = "nobody_takes"
    ATTEMPT_BUDGET_EXHAUSTED = "attempt_budget_exhausted"

    # split
    SPLIT = "split"
    SPLIT_PARENT_BLOCKED = "split_parent_blocked"
    ALL_CHILDREN_MERGED = "all_children_merged"

    # human
    HUMAN_QUESTION = "human.question"
    HUMAN_RESUME = "human.resume"
    HUMAN_REOPEN = "human.reopen"
    HUMAN_REOPEN_NO_OWNER = "human.reopen_no_owner"  # R10-fix-05
    HUMAN_CANCEL = "human.cancel"
    HUMAN_RELEASE = "human.release"
    HUMAN_ADJUDICATE = "human.adjudicate"
    HUMAN_WAIT_TIMEOUT = "human_wait_timeout"
    HUMAN_RESUME_VERIFY_NOT_APPLICABLE = "human.resume_verify_not_applicable"
    ESCALATION_REMINDER = "escalation_reminder"

    # cancel
    CANCEL = "cancel"


#: 迁移表边数（R10 §10 关键迁移表）。MVP 阶段实现子集，但必须记录总数以便核对
R10_TRANSITION_EDGE_COUNT = 86

#: R10 §37.7 集合大小。docstate 会核对规范侧，本处用于代码侧交叉验证
R10_ALLOWLIST_COUNTS = {"M": 74, "O": 13, "R": 1, "UNION": 88, "LOOPS": 10}
