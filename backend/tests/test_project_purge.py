"""回收站到期：删项目与它名下的任务，**事件流留底**。

口径是 2026-11-02 由人拍板的（见 `docs/notes/project-lifecycle.md` §3）：

- 删：项目、群、方案与快照、任务与尝试、输出、审核、验证回执、分配与传阅、群消息；
- 留：`task_events` —— append-only 的"发生过什么"，删了就再也追不回来；
- 只删得掉自己认得的东西：归不到项目上的记账（`command_idempotency`、`jsonl_exports`）不动；
- 没到期的项目一个字不碰；到期清理只影响它自己，别的项目照旧。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.auth.tokens import TokenService
from app.config import settings
from app.events.store import EventStore
from app.projects.purge import TRASH_RETAIN_DAYS, purge_due_projects, purge_due_projects_detailed
from app.scheduler.runner import SWEEPS, run_all_sweeps
from app.scheduler.sweeps import sweep_project_trash
from app.spec import EventKind
from tests.api_support import make_client

OPERATOR = "office-operator"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    client, database = make_client(tmp_path)
    token = TokenService(database).issue(subject_type="human", subject_id=OPERATOR)
    return client, database, token


def _seed(client, database, token, project_id: str, group_id: str, task_id: str) -> None:
    """造一个"真跑过"的项目：项目、群、任务、事件、一条审核记录、一条群消息。"""
    client.post(
        "/api/v1/projects",
        json={"project_id": project_id, "name": project_id},
        headers=bearer(token),
    )
    client.post(
        f"/api/v1/projects/{project_id}/groups",
        json={"group_id": group_id, "name": "主群"},
        headers=bearer(token),
    )
    with database.transaction() as conn:
        # 列名照真实 schema（tasks 没有 title：标题在方案快照里）。
        conn.execute(
            "INSERT INTO tasks(task_id, group_id, status, assigned_harness_ids_json,"
            " created_at, updated_at) VALUES (?, ?, 'ready', '[]', ?, ?)",
            (task_id, group_id, "2026-11-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"),
        )
        # 事件走真正的写入入口：手写 INSERT 要凑一堆 NOT NULL 列，而且容易和 schema 脱节。
        EventStore(database).append(
            event_name=EventKind.PLAN_APPROVE,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="human",
            actor_id=OPERATOR,
            connection=conn,
        )
        # 依赖边：父子都在被删的那批里时也要清掉（这是 `_delete_by_any` 那条分支）。
        conn.execute(
            "INSERT INTO task_dependencies(parent_task_id, child_task_id, relation, output_name)"
            " VALUES (?, ?, 'needs', 'out')",
            (task_id, task_id),
        )
        conn.execute(
            "INSERT INTO task_messages(message_id, group_id, task_id, sender_type, sender_id,"
            " body, created_seq, created_at) VALUES (?, ?, ?, 'human', ?, '说一句', 1,"
            " '2026-11-01T00:00:00+00:00')",
            (f"msg-{task_id}", group_id, task_id, OPERATOR),
        )


def test_a_project_past_the_retention_window_is_purged_with_its_tasks(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-old", "proj-old-g1", "T-OLD")
    # 进回收站 8 天前（超过 7 天）。
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-old'", (old,))

    purged = purge_due_projects(database)

    assert [item["project_id"] for item in purged] == ["proj-old"]
    assert purged[0]["deleted"].get("tasks") == 1
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'proj-old'")
    assert not database.read("SELECT 1 FROM groups WHERE project_id = 'proj-old'")
    assert not database.read("SELECT 1 FROM tasks WHERE task_id = 'T-OLD'")
    assert not database.read("SELECT 1 FROM reviews WHERE task_id = 'T-OLD'")
    assert not database.read("SELECT 1 FROM task_messages WHERE group_id = 'proj-old-g1'")
    # **事件流留底**：这是这次口径的核心。
    assert database.read(
        "SELECT 1 FROM task_events WHERE aggregate_id = 'T-OLD'"
    ), "事件是 append-only 的'发生过什么'，删掉就追不回来了"


def test_a_project_still_inside_the_window_is_untouched(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-new", "proj-new-g1", "T-NEW")
    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-new'", (recent,))

    assert purge_due_projects(database) == []
    assert database.read("SELECT 1 FROM projects WHERE project_id = 'proj-new'")
    assert database.read("SELECT 1 FROM tasks WHERE task_id = 'T-NEW'")


def test_purging_one_project_leaves_the_other_alone(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-a", "proj-a-g1", "T-A")
    _seed(client, database, token, "proj-b", "proj-b-g1", "T-B")
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 3)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-a'", (old,))

    purge_due_projects(database)

    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'proj-a'")
    assert database.read("SELECT 1 FROM projects WHERE project_id = 'proj-b'")
    assert database.read("SELECT 1 FROM tasks WHERE task_id = 'T-B'")
    assert database.read("SELECT 1 FROM task_events WHERE aggregate_id = 'T-B'")


def test_a_project_that_is_not_in_the_trash_is_never_purged(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-live", "proj-live-g1", "T-LIVE")
    # 一年前的项目，但从没进过回收站：`trashed_at` 是 NULL，不该被删。
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET created_at = '2025-01-01T00:00:00+00:00'")

    assert purge_due_projects(database) == []
    assert database.read("SELECT 1 FROM projects WHERE project_id = 'proj-live'")


def test_the_sweep_reports_what_it_deleted(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-sweep", "proj-sweep-g1", "T-SWEEP")
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 2)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-sweep'", (old,))

    report = sweep_project_trash(database, now=datetime.now(UTC))

    assert report.sweep == "project_trash"
    assert report.actions == 1
    assert report.detail["retain_days"] == TRASH_RETAIN_DAYS
    assert report.detail["purged"][0]["project_id"] == "proj-sweep"
    assert report.detail["purged"][0]["deleted"]["projects"] == 1


def test_the_purge_is_part_of_the_sweep_family_and_runs_last():
    names = [name for name, _fn in SWEEPS]
    assert "project_trash" in names
    # 删东西的排在最后：不该插在状态推进中间（别的东西先跑完再清理）。
    assert names[-1] == "project_trash"


def _seed_allocation(client, database, token, project_id: str, group_id: str, task_id: str) -> None:
    """再造一份"真跑过分配与传阅"的项目。

    这一段单独造，是因为分配那几张表有自己的外键链：
    `allocations → allocation_decisions → applications → allocation_rounds → circulation_rounds`。
    只放任务与群的测试永远碰不到它，于是"先删轮次"这种错顺序在测试里是绿的。
    """
    _seed(client, database, token, project_id, group_id, task_id)
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO circulation_rounds(circulation_id, group_id, plan_id, plan_snapshot_id,"
            " round_no, opened_by, opened_at, state) VALUES (?, ?, 'plan-1', 'snap-1', 1,"
            " 'human:office-operator', '2026-11-01T00:00:00+00:00', 'completed')",
            (f"circ-{project_id}", group_id),
        )
        conn.execute(
            "INSERT INTO circulation_reviews(review_id, circulation_id, round_no, harness_id,"
            " submitted_at) VALUES (?, ?, 1, 'qoder-member-1', '2026-11-01T01:00:00+00:00')",
            (f"crev-{project_id}", f"circ-{project_id}"),
        )
        conn.execute(
            "INSERT INTO allocation_rounds(round_id, group_id, plan_snapshot_id, circulation_id,"
            " opened_by, opened_at, state) VALUES (?, ?, 'snap-1', ?, 'human:office-operator',"
            " '2026-11-01T02:00:00+00:00', 'decided')",
            (f"round-{project_id}", group_id, f"circ-{project_id}"),
        )
        conn.execute(
            "INSERT INTO applications(application_id, round_id, group_id, harness_id, state,"
            " submitted_at) VALUES (?, ?, ?, 'qoder-member-1', 'selected',"
            " '2026-11-01T03:00:00+00:00')",
            (f"app-{project_id}", f"round-{project_id}", group_id),
        )
        conn.execute(
            "INSERT INTO application_materials(material_id, application_id, version, content_hash,"
            " created_at) VALUES (?, ?, 1, 'hash-1', '2026-11-01T03:10:00+00:00')",
            (f"mat-{project_id}", f"app-{project_id}"),
        )
        conn.execute(
            "INSERT INTO application_tasks(application_id, task_id) VALUES (?, ?)",
            (f"app-{project_id}", task_id),
        )
        conn.execute(
            "INSERT INTO allocation_decisions(decision_id, round_id, task_id,"
            " chosen_application_id, decider_harness_id, outcome, decided_at)"
            " VALUES (?, ?, ?, ?, 'codex-window-admin', 'selected',"
            " '2026-11-01T04:00:00+00:00')",
            (f"dec-{project_id}", f"round-{project_id}", task_id, f"app-{project_id}"),
        )
        conn.execute(
            "INSERT INTO allocations(allocation_id, round_id, task_id, harness_id, decision_id,"
            " state) VALUES (?, ?, ?, 'qoder-member-1', ?, 'accepted')",
            (f"alloc-{project_id}", f"round-{project_id}", task_id, f"dec-{project_id}"),
        )


def _foreign_key_violations(database) -> list:
    with database.connect() as conn:
        return conn.execute("PRAGMA foreign_key_check").fetchall()


def test_a_project_with_applications_and_allocations_purges_without_fk_violations(
    tmp_path, monkeypatch
):
    """真项目上最容易被踩的一种：申请与裁决都还在，删的顺序一错整个事务就回滚。

    连接是开着 `PRAGMA foreign_keys=ON` 的，所以这条测试断言的是"删得掉"本身；
    顺带用 `foreign_key_check` 钉住"删完没有留下指向已删父行的行"。
    """
    client, database, token = _env(tmp_path, monkeypatch)
    _seed_allocation(client, database, token, "proj-alloc", "proj-alloc-g1", "T-ALLOC")
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-alloc'", (old,))

    purged = purge_due_projects(database)

    assert [item["project_id"] for item in purged] == ["proj-alloc"]
    deleted = purged[0]["deleted"]
    for table in (
        "allocations",
        "allocation_decisions",
        "applications",
        "application_materials",
        "application_tasks",
        "allocation_rounds",
        "circulation_rounds",
        "circulation_reviews",
        "tasks",
        "groups",
        "projects",
    ):
        assert deleted.get(table) == 1, f"{table} 应该删掉 1 行，实际 {deleted.get(table)!r}"
    assert _foreign_key_violations(database) == []
    # 事件流照旧留底：分配与传阅都删了，但"这条活发生过什么"还在。
    assert database.read("SELECT 1 FROM task_events WHERE aggregate_id = 'T-ALLOC'")


def test_running_the_whole_family_does_not_resurrect_or_crash(tmp_path, monkeypatch):
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-full", "proj-full-g1", "T-FULL")
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 5)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-full'", (old,))

    reports = run_all_sweeps(database, now=datetime.now(UTC))
    names = [item.sweep for item in reports]
    assert "project_trash" in names
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'proj-full'")


def test_a_project_with_a_ready_unclaimed_reminder_is_still_purged(tmp_path, monkeypatch):
    """提醒行挂在 task_id 上（外键指着 tasks）—— 漏删它，整条清扫会被挡下来。

    以前这里是直接撞 FOREIGN KEY constraint failed（活库有 36 行这样的提醒），
    而清扫的异常被 scheduler 吞成报告字段：界面毫无异样，看着像清过了，
    其实一行没删。
    """
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-rem", "proj-rem-g1", "T-REM")
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO ready_unclaimed_reminders(task_id, reason, fingerprint, notified_at)"
            " VALUES (?, '没人领', 'fp-1', '2026-11-01T00:00:00+00:00')",
            ("T-REM",),
        )
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-rem'", (old,))

    purged = purge_due_projects(database)

    assert [item["project_id"] for item in purged] == ["proj-rem"]
    assert not database.read("SELECT 1 FROM ready_unclaimed_reminders WHERE task_id = 'T-REM'")
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'proj-rem'")


def test_a_project_with_append_only_evidence_is_purged_and_the_evidence_stays(tmp_path, monkeypatch):
    """验证回执是 append-only 的证据（表上有 BEFORE UPDATE / BEFORE DELETE 触发器）。

    证据**留着**，但它不再绑住投影（迁移 030 去掉了它指向 tasks / task_attempts
    的外键）：所以项目可以被清掉，回执作为"发生过什么"留下 —— 与 task_events
    同一条规矩。

    以前两条路都堵着（删回执被触发器 ABORT，留回执又删不掉任务），回收站到期
    清扫**一次都没成功过**，而异常被 scheduler 吞掉，界面毫无异样。
    """
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-evi", "proj-evi-g1", "T-EVI")
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " head_commit, status, created_at)"
            " VALUES ('T-EVI.a1', 'T-EVI', 1, 'claude-code', 1, 'abc1234', 'verified',"
            " '2026-11-01T00:00:00+00:00')"
        )
        conn.execute(
            'INSERT INTO verification_receipts(receipt_id, task_id, attempt_id, "commit",'
            " fencing_epoch, receipt_hash, differential_mode, verify_profile, receipt_json)"
            " VALUES ('rc-1', 'T-EVI', 'T-EVI.a1', 'abc1234', 1, 'sha256:x', 'required',"
            " 'formal-artifact', '{}')"
        )
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-evi'", (old,))

    purged, skipped = purge_due_projects_detailed(database)

    assert [item["project_id"] for item in purged] == ["proj-evi"]
    assert skipped == []
    # 项目清了，证据留着。
    assert not database.read("SELECT 1 FROM projects WHERE project_id = 'proj-evi'")
    assert database.read("SELECT 1 FROM verification_receipts WHERE task_id = 'T-EVI'")


def test_a_project_with_a_live_lease_is_skipped_with_a_reason(tmp_path, monkeypatch):
    """租约没释放 = 可能还有窗口在那个工作区里干活 —— 这时候不清，并说清原因。

    这是清扫里唯一剩下的"该跳过"的理由：清垃圾与打断正在干的活是两件事。
    """
    client, database, token = _env(tmp_path, monkeypatch)
    _seed(client, database, token, "proj-busy", "proj-busy-g1", "T-BUSY")
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO leases(lease_id, task_id, kind, owner, token_hash, fencing_epoch,"
            " lease_until, grant_wall, grant_mono, boot_id)"
            " VALUES ('L-BUSY', 'T-BUSY', 'development', 'claude-code', 'h', 1,"
            " '2099-01-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00', 1.0, 'boot-1')"
        )
    old = (datetime.now(UTC) - timedelta(days=TRASH_RETAIN_DAYS + 1)).isoformat()
    with database.transaction() as conn:
        conn.execute("UPDATE projects SET trashed_at = ? WHERE project_id = 'proj-busy'", (old,))

    purged, skipped = purge_due_projects_detailed(database)

    assert purged == []
    assert [item["project_id"] for item in skipped] == ["proj-busy"]
    assert "租约" in skipped[0]["reason"]
    # 没清：项目与租约都还在。
    assert database.read("SELECT 1 FROM projects WHERE project_id = 'proj-busy'")
    assert database.read("SELECT 1 FROM leases WHERE lease_id = 'L-BUSY'")
