"""MVP-08 REST routes.

Path naming follows R10 §7 / §21 so an Adapter maps verb to path tail with no
translation table (see docs/prep/MVP-08/README.md §2). Read endpoints answer from
the projections; the event stream is the incremental cursor.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api import schemas
from app.claim.service import ClaimRequest, ClaimService
from app.db.connection import Database
from app.git.publish import PublishProxy
from app.lease.service import renew_lease
from app.tasks.service import SchedulerService


def _database(request: Request) -> Database:
    return request.app.state.database


DatabaseDep = Annotated[Database, Depends(_database)]


def _scheduler(database: DatabaseDep) -> SchedulerService:
    return SchedulerService(database)


SchedulerDep = Annotated[SchedulerService, Depends(_scheduler)]

management = APIRouter(tags=["management"])
adapter = APIRouter(prefix="/adapter", tags=["adapter"])
review = APIRouter(prefix="/review", tags=["review"])
read = APIRouter(tags=["read"])


# ------------------------------------------------------------- management


@management.post("/projects")
def create_project(body: schemas.CreateProject, service: SchedulerDep) -> dict[str, Any]:
    return service.create_project(project_id=body.project_id, name=body.name)


@management.post("/projects/{project_id}/groups")
def create_group(
    project_id: str, body: schemas.CreateGroup, service: SchedulerDep
) -> dict[str, Any]:
    return service.create_group(group_id=body.group_id, project_id=project_id, name=body.name)


@management.post("/groups/{group_id}/plans")
def create_plan(group_id: str, body: schemas.CreatePlan, service: SchedulerDep) -> dict[str, Any]:
    outcome = service.create_plan(plan_id=body.plan_id, group_id=group_id, snapshot=body.snapshot)
    return {
        "plan_id": outcome.plan_id,
        "snapshot_id": outcome.snapshot_id,
        "plan_hash": outcome.plan_hash,
    }


@management.post("/plans/{plan_id}/approve")
def approve_plan(plan_id: str, body: schemas.PlanDecision, service: SchedulerDep) -> dict[str, Any]:
    outcome = service.approve_plan(plan_id=plan_id, decided_by=body.decided_by)
    return {
        "plan_id": outcome.plan_id,
        "ready": list(outcome.ready),
        "waiting": list(outcome.waiting),
    }


@management.post("/plans/{plan_id}/reject")
def reject_plan(plan_id: str, body: schemas.PlanDecision, service: SchedulerDep) -> dict[str, Any]:
    rejected = service.reject_plan(
        plan_id=plan_id, decided_by=body.decided_by, reason=body.reason or ""
    )
    return {"plan_id": plan_id, "rejected": rejected}


# ----------------------------------------------------------------- adapter


@adapter.post("/claim")
def claim(body: schemas.ClaimRequestModel, database: DatabaseDep, response: Response):
    result = ClaimService(database).claim(
        ClaimRequest(
            claim_request_id=body.claim_request_id,
            harness_id=body.harness_id,
            task_ids=tuple(body.task_ids),
            partial_allowed=body.partial_allowed,
            payload_hash=body.payload_hash,
        )
    )
    if result.replayed:
        response.headers["Idempotency-Replayed"] = "true"
    if result.created_seq is not None:
        response.headers["X-Event-Seq"] = str(result.created_seq)
    return result.as_json()


@adapter.post("/heartbeat")
def heartbeat(body: schemas.HeartbeatRequest, database: DatabaseDep) -> dict[str, Any]:
    with database.transaction() as conn:
        lease_until = renew_lease(
            conn,
            task_id=body.task_id,
            attempt_id=body.attempt_id,
            lease_token=body.lease_token,
            fencing_epoch=body.fencing_epoch,
        )
    return {
        "task_id": body.task_id,
        "attempt_id": body.attempt_id,
        "fencing_epoch": body.fencing_epoch,
        "lease_until": lease_until,
    }


@adapter.post("/submit_result")
def submit_result(body: schemas.SubmitResultRequest, service: SchedulerDep) -> dict[str, Any]:
    record = service.submit_result(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        completion=body.completion,
    )
    return {
        "task_id": body.task_id,
        "attempt_id": body.attempt_id,
        "event": record.event_name,
        "seq": record.seq,
        "lease_released": True,
    }


@adapter.post("/publish_task_branch")
def publish_task_branch(
    body: schemas.PublishTaskBranchRequest, database: DatabaseDep
) -> dict[str, Any]:
    """The only way an agent gitdir's objects reach canonical (R10 §13)."""
    result = PublishProxy(database).publish_task_branch(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        branch=body.branch,
        commit=body.commit,
        fencing_epoch=body.fencing_epoch,
        idempotency_key=body.idempotency_key,
    )
    return result.as_json()


