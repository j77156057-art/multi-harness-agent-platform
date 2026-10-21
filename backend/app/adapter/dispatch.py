"""Human dispatch using existing immutable plans and approval transitions."""

import json

from app.claim.service import ClaimService
from app.spec import EventKind, TaskStatus
from app.tasks.service import Conflict, InvalidInput, SchedulerService, _canonical, _hash


def dispatch(
    service: SchedulerService,
    *,
    group_id: str,
    request_id: str,
    title: str,
    goal: str,
    harness_id: str,
    decided_by: str,
    exclusive_paths: list[str],
    acceptance: str,
    decided_by_type: str = "human",
) -> dict:
    payload = {
        "group_id": group_id,
        "title": title,
        "goal": goal,
        "harness_id": harness_id,
        "decided_by": decided_by,
        "exclusive_paths": exclusive_paths,
        "acceptance": acceptance,
    }
    digest = _hash(payload)
    with service.database.transaction() as conn:
        prior = conn.execute(
            "SELECT * FROM command_idempotency WHERE operation = 'formal_dispatch' AND idempotency_key = ?",
            (request_id,),
        ).fetchone()
        if prior:
            if prior["payload_hash"] != digest:
                raise Conflict("dispatch request id reused for different content")
            result = json.loads(prior["first_result_json"])
            return {**result, "replayed": True}
        if not conn.execute("SELECT 1 FROM groups WHERE group_id = ?", (group_id,)).fetchone():
            raise InvalidInput("unknown dispatch group")
        member = conn.execute(
            "SELECT * FROM harnesses WHERE harness_id = ?", (harness_id,)
        ).fetchone()
        if not member or not member["registration_version"] or member["status"] != "online":
            raise InvalidInput("target Harness is not registered online")
        # 登记在册 ≠ 能领活：没有平台凭据的窗口领不了这条任务，派出去它就永远
        # 停在 ready。这里**拒绝**而不是事后提醒：一条永远没人能领的活，比一条
        # 明确报错的派单坏得多。
        credentials = conn.execute(
            "SELECT COUNT(*) AS n FROM auth_tokens WHERE subject_type = 'harness'"
            " AND subject_id = ? AND revoked_at IS NULL",
            (harness_id,),
        ).fetchone()
        if not credentials or int(credentials["n"]) == 0:
            raise InvalidInput(
                "target Harness holds no platform credential, so it could never"
                " claim this task"
            )
        if not exclusive_paths or any(
            p.startswith(("/", ".")) or ":" in p or "\\" in p or ".." in p.split("/")
            for p in exclusive_paths
        ):
            raise InvalidInput("exclusive paths must be normalized relative paths")
        task_id = "FD-" + _hash(request_id).split(":")[1][:16]
        plan_id = "plan-" + task_id
        spec = {
            "id": task_id,
            "title": title,
            "goal": goal,
            "assigned_harness_ids": [harness_id],
            "require_human_assign": True,
            "capabilities": ["coding"],
            "exclusive_paths": exclusive_paths,
            "acceptance": [{"id": "MVP-API-001", "check": "human", "description": acceptance}],
            "risk": "medium",
            "expected_duration_min": 30,
            "budget_tokens": 0,
            "verify_profile": "formal-artifact",
        }
        snapshot_id, plan_hash = service._store_plan(
            conn,
            plan_id=plan_id,
            group_id=group_id,
            raw={
                "tasks": [spec],
                "registered_capabilities": ["coding"],
                "verify_profiles": ["formal-artifact"],
            },
        )
        event = service._approve_one(
            conn,
            task_id=task_id,
            depends_on=[],
            decided_by=decided_by,
            decided_by_type=decided_by_type,
        )
        if event is not EventKind.PLAN_APPROVE:
            raise Conflict("dispatch approval did not reach ready")
        conn.execute("UPDATE plans SET status = 'approved' WHERE plan_id = ?", (plan_id,))
        result = {
            "task_id": task_id,
            "plan_id": plan_id,
            "snapshot_id": snapshot_id,
            "plan_hash": plan_hash,
            "assigned_harness_ids": [harness_id],
            "status": TaskStatus.READY.value,
            "owner": None,
            "replayed": False,
        }
        conn.execute(
            "INSERT INTO command_idempotency(operation, idempotency_key, payload_hash, first_result_json) VALUES ('formal_dispatch', ?, ?, ?)",
            (request_id, digest, _canonical(result)),
        )
        return result


def eligible_tasks(database, *, harness_id: str, group_id: str | None, limit: int) -> list[dict]:
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN (?, ?) ORDER BY task_id",
            (TaskStatus.READY.value, TaskStatus.CHANGES_REQUESTED.value),
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if (not group_id or row["group_id"] == group_id)
            and not ClaimService.eligibility(conn, row, harness_id)[0]
        ][:limit]
