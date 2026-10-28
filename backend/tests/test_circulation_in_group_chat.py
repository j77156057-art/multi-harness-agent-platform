"""成员的意见要出现在项目群聊里，作者是成员自己。

传阅意见一直只写在 ``circulation_reviews`` 表里，群里只看得到管理员的转述 ——
六个成员各写了两轮意见，群里的样子却像管理员跟群主两个人在说话。群主看到的
是"过程"，看不到"谁说了什么"。

这一组钉的是出口：

* 成员提交意见 → 同一条意见进群聊，作者是成员本人（不是管理员转述）；
* 群里那条与记录里的那条指同一份意见（refs 里带着 review_id）；
* 记录写不进去时，群里也不会多出一条（同一个事务，不留半截）；
* 名单外的人依然被拒，且被拒时不留下任何群聊消息。
"""

from __future__ import annotations

import json

import pytest

from app.allocation.service import AllocationError, AllocationService
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


def _open_round(service, reviewers=(MEMBER_A, MEMBER_B)):
    return service.open_circulation(
        group_id=DEFAULT_GROUP,
        plan_id=PLAN,
        plan_snapshot_id=SNAPSHOT,
        reviewer_harness_ids=list(reviewers),
        opened_by=ADMIN,
    )


def _messages(database, group_id=DEFAULT_GROUP):
    rows = database.read(
        "SELECT message_id, sender_type, sender_id, body, refs FROM task_messages"
        " WHERE group_id = ? ORDER BY created_seq",
        (group_id,),
    )
    return [dict(row) for row in rows]


def test_a_members_opinion_lands_in_the_group_chat_as_its_own(tmp_path):
    database, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]

    service.submit_circulation_review(
        circulation_id=circulation_id,
        harness_id=MEMBER_A,
        body="第 1 轮：把验收命令的实际输出写进说明里。",
    )

    (message,) = _messages(database)
    # 作者是成员本人：群里要看见"谁说的"，不是"谁复述的"。
    assert message["sender_type"] == "harness"
    assert message["sender_id"] == MEMBER_A
    assert "第 1 轮：把验收命令的实际输出写进说明里。" in message["body"]
    # 标题说明这是哪一轮、针对哪个方案。
    assert "第 1 轮传阅意见" in message["body"]
    assert PLAN in message["body"]


def test_the_chat_message_points_back_at_the_recorded_review(tmp_path):
    database, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]

    result = service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="我的意见"
    )

    (message,) = _messages(database)
    refs = json.loads(message["refs"])
    assert "circulation-review" in refs
    assert f"review:{result['review_id']}" in refs
    assert f"circulation:{circulation_id}" in refs


def test_each_round_of_opinions_reaches_the_chat(tmp_path):
    """两轮就是两份意见进群：第二轮不是改第一轮，是另起一条。"""
    database, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]

    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第一轮的意见"
    )
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_B, body="另一个人的第一轮意见"
    )
    service.close_circulation_round(circulation_id=circulation_id, closed_by=ADMIN)
    service.revise_circulation(circulation_id=circulation_id, revised_by=ADMIN)
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第二轮的意见"
    )

    messages = _messages(database)
    assert len(messages) == 3
    assert [m["sender_id"] for m in messages] == [MEMBER_A, MEMBER_B, MEMBER_A]
    assert "第 2 轮传阅意见" in messages[2]["body"]
    assert "第二轮的意见" in messages[2]["body"]


def test_a_refused_reviewer_leaves_nothing_in_the_chat(tmp_path):
    """名单外的人被拒时，不能留下一条群聊消息（半截状态最像丢件）。"""
    database, service = _service(tmp_path)
    circulation_id = _open_round(service, reviewers=(MEMBER_A,))["circulation_id"]

    with pytest.raises(AllocationError, match="not on the reviewer list"):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_B, body="我不该被听见"
        )

    assert _messages(database) == []


def test_a_duplicate_submission_does_not_duplicate_the_chat_message(tmp_path):
    """同一个人同一轮只能有一份意见，群里也只能有一条。"""
    database, service = _service(tmp_path)
    circulation_id = _open_round(service)["circulation_id"]
    service.submit_circulation_review(
        circulation_id=circulation_id, harness_id=MEMBER_A, body="第一版"
    )

    with pytest.raises(AllocationError, match="already submitted"):
        service.submit_circulation_review(
            circulation_id=circulation_id, harness_id=MEMBER_A, body="想改口"
        )

    assert len(_messages(database)) == 1
