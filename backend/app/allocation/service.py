"""R10 §39 的分配与传阅服务（R10-fix-13）。

这一层只做一件事：把 §39 的边落到"事件 + 投影"上，并守住三条硬规则。

为什么每个操作都要写事件：§115 要求"事件写入与投影修改原子提交，重启可回放和重试"。
所以这里**没有**任何一处直接 `UPDATE ... SET state = 'x'`。下一个状态一律由
`AllocationTable.resolve(current, event).next` 算出 —— 状态机是唯一真相源，改名或
删边会让服务立刻抛错，而不是悄悄把某个状态写成一个没人登记的字符串。

三条硬规则在这里的落点：

* **硬规则 1（硬门禁，群主 2026-10-17 决定）**：`open_allocation_round` 必须先验证
  绑定的方案快照已完成至少一轮传阅（存在 `state='completed'` 的传阅轮）。没传阅就
  开分配轮 = 直接拒绝，不是提醒。这是"避免只有一个 harness 导致架构不清晰"的可执行
  形态：方案的互审在分配之前，且不可跳过。
* **硬规则 2（申请不等于领取）**：本层的任何写操作都不产生 owner、attempt、开发
  Lease 或工作区，也**不碰 `tasks.status`**。分配只决定"谁有权 Claim"。
* **硬规则 3（回避）**：`decide` 拒绝裁决者自己参与竞争的任务（在分配裁决那一半实现）。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.allocation.machine import InvalidAllocationTransition, default_allocation_table
from app.db.connection import Database
from app.events.store import EventStore
from app.spec import AllocationEvent

#: 传阅轮的终态与在途态的区分，供门禁查询复用。
COMPLETED = "completed"

#: `applications.state` 的取值。申请只有"在候选集合里"和"本人撤回"两种；轮次冻结后
#: 在候选集合里的申请转为 `frozen` —— 它仍在候选集合内（管理员还要裁决它），但已经不
#: 能被本人修改或撤回（§39 的 `application.*` 三条边都只从 `opened` 出发）。
APPLICATION_SUBMITTED = "submitted"
APPLICATION_FROZEN = "frozen"
APPLICATION_WITHDRAWN = "withdrawn"

#: `allocations.state` 的取值 —— 这是**一份分配**（某任务给了某人）的状态，不是分配轮
#: 的状态，两者刻意分开：轮次状态在 `allocation_rounds.state`（§39 的表），这里回答的
#: 是"这份授权能不能用来 Claim"。
ASSIGNMENT_INVITED = "invited"
ASSIGNMENT_ACCEPTED = "accepted"
ASSIGNMENT_DECLINED = "declined"
ASSIGNMENT_REVOKED = "revoked"

#: `allocation_decisions.outcome` 的取值。暂缓不写这张表（见 `defer_decision`）。
DECISION_ASSIGNED = "assigned"


class AllocationError(RuntimeError):
    """§39 明确拒绝的请求（把规范里的"拒绝"变成异常，而不是静默降级）。"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


#: `authorization_row` 的"不限快照"哨兵：默认行为与 §39 之前一致（只看 accepted），
#: 显式传入时才额外要求分配轮绑定的方案快照与任务的当前快照一致。
_ANY_SNAPSHOT = object()


def authorization_row(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    harness_id: str,
    plan_snapshot_id: object = _ANY_SNAPSHOT,
) -> sqlite3.Row | None:
    """§39 硬规则 2 的**唯一读口径**：某任务给某 harness 的、能用来 Claim 的授权。

    模块级而不是方法，是因为 `ClaimService` 必须在**它自己已经持有的那个事务**
    上问同一个问题：`Database.transaction()` 每次开一条新连接，嵌套进去会在
    `BEGIN IMMEDIATE` 上互相等到超时。读口径只有一处，才不会有第二处悄悄放宽。

    `plan_snapshot_id` 给了就额外要求"分配轮绑定的快照 = 任务当前快照"——这是硬规则 2
    列的"计划版本"一格：方案换版后旧授权必须失效（硬规则 5）。任务自己没有快照时
    传进来的是 `None`，`IS ?` 会等价于 `IS NULL`，而 `allocation_rounds.plan_snapshot_id`
    是 NOT NULL，因此判定为"没有可用授权"（fail-closed，不猜）。
    """
    sql = (
        "SELECT a.*, r.plan_snapshot_id AS round_plan_snapshot_id"
        " FROM allocations a JOIN allocation_rounds r ON r.round_id = a.round_id"
        " WHERE a.task_id = ? AND a.harness_id = ? AND a.state = ?"
    )
    parameters: list[object] = [task_id, harness_id, ASSIGNMENT_ACCEPTED]
    if plan_snapshot_id is not _ANY_SNAPSHOT:
        sql += " AND r.plan_snapshot_id IS ?"
        parameters.append(plan_snapshot_id)
    return conn.execute(sql, parameters).fetchone()


def consume_authorization(conn: sqlite3.Connection, *, allocation_id: str, claim_id: str) -> None:
    """把一份授权标成"被这次 Claim 用掉"（同一 `claim_id` 幂等）。

    为什么必须记：`held`/`accepted` 只说明被选中者答应了，不说明他已经开工。没有
    `claimed_at`/`claim_id` 就分不出"撤销一份没人用的授权"（合法）和"撤掉一个正在跑的
    Lease"（会留下孤儿工作区），§39 的撤销前置条件就无从判定。
    """
    row = conn.execute(
        "SELECT * FROM allocations WHERE allocation_id = ?", (allocation_id,)
    ).fetchone()
    if row is None:
        raise AllocationError(f"unknown assignment {allocation_id}")
    if str(row["state"]) != ASSIGNMENT_ACCEPTED:
        raise AllocationError(
            f"assignment {allocation_id} is {row['state']}, not accepted;"
            " it cannot authorize a claim"
        )
    if row["claimed_at"] is not None:
        if str(row["claim_id"]) == claim_id:
            return
        raise AllocationError(
            f"assignment {allocation_id} was already claimed by {row['claim_id']}"
        )
    conn.execute(
        "UPDATE allocations SET claimed_at = ?, claim_id = ? WHERE allocation_id = ?",
        (_now(), claim_id, allocation_id),
    )


