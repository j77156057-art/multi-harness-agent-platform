"""Request models for the MVP-08 REST surface.

Response bodies are built from the service layer's dictionaries: they include
columns whose shape is owned by the schema, and mirroring all of them in pydantic
would be a second definition that drifts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateProject(BaseModel):
    project_id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class CreateGroup(BaseModel):
    group_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    group_short: str | None = None


class CreatePlan(BaseModel):
    plan_id: str = Field(min_length=1)
    snapshot: dict[str, Any]


class PlanDecision(BaseModel):
    decided_by: str = Field(min_length=1)
    reason: str | None = None


class HarnessRegistrationRequest(BaseModel):
    """The human-maintained R10 §6 registration record."""

    registered_by: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    adapter: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    capabilities: list[str] = Field(default_factory=list)
    max_parallel_tasks: int = Field(default=1, ge=1)
    status: Literal["online", "degraded", "offline"] = "online"
    registration_version: str = Field(min_length=1)


class ClaimRequestModel(BaseModel):
    claim_request_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1)
    partial_allowed: bool = False
    payload_hash: str | None = None


class FormalDispatchRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    goal: str = Field(min_length=1, max_length=8000)
    harness_id: str = Field(min_length=1, max_length=100)
    decided_by: str = Field(min_length=1)
    exclusive_paths: list[str] = Field(min_length=1, max_length=50)
    acceptance: str = Field(min_length=1, max_length=4000)
    #: The task this one takes over (a re-point).  Optional; the platform
    #: checks that it exists, is in the same group, and is no longer live.
    supersedes: str | None = Field(default=None, min_length=1, max_length=64)


class HeartbeatRequest(BaseModel):
    task_id: str
    attempt_id: str
    lease_token: str
    fencing_epoch: int
    progress_hash: str | None = None


class AttemptAction(BaseModel):
    task_id: str
    attempt_id: str
    fencing_epoch: int
    reason: str = ""


class ReportBlockedRequest(BaseModel):
    task_id: str
    attempt_id: str
    fencing_epoch: int
    blocker_ref: str


class SubmitResultRequest(BaseModel):
    task_id: str
    attempt_id: str
    fencing_epoch: int
    completion: dict[str, Any]


class ReviewSubmitRequest(BaseModel):
    task_id: str
    attempt_id: str
    reviewer_harness_id: str
    review_id: str | None = None
    verdict: str
    reviewed_commit: str
    review_revision: int = 1
    findings: list[dict[str, Any]] = Field(default_factory=list)


class PostMessage(BaseModel):
    author_kind: str
    author_id: str
    body: str
    mentions: list[str] = Field(default_factory=list)
    task_id: str | None = None
    refs: list[str] = Field(default_factory=list)
    # 定向消息：收件人必须在 mentions 里（R10 §19）。
    direct: bool = False


class PublishTaskBranchRequest(BaseModel):
    task_id: str
    attempt_id: str
    branch: str
    commit: str
    fencing_epoch: int
    idempotency_key: str


class ReviewClaimRequest(BaseModel):
    task_id: str
    attempt_id: str
    reviewer_harness_id: str
    review_id: str | None = None


class HumanReviewClaimRequest(BaseModel):
    task_id: str
    attempt_id: str
    decided_by: str = Field(min_length=1)
    review_id: str | None = None


class HumanReviewSubmitRequest(BaseModel):
    task_id: str
    attempt_id: str
    decided_by: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    verdict: Literal["approve", "request_changes"]
    reviewed_commit: str = Field(min_length=1)
    review_revision: int = Field(default=1, ge=1)
    findings: list[dict[str, Any]] = Field(default_factory=list)


class ProbeRequest(BaseModel):
    harness_id: str
    adapter: str
    adapter_version: str
    self_reported_capabilities: list[str] = Field(default_factory=list)
    host_mode: str | None = None


class AdminRefineRequest(BaseModel):
    """管理员 harness 的细化入参（``admin:refine``）。

    ``group_id`` 只是事件的归属，用来回答"这条要求是哪个群提出的"；细化本身
    不改任何任务状态——写状态仍然走 ``plan:approve``。

    ``answers`` 是"管理员不肯猜、先问群主"那条路径的回合：管理员上一轮把问题
    发进群对话，群主用大白话回答，回答原样回灌给模型。没有它，回合就只能靠
    前端拼字符串，管理员问过什么、群主答过什么在平台里就查不到了。
    """

    group_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1, max_length=4000)
    max_tasks: int = Field(default=3, ge=1, le=8)
    answers: list[str] = Field(default_factory=list, max_length=8)


class AskHumanRequest(BaseModel):
    task_id: str
    attempt_id: str
    fencing_epoch: int
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    harness_id: str = "harness"


class HelpVoteRequest(BaseModel):
    task_id: str
    help_id: str = Field(min_length=1)
    harness_id: str = "harness"


class SplitTaskRequest(BaseModel):
    task_id: str
    attempt_id: str
    fencing_epoch: int
    child_specs: list[dict[str, Any]] = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    harness_id: str = "harness"


class HumanResumeRequest(BaseModel):
    task_id: str
    decided_by: str = Field(min_length=1)
    answer: str | None = None


class HumanReopenRequest(BaseModel):
    task_id: str
    decided_by: str = Field(min_length=1)
    answer: str | None = None


class HumanAttemptBudgetOverrideRequest(BaseModel):
    """A human gives a task whose attempt budget is spent one more chance."""

    task_id: str
    decided_by: str = Field(min_length=1)
    reason: str = ""


class HumanReleaseRequest(BaseModel):
    task_id: str
    decided_by: str = Field(min_length=1)
    # R10 §8 / R8-08: after the attempt budget is exhausted, human.release may
    # only proceed when it carries the override (event-side name
    # ``attempt_budget_override``). The field itself stays ``budget_override_by``.
    budget_override: bool = False
    reason: str = ""


class HumanAdjudicateRequest(BaseModel):
    task_id: str
    decided_by: str = Field(min_length=1)
    question: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)


class HumanCancelRequest(BaseModel):
    task_id: str
    decided_by: str = Field(min_length=1)
    reason: str = ""


class AdminCancelRequest(BaseModel):
    """Path-addressed cancel for the administrator harness.

    The human route carries ``task_id`` in the body; this one has it in the
    path. ``decided_by`` is bound to the authenticated principal by
    ``bind_declared``, so it cannot name someone else.
    """

    decided_by: str = Field(min_length=1)
    reason: str = ""


# ------------------------------------------------- R10 §39 分配与传阅（R10-fix-13）
#
# 这一组刻意把"谁在做"放进请求体（`opened_by` / `harness_id` / `decided_by`），
# 理由和 `ReviewClaimRequest.reviewer_harness_id` 一样：加性部署（`AP_AUTH_MODE=off`）
# 下没有 principal 可读，请求体是唯一的身份来源；开启 enforce 后路由用
# `bind_declared` 把请求体与 token 主体绑死，冒用他人 id 会 403。


class CirculationOpenRequest(BaseModel):
    group_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_snapshot_id: str = Field(min_length=1)
    reviewer_harness_ids: list[str] = Field(min_length=1)
    opened_by: str = Field(min_length=1)
    deadline_at: str | None = None


class PlanDraftRequest(BaseModel):
    """The administrator's refined plan, on its way into circulation."""

    group_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    #: The refined task specs (title/goal/paths/acceptance and the window).
    tasks: list[dict[str, object]] = Field(min_length=1)
    #: Who reads the draft before anything is assigned.
    reviewer_harness_ids: list[str] = Field(min_length=1)
    opened_by: str = Field(min_length=1)
    plan_id: str | None = None


