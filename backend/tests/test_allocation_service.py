"""分配（申请 → 裁决 → 接单 → Claim 授权）的落库回归（R10 §39 / R10-fix-13）。

这一组钉的是 §39 分配那一半的硬规则，外加两条**只有在真写服务时才会暴露**的回归：

* 一轮多任务：分配轮一旦有一处裁决就会进入 `decided`，之后同轮的下一个任务必须还能
  被裁决/暂缓/要求补材料 —— 否则"一轮只能分一个任务"，多任务轮在第二个任务上死掉。
* 一轮一任务一条裁决：`(round_id, task_id)` 唯一，暂缓**不写**裁决表，所以暂缓之后
  还能改主意。

以及最要紧的一条：这一层全程不许碰 `tasks.status`、不许产生 attempt 与工作区
（§39 硬规则 2/6）—— "申请不等于领取"。
"""

from __future__ import annotations

import pytest

from app.allocation.service import (
    APPLICATION_FROZEN,
    APPLICATION_SUBMITTED,
    APPLICATION_WITHDRAWN,
    ASSIGNMENT_ACCEPTED,
    ASSIGNMENT_DECLINED,
    ASSIGNMENT_INVITED,
    ASSIGNMENT_REVOKED,
    AllocationError,
    AllocationService,
)
from app.spec import AllocationEvent
from tests.claim_support import DEFAULT_GROUP, make_database, seed_project, seed_task
from tests.test_allocation_circulation import (
    ADMIN,
    MEMBER_A,
    MEMBER_B,
    PLAN,
    SNAPSHOT,
    _reach_completed,
)

OTHER_GROUP = "G-2"
TASK_1 = "T-1"
TASK_2 = "T-2"
#: 一个既没申请、也不在传阅名单里的第三方 harness —— 改派邀请的典型对象。
MEMBER_C = "qoder-member-2"


