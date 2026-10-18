"""方案传阅与分配轮的落库回归（R10 §39 / R10-fix-13）。

这一组测试钉的是**硬门禁**和"申请不等于领取"两条：群主要求"管理员做好方案后先传给
其他 harness 成员互审、再分派"，所以"没传阅就分配"必须是**拒绝**，而不是前端提示。
另外每条写操作都必须留下一条 A 集合事件，且都不许碰 `tasks.status`。
"""

from __future__ import annotations

import pytest

from app.allocation.service import AllocationError, AllocationService
from app.events.store import EventStore
from app.spec import AllocationEvent
from tests.claim_support import DEFAULT_GROUP, make_database, seed_project

SNAPSHOT = "snap-0001"
PLAN = "plan-0001"
ADMIN = "office-admin"
MEMBER_A = "qoder-member-1"
MEMBER_B = "codebuddy-member-1"


def _service(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    return database, AllocationService(database)


def _events(database):
    return database.read("SELECT event_name, actor_type, actor_id FROM task_events ORDER BY seq")


def _open_round(service, reviewers=(MEMBER_A, MEMBER_B), *, snapshot=SNAPSHOT, **overrides):
    kwargs = {
        "group_id": DEFAULT_GROUP,
        "plan_id": PLAN,
        "plan_snapshot_id": snapshot,
        "reviewer_harness_ids": list(reviewers),
        "opened_by": ADMIN,
    }
    kwargs.update(overrides)
    return service.open_circulation(**kwargs)


def _round_state(service, circulation_id: str) -> str:
    return str(
        service.database.read(
            "SELECT state FROM circulation_rounds WHERE circulation_id = ?",
            (circulation_id,),
        )[0]["state"]
    )


def _reach_completed(service, *, rounds: int = 2, snapshot: str = SNAPSHOT) -> str:
    """走完整整 `rounds` 轮传阅，返回 circulation_id。

    每一轮都是"收齐本轮意见 → 关闭 → 修订进入下一轮"；最后一轮关闭后不再修订，
    直接完成（对应 §39 的 `revising --circulation.completed--> completed`）。
    """
    circulation_id = _open_round(service, snapshot=snapshot)["circulation_id"]
    for index in range(rounds):
        for member in (MEMBER_A, MEMBER_B):
            service.submit_circulation_review(
                circulation_id=circulation_id,
                harness_id=member,
                body=f"{member} 第 {index + 1} 轮的意见",
            )
        service.close_circulation_round(circulation_id=circulation_id, closed_by=ADMIN)
        if index < rounds - 1:
            service.revise_circulation(circulation_id=circulation_id, revised_by=ADMIN)
    service.complete_circulation(circulation_id=circulation_id, completed_by=ADMIN)
    return circulation_id


def test_open_circulation_lands_in_circulating_and_records_one_event(tmp_path):
    database, service = _service(tmp_path)
    result = _open_round(service)

    assert result["state"] == "circulating"
    assert result["round_no"] == 1
    (event,) = _events(database)
    assert event["event_name"] == AllocationEvent.CIRCULATION_OPENED.value
    assert event["actor_type"] == "harness"
    assert event["actor_id"] == ADMIN


def test_a_circulation_round_needs_at_least_one_reviewer(tmp_path):
    _, service = _service(tmp_path)
    with pytest.raises(AllocationError):
        _open_round(service, reviewers=())


def test_only_the_named_reviewers_may_submit_in_this_round(tmp_path):
    _, service = _service(tmp_path)
    circulation_id = _open_round(service, reviewers=(MEMBER_A,))["circulation_id"]

    with pytest.raises(AllocationError, match="not on the reviewer list"):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_B, body="我没被邀请"
        )
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="我的意见"
    )
    assert (
        service.database.read(
            "SELECT COUNT(*) AS n FROM circulation_reviews WHERE circulation_id = ?",
            (circulation_id,),
        )[0]["n"]
        == 1
    )