class PlanRevisionRequest(BaseModel):
    """The administrator's revised plan: a new snapshot of the same draft."""

    plan_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    tasks: list[dict[str, object]] = Field(min_length=1)
    revised_by: str = Field(min_length=1)


class CirculationReviewRequest(BaseModel):
    circulation_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    body: str = Field(min_length=1)
    material_hash: str | None = None


class CirculationRoundRequest(BaseModel):
    circulation_id: str = Field(min_length=1)
    #: 收轮/定稿/作废的经办人（管理员 harness）。
    harness_id: str = Field(min_length=1)
    #: 修订时可以换下一轮的传阅名单；不传则沿用本轮名单。
    reviewer_harness_ids: list[str] | None = None
    #: 作废必须给理由（§39 要求这几条边留理由）。
    reason: str = ""


class AllocationOpenRequest(BaseModel):
    group_id: str = Field(min_length=1)
    plan_snapshot_id: str = Field(min_length=1)
    opened_by: str = Field(min_length=1)
    deadline_at: str | None = None


class AllocationRoundRequest(BaseModel):
    round_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    reason: str = ""


class ApplicationSubmitRequest(BaseModel):
    round_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1)
    reason: str = ""
    body: str = ""
    instance_id: str | None = None


class ApplicationUpdateRequest(BaseModel):
    application_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    #: 乐观锁版本（§39 硬规则 4）。
    expected_revision: int = Field(ge=1)
    reason: str | None = None
    body: str | None = None
    task_ids: list[str] | None = None


class ApplicationWithdrawRequest(BaseModel):
    application_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)


class AllocationDecisionRequest(BaseModel):
    round_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    application_id: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    reason: str = ""
    compared_harness_ids: list[str] | None = None


class AllocationDeferRequest(BaseModel):
    round_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AllocationMaterialRequest(BaseModel):
    round_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AssignmentInviteRequest(BaseModel):
    round_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    invited_by: str = Field(min_length=1)
    reason: str = ""


class AssignmentResolveRequest(BaseModel):
    allocation_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)


class AssignmentRevokeRequest(BaseModel):
    allocation_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    revoked_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
