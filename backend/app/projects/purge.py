"""回收站到期：把项目连它名下的任务一起删掉，**事件流留底**。

口径（2026-11-02 由人拍板）：

- **删**：项目、它的群、方案与快照、任务与尝试、领取与租约、输出与工作树记录、审核与决策、
  分配与传阅、群消息与投递记录 —— 也就是"界面上还看得见的那些东西"。
- **留**：`task_events` 与 `verification_receipts`。它们是 append-only 的
  **发生过什么**，删掉就再也追不回来了；而"这条活现在是什么状态"是投影，投影可以没有。
  （`verification_receipts` 上有 BEFORE UPDATE / BEFORE DELETE 触发器，删改都不许；
  迁移 030 又把它指向 `tasks` / `task_attempts` 的外键去掉了 —— 证据留底，
  但不再绑住投影，回收站才真的清得掉。2026-11-19 真机复核：改之前，只要项目名下
  有过验证回执，回收站**一次都没清成功过**，而清扫的异常被 scheduler 吞成报告字段，
  界面毫无异样。）

一条原则写在函数里：**只删得掉自己认得的东西**。归不到某个项目上的表（比如
`command_idempotency`、`jsonl_exports`）一律不碰 —— 宁可留几行孤儿，也不能顺手清掉
别的项目还在用的记账。

**删除顺序由外键决定，不能随手排。** 连接是开着 `PRAGMA foreign_keys=ON` 的（见
`app/db/connection.py`），父行还在、子行先删是合法的；反过来会被 SQLite 直接拒掉，
整个事务回滚 —— 那等于回收站永远清不掉。所以这里的表顺序照 `PRAGMA foreign_key_list`
排出来（子在前、父在后），不是照"看起来顺眼"排。分配那几张最容易排错：
`applications → allocation_rounds`、`allocations → allocation_decisions → applications`，
写成"先删轮次"就会在真有申请的项目上炸掉，而没有申请数据的测试是绿的。
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app.db.connection import Database

#: 回收站保留几天。与界面上写的"还剩 N 天"同一个口径（`office/src/project-workspace.js`）。
TRASH_RETAIN_DAYS = 7


def _cutoff(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - timedelta(days=TRASH_RETAIN_DAYS)).isoformat()


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def _table_columns(conn: Any, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:  # pragma: no cover - 表不存在时按"什么都不认得"处理
        return set()


def _delete_rows(conn: Any, table: str, pairs: Iterable[tuple[str, list[str]]]) -> int:
    """一张表里"哪个键对得上就删哪行"。

    列先按**真实 schema** 过滤：不同表挂的键不一样（`sweep_progress` 只有 `task_id`、
    `resource_leases` 只有 `attempt_id`），写死两句 `IN (...)` 会撞上"没有这一列"而整条
    清扫报错 —— 那等于回收站永远清不掉。空集合直接返回 0（`IN ()` 在 SQLite 里是语法错）。
    """
    keys = [(column, values) for column, values in pairs if values]
    present = [column for column, _values in keys if column in _table_columns(conn, table)]
    if not present:
        return 0
    wanted = dict(keys)
    where = " OR ".join(f"{column} IN ({_placeholders(wanted[column])})" for column in present)
    params: list[str] = []
    for column in present:
        params.extend(wanted[column])
    cursor = conn.execute(f"DELETE FROM {table} WHERE {where}", tuple(params))
    return int(cursor.rowcount or 0)


def _column_values(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[str]:
    return [str(row[0]) for row in conn.execute(sql, params) if row and row[0] is not None]


def _ids_in_groups(conn: Any, sql: str, groups: list[str]) -> list[str]:
    if not groups:
        return []
    return _column_values(conn, sql.format(marks=_placeholders(groups)), tuple(groups))


def purge_project(conn: Any, project_id: str) -> dict[str, int]:
    """删一个项目连同它名下的东西。**不碰 `task_events`**。

    单开后门让测试直接调；`purge_due_projects` 只是"找出到期的，一个个来"。
    """
    counts: dict[str, int] = {}

    groups = _column_values(conn, "SELECT group_id FROM groups WHERE project_id = ?", (project_id,))
    tasks = _ids_in_groups(conn, "SELECT task_id FROM tasks WHERE group_id IN ({marks})", groups)
    attempts = _ids_in_groups(
        conn, "SELECT attempt_id FROM task_attempts WHERE task_id IN ({marks})", tasks
    )
    plans = _ids_in_groups(conn, "SELECT plan_id FROM plans WHERE group_id IN ({marks})", groups)
    circulations = _ids_in_groups(
        conn,
        "SELECT circulation_id FROM circulation_rounds WHERE group_id IN ({marks})",
        groups,
    )
    rounds = _ids_in_groups(
        conn, "SELECT round_id FROM allocation_rounds WHERE group_id IN ({marks})", groups
    )
    applications = _ids_in_groups(
        conn, "SELECT application_id FROM applications WHERE group_id IN ({marks})", groups
    )
    messages = _ids_in_groups(
        conn, "SELECT message_id FROM task_messages WHERE group_id IN ({marks})", groups
    )

    # 一张表能挂的键都列出来，`_delete_rows` 按真实存在的列取交集：多写不报错，少写才留孤儿。
    by_task = [("task_id", tasks), ("attempt_id", attempts)]
    by_group = [("group_id", groups)]

    def drop(table: str, *pairs: tuple[str, list[str]]) -> None:
        counts[table] = _delete_rows(conn, table, pairs)

    # 1) 传阅与分配 —— 顺序照外键：意见/授权/裁决 → 申请 → 轮次。
    #    `applications` 在 `allocation_rounds` 之前：反过来的话，真有申请的项目会被
    #    `FOREIGN KEY constraint failed` 整条挡下来。
    drop("message_deliveries", ("message_id", messages))
    drop("application_materials", ("application_id", applications))
    drop("application_tasks", ("application_id", applications), ("task_id", tasks))
    drop("circulation_reviews", ("circulation_id", circulations))
    drop("allocations", ("round_id", rounds), ("task_id", tasks))
    drop("allocation_decisions", ("round_id", rounds), ("task_id", tasks))
    drop("applications", ("application_id", applications), *by_group, ("round_id", rounds))
    drop("allocation_rounds", ("round_id", rounds), *by_group)
    drop("circulation_rounds", ("circulation_id", circulations), *by_group)

    # 2) 挂在任务与尝试上的叶子。全部排在任务之前。
    for table in (
        "task_claims",
        "task_outputs",
        "task_decisions",
        "verify_jobs",
        # `verification_receipts` **不在这里**：它是 append-only 的证据（表上有
        # BEFORE DELETE 触发器），删它会被 SQLite ABORT，整条清扫回滚。
        # 与 `task_events` 同性质 —— 留着。
        #
        # `ready_unclaimed_reminders` **要在这里**：它的 task_id 是
        # `PRIMARY KEY REFERENCES tasks(task_id)`，漏删就会在删 tasks 时撞
        # FOREIGN KEY constraint failed（2026-11-19 真机复核：活库 36 行）。
        "ready_unclaimed_reminders",
        "reviews",
        "workspaces",
        "artifacts",
        "leases",
        "budget_reservations",
        "budget_incidents",
        "side_effect_intents",
        "resource_leases",
        "sweep_progress",
        "alert_deliveries",
        # 尝试自己也是叶子：它在任务之前删。
        "task_attempts",
    ):
        drop(table, *by_task)

    drop("task_dependencies", ("child_task_id", tasks), ("parent_task_id", tasks))
    drop("task_messages", *by_group, ("task_id", tasks))

    # 3) 任务 → 方案与快照 → 群。`tasks` 在 `plan_snapshots` 之前：任务上挂着快照的外键。
    drop("tasks", *by_group, ("task_id", tasks))
    drop("plan_snapshots", ("plan_id", plans))
    drop("plans", ("plan_id", plans), *by_group)
    drop("groups", ("group_id", groups))

    # 4) 项目本身。
    drop("integration_health", ("project_id", [project_id]))
    drop("projects", ("project_id", [project_id]))
    # task_events 不在这里 —— 见模块开头。
    return {name: value for name, value in counts.items() if value}


def _live_lease_reason(conn: Any, project_id: str) -> str:
    """这个项目名下还有**没释放的租约**吗？有的话给出原因，没有就空串。

    租约没释放 = 某个窗口**可能还在这个工作区里干活**。那不是"该清的垃圾"，
    是还在进行中的活 —— 删了等于把人家脚底下的东西抽走。所以这时**不清**，
    并把原因说出来。

    以前没有这道闸：清扫失败（外键、触发器）被 scheduler 吞成报告字段，界面
    毫无异样，看着像"清过了"，其实一行没删（2026-11-19 真机复核）。
    """
    groups = _column_values(conn, "SELECT group_id FROM groups WHERE project_id = ?", (project_id,))
    if not groups:
        return ""
    tasks = _ids_in_groups(conn, "SELECT task_id FROM tasks WHERE group_id IN ({marks})", groups)
    if not tasks:
        return ""
    marks = _placeholders(tasks)
    count = conn.execute(
        "SELECT count(*) FROM leases WHERE released_at IS NULL AND task_id IN (" + marks + ")",
        tuple(tasks),
    ).fetchone()[0]
    if count:
        return f"这个项目名下还有 {count} 条没释放的租约（可能还有窗口在干活），所以没清扫"
    return ""


def purge_due_projects_detailed(
    database: Database, now: datetime | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把满了保留期的项目一个个删掉，返回（删了什么，跳过了什么）。"""
    cutoff = _cutoff(now)
    due = database.read(
        "SELECT project_id, name, trashed_at FROM projects"
        " WHERE trashed_at IS NOT NULL AND trashed_at <= ?"
        " ORDER BY trashed_at",
        (cutoff,),
    )
    purged: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for row in due:
        project_id = str(row["project_id"])
        with database.transaction() as conn:
            reason = _live_lease_reason(conn, project_id)
            if reason:
                skipped.append(
                    {
                        "project_id": project_id,
                        "name": str(row["name"] or ""),
                        "trashed_at": str(row["trashed_at"] or ""),
                        "reason": reason,
                    }
                )
                continue
            counts = purge_project(conn, project_id)
        purged.append(
            {
                "project_id": project_id,
                "name": str(row["name"] or ""),
                "trashed_at": str(row["trashed_at"] or ""),
                "deleted": counts,
            }
        )
    return purged, skipped


def purge_due_projects(database: Database, now: datetime | None = None) -> list[dict[str, Any]]:
    """把满了保留期的项目一个个删掉，返回每个删了什么。

    跳过的项目不在这里 —— 想看"为什么没删"，用
    ``purge_due_projects_detailed``。
    """
    purged, _skipped = purge_due_projects_detailed(database, now)
    return purged