@adapter.post("/request_help")
def request_help(body: schemas.AttemptAction, service: SchedulerDep) -> dict[str, Any]:
    return service.request_help(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        reason=body.reason,
    )


@adapter.post("/report_blocked")
def report_blocked(body: schemas.ReportBlockedRequest, service: SchedulerDep) -> dict[str, Any]:
    return service.report_blocked(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        blocker_ref=body.blocker_ref,
    )


@adapter.post("/release")
def release(body: schemas.AttemptAction, service: SchedulerDep) -> dict[str, Any]:
    return service.release(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        reason=body.reason,
    )


@adapter.post("/abandon")
def abandon(body: schemas.AttemptAction, service: SchedulerDep) -> dict[str, Any]:
    return service.abandon(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        reason=body.reason,
    )


# ------------------------------------------------------------------ review


@review.post("/submit")
def review_submit(body: schemas.ReviewSubmitRequest, service: SchedulerDep) -> dict[str, Any]:
    return service.submit_review(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        reviewer_harness_id=body.reviewer_harness_id,
        verdict=body.verdict,
        reviewed_commit=body.reviewed_commit,
        review_revision=body.review_revision,
        findings=body.findings,
    )


# -------------------------------------------------------------------- read


@read.get("/tasks/{task_id}")
def get_task(task_id: str, service: SchedulerDep) -> dict[str, Any]:
    return service.get_task(task_id)


@read.get("/groups/{group_id}/tasks")
def list_tasks(
    group_id: str,
    service: SchedulerDep,
    status: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return {"tasks": service.list_tasks(group_id, status=status)}


@read.get("/tasks/{task_id}/events")
def task_events(
    task_id: str,
    service: SchedulerDep,
    since_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    return _event_page(service, since_seq=since_seq, limit=limit, task_id=task_id)


@read.get("/events")
def events(
    service: SchedulerDep,
    since_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    return _event_page(service, since_seq=since_seq, limit=limit, task_id=None)


def _event_page(
    service: SchedulerService, *, since_seq: int, limit: int, task_id: str | None
) -> dict[str, Any]:
    found = service.list_events(since_seq=since_seq, limit=limit + 1, task_id=task_id)
    has_more = len(found) > limit
    found = found[:limit]
    rows = [
        {
            "seq": event.seq,
            "event_name": event.event_name,
            "aggregate_type": event.aggregate_type,
            "aggregate_id": event.aggregate_id,
            "actor_type": event.actor_type,
            "actor_id": event.actor_id,
            "payload": event.payload,
            "schema_version": event.schema_version,
            "created_at": event.created_at,
        }
        for event in found
    ]
    last = rows[-1]["seq"] if rows else since_seq
    return {"events": rows, "next_seq": last, "has_more": has_more}


@read.post("/groups/{group_id}/messages")
def post_message(group_id: str, body: schemas.PostMessage, service: SchedulerDep) -> dict[str, Any]:
    return service.post_message(
        group_id=group_id,
        author_kind=body.author_kind,
        author_id=body.author_id,
        body=body.body,
        mentions=body.mentions,
        task_id=body.task_id,
        refs=body.refs,
    )


@read.get("/groups/{group_id}/messages")
def list_messages(
    group_id: str,
    service: SchedulerDep,
    since_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    rows = service.list_messages(group_id, since_seq=since_seq, limit=limit + 1)
    has_more = len(rows) > limit
    rows = rows[:limit]
    last = int(rows[-1]["created_seq"]) if rows else since_seq
    return {"messages": rows, "next_seq": last, "has_more": has_more}
