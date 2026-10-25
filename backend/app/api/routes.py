"""MVP-08 REST routes.

Path naming follows R10 §7 / §21 so an Adapter maps verb to path tail with no
translation table (see docs/prep/MVP-08/README.md §2). Read endpoints answer from
the projections; the event stream is the incremental cursor.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.adapter import cli_output, model_choice
from app.adapter.dispatch import dispatch, eligible_tasks
from app.adapter.harnesses.generic import declared_base_templates
from app.adapter.models import configured_model
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry, RegistrationConflict, capabilities_from
from app.admin.service import AdminService
from app.allocation.service import AllocationService
from app.api import schemas
from app.api.auth_deps import (
    Principal,
    bearer_token,
    bind_branch_subject,
    bind_declared,
    bind_task_owner,
    require_for,
)
from app.api.errors import ApiError
from app.auth.tokens import TokenInvalid, TokenService
from app.claim.service import ClaimRequest, ClaimService
from app.config import settings
from app.db.connection import Database
from app.git.publish import PublishProxy
from app.human.service import HumanOpsService
from app.lease.service import renew_lease
from app.review_doc import build_document
from app.spec import TaskStatus
from app.tasks.service import NotFound, SchedulerService


def _guard(key: str):
    """Annotated dependency for one R10 §17 permission key.

    One factory for both faces: the allowed subject types come from
    ``auth.POLICY[key]``, so a route cannot drift from the matrix silently —
    an unregistered key refuses everything (``unknown_permission``).
    """
    return Annotated[Principal | None, Depends(require_for(key))]


def _database(request: Request) -> Database:
    return request.app.state.database


DatabaseDep = Annotated[Database, Depends(_database)]


def _scheduler(database: DatabaseDep) -> SchedulerService:
    return SchedulerService(database)


SchedulerDep = Annotated[SchedulerService, Depends(_scheduler)]


def _human(database: DatabaseDep) -> HumanOpsService:
    return HumanOpsService(database)


HumanDep = Annotated[HumanOpsService, Depends(_human)]


def _allocation(database: Database) -> AllocationService:
    """§39 分配与传阅服务。按请求构造：它只带 database 和一张不可变迁移表。"""
    return AllocationService(database)


management = APIRouter(tags=["management"])
adapter = APIRouter(prefix="/adapter", tags=["adapter"])
review = APIRouter(prefix="/review", tags=["review"])
human = APIRouter(prefix="/human", tags=["human"])
allocation = APIRouter(prefix="/allocation", tags=["allocation"])
read = APIRouter(tags=["read"])


# ------------------------------------------------------------- management


@management.post("/projects")
def create_project(
    body: schemas.CreateProject,
    service: SchedulerDep,
    _principal: _guard("project:create"),
) -> dict[str, Any]:
    return service.create_project(project_id=body.project_id, name=body.name)


@management.post("/projects/{project_id}/groups")
def create_group(
    project_id: str,
    body: schemas.CreateGroup,
    service: SchedulerDep,
    _principal: _guard("group:create"),
) -> dict[str, Any]:
    return service.create_group(group_id=body.group_id, project_id=project_id, name=body.name)


@management.post("/groups/{group_id}/plans")
def create_plan(
    group_id: str,
    body: schemas.CreatePlan,
    service: SchedulerDep,
    _principal: _guard("plan:submit"),
) -> dict[str, Any]:
    outcome = service.create_plan(plan_id=body.plan_id, group_id=group_id, snapshot=body.snapshot)
    return {
        "plan_id": outcome.plan_id,
        "snapshot_id": outcome.snapshot_id,
        "plan_hash": outcome.plan_hash,
    }


@management.post("/plans/{plan_id}/approve")
def approve_plan(
    plan_id: str,
    body: schemas.PlanDecision,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("plan:approve"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.decided_by, key="plan:approve", database=database, request=request
    )
    outcome = service.approve_plan(
        plan_id=plan_id,
        decided_by=body.decided_by,
        decided_by_type=_actor_type(principal),
    )
    return {
        "plan_id": outcome.plan_id,
        "ready": list(outcome.ready),
        "waiting": list(outcome.waiting),
    }


@management.post("/plans/{plan_id}/reject")
def reject_plan(
    plan_id: str,
    body: schemas.PlanDecision,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("plan:reject"),
) -> dict[str, Any]:
    bind_declared(principal, body.decided_by, key="plan:reject", database=database, request=request)
    rejected = service.reject_plan(
        plan_id=plan_id,
        decided_by=body.decided_by,
        reason=body.reason or "",
        decided_by_type=_actor_type(principal),
    )
    return {"plan_id": plan_id, "rejected": rejected}


@management.post("/harnesses")
def register_harness(
    body: schemas.HarnessRegistrationRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """Register a Harness through the administrator face (R10 §6/§17).

    Runtime probes remain informational. Only this human-maintained registry
    authorises a Harness to claim work, and each registration version is kept
    so a task can recover the version pinned at Claim time.
    """
    bind_declared(
        principal,
        body.registered_by,
        key="harness:register",
        database=database,
        request=request,
    )
    registration = HarnessRegistration(
        harness_id=body.harness_id,
        name=body.name,
        provider=body.provider,
        adapter=body.adapter,
        adapter_version=body.adapter_version,
        capabilities=tuple(body.capabilities),
        max_parallel_tasks=body.max_parallel_tasks,
        status=body.status,
        registration_version=body.registration_version,
    )
    try:
        result = HarnessRegistry(database).register(registration, registered_by=body.registered_by)
    except RegistrationConflict as exc:
        raise ApiError("registration.conflict", 409, str(exc)) from exc
    return result


@management.get("/harnesses")
def list_harnesses(database: DatabaseDep, _principal: _guard("harness:register")):
    """The registry, plus the bases an operator declared.

    The declared bases (``adapters/cli.json``) are the ones the office
    registers windows for -- without them the office can only offer the
    handful of bases it knows by heart, and adding a new CLI would need a
    code change on the office side too.  Same route, same permission: no new
    endpoint, no new matrix row.
    """
    return {
        "harnesses": HarnessRegistry(database).listing(),
        "base_adapters": declared_base_templates(),
    }


@management.post("/groups/{group_id}/dispatch")
def formal_dispatch(
    group_id: str,
    body: schemas.FormalDispatchRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("plan:approve"),
):
    bind_declared(
        principal, body.decided_by, key="plan:approve", database=database, request=request
    )
    return dispatch(
        service,
        group_id=group_id,
        decided_by_type=_actor_type(principal),
        **body.model_dump(),
    )


# ----------------------------------------------------------------- adapter


@adapter.post("/admin/plan_draft")
def admin_plan_draft(
    body: schemas.PlanDraftRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("admin:refine"),
) -> dict[str, Any]:
    """The administrator's refined plan becomes a **draft** and a circulation round.

    This is the step the owner asked for: after the refinement, the plan is
    passed on for review (传阅) instead of being dispatched straight away.  The
    plan stays a draft, so **no task can be claimed** while the readers have
    not had their say.
    """
    bind_declared(principal, body.opened_by, key="admin:refine", database=database, request=request)
    plan_id = body.plan_id or ("plan-" + uuid.uuid4().hex[:16])
    snapshot = _plan_snapshot(body.title, body.goal, body.tasks)
    outcome = SchedulerService(database).create_plan(
        plan_id=plan_id, group_id=body.group_id, snapshot=snapshot
    )
    round_info = _allocation(database).open_circulation(
        group_id=body.group_id,
        plan_id=plan_id,
        plan_snapshot_id=outcome.snapshot_id,
        reviewer_harness_ids=list(body.reviewer_harness_ids),
        opened_by=body.opened_by,
    )
    return {
        "plan_id": plan_id,
        "snapshot_id": outcome.snapshot_id,
        "plan_hash": outcome.plan_hash,
        "circulation_id": round_info["circulation_id"],
        "round_no": round_info["round_no"],
        "state": round_info["state"],
    }


@adapter.post("/admin/plan_revise")
def admin_plan_revise(
    body: schemas.PlanRevisionRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("admin:refine"),
) -> dict[str, Any]:
    """The administrator folds the circulation comments into a **new snapshot**.

    The plan stays a draft: the human still has the final word.
    """
    bind_declared(
        principal, body.revised_by, key="admin:refine", database=database, request=request
    )
    title = _plan_title(database, body.plan_id)
    # The revision keeps the plan's **own** task ids, in order: a revision is
    # the same tasks, better specified.  A model that invents ids would
    # silently create new work, and the plan's tasks would no longer match.
    existing_ids = _plan_task_ids(database, body.plan_id)
    specs = []
    for index, spec in enumerate(body.tasks):
        item = dict(spec)
        if index < len(existing_ids):
            item["id"] = existing_ids[index]
        specs.append(item)
    snapshot = _plan_snapshot(title, body.goal, specs)
    outcome = SchedulerService(database).revise_plan(plan_id=body.plan_id, snapshot=snapshot)
    return {
        "plan_id": body.plan_id,
        "snapshot_id": outcome.snapshot_id,
        "plan_hash": outcome.plan_hash,
    }


def _plan_snapshot(title: str, goal: str, tasks: list[dict[str, object]]) -> dict[str, object]:
    """The plan snapshot the platform stores: the tasks with their windows."""
    items: list[dict[str, object]] = []
    for spec in tasks:
        task_id = str(spec.get("id") or "").strip() or ("FD-" + uuid.uuid4().hex[:16])
        targets = spec.get("assigned_harness_ids")
        if not targets:
            harness = str(spec.get("harness_id") or "").strip()
            targets = [harness] if harness else []
        acceptance = spec.get("acceptance")
        if isinstance(acceptance, str):
            acceptance = [{"id": "MVP-API-001", "check": "human", "description": acceptance}]
        elif not isinstance(acceptance, list) or not acceptance:
            acceptance = [
                {
                    "id": "MVP-API-001",
                    "check": "human",
                    "description": str(spec.get("acceptance") or "由人最终审核"),
                }
            ]
        items.append(
            {
                "id": task_id,
                "title": str(spec.get("title") or task_id),
                "goal": str(spec.get("goal") or goal),
                # The acceptance is a list of **contracts**, not a sentence: the
                # plan validator refuses anything else, and it is right to -- an
                # acceptance nobody can check is not an acceptance.
                "acceptance": acceptance,
                "capabilities": ["coding"],
                "exclusive_paths": [str(p) for p in (spec.get("exclusive_paths") or [])],
                "assigned_harness_ids": [str(t) for t in targets],
                "require_human_assign": bool(targets),
                "risk": "medium",
                "expected_duration_min": 30,
                "budget_tokens": 0,
                "verify_profile": "formal-artifact",
            }
        )
    return {
        "plan_version": "v1",
        "title": title,
        "goal": goal,
        "tasks": items,
        "registered_capabilities": ["coding"],
        "verify_profiles": ["formal-artifact"],
    }


def _plan_task_ids(database: Database, plan_id: str) -> list[str]:
    """The task ids of a plan's current snapshot, in order."""
    rows = database.read(
        "SELECT content_json FROM plan_snapshots WHERE plan_id = ?"
        " ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    )
    if not rows:
        return []
    try:
        raw = json.loads(str(rows[0]["content_json"] or "{}"))
    except ValueError:
        return []
    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        return []
    return [str(item.get("id")) for item in tasks if isinstance(item, dict) and item.get("id")]


def _plan_title(database: Database, plan_id: str) -> str:
    rows = database.read(
        "SELECT content_json FROM plan_snapshots WHERE plan_id = ?"
        " ORDER BY created_at DESC LIMIT 1",
        (plan_id,),
    )
    if not rows:
        return plan_id
    try:
        raw = json.loads(str(rows[0]["content_json"] or "{}"))
    except ValueError:
        return plan_id
    return str(raw.get("title") or plan_id)


@adapter.post("/probe")
def probe(
    body: schemas.ProbeRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("harness:probe"),
) -> dict[str, Any]:
    """R10 §7 ``probe()`` / §6 capability source.

    The self-report is recorded every time, but it does not become the effective
    capability set on its own. R10 §6: runtime self-report is for alarms and
    degradation, and may not by itself authorise a high-risk claim -- so a harness
    the human-maintained registry does not know comes back degraded with no
    effective capabilities, rather than being trusted because it said so.
    """
    bind_declared(
        principal, body.harness_id, key="harness:probe", database=database, request=request
    )
    registry = HarnessRegistry(database)
    registered = registry.current(body.harness_id)
    warnings: list[str] = []
    degraded = False
    if registered is None:
        degraded = True
        effective: list[str] = []
        warnings.append("harness is not in the human-maintained registry; self-report only")
    else:
        effective = list(capabilities_from(registered))
        missing = sorted(set(body.self_reported_capabilities) - set(effective))
        if missing:
            degraded = True
            warnings.append(f"self-reported capabilities not in the registry: {missing}")
    return registry.record_probe(
        harness_id=body.harness_id,
        reported_capabilities=list(body.self_reported_capabilities),
        effective_capabilities=effective,
        adapter_version=body.adapter_version,
        host_mode=body.host_mode,
        degraded=degraded,
        warnings=warnings,
    )


@adapter.get("/capabilities")
def capabilities(
    database: DatabaseDep,
    harness_id: Annotated[str, Query()],
    request: Request,
    principal: _guard("harness:capabilities"),
) -> dict[str, Any]:
    """R10 §7 ``capabilities()``.

    Identity is an explicit parameter because v1 has no authentication layer yet;
    the card records that rather than pretending the caller is authenticated.
    """
    bind_declared(
        principal, harness_id, key="harness:capabilities", database=database, request=request
    )
    row = HarnessRegistry(database).current(harness_id)
    if row is None:
        raise NotFound(f"unknown harness: {harness_id}")
    return {
        "harness_id": harness_id,
        "provider": row["provider"],
        "adapter": row["adapter"],
        "status": row["status"],
        "registration_version": row["registration_version"],
        "capabilities": list(capabilities_from(row)),
    }


#: How long a failure still counts as "this window cannot work right now".
#: After that the failure is history: it is reported as a warning with its age
#: (the provider may have been fixed), but it no longer decides usability --
#: unless a *new* attempt fails, which starts the clock again.
AVAILABILITY_FRESH_FAILURE_MINUTES = 30.0


@adapter.get("/availability")
def availability(
    database: DatabaseDep,
    request: Request,
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """Who can work right now, and with which model.

    The office needs this to answer a human question after a failure: "the
    upstream died -- who takes over?". Three facts, each from where it actually
    lives:

    * **the model** is a deployment fact: the CLI reads its own config, and the
      platform holds no model credentials, so it reads the same config file and
      reports what it says (never a guess -- ``None`` when unreadable);
    * **whether it can act at all**: a window without a platform credential
      cannot claim a task, so it is not a candidate;
    * **whether it worked recently**: the platform saw the last attempt fail
      (provider error, missing binary, refused submission) and can say so
      instead of leaving the person to guess.

    Read-only; it writes no state and grants nothing.
    """
    registry = HarnessRegistry(database)
    listing = registry.listing()
    probes = {
        str(row["harness_id"]): dict(row)
        for row in database.read(
            "SELECT p.* FROM capability_probes p JOIN ("
            "  SELECT harness_id, MAX(rowid) AS latest FROM capability_probes GROUP BY harness_id"
            ") latest ON latest.latest = p.rowid"
        )
    }
    attempts = {
        str(row["owner"]): dict(row)
        for row in database.read(
            "SELECT a.owner, a.task_id, a.attempt_id, a.status, a.created_at FROM task_attempts a"
            " JOIN (SELECT owner, MAX(rowid) AS latest FROM task_attempts GROUP BY owner) latest"
            "  ON latest.latest = a.rowid"
        )
    }
    failures: dict[str, str] = {}
    failure_seqs: dict[str, int] = {}
    failure_times: dict[str, str] = {}
    for row in database.read(
        "SELECT aggregate_id, payload_json, seq, created_at FROM task_events"
        " WHERE event_name IN ('abandon', 'release') ORDER BY seq DESC"
    ):
        # Newest first, and the first one wins.
        task_id = str(row["aggregate_id"])
        if task_id not in failures:
            failures[task_id] = str(row["payload_json"] or "")
            failure_seqs[task_id] = int(row["seq"])
            failure_times[task_id] = str(row["created_at"] or "")
    # A harness that delivered something *after* its last failure is working
    # again: the failure must not black-list it forever.
    successes: dict[str, int] = {}
    for row in database.read(
        "SELECT actor_id, MAX(seq) AS seq FROM task_events"
        " WHERE event_name = 'submit_result' GROUP BY actor_id"
    ):
        successes[str(row["actor_id"])] = int(row["seq"])
    entries: list[dict[str, Any]] = []
    evidence_dir = Path(database.path).parent / "formal-runs"
    for row in listing:
        harness_id = str(row["harness_id"])
        model = configured_model(harness_id)
        probe = probes.get(harness_id)
        attempt = attempts.get(harness_id)
        failure_reason = failures.get(str(attempt["task_id"])) if attempt else None
        # The failure *evidence* is the precise record (it names the error class
        # and whether the failure was retryable); the event reason is the
        # fallback for a failure recorded before the evidence existed.
        failure_detail: dict[str, Any] | None = None
        if attempt:
            try:
                failure_detail = json.loads(
                    (evidence_dir / f"{attempt['task_id']}.failure.json").read_text(
                        encoding="utf-8"
                    )
                )
            except (OSError, ValueError):
                failure_detail = None
        credential_count = int(row.get("credential_count") or 0)
        registered = bool(row.get("registration_version"))
        online = str(row.get("status")) == "online"
        degraded = bool(probe["degraded"]) if probe else False
        # ``reasons`` carries a stable code plus a human-readable detail: the
        # office localises the code, and the detail keeps the exact fact.
        reasons: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        if not registered:
            reasons.append({"code": "not_registered", "detail": "not in the registry"})
        if not online:
            reasons.append(
                {"code": "offline", "detail": f"registration status: {row.get('status')}"}
            )
        if credential_count == 0:
            reasons.append({"code": "no_credential", "detail": "no platform credential"})
        if degraded:
            reasons.append(
                {"code": "self_reported_degraded", "detail": "the window reported degraded"}
            )
        recovered = False
        if attempt:
            last_failure_seq = failure_seqs.get(str(attempt["task_id"]))
            last_success_seq = successes.get(harness_id)
            if (
                last_failure_seq is not None
                and last_success_seq is not None
                and last_success_seq > last_failure_seq
            ):
                recovered = True
        # A failure is evidence about *that moment*. If nobody retried for a
        # while (no worker was running), it stops being evidence about **now**:
        # the provider may have been fixed since. So a stale failure is reported
        # as a warning with its age, and only a fresh one blocks the window.
        failure_age_minutes: float | None = None
        if attempt:
            failed_at = failure_times.get(str(attempt["task_id"]))
            if failed_at:
                try:
                    failed = datetime.fromisoformat(failed_at)
                    if failed.tzinfo is None:
                        failed = failed.replace(tzinfo=UTC)
                    failure_age_minutes = (datetime.now(UTC) - failed).total_seconds() / 60.0
                except ValueError:
                    failure_age_minutes = None
        failure_reason_text = ""
        if failure_detail and failure_detail.get("error_type"):
            failure_reason_text = (
                f"{failure_detail.get('error_type')}: "
                f"{str(failure_detail.get('retry_reason') or failure_detail.get('error') or '')[:120]}"
            )
        elif failure_reason and "retryable failure" in failure_reason:
            failure_reason_text = failure_reason[:140]
        stale_failure = (
            failure_reason_text
            and not recovered
            and failure_age_minutes is not None
            and failure_age_minutes > AVAILABILITY_FRESH_FAILURE_MINUTES
        )
        if failure_reason_text and not recovered and not stale_failure:
            # Only a failure that points at the **harness or its provider** says
            # this window cannot work now. A platform-side failure (a Git error,
            # a locked database) is the platform's problem, not the window's.
            if failure_detail is None or failure_detail.get("retryable") is True:
                reasons.append({"code": "last_attempt_failed", "detail": failure_reason_text})
        elif stale_failure:
            warnings.append(
                {
                    "code": "stale_last_failure",
                    "detail": f"{failure_reason_text}（{int(failure_age_minutes)} 分钟前，尚未复测）",
                }
            )
        entries.append(
            {
                "harness_id": harness_id,
                "provider": row.get("provider"),
                "adapter": row.get("adapter"),
                "status": row.get("status"),
                "registration_version": row.get("registration_version"),
                "credential_count": credential_count,
                "model": model["model"],
                "model_alias": model.get("model_alias"),
                "model_source": model["source"],
                # 人给这个窗口指定的模型来源（自带 / 额度池里的哪个模型），
                # 以及**人点头时那一档**（档位会变：免费额度改成收费之后，平台不再花它）。
                "model_mode": str(row.get("model_mode") or "cli"),
                "model_id": str(row["model_id"]) if row.get("model_id") else None,
                "model_tier": str(row["model_tier"]) if row.get("model_tier") else None,
                "degraded": degraded,
                "last_probe_at": probe["created_at"] if probe else None,
                "warnings": [
                    *(
                        [
                            {"code": "probe_warning", "detail": str(item)}
                            for item in (
                                json.loads(probe["warnings_json"])
                                if probe and probe.get("warnings_json")
                                else []
                            )
                        ]
                    ),
                    *warnings,
                ],
                "last_task_id": attempt["task_id"] if attempt else None,
                "last_attempt_status": attempt["status"] if attempt else None,
                "recovered": recovered,
                "usable": not reasons,
                "reasons": reasons,
            }
        )
    return {"availability": entries}


def _pool_models() -> list[dict[str, Any]]:
    """额度池现在有哪些模型（每条带它属于哪一档）。

    池子是活的：额度会下线、配置会改，**档位也会变**（某个 free 改成 metered）。
    **读不到**就返回空清单 —— 空清单表示「查不到」，不是「一个模型都没有」，
    所以调用方不要拿它当「池子空了」来拒绝人。
    """
    from app.pool.service import PoolService  # local import: keeps the module cycle out

    try:
        return list(PoolService().models())
    except Exception:
        return []


@adapter.post("/model-selection")
def set_model_selection(
    body: schemas.ModelSelectionRequest,
    database: DatabaseDep,
    principal: _guard("harness:register"),
) -> dict[str, Any]:
    """人给某个窗口指定用哪个模型；下一轮这个窗口干活时，平台照办。

    权限复用 ``harness:register``（人可用）：这是**人**对部署的决定，harness 令牌
    改它一律 403（harness 改自己的模型 = 自己给自己批额度）。

    存之前先核对池子里有没有这个模型：存下一个池子里没有的模型，只会让界面显示
    「已保存」而下一轮悄悄回落 —— 那比当场拒绝更坏。
    """
    tier = ""
    if body.model_mode == "pool":
        models = _pool_models()
        available = {str(entry.get("id")) for entry in models}
        if available and body.model_id not in available:
            raise ApiError(
                "request.invalid",
                400,
                f"the pool has no model named {body.model_id} right now",
            )
        entry = next((item for item in models if str(item.get("id")) == body.model_id), None)
        tier = str((entry or {}).get("tier") or "")
        if tier == model_choice.PAID_TIER and not body.paid_consent:
            # 按量计费：每调用一次都花钱。人没点头就不许花 —— 这一条写在服务端，
            # 界面点不点得到都不算数。
            raise ApiError(
                "model.paid_consent_required",
                409,
                "这个模型按量计费（每调用一次都花钱），要你明确点头才用它",
            )
    try:
        choice = model_choice.save_choice(
            database,
            harness_id=body.harness_id,
            model_mode=body.model_mode,
            model_id=body.model_id,
            model_tier=tier,
        )
    except model_choice.ModelChoiceError as error:
        raise ApiError("request.invalid", 400, str(error)) from error
    return {"selection": choice.as_dict()}


@adapter.get("/list_ready_tasks")
def list_ready_tasks(
    database: DatabaseDep,
    _principal: _guard("task:list_ready"),
    visibility: Annotated[str, Query(pattern="^(authorized|redacted)$")] = "authorized",
    group_id: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """R10 §7 ``list_ready_tasks()`` -- the pull model's entry point.

    ``authorized`` answers the same ready-task listing the read API gives.
    ``redacted`` is refused loudly: R10 names the value once and §17 has not
    fixed what it hides, so answering as if something were redacted would be a
    lie. The 10a gap note (no auth layer yet) stays true for both.
    """
    if visibility != "authorized":
        raise ApiError(
            "request.invalid",
            422,
            "visibility=redacted is not implemented; R10 §17 defines redaction",
        )
    if _principal is not None:
        rows = eligible_tasks(
            database, harness_id=_principal.subject_id, group_id=group_id, limit=limit
        )
        return {"visibility": visibility, "count": len(rows), "tasks": rows}
    # changes_requested is claimable too (R10 §10 claim_for_revision, L449) --
    # a rework the pull model cannot see would stay a dead end.
    sql = "SELECT * FROM tasks WHERE status IN (?, ?)"
    params: list[Any] = [TaskStatus.READY.value, TaskStatus.CHANGES_REQUESTED.value]
    if group_id is not None:
        sql += " AND group_id = ?"
        params.append(group_id)
    sql += " ORDER BY task_id LIMIT ?"
    params.append(limit)
    rows = [dict(row) for row in database.read(sql, tuple(params))]
    return {"visibility": visibility, "count": len(rows), "tasks": rows}


@adapter.get("/context/{task_id}")
def read_context(
    task_id: str,
    database: DatabaseDep,
    service: SchedulerDep,
    _principal: _guard("task:context"),
    since_seq: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """R10 §7 ``read_context()``: the task's events from a cursor (§15).

    Context is the event log -- there is no second, private view of task state
    -- so this is the same page the read API serves, scoped to one task.
    """
    if not database.read("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)):
        raise NotFound(f"unknown task: {task_id}")
    page = _event_page(service, since_seq=since_seq, limit=200, task_id=task_id)
    task = service.get_task(task_id)
    snapshots = database.read(
        "SELECT content_json FROM plan_snapshots WHERE snapshot_id = ?", (task["plan_snapshot_id"],)
    )
    import json

    spec = (
        next(
            (x for x in json.loads(snapshots[0][0]).get("tasks", []) if x.get("id") == task_id), {}
        )
        if snapshots
        else {}
    )
    # R10 §10 ``claim_for_revision``: a rework needs the reviewer's findings, not
    # only their hash. The review row is the record of the review, so serving it
    # here is still "the event log and nothing else private" -- without it the
    # reworking harness re-runs the identical goal and the loop cannot converge.
    latest = database.read(
        "SELECT review_id, verdict, reviewer_harness_id, findings_json, completed_seq"
        " FROM reviews WHERE task_id = ? AND status = 'completed'"
        " ORDER BY completed_seq DESC, review_id DESC LIMIT 1",
        (task_id,),
    )
    review = (
        {
            "review_id": str(latest[0]["review_id"]),
            "verdict": latest[0]["verdict"],
            "reviewer_harness_id": latest[0]["reviewer_harness_id"],
            "findings": json.loads(latest[0]["findings_json"])
            if latest[0]["findings_json"]
            else [],
            "completed_seq": latest[0]["completed_seq"],
        }
        if latest
        else None
    )
    return {"task_id": task_id, "task": task, "spec": spec, "review": review, **page}


@adapter.post("/claim")
def claim(
    body: schemas.ClaimRequestModel,
    database: DatabaseDep,
    response: Response,
    request: Request,
    principal: _guard("task:claim"),
):
    bind_declared(principal, body.harness_id, key="task:claim", database=database, request=request)
    result = ClaimService(database, request.app.state.canonical_repo).claim(
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
def heartbeat(
    body: schemas.HeartbeatRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:heartbeat"),
) -> dict[str, Any]:
    bind_task_owner(
        principal, task_id=body.task_id, key="task:heartbeat", database=database, request=request
    )
    with database.transaction() as conn:
        lease_until = renew_lease(
            conn,
            task_id=body.task_id,
            attempt_id=body.attempt_id,
            lease_token=body.lease_token,
            fencing_epoch=body.fencing_epoch,
        )
        if body.progress_hash is not None:
            # The stuck sweep's fingerprint reads this (§7's progress_hash had
            # no producer before sweeper-aging-stuck); same transaction as the
            # renewal that authorised the write.
            conn.execute(
                "UPDATE tasks SET progress_hash = ? WHERE task_id = ?",
                (body.progress_hash, body.task_id),
            )
    return {
        "task_id": body.task_id,
        "attempt_id": body.attempt_id,
        "fencing_epoch": body.fencing_epoch,
        "lease_until": lease_until,
    }


@adapter.post("/submit_result")
def submit_result(
    body: schemas.SubmitResultRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:submit_result"),
) -> dict[str, Any]:
    bind_task_owner(
        principal,
        task_id=body.task_id,
        key="task:submit_result",
        database=database,
        request=request,
    )
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
    body: schemas.PublishTaskBranchRequest,
    request: Request,
    database: DatabaseDep,
    principal: _guard("task:publish_branch"),
) -> dict[str, Any]:
    """The only way an agent gitdir's objects reach canonical (R10 §13)."""
    bind_task_owner(
        principal,
        task_id=body.task_id,
        key="task:publish_branch",
        database=database,
        request=request,
    )
    bind_branch_subject(
        principal, body.branch, key="task:publish_branch", database=database, request=request
    )
    result = PublishProxy(database, request.app.state.canonical_repo).publish_task_branch(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        branch=body.branch,
        commit=body.commit,
        fencing_epoch=body.fencing_epoch,
        idempotency_key=body.idempotency_key,
    )
    return result.as_json()


@adapter.post("/request_help")
def request_help(
    body: schemas.AttemptAction,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:request_help"),
) -> dict[str, Any]:
    bind_task_owner(
        principal,
        task_id=body.task_id,
        key="task:request_help",
        database=database,
        request=request,
    )
    return service.request_help(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        reason=body.reason,
    )


@adapter.post("/ask_human")
def ask_human(
    body: schemas.AskHumanRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:ask_human"),
) -> dict[str, Any]:
    """R10 §7's 14th verb, promoted in human-ops.

    The attempt context is required like the other harness write verbs: the
    §13 owner guard is what keeps a stale harness from parking a task that has
    changed hands. §18's token rules (harness tokens get 403 on human
    decisions) landed with auth-layer: the harness face is token-checked, and
    the human face lives on /human/*.
    """
    bind_declared(
        principal, body.harness_id, key="task:ask_human", database=database, request=request
    )
    bind_task_owner(
        principal,
        task_id=body.task_id,
        key="task:ask_human",
        database=database,
        request=request,
    )
    return service.ask_human(
        task_id=body.task_id,
        question=body.question,
        options=body.options,
        actor=body.harness_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
    )


@adapter.post("/report_blocked")
def report_blocked(
    body: schemas.ReportBlockedRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:report_blocked"),
) -> dict[str, Any]:
    bind_task_owner(
        principal,
        task_id=body.task_id,
        key="task:report_blocked",
        database=database,
        request=request,
    )
    return service.report_blocked(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        blocker_ref=body.blocker_ref,
    )


@adapter.post("/accept_help")
def accept_help(
    body: schemas.HelpVoteRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:accept_help"),
) -> dict[str, Any]:
    """§7 collaborator side; not owner-scoped, so no attempt context rides along."""
    bind_declared(
        principal, body.harness_id, key="task:accept_help", database=database, request=request
    )
    return service.accept_help(
        task_id=body.task_id, help_id=body.help_id, collaborator=body.harness_id
    )


@adapter.post("/reject_help")
def reject_help(
    body: schemas.HelpVoteRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:reject_help"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="task:reject_help", database=database, request=request
    )
    return service.reject_help(
        task_id=body.task_id, help_id=body.help_id, collaborator=body.harness_id
    )


@adapter.post("/split_task")
def split_task(
    body: schemas.SplitTaskRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:split"),
) -> dict[str, Any]:
    """Owner-fenced like the other harness write verbs (the split guard is
    "human or owner initiated"; the human side of that clause has no route in
    v1 -- recorded in the adapter-verbs card)."""
    bind_declared(principal, body.harness_id, key="task:split", database=database, request=request)
    bind_task_owner(
        principal, task_id=body.task_id, key="task:split", database=database, request=request
    )
    return service.split_task(
        task_id=body.task_id,
        child_specs=body.child_specs,
        idempotency_key=body.idempotency_key,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
    )


@adapter.post("/release")
def release(
    body: schemas.AttemptAction,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:release"),
) -> dict[str, Any]:
    bind_task_owner(
        principal, task_id=body.task_id, key="task:release", database=database, request=request
    )
    return service.release(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        reason=body.reason,
    )


@adapter.post("/abandon")
def abandon(
    body: schemas.AttemptAction,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:abandon"),
) -> dict[str, Any]:
    bind_task_owner(
        principal, task_id=body.task_id, key="task:abandon", database=database, request=request
    )
    return service.abandon(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        fencing_epoch=body.fencing_epoch,
        reason=body.reason,
    )


# ------------------------------------------------------------------- human


@human.post("/resume")
def human_resume(
    body: schemas.HumanResumeRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:resume"),
) -> dict[str, Any]:
    """§18 human.resume: same attempt, same epoch, fresh development lease."""
    bind_declared(principal, body.decided_by, key="task:resume", database=database, request=request)
    return service.resume(task_id=body.task_id, decided_by=body.decided_by, answer=body.answer)


@human.post("/reopen")
def human_reopen(
    body: schemas.HumanReopenRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:reopen"),
) -> dict[str, Any]:
    bind_declared(principal, body.decided_by, key="task:reopen", database=database, request=request)
    return service.reopen(task_id=body.task_id, decided_by=body.decided_by, answer=body.answer)


@human.post("/attempt_budget_override")
def human_attempt_budget_override(
    body: schemas.HumanAttemptBudgetOverrideRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:attempt_budget_override"),
) -> dict[str, Any]:
    """Give a task whose attempt budget is spent **one more chance**.

    R10 §8 refuses the plain loop-back: the next claim would be refused by
    the same budget guard.  Only the human can say "give it one more try" --
    the platform does not grant it to itself, and neither does the
    administrator harness.

    No task state moves here (the task stays where it is); only the claim
    guard learns that one more attempt is allowed.  No new event name either:
    the decision row is the audit trail.
    """
    bind_declared(
        principal,
        body.decided_by,
        key="task:attempt_budget_override",
        database=database,
        request=request,
    )
    return _human(database).grant_attempt_budget_override(
        task_id=body.task_id,
        decided_by=body.decided_by,
        reason=body.reason,
    )


@human.post("/release")
def human_release(
    body: schemas.HumanReleaseRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:release_escalated"),
) -> dict[str, Any]:
    bind_declared(
        principal,
        body.decided_by,
        key="task:release_escalated",
        database=database,
        request=request,
    )
    return service.release(
        task_id=body.task_id,
        decided_by=body.decided_by,
        budget_override=body.budget_override,
        reason=body.reason,
    )


@human.post("/adjudicate")
def human_adjudicate(
    body: schemas.HumanAdjudicateRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:adjudicate"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.decided_by, key="task:adjudicate", database=database, request=request
    )
    return service.adjudicate(
        task_id=body.task_id,
        decided_by=body.decided_by,
        question=body.question,
        options=body.options,
    )


@human.post("/cancel")
def human_cancel(
    body: schemas.HumanCancelRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:cancel"),
) -> dict[str, Any]:
    bind_declared(principal, body.decided_by, key="task:cancel", database=database, request=request)
    return service.cancel(task_id=body.task_id, decided_by=body.decided_by, reason=body.reason)


@management.post("/tasks/{task_id}/cancel")
def admin_cancel(
    task_id: str,
    body: schemas.AdminCancelRequest,
    service: HumanDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("task:cancel"),
) -> dict[str, Any]:
    """Cancel as the administrator harness, not as a human.

    §17 gives ``task:cancel`` to the human administrator. The deployment's
    administrator is a harness subject, so this route records
    ``_actor_type(principal)`` -- a harness decision stays a harness decision.
    The edge itself is unchanged (`cancel` / `human.cancel` already exist).
    """
    bind_declared(principal, body.decided_by, key="task:cancel", database=database, request=request)
    return service.cancel(
        task_id=task_id,
        decided_by=body.decided_by,
        reason=body.reason,
        actor_type=_actor_type(principal),
    )


# ------------------------------------------------------------------ review


@review.post("/claim")
def review_claim(
    body: schemas.ReviewClaimRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("review:claim"),
) -> dict[str, Any]:
    """R10 §21 ``review.claim``: take the review lease, independent of development."""
    bind_declared(
        principal,
        body.reviewer_harness_id,
        key="review:claim",
        database=database,
        request=request,
    )
    return service.review_claim(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        reviewer_harness_id=body.reviewer_harness_id,
        review_id=body.review_id,
    )


@review.post("/submit")
def review_submit(
    body: schemas.ReviewSubmitRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("review:submit"),
) -> dict[str, Any]:
    bind_declared(
        principal,
        body.reviewer_harness_id,
        key="review:submit",
        database=database,
        request=request,
    )
    return service.submit_review(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        reviewer_harness_id=body.reviewer_harness_id,
        verdict=body.verdict,
        reviewed_commit=body.reviewed_commit,
        review_revision=body.review_revision,
        findings=body.findings,
        review_id=body.review_id,
    )


@human.post("/review/claim")
def human_review_claim(
    body: schemas.HumanReviewClaimRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("review:human_claim"),
) -> dict[str, Any]:
    bind_declared(
        principal,
        body.decided_by,
        key="review:human_claim",
        database=database,
        request=request,
    )
    return service.review_claim(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        reviewer_harness_id=body.decided_by,
        review_id=body.review_id,
        reviewer_kind="human",
    )


@human.post("/review/submit")
def human_review_submit(
    body: schemas.HumanReviewSubmitRequest,
    service: SchedulerDep,
    database: DatabaseDep,
    request: Request,
    principal: _guard("review:human_submit"),
) -> dict[str, Any]:
    bind_declared(
        principal,
        body.decided_by,
        key="review:human_submit",
        database=database,
        request=request,
    )
    return service.submit_review(
        task_id=body.task_id,
        attempt_id=body.attempt_id,
        reviewer_harness_id=body.decided_by,
        verdict=body.verdict,
        reviewed_commit=body.reviewed_commit,
        review_revision=body.review_revision,
        findings=body.findings,
        review_id=body.review_id,
        reviewer_kind="human",
        require_lease=True,
    )


# --------------------------------------------------- allocation 分配与传阅


@allocation.post("/circulation/open")
def open_circulation(
    body: schemas.CirculationOpenRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("circulation:start"),
) -> dict[str, Any]:
    """R10 §39 ``circulation.opened``: publish the plan draft and this round's reviewers.

    This is the step the owner asked for in plain language: the admin finishes a plan,
    then it is passed to *other* harnesses to review before anyone is assigned work.
    """
    bind_declared(
        principal, body.opened_by, key="circulation:start", database=database, request=request
    )
    return _allocation(database).open_circulation(
        group_id=body.group_id,
        plan_id=body.plan_id,
        plan_snapshot_id=body.plan_snapshot_id,
        reviewer_harness_ids=body.reviewer_harness_ids,
        opened_by=body.opened_by,
        deadline_at=body.deadline_at,
    )


@allocation.get("/circulation")
def list_circulation_rounds(
    database: DatabaseDep,
    group_id: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """The circulation rounds of a group (and their comments).

    The write side existed; the read side did not, so the administrator (and
    the member that has to review) had no way to ask "is there a round waiting
    for me?".
    """
    sql = "SELECT * FROM circulation_rounds"
    clauses: list[str] = []
    params: list[object] = []
    if group_id:
        clauses.append("group_id = ?")
        params.append(group_id)
    if state:
        clauses.append("state = ?")
        params.append(state)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY opened_at DESC"
    rounds = []
    for row in database.read(sql, tuple(params)):
        reviews = database.read(
            "SELECT harness_id, body, submitted_at FROM circulation_reviews"
            " WHERE circulation_id = ? AND round_no = ? ORDER BY submitted_at",
            (str(row["circulation_id"]), int(row["round_no"])),
        )
        rounds.append(
            {
                "circulation_id": str(row["circulation_id"]),
                "group_id": str(row["group_id"]),
                "plan_id": str(row["plan_id"]),
                "plan_snapshot_id": str(row["plan_snapshot_id"]),
                "round_no": int(row["round_no"]),
                "state": str(row["state"]),
                "opened_by": str(row["opened_by"] or ""),
                "reviewer_harness_ids": json.loads(str(row["reviewer_harness_ids_json"] or "[]")),
                "reviews": [
                    {
                        "harness_id": str(review["harness_id"]),
                        "body": str(review["body"] or ""),
                        "submitted_at": str(review["submitted_at"] or ""),
                    }
                    for review in reviews
                ],
            }
        )
    return {"rounds": rounds}


@allocation.get("/circulation/{circulation_id}")
def read_circulation_round(
    circulation_id: str,
    database: DatabaseDep,
) -> dict[str, Any]:
    """One circulation round with the comments it collected."""
    rows = database.read(
        "SELECT * FROM circulation_rounds WHERE circulation_id = ?", (circulation_id,)
    )
    if not rows:
        raise NotFound(f"unknown circulation round: {circulation_id}")
    row = rows[0]
    reviews = database.read(
        "SELECT harness_id, body, submitted_at FROM circulation_reviews"
        " WHERE circulation_id = ? AND round_no = ? ORDER BY submitted_at",
        (circulation_id, int(row["round_no"])),
    )
    return {
        "circulation_id": circulation_id,
        "group_id": str(row["group_id"]),
        "plan_id": str(row["plan_id"]),
        "plan_snapshot_id": str(row["plan_snapshot_id"]),
        "round_no": int(row["round_no"]),
        "state": str(row["state"]),
        "opened_by": str(row["opened_by"] or ""),
        "reviewer_harness_ids": json.loads(str(row["reviewer_harness_ids_json"] or "[]")),
        "reviews": [
            {
                "harness_id": str(review["harness_id"]),
                "body": str(review["body"] or ""),
                "submitted_at": str(review["submitted_at"] or ""),
            }
            for review in reviews
        ],
    }


@allocation.post("/circulation/review")
def submit_circulation_review(
    body: schemas.CirculationReviewRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("circulation:review"),
) -> dict[str, Any]:
    """R10 §39 ``circulation.review_submitted``: a named reviewer gives this round's opinion."""
    bind_declared(
        principal, body.harness_id, key="circulation:review", database=database, request=request
    )
    return _allocation(database).submit_circulation_review(
        circulation_id=body.circulation_id,
        harness_id=body.harness_id,
        body=body.body,
        material_hash=body.material_hash,
    )


@allocation.post("/circulation/close")
def close_circulation(
    body: schemas.CirculationRoundRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("circulation:close"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="circulation:close", database=database, request=request
    )
    return _allocation(database).close_circulation_round(
        circulation_id=body.circulation_id, closed_by=body.harness_id
    )


@allocation.post("/circulation/revise")
def revise_circulation(
    body: schemas.CirculationRoundRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("circulation:revise"),
) -> dict[str, Any]:
    """R10 §39 ``circulation.revised``: fold this round's opinions in, open the next round."""
    bind_declared(
        principal, body.harness_id, key="circulation:revise", database=database, request=request
    )
    return _allocation(database).revise_circulation(
        circulation_id=body.circulation_id,
        revised_by=body.harness_id,
        reviewer_harness_ids=body.reviewer_harness_ids,
    )


@allocation.post("/circulation/complete")
def complete_circulation(
    body: schemas.CirculationRoundRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("circulation:complete"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="circulation:complete", database=database, request=request
    )
    return _allocation(database).complete_circulation(
        circulation_id=body.circulation_id, completed_by=body.harness_id
    )


@allocation.post("/circulation/cancel")
def cancel_circulation(
    body: schemas.CirculationRoundRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("circulation:complete"),
) -> dict[str, Any]:
    """Void the plan: in-flight circulation and unexecuted allocations lapse together."""
    bind_declared(
        principal, body.harness_id, key="circulation:complete", database=database, request=request
    )
    return _allocation(database).cancel_circulation(
        circulation_id=body.circulation_id, cancelled_by=body.harness_id, reason=body.reason
    )


@allocation.post("/round/open")
def open_allocation_round(
    body: schemas.AllocationOpenRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:open"),
) -> dict[str, Any]:
    """Open an allocation round. §39 硬规则 1 (hard gate): the snapshot must have a
    completed circulation round first -- an unreviewed plan is refused, not warned."""
    bind_declared(
        principal, body.opened_by, key="allocation:open", database=database, request=request
    )
    return _allocation(database).open_allocation_round(
        group_id=body.group_id,
        plan_snapshot_id=body.plan_snapshot_id,
        opened_by=body.opened_by,
        deadline_at=body.deadline_at,
    )


@allocation.post("/round/close")
def close_allocation_round(
    body: schemas.AllocationRoundRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:close"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="allocation:close", database=database, request=request
    )
    return _allocation(database).close_allocation_round(
        round_id=body.round_id, closed_by=body.harness_id
    )


@allocation.post("/application")
def submit_application(
    body: schemas.ApplicationSubmitRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:submit"),
) -> dict[str, Any]:
    """R10 §39 ``application.submitted``: "I want these tasks", plus one material version.

    §39 硬规则 2: this creates no owner, attempt, lease or worktree. Applying is not claiming.
    """
    bind_declared(
        principal, body.harness_id, key="allocation:submit", database=database, request=request
    )
    return _allocation(database).submit_application(
        round_id=body.round_id,
        harness_id=body.harness_id,
        task_ids=body.task_ids,
        reason=body.reason,
        body=body.body,
        instance_id=body.instance_id,
    )


@allocation.post("/application/update")
def update_application(
    body: schemas.ApplicationUpdateRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:submit"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="allocation:submit", database=database, request=request
    )
    return _allocation(database).update_application(
        application_id=body.application_id,
        harness_id=body.harness_id,
        expected_revision=body.expected_revision,
        reason=body.reason,
        body=body.body,
        task_ids=body.task_ids,
    )


@allocation.post("/application/withdraw")
def withdraw_application(
    body: schemas.ApplicationWithdrawRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:submit"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="allocation:submit", database=database, request=request
    )
    return _allocation(database).withdraw_application(
        application_id=body.application_id, harness_id=body.harness_id
    )


@allocation.post("/material/request")
def request_material(
    body: schemas.AllocationMaterialRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:material"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.requested_by, key="allocation:material", database=database, request=request
    )
    return _allocation(database).request_material(
        round_id=body.round_id,
        task_id=body.task_id,
        requested_by=body.requested_by,
        reason=body.reason,
    )


@allocation.post("/decision")
def decide_allocation(
    body: schemas.AllocationDecisionRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:decide"),
) -> dict[str, Any]:
    """R10 §39 ``allocation.decision_made``: pick a candidate for one task.

    §39 硬规则 3 (回避) lives in the service: a decider who competes for this task is
    refused, and the refusal says to hand it to the group owner -- not to pick someone else.
    """
    bind_declared(
        principal, body.decided_by, key="allocation:decide", database=database, request=request
    )
    return _allocation(database).decide(
        round_id=body.round_id,
        task_id=body.task_id,
        application_id=body.application_id,
        decided_by=body.decided_by,
        expected_revision=body.expected_revision,
        reason=body.reason,
        compared_harness_ids=body.compared_harness_ids,
    )


@allocation.post("/decision/defer")
def defer_allocation(
    body: schemas.AllocationDeferRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:decide"),
) -> dict[str, Any]:
    """R10 §39 ``allocation.decision_deferred``: decide *not* to assign yet, and say why."""
    bind_declared(
        principal, body.decided_by, key="allocation:decide", database=database, request=request
    )
    return _allocation(database).defer_decision(
        round_id=body.round_id,
        task_id=body.task_id,
        decided_by=body.decided_by,
        reason=body.reason,
    )


@allocation.post("/invite")
def invite_assignment(
    body: schemas.AssignmentInviteRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("allocation:decide"),
) -> dict[str, Any]:
    """R10 §39 ``assignment.invited``: hand an (unapplied-for) task to a named harness."""
    bind_declared(
        principal, body.invited_by, key="allocation:decide", database=database, request=request
    )
    return _allocation(database).invite(
        round_id=body.round_id,
        task_id=body.task_id,
        harness_id=body.harness_id,
        invited_by=body.invited_by,
        reason=body.reason,
    )


@allocation.post("/accept")
def accept_assignment(
    body: schemas.AssignmentResolveRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("assignment:accept"),
) -> dict[str, Any]:
    """R10 §39 ``assignment.accepted``: the *selected harness itself* confirms it takes the task."""
    bind_declared(
        principal, body.harness_id, key="assignment:accept", database=database, request=request
    )
    return _allocation(database).accept_assignment(
        allocation_id=body.allocation_id, harness_id=body.harness_id
    )


@allocation.post("/decline")
def decline_assignment(
    body: schemas.AssignmentResolveRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("assignment:decline"),
) -> dict[str, Any]:
    bind_declared(
        principal, body.harness_id, key="assignment:decline", database=database, request=request
    )
    return _allocation(database).decline_assignment(
        allocation_id=body.allocation_id, harness_id=body.harness_id
    )


@allocation.post("/revoke")
def revoke_assignment(
    body: schemas.AssignmentRevokeRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("assignment:revoke"),
) -> dict[str, Any]:
    """R10 §39 ``assignment.revoked``: §17 gives the owner (and the delegated admin) this
    control -- but only over an assignment nobody has claimed yet."""
    bind_declared(
        principal, body.revoked_by, key="assignment:revoke", database=database, request=request
    )
    return _allocation(database).revoke_assignment(
        allocation_id=body.allocation_id,
        expected_revision=body.expected_revision,
        revoked_by=body.revoked_by,
        reason=body.reason,
    )


@allocation.get("/authorization")
def read_claim_authorization(
    database: DatabaseDep,
    request: Request,
    task_id: str,
    harness_id: str,
    expected_revision: int | None = None,
    principal: _guard("claim:authorized") = None,  # noqa: RUF013 - FastAPI Depends 占位
) -> dict[str, Any]:
    """R10 §39 硬规则 2 / §17 ``claim:authorized``: is there an authorization to claim this?

    ``{"authorized": false}`` is a 200, not a 403: "you have no authorization" is an answer,
    and the caller (adapter/UI) decides how to phrase it. A *stale* ``expected_revision``
    is refused, because that is "working from an outdated authorization".
    """
    bind_declared(principal, harness_id, key="claim:authorized", database=database, request=request)
    authorization = _allocation(database).claim_authorization(
        task_id=task_id, harness_id=harness_id, expected_revision=expected_revision
    )
    return {"authorized": authorization is not None, "allocation": authorization}


@read.get("/allocation/candidates")
def list_allocation_candidates(
    database: DatabaseDep, round_id: str, task_id: str
) -> dict[str, Any]:
    """Read the frozen candidate set for one task (the decision screen's input).

    Read-only and deliberately open like the other ``/read`` endpoints: it shows what the
    admin already published, and the decision itself still needs ``allocation:decide``.
    """
    return {"candidates": _allocation(database).list_candidates(round_id=round_id, task_id=task_id)}


# --------------------------------------------------------------- admin harness


@adapter.post("/admin/refine")
def admin_refine(
    body: schemas.AdminRefineRequest,
    database: DatabaseDep,
    request: Request,
    principal: _guard("admin:refine"),
) -> dict[str, Any]:
    """Turn the group owner's natural-language requirement into task specs.

    The refinement itself is the admin harness's own model call; this route only
    carries it and leaves the exchange in the group conversation. It deliberately
    does NOT dispatch: writing task state is ``plan:approve``, a separate
    decision with its own permission key.

    Responds with ``status: "ok"`` (validated specs) or ``status: "needs_owner"``
    (the admin asked the owner instead of guessing). The second is a normal 200:
    refusing to guess is the required behaviour, not a server error.
    """
    admin_id = _admin_subject(principal)
    if HarnessRegistry(database).current(admin_id) is None:
        raise ApiError(
            "admin.not_registered",
            409,
            f"administrator harness {admin_id!r} is not in the registry;"
            " register it before it can refine requirements",
        )
    if not database.read("SELECT 1 FROM groups WHERE group_id = ?", (body.group_id,)):
        raise ApiError("group.not_found", 404, "unknown group")
    return AdminService(database, harness_id=admin_id).refine(
        requirement=body.requirement,
        group_id=body.group_id,
        max_tasks=body.max_tasks,
        answers=body.answers,
    )


def _actor_type(principal: Principal | None) -> str:
    """The real subject type of whoever made this decision.

    ``approve_plan`` / ``reject_plan`` used to hardcode ``actor_type="human"``
    in the audit event while writing whatever id the caller declared. Once the
    administrator is a harness, that records "a human approved this plan" for a
    decision a harness made -- the same conflation this round is unwinding.
    With auth off there is no principal, and the caller is then the human
    operator, which is the honest default.
    """
    return principal.subject_type if principal is not None else "human"


def _viewer_subject(request: Request, database: DatabaseDep) -> str | None:
    """这次读取的是谁。

    定向消息（R10 §19）只能给发送方与收件方看，所以读路径必须知道谁在看。
    认证关不开时没有主体可识别——那就看不到定向消息（失败关闭）。
    """
    if settings.auth_mode != "enforce":
        return None
    try:
        principal = TokenService(database).verify(bearer_token(request))
    except TokenInvalid:
        return None
    return principal.subject_id


def _admin_subject(principal: Principal | None) -> str:
    """Who the refinement is attributed to.

    Under ``AP_AUTH_MODE=enforce`` the guard has already proven the caller is a
    harness whose id is in ``AP_ADMIN_SUBJECTS``, so the token decides. With auth
    off there is no principal to read; the first configured admin subject is the
    only defensible stand-in, and an unconfigured deployment is refused rather
    than attributed to a made-up identity.
    """
    if principal is not None:
        return principal.subject_id
    configured = sorted(settings.admin_subjects)
    if not configured:
        raise ApiError(
            "admin.not_configured",
            503,
            "no administrator harness configured: set AP_ADMIN_SUBJECTS",
        )
    return configured[0]


# -------------------------------------------------------------------- read


@read.get("/auth/whoami")
def auth_whoami(request: Request, database: DatabaseDep) -> dict[str, Any]:
    """Read-only self-introspection for the console (frontend-token card).

    Deliberately never refuses and never audits: it answers "who would I be if
    I presented this token", so a browser probe on load must not manufacture
    denial noise. ``token_reason`` carries the precise state for the badge.
    """
    if settings.auth_mode != "enforce":
        return {"mode": settings.auth_mode, "subject": None, "token_reason": None}
    try:
        principal = TokenService(database).verify(bearer_token(request))
    except TokenInvalid as exc:
        return {"mode": settings.auth_mode, "subject": None, "token_reason": exc.reason}
    return {
        "mode": settings.auth_mode,
        "subject": {"type": principal.subject_type, "id": principal.subject_id},
        "token_reason": None,
    }


@read.get("/projects")
def list_projects(service: SchedulerDep) -> dict[str, Any]:
    """Read side of the management pair (frontend-thread): the rail's top level."""
    return {"projects": service.list_projects()}


@read.get("/groups")
def list_groups(
    service: SchedulerDep,
    project_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    return {"groups": service.list_groups(project_id=project_id)}


@read.get("/tasks/{task_id}")
def get_task(task_id: str, service: SchedulerDep) -> dict[str, Any]:
    return service.get_task(task_id)


@read.get("/tasks/{task_id}/verification")
def task_verification(task_id: str, service: SchedulerDep, database: DatabaseDep):
    task = service.get_task(task_id)
    rows = database.read(
        "SELECT receipt_json FROM verification_receipts WHERE task_id = ? "
        "AND attempt_id = ? ORDER BY rowid DESC LIMIT 1",
        (task_id, task["current_attempt_id"]),
    )
    if not rows:
        return {"verification": None}
    import json

    receipt = json.loads(rows[0][0])
    fields = (
        "receipt_id",
        "receipt_hash",
        "status",
        "base_commit",
        "commit",
        "baseline_exit_code",
        "head_exit_code",
        "failure_reason",
        "verify_profile",
    )
    return {"verification": {key: receipt.get(key) for key in fields}}


@read.get("/groups/{group_id}/tasks")
def list_tasks(
    group_id: str,
    service: SchedulerDep,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=1000)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """The group's task list.

    Paging is optional and **off by default** (no ``limit`` → the whole list,
    as before).  A caller that only needs a window asks for one: the assistant
    does — it used to pull every task's every field into a single observation,
    and the model only saw the first half of it.
    """
    tasks = service.list_tasks(group_id, status=status)
    total = len(tasks)
    window = tasks[offset:] if limit is None else tasks[offset : offset + limit]
    # The counts of the **whole** list, not of the window: a caller that pages
    # through it still gets the distribution of the list it is paging.
    by_status: dict[str, int] = {}
    unclaimed_total = 0
    for task in tasks:
        state = str(task.get("status") or "unknown")
        by_status[state] = by_status.get(state, 0) + 1
        if not task.get("owner"):
            unclaimed_total += 1
    return {
        "tasks": window,
        "total": total,
        "by_status": by_status,
        "unclaimed_total": unclaimed_total,
        "offset": offset,
        "has_more": offset + len(window) < total,
    }


@read.get("/groups/{group_id}/plans")
def list_group_plans(
    group_id: str,
    database: DatabaseDep,
) -> dict[str, Any]:
    """The plans of a group, newest first, with the document of the draft one.

    The office shows the *plan under review* so the human can read it and give
    the final word.
    """
    rows = database.read(
        "SELECT plan_id, status, plan_hash, created_at FROM plans WHERE group_id = ?"
        " ORDER BY created_at DESC",
        (group_id,),
    )
    plans = []
    for row in rows:
        entry = {
            "plan_id": str(row["plan_id"]),
            "status": str(row["status"]),
            "plan_hash": str(row["plan_hash"] or ""),
            "created_at": str(row["created_at"] or ""),
        }
        plans.append(entry)
    draft = next((plan for plan in plans if plan["status"] == "draft"), None)
    if draft is not None:
        draft["document"] = build_document(database, plan_id=draft["plan_id"])
    return {"plans": plans, "draft": draft}


@read.get("/plans/{plan_id}/document")
def plan_document(
    plan_id: str,
    database: DatabaseDep,
    project_name: Annotated[str | None, Query()] = None,
    goal: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """The final plan as a document a human can read (Markdown + HTML).

    The plan is rendered together with the circulation rounds it went through,
    so the reader sees *who said what* while the plan was being shaped -- and
    the task list it will produce.
    """
    document = build_document(
        database,
        plan_id=plan_id,
        project_name=project_name or "",
        goal=goal or "",
    )
    return document


@read.get("/tasks/{task_id}/output")
def task_output(
    task_id: str,
    service: SchedulerDep,
    database: DatabaseDep,
    attempt_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """The harness CLI's own output for the current attempt, as it streams.

    The member worker tees the child's stdout/stderr into the data dir while
    the CLI runs; this route hands out the tail. It is the same fact the
    failure evidence keeps, only live -- so the office can show what a harness
    is saying right now instead of only what it did.

    Read-only, and honest about absence: no attempt, no file, or no bytes yet
    all come back as an empty output rather than as an error.
    """
    task = service.get_task(task_id)
    # 指定了 attempt 就给那一次（"**这次**失败的真实日志"要看的是失败的那一轮，
    # 不是正在重跑的那一轮）；没指定才按"当前 -> 最近一次"回退。
    if attempt_id:
        attempt_id = str(attempt_id)
        current = attempt_id == str(task.get("current_attempt_id") or "")
        known = database.read(
            "SELECT attempt_id FROM task_attempts WHERE attempt_id = ? AND task_id = ?",
            (attempt_id, task_id),
        )
        if not known:
            return {"output": None}
    else:
        attempt_id = task.get("current_attempt_id")
        current = attempt_id is not None
        if not attempt_id:
            # 没有当前 attempt（abandoned / merged / cancelled……）：给人看**最近一次**
            # attempt 的 transcript（"这个 harness 刚才到底说了什么"）。
            rows = database.read(
                "SELECT attempt_id FROM task_attempts WHERE task_id = ?"
                " ORDER BY attempt_no DESC LIMIT 1",
                (task_id,),
            )
            if not rows:
                return {"output": None}
            attempt_id = str(rows[0]["attempt_id"])
    stdout_path, stderr_path = cli_output.log_paths(settings.data_dir, task_id, str(attempt_id))
    stdout = cli_output.read_tail(stdout_path)
    stderr = cli_output.read_tail(stderr_path)
    attempt = database.read(
        "SELECT status, owner, head_commit FROM task_attempts WHERE attempt_id = ?",
        (str(attempt_id),),
    )
    attempt_row = dict(attempt[0]) if attempt else {}
    return {
        "output": {
            "task_id": task_id,
            "attempt_id": str(attempt_id),
            "current": current,
            "attempt_status": attempt_row.get("status"),
            "harness_id": attempt_row.get("owner") or task.get("owner"),
            "stdout": stdout["text"],
            "stderr": stderr["text"],
            "stdout_bytes": stdout["bytes"],
            "stderr_bytes": stderr["bytes"],
            "stdout_lines": stdout["lines"],
            "updated_at": stdout["updated_at"] or stderr["updated_at"],
        }
    }


@read.get("/harnesses/bases")
def harness_bases() -> dict[str, Any]:
    """The bases a CLI can answer for, and what each one still lacks.

    For "how do I add a new harness here": the built-in bases need no
    declaration, and every declared one comes with its template plus the
    gaps that keep it from being usable (no read-only review, no pool slot, no
    declared tier).

    A declaration is **trusted input** (it names a binary and its argv) --
    operator-owned, never written by the platform.  This face only reads it and
    reports what is missing; the draft of a new declaration is the human's to
    write and install.
    """
    from app.adapter.formal_worker import ADAPTER_TYPES
    from app.adapter.harnesses.generic import (
        base_gaps,
        base_template,
        load_declarations,
    )
    from app.pool.sources import declared_cli_slots

    slots = {
        str(slot.get("name") or ""): slot
        for slot in declared_cli_slots()
        if str(slot.get("name") or "")
    }
    bases: list[dict[str, Any]] = []
    error: str | None = None
    try:
        declarations = load_declarations()
    except ValueError as exc:
        # A declaration file that cannot be read is an error the human must see
        # (the platform refuses to start on it); it is not "no declarations".
        declarations = {}
        error = str(exc)
    for base in sorted(declarations):
        declaration = declarations[base]
        template = base_template(declaration)
        bases.append(
            {
                **template,
                "source": "declared",
                "declaration": {
                    "binary": declaration.binary,
                    "args": list(declaration.args),
                    "prompt_via_stdin": declaration.prompt_via_stdin,
                    "pool_protocol": declaration.pool_protocol,
                    "review_args": list(declaration.review_args),
                    "model": declaration.model,
                    "capabilities": list(declaration.capabilities),
                },
                "gaps": base_gaps(declaration, pool_slot=slots.get(base)),
            }
        )
    return {
        "bases": bases,
        "builtin_bases": sorted(ADAPTER_TYPES),
        "declaration_error": error,
    }


#: How much of a transcript the forensics face hands out.  Deeper than the
#: office's live tail (32KB / 200 lines) and still bounded -- a 5 MB transcript
#: does not belong in a model context.
FORENSICS_MAX_BYTES = 256 * 1024
FORENSICS_MAX_LINES = 2000
FORENSICS_HARD_MAX_BYTES = 1024 * 1024
FORENSICS_HARD_MAX_LINES = 20000


@read.get("/tasks/{task_id}/forensics")
def task_forensics(
    task_id: str,
    service: SchedulerDep,
    database: DatabaseDep,
    attempt_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """The deeper evidence an assistant needs to say *where* it got stuck.

    More of the CLI's own output than the live tail, the stderr side as well,
    and the **provenance** of every byte: which task, which attempt, which log
    file, how many bytes, how many lines, when it was last written.

    Lines that carry a credential-looking value are masked **here**, so the
    raw value never reaches the reader; the count of masked lines travels with
    the text.
    """
    task = service.get_task(task_id)
    if attempt_id:
        attempt_id = str(attempt_id)
        current = attempt_id == str(task.get("current_attempt_id") or "")
        known = database.read(
            "SELECT attempt_id FROM task_attempts WHERE attempt_id = ? AND task_id = ?",
            (attempt_id, task_id),
        )
        if not known:
            return {"forensics": None}
    else:
        attempt_id = task.get("current_attempt_id")
        current = attempt_id is not None
        if not attempt_id:
            rows = database.read(
                "SELECT attempt_id FROM task_attempts WHERE task_id = ?"
                " ORDER BY attempt_no DESC LIMIT 1",
                (task_id,),
            )
            if not rows:
                return {"forensics": None}
            attempt_id = str(rows[0]["attempt_id"])
    stdout_path, stderr_path = cli_output.log_paths(settings.data_dir, task_id, str(attempt_id))
    stdout = cli_output.read_tail(
        stdout_path, max_bytes=FORENSICS_MAX_BYTES, max_lines=FORENSICS_MAX_LINES
    )
    stderr = cli_output.read_tail(
        stderr_path, max_bytes=FORENSICS_MAX_BYTES, max_lines=FORENSICS_MAX_LINES
    )
    stdout_text, stdout_masked = cli_output.redact(stdout["text"])
    stderr_text, stderr_masked = cli_output.redact(stderr["text"])
    attempt = database.read(
        "SELECT status, owner, head_commit, attempt_no FROM task_attempts WHERE attempt_id = ?",
        (str(attempt_id),),
    )
    attempt_row = dict(attempt[0]) if attempt else {}
    return {
        "forensics": {
            "task_id": task_id,
            "attempt_id": str(attempt_id),
            "attempt_no": attempt_row.get("attempt_no"),
            "current": current,
            "attempt_status": attempt_row.get("status"),
            "harness_id": attempt_row.get("owner") or task.get("owner"),
            "stdout": {
                "text": stdout_text,
                "bytes": stdout["bytes"],
                "read_lines": stdout["lines"],
                "returned_lines": len(stdout_text.splitlines()),
                "redacted_lines": stdout_masked,
            },
            "stderr": {
                "text": stderr_text,
                "bytes": stderr["bytes"],
                "read_lines": stderr["lines"],
                "returned_lines": len(stderr_text.splitlines()),
                "redacted_lines": stderr_masked,
            },
            "source": {
                "stdout_log": str(stdout_path.relative_to(settings.data_dir))
                if stdout_path.is_relative_to(settings.data_dir)
                else stdout_path.name,
                "stderr_log": str(stderr_path.relative_to(settings.data_dir))
                if stderr_path.is_relative_to(settings.data_dir)
                else stderr_path.name,
                "stdout_updated_at": stdout["updated_at"],
                "stderr_updated_at": stderr["updated_at"],
            },
            "bounds": {
                "max_bytes": FORENSICS_MAX_BYTES,
                "max_lines": FORENSICS_MAX_LINES,
                "hard_max_bytes": FORENSICS_HARD_MAX_BYTES,
                "hard_max_lines": FORENSICS_HARD_MAX_LINES,
            },
        }
    }


@read.get("/assistant/runs")
def assistant_runs() -> dict[str, Any]:
    """助手最近几轮做了什么（只读）。

    The engine writes every step to a local trace file; this face reads those
    traces into a **summary** the office can show -- what the assistant thought,
    which tool it called, what it saw.  The raw material (a 256KB forensics
    dump, say) stays on the server: the summary says how big it was and how it
    began, not the whole thing.
    """
    from app.assistant.runs import read_runs

    return {"runs": read_runs(settings.data_dir)}


@read.get("/tasks/{task_id}/events")
def task_events(
    task_id: str,
    service: SchedulerDep,
    since_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> dict[str, Any]:
    return _event_page(service, since_seq=since_seq, limit=limit, task_id=task_id)


@read.get("/tasks/{task_id}/diff")
def task_diff(
    task_id: str,
    request: Request,
    service: SchedulerDep,
    attempt_id: Annotated[str | None, Query()] = None,
    include_patch: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """The task thread's diff reading (diff-message card; Buzz kind:40008)."""
    return service.diff_for_task(
        task_id,
        attempt_id=attempt_id,
        include_patch=include_patch,
        repo=request.app.state.canonical_repo,
    )


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
        direct=body.direct,
    )


@read.get("/groups/{group_id}/messages")
def list_messages(
    group_id: str,
    service: SchedulerDep,
    request: Request,
    database: DatabaseDep,
    since_seq: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    # 观看人决定定向消息能否看到：读路径按身份过滤（R10 §19）。
    viewer = _viewer_subject(request, database)
    rows = service.list_messages(group_id, since_seq=since_seq, limit=limit + 1, viewer=viewer)
    has_more = len(rows) > limit
    rows = rows[:limit]
    last = int(rows[-1]["created_seq"]) if rows else since_seq
    return {"messages": rows, "next_seq": last, "has_more": has_more}
