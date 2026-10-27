"""同厂商审阅通道：人开一次口子，审阅记录上写明是谁开的。

默认仍是 ``cross_provider``。这条通道解决的是"平台上没有一个与持有者不同厂商
的窗口能审这一条"的处境：管理员反复被 ``review.cross_provider_required`` 拒，
任务就停在 ``awaiting_review``，而群主不知道要去开。

三条边界在这里钉住：

* 没人开通道时，同厂商照样被拒（默认不因为加了这条通道而松动）；
* 开了之后放行，但审阅记录上必须留下"谁开的、依据哪条决定"；
* 只有人在真的有活在等审时才能开，且一条任务只能开一次。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.api.app import create_app
from app.human.service import HumanOpsService
from app.tasks.service import Conflict
from tests.merge_support import (
    HARNESS,
    approve,
    build_chain,
    register_harness,
    status_of,
)

LOOPBACK = "http://127.0.0.1"
OPERATOR = "owner-1"


def _grant(chain, *, reason: str = "") -> dict[str, object]:
    return HumanOpsService(chain.database).grant_review_same_provider(
        task_id=chain.task_id, decided_by=OPERATOR, reason=reason
    )


def _review_row(chain, review_id: str):
    return chain.database.read(
        "SELECT provider_confirmed_by, provider_override_decision_id"
        " FROM reviews WHERE review_id = ?",
        (review_id,),
    )[0]


def test_without_a_human_channel_the_same_provider_is_still_refused(tmp_path):
    """加了通道不等于松了口子：没人开时行为与之前一模一样。"""
    chain = build_chain(tmp_path)
    register_harness(chain.database, "claude-1", provider="openai")  # 与 harness-1 同厂商

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            reviewer_harness_id="claude-1",
        )

    assert caught.value.code == "review.cross_provider_required"


def test_the_channel_lets_it_through_and_records_who_opened_it(tmp_path):
    """开了之后能审，而且两条记录面（审阅行、事件）都留下是谁开的。"""
    chain = build_chain(tmp_path)
    register_harness(chain.database, "claude-1", provider="openai")
    grant = _grant(chain, reason="平台上没有与 openai 不同厂商的空闲窗口")

    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id="claude-1",
    )
    row = _review_row(chain, str(claimed["review_id"]))
    assert row["provider_confirmed_by"] == OPERATOR
    assert row["provider_override_decision_id"] == grant["decision_id"]

    chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id="claude-1",
        verdict="approve",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[],
        review_id=str(claimed["review_id"]),
    )

    assert status_of(chain) == "approved"
    (event,) = chain.database.read(
        "SELECT payload_json FROM task_events WHERE aggregate_id = ? AND event_name = 'approve'",
        (chain.task_id,),
    )
    payload = json.loads(event["payload_json"])
    assert payload["provider_confirmed_by"] == OPERATOR
    assert payload["provider_override_decision_id"] == grant["decision_id"]


def test_the_read_face_says_it_was_same_provider(tmp_path):
    """只读面上必须能看出这是同厂商放行的，不能与跨厂商审阅长得一样。"""
    chain = build_chain(tmp_path)
    register_harness(chain.database, "claude-1", provider="openai")
    grant = _grant(chain)
    approve(chain, reviewer="claude-1")

    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)
    response = client.get(f"/api/v1/tasks/{chain.task_id}/reviews")

    assert response.status_code == 200, response.text
    (row,) = response.json()["reviews"]
    assert row["same_provider"] is True
    assert row["provider_confirmed_by"] == OPERATOR
    assert row["provider_override_decision_id"] == grant["decision_id"]


def test_a_cross_provider_review_is_not_marked_as_waived(tmp_path):
    """跨厂商审阅不带任何豁免痕迹 —— 两个口径不能混。"""
    chain = build_chain(tmp_path)
    approve(chain)

    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)
    (row,) = client.get(f"/api/v1/tasks/{chain.task_id}/reviews").json()["reviews"]

    assert row["same_provider"] is False
    assert row["provider_confirmed_by"] is None
    assert row["provider_override_decision_id"] is None


def test_the_channel_only_opens_while_a_review_is_pending(tmp_path):
    """不能提前给所有活开口子：还没活等审时开不出来。"""
    chain = build_chain(tmp_path)
    approve(chain)  # 已经是 approved，没有在等审的东西
    assert status_of(chain) == "approved"

    with pytest.raises(Conflict) as caught:
        _grant(chain)

    assert caught.value.code == "review.same_provider_not_pending"


def test_the_channel_opens_once_per_task(tmp_path):
    """一条任务只开一次：重复开会让"谁批的"变得没有唯一答案。"""
    chain = build_chain(tmp_path)
    _grant(chain)

    with pytest.raises(Conflict) as caught:
        _grant(chain, reason="再开一次")

    assert caught.value.code == "review.same_provider_already_open"


def test_the_grant_is_a_decision_row_anchored_on_the_task_history(tmp_path):
    """豁免落在决策表上（谁、为什么），并锚在任务历史的时间点上。"""
    chain = build_chain(tmp_path)
    grant = _grant(chain, reason="只有管理员窗口能判，但它与持有者同厂商")

    row = chain.database.read(
        "SELECT * FROM task_decisions WHERE decision_id = ?", (grant["decision_id"],)
    )[0]
    assert row["task_id"] == chain.task_id
    assert row["scope"] == "review_same_provider"
    assert row["actor"] == OPERATOR
    assert row["answer"] == "只有管理员窗口能判，但它与持有者同厂商"

    last_seq = chain.database.read(
        "SELECT COALESCE(MAX(seq), 0) AS seq FROM task_events WHERE aggregate_id = ?",
        (chain.task_id,),
    )[0]["seq"]
    assert int(row["decided_seq"]) == int(last_seq)
    # 持有人身份没有被这条豁免改掉：任务还在等审，只是现在允许同厂商来审。
    assert status_of(chain) == "awaiting_review"


def test_the_registry_still_has_to_know_both_sides(tmp_path):
    """豁免只放开"同厂商"，不放开"不知道是谁"：注册表里没有的一方照样被拒。"""
    chain = build_chain(tmp_path)
    _grant(chain)

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            reviewer_harness_id="ghost-1",
        )

    assert caught.value.code == "review.provider_unknown"


def test_a_self_review_is_refused_even_with_the_channel_open(tmp_path):
    """通道放的是"同厂商"，不是"我自己"：持有者本人仍然不能审自己。"""
    chain = build_chain(tmp_path)
    _grant(chain)

    with pytest.raises(Conflict) as caught:
        chain.service.review_claim(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            reviewer_harness_id=HARNESS,
        )

    assert caught.value.code == "review.self_review_forbidden"