def test_a_reviewer_cannot_submit_twice_in_one_round(tmp_path):
    _, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第一版"
    )
    with pytest.raises(AllocationError, match="already submitted"):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_A, body="想改口"
        )


def test_a_review_must_not_be_empty(tmp_path):
    _, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]
    with pytest.raises(AllocationError):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_A, body="   "
        )


def test_the_two_round_path_reaches_completed(tmp_path):
    database, service = _service(tmp_path)
    circulation_id = _reach_completed(service)

    assert _round_state(service, circulation_id) == "completed"
    names = [event["event_name"] for event in _events(database)]
    assert names[0] == AllocationEvent.CIRCULATION_OPENED.value
    assert names[-1] == AllocationEvent.CIRCULATION_COMPLETED.value
    assert AllocationEvent.CIRCULATION_REVISED.value in names


def test_a_round_one_reviewer_may_review_round_two(tmp_path):
    """回归：第一轮评审过的人在第二轮必须还能提交意见。

    曾经的唯一键是 `(circulation_id, harness_id)`，而 §39 的 `revising → circulating`
    复用同一条传阅记录 —— 于是第二轮谁都没法再提交，传阅永远走不到 `completed`，
    分配也就永远开不了（硬门禁反过来把整个流程锁死）。修法是唯一键带上 `round_no`，
    并让修订把轮号往前推。
    """
    database, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]

    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第一轮意见"
    )
    service.close_circulation_round(circulation_id=circulation_id, closed_by=ADMIN)
    revised = service.revise_circulation(circulation_id=circulation_id, revised_by=ADMIN)
    assert revised["round_no"] == 2 and revised["state"] == "circulating"

    # 同一个人，新的一轮 —— 必须放行。
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第二轮的补充意见"
    )
    rows = database.read(
        "SELECT round_no, body FROM circulation_reviews WHERE circulation_id = ?"
        " ORDER BY round_no",
        (circulation_id,),
    )
    assert [(row["round_no"], row["body"]) for row in rows] == [
        (1, "第一轮意见"),
        (2, "第二轮的补充意见"),
    ]
    # 同一轮里重复提交仍然被拒。
    with pytest.raises(AllocationError, match="already submitted"):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_A, body="同一轮改口"
        )


def test_revising_can_rotate_the_reviewer_list(tmp_path):
    """§39 让名单按轮发布：下一轮可以换人看。"""
    database, service = _service(tmp_path)
    circulation_id = _open_round(service, reviewers=(MEMBER_A,))["circulation_id"]
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第一轮"
    )
    service.close_circulation_round(circulation_id=circulation_id, closed_by=ADMIN)
    service.revise_circulation(
        circulation_id=circulation_id, revised_by=ADMIN, reviewer_harness_ids=[MEMBER_B]
    )
    # 上一轮的 A 已不在本轮名单里。
    with pytest.raises(AllocationError, match="not on the reviewer list"):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_A, body="想再来一轮"
        )
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_B, body="换我来看"
    )
    assert (
        database.read(
            "SELECT COUNT(*) AS n FROM circulation_reviews WHERE circulation_id = ?",
            (circulation_id,),
        )[0]["n"]
        == 2
    )


def test_allocation_cannot_open_before_any_circulation(tmp_path):
    """硬门禁：没传阅就分配 = 拒绝（群主 2026-10-17 决定）。"""
    database, service = _service(tmp_path)

    with pytest.raises(AllocationError, match="no completed circulation round"):
        service.open_allocation_round(
            group_id=DEFAULT_GROUP, plan_snapshot_id=SNAPSHOT, opened_by=ADMIN
        )
    assert _events(database) == []
    assert database.read("SELECT COUNT(*) AS n FROM allocation_rounds")[0]["n"] == 0


