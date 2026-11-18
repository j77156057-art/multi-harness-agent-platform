"""本群名册：谁在群里、在线否、在忙什么、谁是管理员。

这个读面是新加的（2026-11-18）：办公室左栏要看"谁在跑"，平台助手
要看"别人在忙什么"，两边读的是同一份，免得两处各算一套、互相打架。

名册的成员来自**本群账本**：只有在这群里领过活、被派过活、做过一轮的
窗口才算成员。全局注册表里的窗口不算 —— 否则换个项目就看见别处的窗口。
"""

from __future__ import annotations

from pathlib import Path

from app.adapter.presence import record_presence
from app.adapter.protocol import HarnessRegistration
from app.config import settings
from app.adapter.registry import HarnessRegistry
from app.tasks.service import SchedulerService
from tests.api_support import bootstrap, make_client, publish_plan


def _register(database, harness_id: str, *, provider: str = "local", name: str = "") -> None:
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=harness_id,
            provider=provider,
            adapter="cli",
            adapter_version="1.0.0",
            capabilities=("coding",),
            max_parallel_tasks=1,
            status="online",
            registration_version="reg-001",
            name=name or harness_id,
        ),
        registered_by="human-1",
    )
    # 窗口要有凭据才算"能领活"（平台的判据，不是测试的偏好）。
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO auth_tokens(token_hash, subject_type, subject_id, created_at)"
            " VALUES (?, 'harness', ?, ?)",
            (f"hash-{harness_id}", harness_id, "2026-01-01T00:00:00+00:00"),
        )


def test_the_roster_lists_only_the_windows_that_touched_this_group(tmp_path: Path):
    client, database = make_client(tmp_path)
    bootstrap(client, group_id="G-1")
    _register(database, "member-1", name="Member One")
    _register(database, "member-2", name="Member Two")
    _register(database, "outsider-1", name="Outsider")
    # 派单目标写在计划快照里（平台的派单面就是这么读的）。
    publish_plan(
        client,
        plan_id="PL-1",
        group_id="G-1",
        tasks=[{"id": "T-1", "assigned_harness_ids": ["member-1"]}],
    )
    approve = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})
    assert approve.status_code == 200, approve.text

    roster = client.get("/api/v1/groups/G-1/roster")
    assert roster.status_code == 200, roster.text
    body = roster.json()
    ids = [row["harness_id"] for row in body["members"]]
    assert "member-1" in ids
    assert "outsider-1" not in ids, "没在这个群里干过活的窗口不算本群成员"


def test_the_roster_says_who_is_alive_and_who_is_not(tmp_path: Path):
    client, database = make_client(tmp_path)
    bootstrap(client, group_id="G-1")
    _register(database, "member-1")
    publish_plan(
        client,
        plan_id="PL-1",
        group_id="G-1",
        tasks=[{"id": "T-1", "assigned_harness_ids": ["member-1"]}],
    )
    approve = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})
    assert approve.status_code == 200, approve.text

    # 只有报过到（presence）的窗口才算"进程还在跑"。
    record_presence(database, "member-1")

    body = client.get("/api/v1/groups/G-1/roster").json()
    rows = {row["harness_id"]: row for row in body["members"]}
    assert rows["member-1"]["live"] is True
    assert body["live_count"] == 1


def test_the_administrator_is_in_the_roster_even_when_it_never_took_work(
    tmp_path: Path, monkeypatch
):
    """管理员是**角色**：它一条活都没领过时，名册里也得有它。

    否则"本群的管理员是谁"永远答不出来 —— 而这正是"代用户转达给管理员"
    要用到的那一条事实。
    """
    monkeypatch.setattr(settings, "admin_subjects", frozenset({"codebuddy"}))
    client, database = make_client(tmp_path)
    bootstrap(client, group_id="G-1")
    _register(database, "codebuddy", name="Administrator window")
    _register(database, "member-1")

    body = client.get("/api/v1/groups/G-1/roster").json()
    ids = [row["harness_id"] for row in body["members"]]
    assert "codebuddy" in ids, "管理员没干过活也要在名册里"
    assert body["admin_harness_ids"] == ["codebuddy"]


def test_an_empty_group_answers_an_empty_roster_not_an_error(tmp_path: Path):
    client, _database = make_client(tmp_path)
    bootstrap(client, group_id="G-empty")
    response = client.get("/api/v1/groups/G-empty/roster")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["members"] == []
    assert body["member_count"] == 0


def test_the_live_face_answers_none_before_the_assistant_runs(tmp_path: Path):
    """没有跑过时就说"没有"——不是空对象，也不是一个假装在忙的状态。"""
    client, _database = make_client(tmp_path)
    bootstrap(client, group_id="G-1")
    response = client.get("/api/v1/groups/G-1/assistant/live")
    assert response.status_code == 200, response.text
    assert response.json()["live"] is None


def test_the_live_face_reports_what_the_assistant_is_doing(tmp_path: Path, monkeypatch):
    from app.assistant.live import write_state

    client, _database = make_client(tmp_path)
    bootstrap(client, group_id="G-1")
    # 读面读的是 settings.data_dir；测试里把它指向 tmp，别往真目录里写。
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    write_state(
        tmp_path,
        "G-1",
        {"state": "running", "asker": "operator", "question": "还有谁在忙？"},
    )
    body = client.get("/api/v1/groups/G-1/assistant/live").json()
    assert body["live"]["state"] == "running"
    assert body["live"]["asker"] == "operator"


def test_the_roster_service_is_the_same_source_the_read_face_uses(tmp_path: Path):
    """服务层与读面必须给同一份答案（读面不许自己再拼一套）。"""
    client, database = make_client(tmp_path)
    bootstrap(client, group_id="G-1")
    _register(database, "member-1")
    publish_plan(
        client,
        plan_id="PL-1",
        group_id="G-1",
        tasks=[{"id": "T-1", "assigned_harness_ids": ["member-1"]}],
    )
    approve = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})
    assert approve.status_code == 200, approve.text
    from_face = client.get("/api/v1/groups/G-1/roster").json()
    from_service = SchedulerService(database).group_roster("G-1")
    assert from_face["members"] == from_service["members"]