def _require_reason(reason: str, action: str) -> None:
    """§39 要求这几条边都留理由（暂缓、撤销、要求补材料）。空理由一律拒绝。"""
    if not reason.strip():
        raise AllocationError(f"{action} needs a reason")


def _clean_task_ids(task_ids: list[str]) -> list[str]:
    """去重保序。空集合直接拒绝 —— "申请了 0 个任务"不是一份申请。"""
    cleaned: list[str] = []
    for task_id in task_ids:
        value = str(task_id).strip()
        if not value:
            raise AllocationError("task_ids must not contain empty entries")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise AllocationError("an application must name at least one task")
    return cleaned


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AllocationService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.store = EventStore(database)
        self.table = default_allocation_table()

    # ------------------------------------------------------- circulation 传阅

    def open_circulation(
        self,
        *,
        group_id: str,
        plan_id: str,
        plan_snapshot_id: str,
        reviewer_harness_ids: list[str],
        opened_by: str,
        deadline_at: str | None = None,
    ) -> dict[str, Any]:
        """开一轮方案传阅：把方案草案连同"本轮该谁看"一起发出去。"""
        if not reviewer_harness_ids:
            raise AllocationError("a circulation round needs at least one reviewer")
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(round_no), 0) FROM circulation_rounds"
                " WHERE plan_snapshot_id = ?",
                (plan_snapshot_id,),
            ).fetchone()
            round_no = int(row[0]) + 1
            circulation_id = _new_id("circ")
            next_state = self._next("absent", AllocationEvent.CIRCULATION_OPENED)
            self._write(
                conn,
                event=AllocationEvent.CIRCULATION_OPENED,
                aggregate_type="circulation_round",
                aggregate_id=circulation_id,
                actor_type="harness",
                actor_id=opened_by,
                payload={
                    "group_id": group_id,
                    "plan_id": plan_id,
                    "plan_snapshot_id": plan_snapshot_id,
                    "round_no": round_no,
                    "reviewer_harness_ids": reviewer_harness_ids,
                },
            )
            conn.execute(
                "INSERT INTO circulation_rounds (circulation_id, group_id, plan_id,"
                " plan_snapshot_id, round_no, reviewer_harness_ids_json, opened_by,"
                " opened_at, deadline_at, state, revision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    circulation_id,
                    group_id,
                    plan_id,
                    plan_snapshot_id,
                    round_no,
                    json.dumps(reviewer_harness_ids, ensure_ascii=False),
                    opened_by,
                    _now(),
                    deadline_at,
                    next_state,
                ),
            )
            return {
                "circulation_id": circulation_id,
                "round_no": round_no,
                "state": next_state,
            }

    def submit_circulation_review(
        self,
        *,
        circulation_id: str,
        harness_id: str,
        body: str,
        material_hash: str | None = None,
    ) -> dict[str, Any]:
        """名单内成员提交本轮意见。名单外的人被拒（§39 硬规则：只在本轮名单内）。"""
        if not body.strip():
            raise AllocationError("a circulation review must not be empty")
        with self.database.transaction() as conn:
            row = self._round(conn, circulation_id)
            next_state = self._next(str(row["state"]), AllocationEvent.CIRCULATION_REVIEW_SUBMITTED)
            reviewers = json.loads(row["reviewer_harness_ids_json"])
            if harness_id not in reviewers:
                raise AllocationError(
                    f"{harness_id} is not on the reviewer list of {circulation_id}"
                )
            existing = conn.execute(
                "SELECT review_id FROM circulation_reviews WHERE circulation_id = ?"
                " AND round_no = ? AND harness_id = ?",
                (circulation_id, int(row["round_no"]), harness_id),
            ).fetchone()
            if existing is not None:
                raise AllocationError(
                    f"{harness_id} already submitted a review for round"
                    f" {int(row['round_no'])} of {circulation_id}; a second round is a new"
                    " round, not an edit"
                )
            review_id = _new_id("crev")
            self._write(
                conn,
                event=AllocationEvent.CIRCULATION_REVIEW_SUBMITTED,
                aggregate_type="circulation_round",
                aggregate_id=circulation_id,
                actor_type="harness",
                actor_id=harness_id,
                payload={
                    "review_id": review_id,
                    "harness_id": harness_id,
                    "round_no": int(row["round_no"]),
                    "material_hash": material_hash,
                },
            )
            conn.execute(
                "INSERT INTO circulation_reviews (review_id, circulation_id, round_no,"
                " harness_id, body, material_hash, submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    review_id,
                    circulation_id,
                    int(row["round_no"]),
                    harness_id,
                    body,
                    material_hash,
                    _now(),
                ),
            )
            conn.execute(
                "UPDATE circulation_rounds SET state = ?, revision = revision + 1"
                " WHERE circulation_id = ?",
                (next_state, circulation_id),
            )
            return {"review_id": review_id, "state": next_state}

    def close_circulation_round(self, *, circulation_id: str, closed_by: str) -> dict[str, Any]:
        """本轮收齐（或截止）→ 进入修订态。"""
        with self.database.transaction() as conn:
            row = self._round(conn, circulation_id)
            next_state = self._next(str(row["state"]), AllocationEvent.CIRCULATION_ROUND_CLOSED)
            self._write(
                conn,
                event=AllocationEvent.CIRCULATION_ROUND_CLOSED,
                aggregate_type="circulation_round",
                aggregate_id=circulation_id,
                actor_type="harness",
                actor_id=closed_by,
                payload={"round_no": int(row["round_no"])},
            )
            conn.execute(
                "UPDATE circulation_rounds SET state = ?, closed_at = ?,"
                " revision = revision + 1 WHERE circulation_id = ?",
                (next_state, _now(), circulation_id),
            )
            return {"circulation_id": circulation_id, "state": next_state}

    def revise_circulation(
        self,
        *,
        circulation_id: str,
        revised_by: str,
        reviewer_harness_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """管理员按本轮意见修订方案，进入**下一轮**传阅。

        `round_no` 会前进：上一轮的意见留在原轮号上，新轮从零开始收意见。名单可以
        换（`reviewer_harness_ids`），不传则沿用当前名单 —— §39 让管理员按轮发布
        名单，正是为了下一轮可以换人看。
        """
        if reviewer_harness_ids is not None and not reviewer_harness_ids:
            raise AllocationError("a circulation round needs at least one reviewer")
        return self._transition_round(
            circulation_id=circulation_id,
            event=AllocationEvent.CIRCULATION_REVISED,
            actor=revised_by,
            reviewer_harness_ids=reviewer_harness_ids,
        )

    def complete_circulation(self, *, circulation_id: str, completed_by: str) -> dict[str, Any]:
        """轮数达标、方案定稿：这一步之后分配轮才允许开（§39 硬规则 1）。"""
        return self._transition_round(
            circulation_id=circulation_id,
            event=AllocationEvent.CIRCULATION_COMPLETED,
            actor=completed_by,
        )

    def cancel_circulation(
        self, *, circulation_id: str, cancelled_by: str, reason: str
    ) -> dict[str, Any]:
        """群主取消/方案作废：在途的传阅与未执行的分配一并失效。"""
        if not reason.strip():
            raise AllocationError("cancelling a circulation needs a reason")
        return self._transition_round(
            circulation_id=circulation_id,
            event=AllocationEvent.CIRCULATION_CANCELLED,
            actor=cancelled_by,
            extra_payload={"reason": reason},
        )

    # -------------------------------------------------- 硬门禁（§39 硬规则 1）

    def completed_circulation_for(self, conn: sqlite3.Connection, plan_snapshot_id: str):
        """该方案快照上最近一轮**已完成**的传阅，或 None。"""
        return conn.execute(
            "SELECT * FROM circulation_rounds WHERE plan_snapshot_id = ? AND state = ?"
            " ORDER BY round_no DESC LIMIT 1",
            (plan_snapshot_id, COMPLETED),
        ).fetchone()

    def open_allocation_round(
        self,
        *,
        group_id: str,
        plan_snapshot_id: str,
        opened_by: str,
        deadline_at: str | None = None,
    ) -> dict[str, Any]:
        """开分配轮。**硬门禁**：方案快照必须先完成至少一轮传阅。

        这是"方案先经其他 harness 互审、再谈谁来做"的可执行形态。未传阅就分配在
        这里被拒绝，而不是由前端弹一句提醒 —— 顺手也挡住"管理员自己一个人把方案
        定完就派活"这条退化路径。
        """
        with self.database.transaction() as conn:
            circulated = self.completed_circulation_for(conn, plan_snapshot_id)
            if circulated is None:
                raise AllocationError(
                    f"plan snapshot {plan_snapshot_id} has no completed circulation round;"
                    " §39 硬规则 1 forbids opening allocation before the plan was reviewed"
                )
            round_id = _new_id("alloc")
            next_state = self._next("absent", AllocationEvent.ALLOCATION_OPENED)
            self._write(
                conn,
                event=AllocationEvent.ALLOCATION_OPENED,
                aggregate_type="allocation_round",
                aggregate_id=round_id,
                actor_type="harness",
                actor_id=opened_by,
                payload={
                    "group_id": group_id,
                    "plan_snapshot_id": plan_snapshot_id,
                    "circulation_id": circulated["circulation_id"],
                    "circulation_round_no": int(circulated["round_no"]),
                },
            )
            conn.execute(
                "INSERT INTO allocation_rounds (round_id, group_id, plan_snapshot_id,"
                " circulation_id, opened_by, opened_at, deadline_at, state, revision)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)",
                (
                    round_id,
                    group_id,
                    plan_snapshot_id,
                    circulated["circulation_id"],
                    opened_by,
                    _now(),
                    deadline_at,
                    next_state,
                ),
            )
            return {
                "round_id": round_id,
                "state": next_state,
                "circulation_id": circulated["circulation_id"],
            }

    # ------------------------------------------------------ application 申请

    def submit_application(
        self,
        *,
        round_id: str,
        harness_id: str,
        task_ids: list[str],
        reason: str = "",
        body: str = "",
        instance_id: str | None = None,
    ) -> dict[str, Any]:
        """提交申请：声明"我想做这些任务"并附一份材料。

        §39 硬规则 2：这一步**不**产生 owner / attempt / 开发 Lease / 工作区。它只把
        `(harness_id, task_ids)` 放进候选集合；能不能开工由后面的裁决 + Claim 决定。
        """
        tasks = _clean_task_ids(task_ids)
        with self.database.transaction() as conn:
            round_row = self._round_row(conn, round_id)
            self._next(str(round_row["state"]), AllocationEvent.APPLICATION_SUBMITTED)
            self._require_tasks_in_group(conn, tasks, str(round_row["group_id"]))
            taken = conn.execute(
                "SELECT application_id FROM applications WHERE round_id = ? AND harness_id = ?",
                (round_id, harness_id),
            ).fetchone()
            if taken is not None:
                raise AllocationError(
                    f"{harness_id} already applied in {round_id}"
                    f" ({taken['application_id']}); update that one instead of"
                    " submitting a second application"
                )
            application_id = _new_id("app")
            self._write(
                conn,
                event=AllocationEvent.APPLICATION_SUBMITTED,
                aggregate_type="application",
                aggregate_id=application_id,
                actor_type="harness",
                actor_id=harness_id,
                payload={
                    "application_id": application_id,
                    "round_id": round_id,
                    "harness_id": harness_id,
                    "task_ids": tasks,
                    "revision": 1,
                },
            )
            conn.execute(
                "INSERT INTO applications (application_id, round_id, group_id, harness_id,"
                " instance_id, state, reason, revision, submitted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (
                    application_id,
                    round_id,
                    str(round_row["group_id"]),
                    harness_id,
                    instance_id,
                    APPLICATION_SUBMITTED,
                    reason,
                    _now(),
                ),
            )
            self._set_application_tasks(conn, application_id, tasks)
            self._add_material(conn, application_id, 1, body)
            # 候选集合变了 → 分配轮版本前进：管理员手上的旧版本号不再能用来裁决
            # （§39 硬规则 4 的乐观锁，防"看着旧候选名单就拍板"）。
            self._bump_round(conn, round_id)
            return {
                "application_id": application_id,
                "revision": 1,
                "state": APPLICATION_SUBMITTED,
                "task_ids": tasks,
            }

    def update_application(
        self,
        *,
        application_id: str,
        harness_id: str,
        expected_revision: int,
        reason: str | None = None,
        body: str | None = None,
        task_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """本人改自己的申请（改意向任务/理由/补材料）。`expected_revision` 是乐观锁。

        材料只增不改：`body` 非空时写一个**新版本**，旧版本留着 —— 否则"管理员当初
        看到的是哪一版"就查不出来了。
        """
        with self.database.transaction() as conn:
            application = self._application(conn, application_id)
            round_row = self._round_row(conn, str(application["round_id"]))
            if str(application["harness_id"]) != harness_id:
                raise AllocationError(
                    f"application {application_id} belongs to"
                    f" {application['harness_id']}, not {harness_id}"
                )
            if str(application["state"]) != APPLICATION_SUBMITTED:
                raise AllocationError(
                    f"application {application_id} is {application['state']};"
                    " a withdrawn application cannot be edited"
                )
            if int(application["revision"]) != expected_revision:
                raise AllocationError(
                    f"expected_revision {expected_revision} does not match application"
                    f" {application_id} revision {int(application['revision'])}"
                )
            # 状态机的边放在"本人/未撤回/版本"之后：轮次冻结时也要能说出更具体的
            # 拒绝原因（"是你自己撤回的"比"轮次已冻结"更有用），而两台机器都拦得住。
            self._next(str(round_row["state"]), AllocationEvent.APPLICATION_UPDATED)
            new_revision = int(application["revision"]) + 1
            tasks = _clean_task_ids(task_ids) if task_ids is not None else None
            if tasks is not None:
                self._require_tasks_in_group(conn, tasks, str(round_row["group_id"]))
            self._write(
                conn,
                event=AllocationEvent.APPLICATION_UPDATED,
                aggregate_type="application",
                aggregate_id=application_id,
                actor_type="harness",
                actor_id=harness_id,
                payload={
                    "application_id": application_id,
                    "round_id": str(application["round_id"]),
                    "revision": new_revision,
                    "task_ids": tasks,
                },
            )
            sets = "revision = ?, updated_at = ?"
            params: list[Any] = [new_revision, _now()]
            if reason is not None:
                sets += ", reason = ?"
                params.append(reason)
            params.append(application_id)
            conn.execute(f"UPDATE applications SET {sets} WHERE application_id = ?", params)
            if tasks is not None:
                self._set_application_tasks(conn, application_id, tasks)
            if body is not None:
                self._add_material(conn, application_id, new_revision, body)
            self._bump_round(conn, str(application["round_id"]))
            return {
                "application_id": application_id,
                "revision": new_revision,
                "state": APPLICATION_SUBMITTED,
            }

    def withdraw_application(self, *, application_id: str, harness_id: str) -> dict[str, Any]:
        """本人撤回申请。撤回后不再算候选（回避判定的候选集合也据此收窄）。"""
        with self.database.transaction() as conn:
            application = self._application(conn, application_id)
            round_row = self._round_row(conn, str(application["round_id"]))
            next_state = self._next(str(round_row["state"]), AllocationEvent.APPLICATION_WITHDRAWN)
            assert next_state == str(round_row["state"])  # §39：撤回是自环，轮次状态不变
            if str(application["harness_id"]) != harness_id:
                raise AllocationError(
                    f"application {application_id} belongs to"
                    f" {application['harness_id']}, not {harness_id}"
                )
            if str(application["state"]) != APPLICATION_SUBMITTED:
                raise AllocationError(
                    f"application {application_id} is already {application['state']}"
                )
            self._write(
                conn,
                event=AllocationEvent.APPLICATION_WITHDRAWN,
                aggregate_type="application",
                aggregate_id=application_id,
                actor_type="harness",
                actor_id=harness_id,
                payload={
                    "application_id": application_id,
                    "round_id": str(application["round_id"]),
                },
            )
            conn.execute(
                "UPDATE applications SET state = ?, withdrawn_at = ?,"
                " revision = revision + 1 WHERE application_id = ?",
                (APPLICATION_WITHDRAWN, _now(), application_id),
            )
            self._bump_round(conn, str(application["round_id"]))
            return {"application_id": application_id, "state": APPLICATION_WITHDRAWN}

    def close_allocation_round(self, *, round_id: str, closed_by: str) -> dict[str, Any]:
        """截止或管理员关闭：候选集合冻结，之后只允许裁决/邀请/要求补材料。

        冻结是两层：轮次状态进 `closed`，仍在候选集合里的申请转 `frozen`（还能被裁决，
        但本人已经改不了、也撤不回了 —— §39 的 `application.*` 三条边都只从 `opened`
        出发）。
        """
        with self.database.transaction() as conn:
            round_row = self._round_row(conn, round_id)
            next_state = self._next(str(round_row["state"]), AllocationEvent.ALLOCATION_CLOSED)
            self._write(
                conn,
                event=AllocationEvent.ALLOCATION_CLOSED,
                aggregate_type="allocation_round",
                aggregate_id=round_id,
                actor_type="harness",
                actor_id=closed_by,
                payload={"deadline_at": round_row["deadline_at"]},
            )
            self._advance_round(conn, round_id, next_state, closed=True)
            # 候选集合冻结：仍在候选集合里的申请转 `frozen`（还能被裁决，不能再被本人改）。
            conn.execute(
                "UPDATE applications SET state = ? WHERE round_id = ? AND state = ?",
                (APPLICATION_FROZEN, round_id, APPLICATION_SUBMITTED),
            )
            return {"round_id": round_id, "state": next_state}

    def request_material(
        self, *, round_id: str, task_id: str, requested_by: str, reason: str
    ) -> dict[str, Any]:
        """要求某申请的候选补材料（自环保留给该任务重开材料审核）。"""
        _require_reason(reason, "requesting more material")
        with self.database.transaction() as conn:
            round_row = self._round_row(conn, round_id)
            next_state = self._next(
                str(round_row["state"]), AllocationEvent.ALLOCATION_MATERIAL_REQUESTED
            )
            self._require_tasks_in_group(conn, [task_id], str(round_row["group_id"]))
            self._write(
                conn,
                event=AllocationEvent.ALLOCATION_MATERIAL_REQUESTED,
                aggregate_type="allocation_round",
                aggregate_id=round_id,
                actor_type="harness",
                actor_id=requested_by,
                payload={"task_id": task_id, "reason": reason},
            )
            self._advance_round(conn, round_id, next_state)
            return {"round_id": round_id, "task_id": task_id, "state": next_state}

    def defer_decision(
        self, *, round_id: str, task_id: str, decided_by: str, reason: str
    ) -> dict[str, Any]:
        """暂不分配某个任务并记录理由。

        **不**写 `allocation_decisions`：那条表的唯一键是 `(round_id, task_id)`，写着
        "一轮一任务一条裁决"。暂缓不是裁决 —— 写进去会把该任务在这个轮次里永久钉死，
        之后想改主意就再也分配不了了。暂缓的持久记录是事件本身（§115 可回放）。
        """
        _require_reason(reason, "deferring an allocation")
        with self.database.transaction() as conn:
            round_row = self._round_row(conn, round_id)
            next_state = self._next(
                str(round_row["state"]), AllocationEvent.ALLOCATION_DECISION_DEFERRED
            )
            self._require_tasks_in_group(conn, [task_id], str(round_row["group_id"]))
            self._write(
                conn,
                event=AllocationEvent.ALLOCATION_DECISION_DEFERRED,
                aggregate_type="allocation_round",
                aggregate_id=round_id,
                actor_type="harness",
                actor_id=decided_by,
                payload={"task_id": task_id, "reason": reason},
            )
            self._advance_round(conn, round_id, next_state)
            return {"round_id": round_id, "task_id": task_id, "state": next_state}

    # -------------------------------------------------------------- 裁决与分配

    def decide(
        self,
        *,
        round_id: str,
        task_id: str,
        application_id: str,
        decided_by: str,
        expected_revision: int,
        reason: str = "",
        compared_harness_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """为一个任务裁决：选定候选，产生一份 `invited` 的分配。

        裁决**不等于**开工，也不等于这一份分配就是最终的：被选中者还要自己 `accept`，
        Claim 时再校验一次。这里的 `compared_json` 记下**当时摆在桌面上的候选**，
        这样"为什么选了这个人"能和选人时的候选集合一起回放。
        """
        with self.database.transaction() as conn:
            round_row = self._round_row(conn, round_id)
            next_state = self._next(
                str(round_row["state"]), AllocationEvent.ALLOCATION_DECISION_MADE
            )
            self._require_round_revision(round_row, expected_revision)
            self._require_tasks_in_group(conn, [task_id], str(round_row["group_id"]))
            candidate = self._application(conn, application_id)
            if str(candidate["round_id"]) != round_id:
                raise AllocationError(
                    f"application {application_id} belongs to round"
                    f" {candidate['round_id']}, not {round_id}"
                )
            if str(candidate["state"]) == APPLICATION_WITHDRAWN:
                raise AllocationError(
                    f"application {application_id} was withdrawn and cannot be chosen"
                )
            if not self._applied_for(conn, application_id, task_id):
                raise AllocationError(f"application {application_id} did not apply for {task_id}")
            chosen_harness_id = str(candidate["harness_id"])
            self._require_no_conflict(conn, round_id, task_id, decided_by)
            compared = list(
                compared_harness_ids
                if compared_harness_ids is not None
                else sorted(self._candidate_harness_ids(conn, round_id, task_id))
            )
            decision_id = _new_id("dec")
            allocation_id = _new_id("al")
            self._write(
                conn,
                event=AllocationEvent.ALLOCATION_DECISION_MADE,
                aggregate_type="allocation_round",
                aggregate_id=round_id,
                actor_type="harness",
                actor_id=decided_by,
                payload={
                    "decision_id": decision_id,
                    "allocation_id": allocation_id,
                    "task_id": task_id,
                    "chosen_application_id": application_id,
                    "chosen_harness_id": chosen_harness_id,
                    "decider_harness_id": decided_by,
                    "compared_harness_ids": compared,
                    "reason": reason,
                },
            )
            try:
                conn.execute(
                    "INSERT INTO allocation_decisions (decision_id, round_id, task_id,"
                    " chosen_application_id, compared_json, decider_harness_id, outcome,"
                    " reason, revision, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        decision_id,
                        round_id,
                        task_id,
                        application_id,
                        json.dumps(compared, ensure_ascii=False),
                        decided_by,
                        DECISION_ASSIGNED,
                        reason,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                # unique(round_id, task_id)：两个管理员同时裁决同一个任务时，输的那个
                # 会撞在这里，而不是悄悄写出第二条裁决。
                raise AllocationError(
                    f"{task_id} already has a decision in round {round_id}"
                ) from exc
            conn.execute(
                "INSERT INTO allocations (allocation_id, round_id, task_id, harness_id,"
                " decision_id, state, revision) VALUES (?, ?, ?, ?, ?, ?, 1)",
                (
                    allocation_id,
                    round_id,
                    task_id,
                    chosen_harness_id,
                    decision_id,
                    ASSIGNMENT_INVITED,
                ),
            )
            self._advance_round(conn, round_id, next_state)
            return {
                "decision_id": decision_id,
                "allocation_id": allocation_id,
                "task_id": task_id,
                "harness_id": chosen_harness_id,
                "state": next_state,
                "allocation_state": ASSIGNMENT_INVITED,
            }

    def invite(
        self,
        *,
        round_id: str,
        task_id: str,
        harness_id: str,
        invited_by: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """给**未申请者**发改派邀请（包括没人申请的任务）。

        §39 把这条边专门留给"改派"：申请者走 `decide`。两条路分开是为了让"这个人自己
        来抢的"和"管理员点名请的"在事件流里一眼可辨。
        """
        with self.database.transaction() as conn:
            round_row = self._round_row(conn, round_id)
            next_state = self._next(str(round_row["state"]), AllocationEvent.ASSIGNMENT_INVITED)
            self._require_tasks_in_group(conn, [task_id], str(round_row["group_id"]))
            if harness_id in self._candidate_harness_ids(conn, round_id, task_id):
                raise AllocationError(
                    f"{harness_id} applied for {task_id}; use decide() for applicants"
                    " (assignment.invited is for non-applicants)"
                )
            live = conn.execute(
                "SELECT allocation_id, state FROM allocations WHERE round_id = ?"
                " AND task_id = ? AND harness_id = ? AND state IN (?, ?)",
                (round_id, task_id, harness_id, ASSIGNMENT_INVITED, ASSIGNMENT_ACCEPTED),
            ).fetchone()
            if live is not None:
                raise AllocationError(
                    f"{harness_id} already has a live assignment"
                    f" ({live['allocation_id']}, {live['state']}) for {task_id}"
                )
            allocation_id = _new_id("al")
            self._write(
                conn,
                event=AllocationEvent.ASSIGNMENT_INVITED,
                aggregate_type="allocation_round",
                aggregate_id=round_id,
                actor_type="harness",
                actor_id=invited_by,
                payload={
                    "allocation_id": allocation_id,
                    "task_id": task_id,
                    "harness_id": harness_id,
                    "invited_by": invited_by,
                    "reason": reason,
                },
            )
            conn.execute(
                "INSERT INTO allocations (allocation_id, round_id, task_id, harness_id,"
                " decision_id, state, revision) VALUES (?, ?, ?, ?, NULL, ?, 1)",
                (allocation_id, round_id, task_id, harness_id, ASSIGNMENT_INVITED),
            )
            self._advance_round(conn, round_id, next_state)
            return {
                "allocation_id": allocation_id,
                "task_id": task_id,
                "harness_id": harness_id,
                "allocation_state": ASSIGNMENT_INVITED,
                "state": next_state,
            }

    def accept_assignment(self, *, allocation_id: str, harness_id: str) -> dict[str, Any]:
        """被选中者/受邀者**本人**确认接单 → 这份分配才构成 Claim 的授权。"""
        return self._resolve_assignment(
            allocation_id=allocation_id,
            harness_id=harness_id,
            event=AllocationEvent.ASSIGNMENT_ACCEPTED,
        )

    def decline_assignment(self, *, allocation_id: str, harness_id: str) -> dict[str, Any]:
        """受邀者本人拒绝改派邀请。"""
        return self._resolve_assignment(
            allocation_id=allocation_id,
            harness_id=harness_id,
            event=AllocationEvent.ASSIGNMENT_DECLINED,
        )

    def revoke_assignment(
        self,
        *,
        allocation_id: str,
        expected_revision: int,
        revoked_by: str,
        reason: str,
    ) -> dict[str, Any]:
        """管理员撤销**尚未 Claim** 的分配（§39 硬规则 7 的"取消尚未执行的分配"）。

        `claimed_at` 非空 = 已经有人拿着这份授权开了工：这时撤销会被拒。撤一份正在
        跑的活要换用 §10 的漂移/回收路径，否则工作区里的东西就成了孤儿。
        """
        _require_reason(reason, "revoking an assignment")
        with self.database.transaction() as conn:
            allocation = self._allocation(conn, allocation_id)
            round_row = self._round_row(conn, str(allocation["round_id"]))
            next_state = self._next(str(round_row["state"]), AllocationEvent.ASSIGNMENT_REVOKED)
            if str(allocation["state"]) not in (ASSIGNMENT_INVITED, ASSIGNMENT_ACCEPTED):
                raise AllocationError(
                    f"assignment {allocation_id} is {allocation['state']} and cannot be revoked"
                )
            if allocation["claimed_at"] is not None:
                raise AllocationError(
                    f"assignment {allocation_id} was already claimed by"
                    f" {allocation['claim_id']}; revoke only covers unclaimed assignments"
                )
            if int(allocation["revision"]) != expected_revision:
                raise AllocationError(
                    f"expected_revision {expected_revision} does not match assignment"
                    f" {allocation_id} revision {int(allocation['revision'])}"
                )
            self._write(
                conn,
                event=AllocationEvent.ASSIGNMENT_REVOKED,
                aggregate_type="allocation_round",
                aggregate_id=str(allocation["round_id"]),
                actor_type="harness",
                actor_id=revoked_by,
                payload={
                    "allocation_id": allocation_id,
                    "task_id": str(allocation["task_id"]),
                    "harness_id": str(allocation["harness_id"]),
                    "reason": reason,
                },
            )
            conn.execute(
                "UPDATE allocations SET state = ?, revoked_at = ?, revision = revision + 1"
                " WHERE allocation_id = ?",
                (ASSIGNMENT_REVOKED, _now(), allocation_id),
            )
            self._bump_round(conn, str(allocation["round_id"]))
            return {
                "allocation_id": allocation_id,
                "allocation_state": ASSIGNMENT_REVOKED,
                "state": next_state,
            }

    # ------------------------------------------------------------ Claim 授权

    def claim_authorization(
        self,
        *,
        task_id: str,
        harness_id: str,
        expected_revision: int | None = None,
    ) -> dict[str, Any] | None:
        """§39 硬规则 2：只有"已 accepted 的分配"才构成 §11 Claim 的授权。

        返回 None 表示"没有授权"，由调用方决定拒绝的措辞；`expected_revision` 给了
        但和手上这份分配对不上时抛错 —— 那是"拿旧版本开工"，不是"没授权"。
        """
        with self.database.transaction() as conn:
            row = authorization_row(conn, task_id=task_id, harness_id=harness_id)
            if row is None:
                return None
            if expected_revision is not None and int(row["revision"]) != expected_revision:
                raise AllocationError(
                    f"expected_revision {expected_revision} does not match allocation"
                    f" {row['allocation_id']} revision {int(row['revision'])}"
                )
            return {
                "allocation_id": str(row["allocation_id"]),
                "round_id": str(row["round_id"]),
                "task_id": str(row["task_id"]),
                "harness_id": str(row["harness_id"]),
                "revision": int(row["revision"]),
                "claimed_at": row["claimed_at"],
                "claim_id": row["claim_id"],
            }

    def mark_claimed(self, *, allocation_id: str, claim_id: str) -> dict[str, Any]:
        """把一份已 accepted 的分配标成"已被这次 Claim 用掉"。幂等。"""
        with self.database.transaction() as conn:
            consume_authorization(conn, allocation_id=allocation_id, claim_id=claim_id)
            return {"allocation_id": allocation_id, "claim_id": claim_id}

    def list_candidates(self, *, round_id: str, task_id: str) -> list[dict[str, Any]]:
        """某任务当前的候选（含最新材料版本），给管理员裁决界面用。"""
        with self.database.transaction() as conn:
            rows = conn.execute(
                "SELECT a.application_id, a.harness_id, a.revision, a.reason, a.state,"
                " a.submitted_at FROM applications a JOIN application_tasks t"
                " ON t.application_id = a.application_id"
                " WHERE a.round_id = ? AND t.task_id = ? AND a.state <> ?"
                " ORDER BY a.submitted_at, a.application_id",
                (round_id, task_id, APPLICATION_WITHDRAWN),
            ).fetchall()
            out: list[dict[str, Any]] = []
            for row in rows:
                material = conn.execute(
                    "SELECT version, content_hash FROM application_materials"
                    " WHERE application_id = ? ORDER BY version DESC LIMIT 1",
                    (row["application_id"],),
                ).fetchone()
                out.append(
                    {
                        "application_id": str(row["application_id"]),
                        "harness_id": str(row["harness_id"]),
                        "revision": int(row["revision"]),
                        "reason": str(row["reason"]),
                        "state": str(row["state"]),
                        "submitted_at": str(row["submitted_at"]),
                        "material_version": None if material is None else int(material["version"]),
                        "material_hash": None
                        if material is None
                        else str(material["content_hash"]),
                    }
                )
            return out

    # --------------------------------------------------------------- internals

    def _round_row(self, conn: sqlite3.Connection, round_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM allocation_rounds WHERE round_id = ?", (round_id,)
        ).fetchone()
        if row is None:
            raise AllocationError(f"unknown allocation round: {round_id}")
        return row

    def _application(self, conn: sqlite3.Connection, application_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM applications WHERE application_id = ?", (application_id,)
        ).fetchone()
        if row is None:
            raise AllocationError(f"unknown application: {application_id}")
        return row

    def _allocation(self, conn: sqlite3.Connection, allocation_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM allocations WHERE allocation_id = ?", (allocation_id,)
        ).fetchone()
        if row is None:
            raise AllocationError(f"unknown assignment: {allocation_id}")
        return row

    def _require_tasks_in_group(
        self, conn: sqlite3.Connection, task_ids: list[str], group_id: str
    ) -> None:
        """任务必须真实存在且属于本群。

        边界写死在这里而不是靠前端：`application_tasks` 里的 `task_id` 没有外键（任务
        表在别的模块维护），所以"这一条申请指的是本群的哪个任务"只能在这一层查。
        """
        placeholders = ",".join("?" * len(task_ids))
        rows = conn.execute(
            f"SELECT task_id, group_id FROM tasks WHERE task_id IN ({placeholders})",
            list(task_ids),
        ).fetchall()
        known = {str(row["task_id"]): str(row["group_id"]) for row in rows}
        for task_id in task_ids:
            actual = known.get(task_id)
            if actual is None:
                raise AllocationError(f"unknown task: {task_id}")
            if actual != group_id:
                raise AllocationError(f"task {task_id} belongs to group {actual}, not {group_id}")

    def _require_round_revision(self, round_row: sqlite3.Row, expected_revision: int) -> None:
        if int(round_row["revision"]) != expected_revision:
            raise AllocationError(
                f"expected_revision {expected_revision} does not match round"
                f" {round_row['round_id']} revision {int(round_row['revision'])}"
            )

    def _set_application_tasks(
        self, conn: sqlite3.Connection, application_id: str, task_ids: list[str]
    ) -> None:
        conn.execute("DELETE FROM application_tasks WHERE application_id = ?", (application_id,))
        for task_id in task_ids:
            conn.execute(
                "INSERT INTO application_tasks (application_id, task_id) VALUES (?, ?)",
                (application_id, task_id),
            )

    def _add_material(
        self, conn: sqlite3.Connection, application_id: str, version: int, body: str
    ) -> str:
        material_id = _new_id("mat")
        conn.execute(
            "INSERT INTO application_materials (material_id, application_id, version,"
            " content_hash, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (material_id, application_id, version, _content_hash(body), body, _now()),
        )
        return material_id

    def _applied_for(self, conn: sqlite3.Connection, application_id: str, task_id: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM application_tasks WHERE application_id = ? AND task_id = ?",
                (application_id, task_id),
            ).fetchone()
            is not None
        )

    def _candidate_harness_ids(
        self, conn: sqlite3.Connection, round_id: str, task_id: str
    ) -> set[str]:
        rows = conn.execute(
            "SELECT a.harness_id FROM applications a JOIN application_tasks t"
            " ON t.application_id = a.application_id"
            " WHERE a.round_id = ? AND t.task_id = ? AND a.state <> ?",
            (round_id, task_id, APPLICATION_WITHDRAWN),
        ).fetchall()
        return {str(row["harness_id"]) for row in rows}

    def _require_no_conflict(
        self, conn: sqlite3.Connection, round_id: str, task_id: str, decider: str
    ) -> None:
        """§39 硬规则 3（回避）：裁决者不得是这条任务的候选。

        这里**不是**"换个人选"：规范写明无其他无利益冲突的管理员时交给群主处理，
        所以冲突就是拒绝，由上层把"请群主裁决"暴露出去。
        """
        if decider in self._candidate_harness_ids(conn, round_id, task_id):
            raise AllocationError(
                f"{decider} applied for {task_id} in round {round_id};"
                " §39 硬规则 3（回避）forbids deciding a task you compete for"
            )

    def _bump_round(self, conn: sqlite3.Connection, round_id: str) -> None:
        conn.execute(
            "UPDATE allocation_rounds SET revision = revision + 1 WHERE round_id = ?",
            (round_id,),
        )

    def _advance_round(
        self, conn: sqlite3.Connection, round_id: str, next_state: str, *, closed: bool = False
    ) -> None:
        """推进分配轮状态 + 版本前进。`sets` 只由字面片段拼成，参数全走占位符。"""
        sets = "state = ?, revision = revision + 1"
        params: list[Any] = [next_state]
        if closed:
            sets += ", closed_at = ?"
            params.append(_now())
        params.append(round_id)
        conn.execute(f"UPDATE allocation_rounds SET {sets} WHERE round_id = ?", params)

    def _resolve_assignment(
        self, *, allocation_id: str, harness_id: str, event: AllocationEvent
    ) -> dict[str, Any]:
        target = (
            ASSIGNMENT_ACCEPTED
            if event is AllocationEvent.ASSIGNMENT_ACCEPTED
            else ASSIGNMENT_DECLINED
        )
        column = "accepted_at" if target == ASSIGNMENT_ACCEPTED else "declined_at"
        with self.database.transaction() as conn:
            allocation = self._allocation(conn, allocation_id)
            round_row = self._round_row(conn, str(allocation["round_id"]))
            next_state = self._next(str(round_row["state"]), event)
            if str(allocation["harness_id"]) != harness_id:
                raise AllocationError(
                    f"assignment {allocation_id} names {allocation['harness_id']},"
                    f" not {harness_id}"
                )
            if str(allocation["state"]) != ASSIGNMENT_INVITED:
                raise AllocationError(
                    f"assignment {allocation_id} is {allocation['state']}; only an invited"
                    " assignment can be accepted or declined"
                )
            self._write(
                conn,
                event=event,
                aggregate_type="allocation_round",
                aggregate_id=str(allocation["round_id"]),
                actor_type="harness",
                actor_id=harness_id,
                payload={
                    "allocation_id": allocation_id,
                    "task_id": str(allocation["task_id"]),
                },
            )
            try:
                conn.execute(
                    f"UPDATE allocations SET state = ?, {column} = ?,"
                    " revision = revision + 1 WHERE allocation_id = ?",
                    (target, _now(), allocation_id),
                )
            except sqlite3.IntegrityError as exc:
                # idx_allocations_one_accepted_per_task：一个任务同时只能有一份
                # "已确认、可用于 Claim"的授权。并发接单时输的一方撞在这里。
                raise AllocationError(
                    f"task {allocation['task_id']} already has an accepted allocation"
                ) from exc
            self._bump_round(conn, str(allocation["round_id"]))
            return {
                "allocation_id": allocation_id,
                "state": next_state,
                "allocation_state": target,
            }

    def _next(self, current: str, event: AllocationEvent) -> str:
        """唯一的状态推进入口：不登记就抛错，绝不返回一个"看起来合理"的状态。"""
        try:
            return self.table.resolve(current, event).next
        except InvalidAllocationTransition as exc:
            raise AllocationError(str(exc)) from exc

    def _write(
        self,
        conn: sqlite3.Connection,
        *,
        event: AllocationEvent,
        aggregate_type: str,
        aggregate_id: str,
        actor_type: str,
        actor_id: str,
        payload: dict[str, Any],
    ) -> Any:
        return self.store.append(
            event_name=event.value,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            actor_type=actor_type,
            actor_id=actor_id,
            payload=payload,
            connection=conn,
        )

    def _round(self, conn: sqlite3.Connection, circulation_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM circulation_rounds WHERE circulation_id = ?", (circulation_id,)
        ).fetchone()
        if row is None:
            raise AllocationError(f"unknown circulation round: {circulation_id}")
        return row

    def _transition_round(
        self,
        *,
        circulation_id: str,
        event: AllocationEvent,
        actor: str,
        extra_payload: dict[str, Any] | None = None,
        reviewer_harness_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        with self.database.transaction() as conn:
            row = self._round(conn, circulation_id)
            next_state = self._next(str(row["state"]), event)
            self._write(
                conn,
                event=event,
                aggregate_type="circulation_round",
                aggregate_id=circulation_id,
                actor_type="harness",
                actor_id=actor,
                payload={"round_no": int(row["round_no"]), **(extra_payload or {})},
            )
            # `sets` 只由下面的字面片段拼成，参数一律走占位符 —— 没有任何用户输入
            # 进入 SQL 文本。
            sets = "state = ?, revision = revision + 1"
            params: list[Any] = [next_state]
            if event is AllocationEvent.CIRCULATION_REVISED:
                # 修订 = 进入下一轮：轮号前进，本轮关闭时间清空，重新开始收意见。
                sets += ", round_no = round_no + 1, closed_at = NULL"
                if reviewer_harness_ids is not None:
                    sets += ", reviewer_harness_ids_json = ?"
                    params.append(json.dumps(list(reviewer_harness_ids), ensure_ascii=False))
            params.append(circulation_id)
            conn.execute(f"UPDATE circulation_rounds SET {sets} WHERE circulation_id = ?", params)
            return {
                "circulation_id": circulation_id,
                "state": next_state,
                "round_no": int(row["round_no"])
                + (1 if event is AllocationEvent.CIRCULATION_REVISED else 0),
            }