def test_an_unfinished_circulation_does_not_satisfy_the_gate(tmp_path):
    """开了一轮传阅、但没走到 completed —— 仍然不许分配。"""
    _, service = _service(tmp_path)
    _open_round(service)
    with pytest.raises(AllocationError, match="no completed circulation round"):
        service.open_allocation_round(
            group_id=DEFAULT_GROUP, plan_snapshot_id=SNAPSHOT, opened_by=ADMIN
        )


def test_allocation_opens_after_a_completed_circulation_and_records_the_gate(tmp_path):
    database, service = _service(tmp_path)
    circulation_id = _reach_completed(service)

    result = service.open_allocation_round(
        group_id=DEFAULT_GROUP, plan_snapshot_id=SNAPSHOT, opened_by=ADMIN
    )
    assert result["state"] == "opened"
    assert result["circulation_id"] == circulation_id

    opened_event = [
        event
        for event in _events(database)
        if event["event_name"] == AllocationEvent.ALLOCATION_OPENED.value
    ]
    assert len(opened_event) == 1
    payload = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = ?",
        (AllocationEvent.ALLOCATION_OPENED.value,),
    )[0]["payload_json"]
    assert circulation_id in payload and SNAPSHOT in payload


def test_the_gate_is_per_snapshot(tmp_path):
    """传阅过的是另一个快照 —— 不能拿它给本快照开分配。"""
    _, service = _service(tmp_path)
    _reach_completed(service)
    with pytest.raises(AllocationError, match="no completed circulation round"):
        service.open_allocation_round(
            group_id=DEFAULT_GROUP, plan_snapshot_id="snap-9999", opened_by=ADMIN
        )


def test_completed_circulation_is_terminal(tmp_path):
    _, service = _service(tmp_path)
    circulation_id = _reach_completed(service)
    for call in (
        lambda: service.revise_circulation(circulation_id=circulation_id, revised_by=ADMIN),
        lambda: service.close_circulation_round(circulation_id=circulation_id, closed_by=ADMIN),
    ):
        with pytest.raises(AllocationError):
            call()


def test_cancel_needs_a_reason_and_lands_in_cancelled(tmp_path):
    _, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]
    with pytest.raises(AllocationError):
        service.cancel_circulation(circulation_id=circulation_id, cancelled_by=ADMIN, reason=" ")
    result = service.cancel_circulation(
        circulation_id=circulation_id, cancelled_by=ADMIN, reason="群主改方向了"
    )
    assert result["state"] == "cancelled"
    # 作废之后不能再提交意见，也不能被"再开一次"绕过 —— 重开是新传阅轮。
    with pytest.raises(AllocationError):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_A, body="迟到的意见"
        )


def test_circulation_never_touches_tasks_or_task_status(tmp_path):
    """§39 封闭规则 6：A 事件不写 `tasks.status`，也不产生 owner/attempt/工作区。"""
    database, service = _service(tmp_path)
    _reach_completed(service)
    service.open_allocation_round(
        group_id=DEFAULT_GROUP, plan_snapshot_id=SNAPSHOT, opened_by=ADMIN
    )
    assert database.read("SELECT COUNT(*) AS n FROM tasks")[0]["n"] == 0
    assert database.read("SELECT COUNT(*) AS n FROM task_attempts")[0]["n"] == 0


def test_every_written_event_is_a_registered_allocation_event(tmp_path):
    """写入的事件名必须全部落在 §37.7 的 A 集合里（否则 EventStore 早已抛错）。"""
    database, service = _service(tmp_path)
    _reach_completed(service)
    service.open_allocation_round(
        group_id=DEFAULT_GROUP, plan_snapshot_id=SNAPSHOT, opened_by=ADMIN
    )
    registered = {event.value for event in AllocationEvent}
    assert {event["event_name"] for event in _events(database)} <= registered
    # 反向：事件真的落库了，可被 EventStore 回放读到（不是只写进投影）。
    replayed = [record.event_name for record in EventStore(database).iter_since(0)]
    assert AllocationEvent.CIRCULATION_COMPLETED.value in replayed