def _service(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    seed_project(database, project_id="P-2", group_id=OTHER_GROUP)
    return database, AllocationService(database)


def _events(database):
    return database.read(
        "SELECT event_name, actor_type, actor_id, payload_json FROM task_events ORDER BY seq"
    )


def _event_names(database):
    return [event["event_name"] for event in _events(database)]


def _open_round(service, *, tasks=(TASK_1,)) -> str:
    """走完传阅（硬门禁的前提）并开分配轮，返回 round_id。"""
    _reach_completed(service)
    return service.open_allocation_round(
        group_id=DEFAULT_GROUP, plan_snapshot_id=SNAPSHOT, opened_by=ADMIN
    )["round_id"]


def _round(service, round_id: str):
    return service.database.read("SELECT * FROM allocation_rounds WHERE round_id = ?", (round_id,))[
        0
    ]


def _revision(service, round_id: str) -> int:
    return int(_round(service, round_id)["revision"])


def _apply(service, round_id: str, harness: str = MEMBER_A, tasks=(TASK_1,), body="材料 v1"):
    return service.submit_application(
        round_id=round_id,
        harness_id=harness,
        task_ids=list(tasks),
        reason=f"{harness} 想接",
        body=body,
    )


def _close(service, round_id: str) -> None:
    service.close_allocation_round(round_id=round_id, closed_by=ADMIN)


def _decide(service, round_id: str, application_id: str, task_id: str = TASK_1, **overrides):
    kwargs = {
        "round_id": round_id,
        "task_id": task_id,
        "application_id": application_id,
        "decided_by": ADMIN,
        "expected_revision": _revision(service, round_id),
        "reason": "候选材料最具体",
    }
    kwargs.update(overrides)
    return service.decide(**kwargs)


# ------------------------------------------------------------------ application


def test_apply_records_one_event_and_lands_in_the_candidate_set(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)

    before = len(_events(database))
    result = _apply(service, round_id)

    assert result["state"] == APPLICATION_SUBMITTED
    assert result["revision"] == 1
    assert len(_events(database)) == before + 1
    applied = _events(database)[-1]
    assert applied["event_name"] == AllocationEvent.APPLICATION_SUBMITTED.value
    assert applied["actor_type"] == "harness" and applied["actor_id"] == MEMBER_A

    rows = database.read(
        "SELECT state, revision, reason FROM applications WHERE application_id = ?",
        (result["application_id"],),
    )
    assert rows[0]["state"] == APPLICATION_SUBMITTED
    assert rows[0]["revision"] == 1
    assert (
        database.read(
            "SELECT task_id FROM application_tasks WHERE application_id = ?",
            (result["application_id"],),
        )[0]["task_id"]
        == TASK_1
    )
    material = database.read(
        "SELECT version, content_hash, body FROM application_materials" " WHERE application_id = ?",
        (result["application_id"],),
    )
    assert len(material) == 1
    assert material[0]["version"] == 1
    assert material[0]["body"] == "材料 v1"
    # 哈希是内容的函数：同样的正文必须得到同样的哈希，改一个字就变。
    other = _apply(service, round_id, harness=MEMBER_B, body="材料 v1")
    assert (
        database.read(
            "SELECT content_hash FROM application_materials WHERE application_id = ?",
            (other["application_id"],),
        )[0]["content_hash"]
        == material[0]["content_hash"]
    )
    candidates = service.list_candidates(round_id=round_id, task_id=TASK_1)
    assert [row["harness_id"] for row in candidates] == [MEMBER_A, MEMBER_B]
    assert {row["material_hash"] for row in candidates} == {material[0]["content_hash"]}
    # 没申请这个任务的人在候选名单里不出现。
    assert service.list_candidates(round_id=round_id, task_id="T-none") == []


def test_an_application_must_name_at_least_one_task(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    with pytest.raises(AllocationError, match="at least one task"):
        _apply(service, round_id, tasks=())


def test_an_application_cannot_name_an_unknown_task(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    with pytest.raises(AllocationError, match="unknown task"):
        _apply(service, round_id, tasks=("T-ghost",))
    assert database.read("SELECT COUNT(*) AS n FROM applications")[0]["n"] == 0


def test_an_application_cannot_name_a_task_from_another_group(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id="T-other", group_id=OTHER_GROUP)
    round_id = _open_round(service)
    with pytest.raises(AllocationError, match="belongs to group"):
        _apply(service, round_id, tasks=("T-other",))


def test_a_harness_has_one_application_per_round(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    _apply(service, round_id)
    with pytest.raises(AllocationError, match="already applied"):
        _apply(service, round_id)


def test_applications_freeze_when_the_round_closes(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    # 冻结的申请仍在候选集合里（管理员还要裁决它），但本人已经动不了它。
    assert (
        database.read(
            "SELECT state FROM applications WHERE application_id = ?",
            (applied["application_id"],),
        )[0]["state"]
        == APPLICATION_FROZEN
    )
    assert [
        row["harness_id"] for row in service.list_candidates(round_id=round_id, task_id=TASK_1)
    ] == [MEMBER_A]
    with pytest.raises(AllocationError, match="not allowed from"):
        _apply(service, round_id, harness=MEMBER_B)


def test_update_replaces_the_task_set_and_advances_the_revision(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    seed_task(database, task_id=TASK_2)
    round_id = _open_round(service)
    applied = _apply(service, round_id)

    with pytest.raises(AllocationError, match="expected_revision"):
        service.update_application(
            application_id=applied["application_id"],
            harness_id=MEMBER_A,
            expected_revision=99,
        )
    updated = service.update_application(
        application_id=applied["application_id"],
        harness_id=MEMBER_A,
        expected_revision=1,
        reason="改主意，两个都想要",
        body="材料 v2",
        task_ids=[TASK_1, TASK_2],
    )
    assert updated["revision"] == 2
    assert sorted(
        row["task_id"]
        for row in database.read(
            "SELECT task_id FROM application_tasks WHERE application_id = ?",
            (applied["application_id"],),
        )
    ) == [TASK_1, TASK_2]
    # 材料只增不改：v1 必须还在。
    versions = database.read(
        "SELECT version FROM application_materials WHERE application_id = ?" " ORDER BY version",
        (applied["application_id"],),
    )
    assert [row["version"] for row in versions] == [1, 2]


def test_only_the_applicant_may_update_or_withdraw(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)

    with pytest.raises(AllocationError, match="belongs to"):
        service.update_application(
            application_id=applied["application_id"],
            harness_id=MEMBER_B,
            expected_revision=1,
        )
    with pytest.raises(AllocationError, match="belongs to"):
        service.withdraw_application(application_id=applied["application_id"], harness_id=MEMBER_B)


def test_withdraw_removes_the_candidate_and_blocks_being_chosen(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    closed = service.withdraw_application(
        application_id=applied["application_id"], harness_id=MEMBER_A
    )
    assert closed["state"] == APPLICATION_WITHDRAWN
    assert service.list_candidates(round_id=round_id, task_id=TASK_1) == []

    _close(service, round_id)
    with pytest.raises(AllocationError, match="withdrawn"):
        _decide(service, round_id, applied["application_id"])
    # 撤回过的申请不能再改（改口要走新申请，而新申请只能在新轮次里提）。
    with pytest.raises(AllocationError, match="withdrawn"):
        service.update_application(
            application_id=applied["application_id"],
            harness_id=MEMBER_A,
            expected_revision=2,
        )


# ------------------------------------------------------------------------ decide


def test_decide_needs_the_current_round_revision(tmp_path):
    """§39 硬规则 4：`expected_revision` 条件更新，拿着旧候选名单不许拍板。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    stale = _revision(service, round_id)
    _apply(service, round_id, harness=MEMBER_B)  # 候选集合变了 → 版本前进
    _close(service, round_id)

    with pytest.raises(AllocationError, match="expected_revision"):
        _decide(service, round_id, applied["application_id"], expected_revision=stale)
    assert database.read("SELECT COUNT(*) AS n FROM allocation_decisions")[0]["n"] == 0


def test_the_decider_must_not_compete_for_the_task(tmp_path):
    """§39 硬规则 3（回避）：管理员不得裁决自己参与竞争的任务。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id, harness=ADMIN)
    _close(service, round_id)

    with pytest.raises(AllocationError, match="回避"):
        _decide(service, round_id, applied["application_id"], decided_by=ADMIN)
    # 换一个没有利益冲突的管理员就能裁决。
    result = _decide(service, round_id, applied["application_id"], decided_by="office-admin-2")
    assert result["harness_id"] == ADMIN


def test_decide_needs_a_candidate_that_applied_for_that_task(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    seed_task(database, task_id=TASK_2)
    round_id = _open_round(service)
    applied = _apply(service, round_id, tasks=(TASK_1,))
    _close(service, round_id)
    with pytest.raises(AllocationError, match="did not apply for"):
        _decide(service, round_id, applied["application_id"], task_id=TASK_2)


def test_one_decision_per_task_per_round(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    first = _apply(service, round_id, harness=MEMBER_A)
    _apply(service, round_id, harness=MEMBER_B)
    _close(service, round_id)

    _decide(service, round_id, first["application_id"])
    with pytest.raises(AllocationError, match="already has a decision"):
        _decide(service, round_id, first["application_id"])
    assert database.read("SELECT COUNT(*) AS n FROM allocation_decisions")[0]["n"] == 1


def test_a_second_task_in_the_same_round_can_still_be_decided(tmp_path):
    """回归：一轮多任务。

    分配轮有一次裁决就进入 `decided`，如果 `allocation.decision_made` 只登记了
    `closed → decided` 这一条边，同轮第二个任务会被状态机拒绝 —— 一张分配轮就只能分
    一个任务。正文为此补了 `decided → decided` 自环（本测试钉住它）。
    """
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    seed_task(database, task_id=TASK_2)
    round_id = _open_round(service)
    one = _apply(service, round_id, harness=MEMBER_A, tasks=(TASK_1,))
    two = _apply(service, round_id, harness=MEMBER_B, tasks=(TASK_2,))
    _close(service, round_id)

    assert _decide(service, round_id, one["application_id"], task_id=TASK_1)["state"] == "decided"
    second = _decide(service, round_id, two["application_id"], task_id=TASK_2)
    assert second["state"] == "decided"
    assert second["harness_id"] == MEMBER_B
    assert _round(service, round_id)["state"] == "decided"
    assert database.read("SELECT COUNT(*) AS n FROM allocation_decisions")[0]["n"] == 2


def test_a_decided_round_can_still_defer_and_request_material(tmp_path):
    """同一个坑的另一半：进入 `decided` 后，暂缓与要求补材料不能变成死路。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    seed_task(database, task_id=TASK_2)
    round_id = _open_round(service)
    one = _apply(service, round_id, harness=MEMBER_A, tasks=(TASK_1, TASK_2))
    _close(service, round_id)
    _decide(service, round_id, one["application_id"], task_id=TASK_1)

    assert (
        service.defer_decision(
            round_id=round_id, task_id=TASK_2, decided_by=ADMIN, reason="等群主确认范围"
        )["state"]
        == "decided"
    )
    assert (
        service.request_material(
            round_id=round_id, task_id=TASK_2, requested_by=ADMIN, reason="材料缺验收口径"
        )["state"]
        == "decided"
    )
    # 暂缓没有占用裁决表，所以同一个任务之后还能真的被分配。
    decided = _decide(service, round_id, one["application_id"], task_id=TASK_2)
    assert decided["task_id"] == TASK_2


def test_defer_and_material_request_need_a_reason(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    _apply(service, round_id)
    _close(service, round_id)
    with pytest.raises(AllocationError, match="needs a reason"):
        service.defer_decision(round_id=round_id, task_id=TASK_1, decided_by=ADMIN, reason="  ")
    with pytest.raises(AllocationError, match="needs a reason"):
        service.request_material(round_id=round_id, task_id=TASK_1, requested_by=ADMIN, reason="")


# ------------------------------------------------------- invite / accept / decline


def test_deciding_creates_an_invitation_not_a_claim_authorization(tmp_path):
    """§39 硬规则 2：申请不等于领取 —— 裁决也不等于领取，还要本人 `accept`。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    decided = _decide(service, round_id, applied["application_id"])

    assert decided["allocation_state"] == ASSIGNMENT_INVITED
    assert service.claim_authorization(task_id=TASK_1, harness_id=MEMBER_A) is None
    assert (
        database.read("SELECT COUNT(*) AS n FROM task_attempts")[0]["n"] == 0
    ), "裁决不许产生 attempt"


def test_accept_is_the_named_harness_own_act(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]

    with pytest.raises(AllocationError, match="names"):
        service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_B)
    accepted = service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)
    assert accepted["allocation_state"] == ASSIGNMENT_ACCEPTED

    authorization = service.claim_authorization(task_id=TASK_1, harness_id=MEMBER_A)
    assert authorization is not None
    assert authorization["allocation_id"] == allocation_id
    assert authorization["revision"] == 2
    assert authorization["claimed_at"] is None
    # 别的 harness 拿不到这份授权。
    assert service.claim_authorization(task_id=TASK_1, harness_id=MEMBER_B) is None


def test_only_one_accepted_allocation_per_task(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    one = _apply(service, round_id, harness=MEMBER_A)
    # 第二位申请者：该轮里确实有竞争，但一个任务最终只能有一份被接单。
    _apply(service, round_id, harness=MEMBER_B)
    _close(service, round_id)
    first = _decide(service, round_id, one["application_id"])["allocation_id"]
    service.accept_assignment(allocation_id=first, harness_id=MEMBER_A)

    # 同一个任务：管理员不能再发第二份邀请并接单 —— 部分唯一索引挡住。
    invited = service.invite(
        round_id=round_id, task_id=TASK_1, harness_id=MEMBER_C, invited_by=ADMIN
    )["allocation_id"]
    events_before = len(_events(database))
    with pytest.raises(AllocationError, match="already has an accepted allocation"):
        service.accept_assignment(allocation_id=invited, harness_id=MEMBER_C)
    # 事件与投影同生同死：撞唯一索引的那次 accept 不许留下一半记录（§115）。
    assert len(_events(database)) == events_before
    assert (
        database.read("SELECT state FROM allocations WHERE allocation_id = ?", (invited,))[0][
            "state"
        ]
        == ASSIGNMENT_INVITED
    )


def test_decline_is_terminal_for_that_invitation(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]

    declined = service.decline_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)
    assert declined["allocation_state"] == ASSIGNMENT_DECLINED
    with pytest.raises(AllocationError, match="only an invited assignment"):
        service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)
    assert service.claim_authorization(task_id=TASK_1, harness_id=MEMBER_A) is None


def test_invite_is_for_non_applicants_and_works_on_a_closed_round(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    _apply(service, round_id, harness=MEMBER_A)
    _close(service, round_id)

    # 申请人走 decide，不走 invite —— 两条路分开，事件流里一眼可辨。
    with pytest.raises(AllocationError, match="use decide"):
        service.invite(round_id=round_id, task_id=TASK_1, harness_id=MEMBER_A, invited_by=ADMIN)
    # 没人申请的任务也要能直接被邀请（`closed/decided --assignment.invited--> decided`）。
    invited = service.invite(
        round_id=round_id,
        task_id=TASK_1,
        harness_id=MEMBER_B,
        invited_by=ADMIN,
        reason="只有他熟悉这块",
    )
    assert invited["allocation_state"] == ASSIGNMENT_INVITED
    assert _round(service, round_id)["state"] == "decided"
    service.accept_assignment(allocation_id=invited["allocation_id"], harness_id=MEMBER_B)
    assert service.claim_authorization(task_id=TASK_1, harness_id=MEMBER_B) is not None


def test_a_duplicate_live_invitation_is_refused(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    _close(service, round_id)
    service.invite(round_id=round_id, task_id=TASK_1, harness_id=MEMBER_B, invited_by=ADMIN)
    with pytest.raises(AllocationError, match="already has a live assignment"):
        service.invite(round_id=round_id, task_id=TASK_1, harness_id=MEMBER_B, invited_by=ADMIN)


# ------------------------------------------------------------------------ revoke


def test_revoke_needs_a_reason_and_a_matching_revision(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]

    with pytest.raises(AllocationError, match="needs a reason"):
        service.revoke_assignment(
            allocation_id=allocation_id, expected_revision=1, revoked_by=ADMIN, reason=" "
        )
    with pytest.raises(AllocationError, match="expected_revision"):
        service.revoke_assignment(
            allocation_id=allocation_id,
            expected_revision=99,
            revoked_by=ADMIN,
            reason="换人",
        )


def test_revoke_covers_an_accepted_but_unclaimed_allocation(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]
    service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)

    assert (
        service.revoke_assignment(
            allocation_id=allocation_id,
            expected_revision=2,
            revoked_by=ADMIN,
            reason="任务范围改了",
        )["allocation_state"]
        == ASSIGNMENT_REVOKED
    )
    assert service.claim_authorization(task_id=TASK_1, harness_id=MEMBER_A) is None
    assert (
        database.read("SELECT state FROM allocations WHERE allocation_id = ?", (allocation_id,))[0][
            "state"
        ]
        == ASSIGNMENT_REVOKED
    )


def test_a_claimed_allocation_cannot_be_revoked(tmp_path):
    """§39 只允许撤销"尚未 Claim 的分配"：已经开工的活撤销会留下孤儿工作区。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]
    service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)
    service.mark_claimed(allocation_id=allocation_id, claim_id="claim-1")

    with pytest.raises(AllocationError, match="already claimed"):
        service.revoke_assignment(
            allocation_id=allocation_id, expected_revision=2, revoked_by=ADMIN, reason="反悔"
        )


def test_mark_claimed_is_idempotent_but_refuses_a_second_claim(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]

    with pytest.raises(AllocationError, match="not accepted"):
        service.mark_claimed(allocation_id=allocation_id, claim_id="claim-1")
    service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)
    service.mark_claimed(allocation_id=allocation_id, claim_id="claim-1")
    service.mark_claimed(allocation_id=allocation_id, claim_id="claim-1")
    with pytest.raises(AllocationError, match="already claimed"):
        service.mark_claimed(allocation_id=allocation_id, claim_id="claim-2")


# ------------------------------------------------------------ 封闭规则与不越界


def test_the_whole_allocation_flow_never_touches_the_task(tmp_path):
    """§39 硬规则 2/6：分配只决定"谁有权 Claim"，不写 `tasks.status`。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    before = database.read(
        "SELECT status, attempt_no, current_attempt_id, owner FROM tasks WHERE task_id = ?",
        (TASK_1,),
    )[0]
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    allocation_id = _decide(service, round_id, applied["application_id"])["allocation_id"]
    service.accept_assignment(allocation_id=allocation_id, harness_id=MEMBER_A)
    service.mark_claimed(allocation_id=allocation_id, claim_id="claim-1")

    after = database.read(
        "SELECT status, attempt_no, current_attempt_id, owner FROM tasks WHERE task_id = ?",
        (TASK_1,),
    )[0]
    assert dict(after) == dict(before)
    assert database.read("SELECT COUNT(*) AS n FROM task_attempts")[0]["n"] == 0
    assert database.read("SELECT COUNT(*) AS n FROM workspaces")[0]["n"] == 0
    assert database.read("SELECT COUNT(*) AS n FROM resource_leases")[0]["n"] == 0


def test_every_written_event_is_a_registered_allocation_event(tmp_path):
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    service.update_application(
        application_id=applied["application_id"],
        harness_id=MEMBER_A,
        expected_revision=1,
        body="补材料",
    )
    service.withdraw_application(application_id=applied["application_id"], harness_id=MEMBER_A)
    _close(service, round_id)
    service.defer_decision(round_id=round_id, task_id=TASK_1, decided_by=ADMIN, reason="先看材料")
    service.request_material(
        round_id=round_id, task_id=TASK_1, requested_by=ADMIN, reason="材料不够"
    )
    invited = service.invite(
        round_id=round_id, task_id=TASK_1, harness_id=MEMBER_B, invited_by=ADMIN
    )
    service.decline_assignment(allocation_id=invited["allocation_id"], harness_id=MEMBER_B)
    # 撤销是另一份分配：被拒的邀请已经是终态，撤销只覆盖 invited/accepted。
    second = service.invite(
        round_id=round_id, task_id=TASK_1, harness_id=MEMBER_C, invited_by=ADMIN
    )
    service.revoke_assignment(
        allocation_id=second["allocation_id"],
        expected_revision=1,
        revoked_by=ADMIN,
        reason="还是让他做",
    )

    registered = {event.value for event in AllocationEvent}
    written = set(_event_names(database))
    assert written <= registered, f"未登记事件：{sorted(written - registered)}"
    # 每条派上用场的边都真的写出来了，不是只改投影。
    assert {
        AllocationEvent.APPLICATION_SUBMITTED.value,
        AllocationEvent.APPLICATION_UPDATED.value,
        AllocationEvent.APPLICATION_WITHDRAWN.value,
        AllocationEvent.ALLOCATION_CLOSED.value,
        AllocationEvent.ALLOCATION_DECISION_DEFERRED.value,
        AllocationEvent.ALLOCATION_MATERIAL_REQUESTED.value,
        AllocationEvent.ASSIGNMENT_INVITED.value,
        AllocationEvent.ASSIGNMENT_DECLINED.value,
        AllocationEvent.ASSIGNMENT_REVOKED.value,
    } <= written


def test_the_round_revision_advances_on_every_edge(tmp_path):
    """版本前进是乐观锁的地基：任何一条边之后，旧版本号都不该再匹配。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    seen = [_revision(service, round_id)]
    _apply(service, round_id)
    seen.append(_revision(service, round_id))
    _close(service, round_id)
    seen.append(_revision(service, round_id))
    assert seen == sorted(set(seen)), f"版本没有严格前进：{seen}"


def test_the_same_task_can_be_re_decided_in_a_new_round(tmp_path):
    """换方案 → 新快照 → 新分配轮：旧裁决不该把新轮次也堵住。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    applied = _apply(service, round_id)
    _close(service, round_id)
    _decide(service, round_id, applied["application_id"])

    _reach_completed(service, snapshot="snap-0002")
    second = service.open_allocation_round(
        group_id=DEFAULT_GROUP, plan_snapshot_id="snap-0002", opened_by=ADMIN
    )["round_id"]
    assert second != round_id
    re_applied = _apply(service, second, harness=MEMBER_B, body="新方案下我的做法")
    _close(service, second)
    assert _decide(service, second, re_applied["application_id"])["harness_id"] == MEMBER_B
    assert database.read("SELECT COUNT(*) AS n FROM allocation_decisions")[0]["n"] == 2


def test_plan_and_snapshot_are_recorded_for_replay(tmp_path):
    """`allocation_decisions.compared_json` 记下"当时桌上有谁"，供回放裁决依据。"""
    database, service = _service(tmp_path)
    seed_task(database, task_id=TASK_1)
    round_id = _open_round(service)
    one = _apply(service, round_id, harness=MEMBER_A)
    _apply(service, round_id, harness=MEMBER_B)
    _close(service, round_id)
    _decide(service, round_id, one["application_id"])

    compared = database.read("SELECT compared_json, outcome FROM allocation_decisions")[0]
    assert MEMBER_A in compared["compared_json"] and MEMBER_B in compared["compared_json"]
    assert compared["outcome"] == "assigned"
    # 裁决落在哪份方案上：轮次记着方案快照，裁决记着当时桌上有谁 —— 两者一起可回放。
    assert _round(service, round_id)["plan_snapshot_id"] == SNAPSHOT
    assert PLAN  # noqa: B018 - 方案 id 由调用方持有，此处只提醒裁决与方案同轮可追溯
