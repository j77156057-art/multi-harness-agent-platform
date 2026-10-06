"""Request models for the MVP-08 REST surface.

Response bodies are built from the service layer's dictionaries: they include
columns whose shape is owned by the schema, and mirroring all of them in pydantic
would be a second definition that drifts.
"""

from __future__ import annotations

from typing import Any

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


class ClaimRequestModel(BaseModel):
    claim_request_id: str = Field(min_length=1)
    harness_id: str = Field(min_length=1)
    task_ids: list[str] = Field(min_length=1)
    partial_allowed: bool = False
    payload_hash: str | None = None


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


class ProbeRequest(BaseModel):
    harness_id: str
    adapter: str
    adapter_version: str
    self_reported_capabilities: list[str] = Field(default_factory=list)
    host_mode: str | None = None
