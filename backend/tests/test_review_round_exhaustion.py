"""返修轮次用完 = 停线问人，不是把评审结论挡在门外（2026-10-31）。

以前 `submit_review` 在轮次用完时直接 409（`review.round_exhausted`）。后果：
评审方已经把「这条交付物不行」看明白了、也写下了具体问题，平台却**拒收这次评审**
—— 结论没落库，任务停在 `awaiting_review`，人从事件流里只看到一次失败，看不出
到底发生了什么。

现在的行为：结论照记，然后走 R10 §10 本来就有的 `review_round_exhausted` 边，把
任务转到 `escalated`（=需要人处理）。人从「需要你处理」里决定继续、换人还是收兵。
"""

from __future__ import annotations

import json

from app.config import settings
from app.spec import TaskStatus
from tests.merge_support import REVIEWER, build_chain


def _status(chain) -> TaskStatus:
    rows = chain.database.read(
        "SELECT status FROM tasks WHERE task_id = ?", (chain.task_id,)
    )
    return TaskStatus(str(rows[0]["status"]))


def test_exhausted_rounds_record_the_review_then_stop_the_line(tmp_path, monkeypatch):
    # 轮次上限 0：第一次 request_changes 就超了。
    monkeypatch.setattr(settings, "max_review_rounds", 0)
    chain = build_chain(tmp_path)
    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
    )

    result = chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="request_changes",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[{"severity": "major", "message": "docs/lane.md 与验收不符"}],
        review_id=str(claimed["review_id"]),
    )

    # 1. 评审没有被拒收：结论、事件都在。
    assert result["review_id"] == str(claimed["review_id"])
    assert result["verdict"] == "request_changes"
    stored = chain.database.read(
        "SELECT verdict, completed_seq FROM reviews WHERE review_id = ?", (result["review_id"],)
    )
    assert stored and stored[0]["verdict"] == "request_changes"
    assert stored[0]["completed_seq"] is not None

    # 2. 平台说清「下一步是人」，不是让人以为还能再返修。
    assert result["review_rounds_exhausted"] is True
    assert result["next"] == TaskStatus.ESCALATED.value

    # 3. 任务停在需要人处理的那一格，而不是继续挂在等审。
    assert _status(chain) is TaskStatus.ESCALATED

    # 4. 停线这件事本身也要在事件流里留下证据（谁停的、上限多少）。
    events = chain.database.read(
        "SELECT event_name, payload_json FROM task_events"
        " WHERE aggregate_id = ? AND event_name = 'review_round_exhausted'",
        (chain.task_id,),
    )
    assert len(events) == 1
    payload = json.loads(str(events[0]["payload_json"]))
    assert payload["max_review_rounds"] == 0
    assert payload["review_id"] == str(claimed["review_id"])


def test_a_review_inside_the_budget_still_asks_for_changes(tmp_path, monkeypatch):
    """对照组：轮次没用完时，行为一个字都没变（回到 changes_requested）。"""
    monkeypatch.setattr(settings, "max_review_rounds", 5)
    chain = build_chain(tmp_path)
    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
    )

    result = chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="request_changes",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[{"severity": "note", "message": "补一句说明"}],
        review_id=str(claimed["review_id"]),
    )

    assert result["review_rounds_exhausted"] is False
    assert result["next"] == TaskStatus.CHANGES_REQUESTED.value
    assert _status(chain) is TaskStatus.CHANGES_REQUESTED
