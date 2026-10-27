"""Human dispatch using existing immutable plans and approval transitions."""

import json

from app.allocation.service import authorization_row as allocation_row
from app.claim.service import ClaimService, allocation_gate_applies
from app.spec import EventKind, TaskStatus
from app.tasks.service import Conflict, InvalidInput, SchedulerService, _canonical, _hash

#: The states a task can be **taken over** from: its work was not delivered,
#: so another task may continue it.  A task that is still running cannot be
#: taken over -- that would be two owners of one piece of work.
_SUPERSEDABLE_STATUSES = frozenset({"cancelled", "abandoned", "stale", "changes_requested"})


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
    supersedes: str | None = None,
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
    # The lineage is part of the request's content, so it belongs in the
    # digest -- but only when there is one: a request without a lineage must
    # keep hashing exactly as it did before the field existed, or an
    # idempotent replay of an older request id would look like a conflict.
    if supersedes:
        payload["supersedes"] = supersedes
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
                "target Harness holds no platform credential, so it could never" " claim this task"
            )
        if not exclusive_paths or any(
            p.startswith(("/", ".")) or ":" in p or "\\" in p or ".." in p.split("/")
            for p in exclusive_paths
        ):
            raise InvalidInput("exclusive paths must be normalized relative paths")
        if supersedes:
            # The link says "this task takes over that task's work", so the
            # old task has to **exist**, be **in this group**, and be in a
            # state that means its work was not delivered.  A link to a task
            # that is still running would be a lie, and a link to another
            # group's task would leak across the boundary.
            previous = conn.execute(
                "SELECT group_id, status FROM tasks WHERE task_id = ?", (supersedes,)
            ).fetchone()
            if not previous:
                raise InvalidInput("the superseded task does not exist")
            if previous["group_id"] != group_id:
                raise InvalidInput("the superseded task belongs to another group")
            if previous["status"] not in _SUPERSEDABLE_STATUSES:
                raise InvalidInput(
                    "the superseded task is still live ("
                    + str(previous["status"])
                    + "); only a task whose work was not delivered can be taken over"
                )
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
        if supersedes:
            # The lineage goes into the **snapshot**: the snapshot is the
            # task's immutable contract, so "this task takes over that one"
            # is part of the contract -- and the new owner reads it in the
            # work order, next to the breakpoint report of the attempt it
            # continues.
            spec["supersedes"] = supersedes
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
    """列出这个 harness 还能领的活。

    开了 §39 分配门禁的群里，"能领"还要求**自己名下有一份已 accepted 的分配**。
    这里必须和 Claim 用同一个读口径（`authorization_row`），否则成员会拿到一串
    永远领不动的活，反复 Claim 被拒、把日志刷成噪声 —— 上一轮 `WorkRefused`
    的重复实例就是这么来的。
    """
    with database.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN (?, ?) ORDER BY task_id",
            (TaskStatus.READY.value, TaskStatus.CHANGES_REQUESTED.value),
        ).fetchall()
        eligible = []
        for row in rows:
            if group_id and row["group_id"] != group_id:
                continue
            if ClaimService.eligibility(conn, row, harness_id)[0]:
                continue
            if allocation_gate_applies(row) and not allocation_row(
                conn,
                task_id=row["task_id"],
                harness_id=harness_id,
                plan_snapshot_id=row["plan_snapshot_id"],
            ):
                continue
            eligible.append(dict(row))
        return eligible[:limit]
