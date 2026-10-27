"""A ``request_changes`` verdict must reach the reworking harness (regression).

``submit_review`` stored the findings as a hash and dropped the text, and
``read_context`` served only the plan spec. A ``changes_requested`` task is
claimable again (R10 §10 ``claim_for_revision``), so the rework re-ran the
identical goal: the reviewer could not read back what it had written, the
member could not learn what to fix, and the loop could only burn attempt
budget. The findings are recorded on the review row now, served by
``read_context``, and appended to the rework goal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.adapter.formal_worker import build_goal
from app.api.app import create_app
from app.tasks.service import Conflict
from tests.merge_support import REVIEWER, build_chain, status_of

#: The app rejects non-loopback Host headers, so the client needs a loopback URL.
LOOPBACK = "http://127.0.0.1"
FINDINGS = [
    {"severity": "note", "message": "docs/lane.md 缺少验收命令的实际输出"},
    {"severity": "note", "message": "自审记录未写明已知限制"},
]


def _request_changes(chain, findings):
    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
    )
    chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="request_changes",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=findings,
        review_id=str(claimed["review_id"]),
    )
    return str(claimed["review_id"])


def _context(chain):
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)
    response = client.get(f"/api/v1/adapter/context/{chain.task_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_reviews_read_face_names_the_reviewer_and_the_verdict(tmp_path):
    """「谁审的、结论是什么」必须能从只读面上读到。

    这一单是最自然的问题（交付物是谁审的），而任务行里只有 `last_review_id`
    一个指针：平台记着审阅记录，助理却只能答"未记上"。补的是读的口子，不是
    新的判定 —— 结论仍是审阅人写的，这里只负责如实转述。
    """
    chain = build_chain(tmp_path)
    review_id = _request_changes(chain, FINDINGS)
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)

    response = client.get(f"/api/v1/tasks/{chain.task_id}/reviews")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["task_id"] == chain.task_id
    (row,) = payload["reviews"]
    assert row["review_id"] == review_id
    assert row["reviewer_harness_id"] == REVIEWER
    assert row["reviewer_provider"] == "anthropic"
    assert row["verdict"] == "request_changes"
    assert row["status"] == "completed"
    # 意见正文要原样带出来（返工的人读的就是它），不是只给一个哈希。
    assert [item["message"] for item in row["findings"]] == [
        "docs/lane.md 缺少验收命令的实际输出",
        "自审记录未写明已知限制",
    ]
    assert row["reviewed_commit"] == chain.head_commit


def test_a_task_with_no_review_answers_an_empty_list_not_a_guess(tmp_path):
    """没有审阅记录时说"没有"，不能说成"审阅通过"。"""
    chain = build_chain(tmp_path)
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)

    payload = client.get(f"/api/v1/tasks/{chain.task_id}/reviews").json()

    assert payload["reviews"] == []
    assert "不等于审阅通过" in payload["note"]


def test_reading_reviews_of_an_unknown_task_is_a_404(tmp_path):
    """与邻居端点同一口径：不认识的任务就是 404，不返回空清单。"""
    chain = build_chain(tmp_path)
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)

    response = client.get("/api/v1/tasks/FD-not-a-task/reviews")

    assert response.status_code == 404


def test_the_review_records_the_reviewers_registry_provider(tmp_path):
    """The audit trail must not say "unknown" when the registry knows (§6).

    Live evidence: ``rv-1fb66923834e4b4eb0ec10fa1db8f209`` was the administrator
    harness ``office-admin`` (provider ``qoder``) and both insert paths wrote
    ``reviewer_provider = "unknown"``, so a cross-provider review could not be
    reconstructed from its own row.
    """
    chain = build_chain(tmp_path)
    review_id = _request_changes(chain, FINDINGS)
    (row,) = chain.database.read(
        "SELECT reviewer_harness_id, reviewer_provider FROM reviews WHERE review_id = ?",
        (review_id,),
    )
    assert row["reviewer_harness_id"] == REVIEWER
    assert row["reviewer_provider"] == "anthropic", "the registry's provider, not a literal"


def test_an_unknown_harness_provider_degrades_to_unknown_not_invented(tmp_path):
    """Fail closed in the helper: no registry row means "unknown".

    ``review_claim`` refuses an unregistered reviewer under ``cross_provider``
    before this helper runs, so the degraded branch is asserted directly rather
    than through a path the policy makes unreachable.
    """
    from app.tasks.service import _reviewer_provider

    chain = build_chain(tmp_path)
    with chain.database.transaction() as conn:
        assert (
            _reviewer_provider(
                conn, reviewer_harness_id="never-registered", reviewer_kind="harness"
            )
            == "unknown"
        )
        assert (
            _reviewer_provider(conn, reviewer_harness_id=REVIEWER, reviewer_kind="harness")
            == "anthropic"
        )
        assert (
            _reviewer_provider(conn, reviewer_harness_id=REVIEWER, reviewer_kind="human") == "human"
        )


def test_a_human_review_records_human(tmp_path):
    chain = build_chain(tmp_path)
    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id="office-operator",
        reviewer_kind="human",
    )
    (row,) = chain.database.read(
        "SELECT reviewer_provider FROM reviews WHERE review_id = ?",
        (str(claimed["review_id"]),),
    )
    assert row["reviewer_provider"] == "human"


def test_request_changes_records_the_findings_text(tmp_path):
    chain = build_chain(tmp_path)
    _request_changes(chain, FINDINGS)
    assert status_of(chain) == "changes_requested"

    (row,) = chain.database.read(
        "SELECT verdict, findings_hash, findings_json FROM reviews WHERE task_id = ?",
        (chain.task_id,),
    )
    assert row["verdict"] == "request_changes"
    assert str(row["findings_hash"]).startswith("sha256:")
    assert "缺少验收命令的实际输出" in str(
        row["findings_json"]
    ), "a hash cannot be acted on; the reviewer's text has to survive the submit"


def test_read_context_serves_the_latest_review(tmp_path):
    chain = build_chain(tmp_path)
    _request_changes(chain, FINDINGS)

    context = _context(chain)

    assert context["review"]["verdict"] == "request_changes"
    assert context["review"]["reviewer_harness_id"] == REVIEWER
    assert context["review"]["findings"] == FINDINGS


def test_read_context_reports_no_review_before_the_first_verdict(tmp_path):
    chain = build_chain(tmp_path)

    assert _context(chain)["review"] is None


def test_a_completed_review_is_anchored_to_its_event(tmp_path):
    """``completed_seq`` is what makes "the latest review" well defined."""
    chain = build_chain(tmp_path)
    _request_changes(chain, FINDINGS)

    (row,) = chain.database.read(
        "SELECT completed_seq FROM reviews WHERE task_id = ?", (chain.task_id,)
    )
    assert row["completed_seq"] is not None, (
        "the review row had no completion order at all, so read_context could "
        "not tell the newest verdict from an older one"
    )
    (event,) = chain.database.read(
        "SELECT event_name FROM task_events WHERE seq = ?", (row["completed_seq"],)
    )
    assert event["event_name"] == "request_changes"


def test_the_rework_goal_carries_the_reviewer_findings(tmp_path):
    chain = build_chain(tmp_path)
    _request_changes(chain, FINDINGS)

    goal = build_goal({"id": chain.task_id}, harness_id="qoder", review=_context(chain)["review"])

    assert "REWORK" in goal
    assert "缺少验收命令的实际输出" in goal, "the member has to be able to act on the finding"
    assert "自审记录未写明已知限制" in goal


def test_an_approving_review_asks_for_no_rework(tmp_path):
    chain = build_chain(tmp_path)
    claimed = chain.service.review_claim(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
    )
    chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="approve",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[{"severity": "note", "message": "看起来可以"}],
        review_id=str(claimed["review_id"]),
    )

    goal = build_goal({"id": chain.task_id}, harness_id="qoder", review=_context(chain)["review"])

    assert "REWORK" not in goal, "approval must not be turned into a rework instruction"


def test_approval_requires_each_acceptance_contract_to_be_addressed(tmp_path):
    chain = build_chain(
        tmp_path,
        acceptance=[
            {"id": "A-1", "check": "cmd", "evidence_source": "tests", "description": "a"},
            {"id": "A-2", "check": "human", "description": "b"},
        ],
    )
    claimed = chain.service.review_claim(
        task_id=chain.task_id, attempt_id=chain.attempt_id, reviewer_harness_id=REVIEWER
    )
    with pytest.raises(Conflict) as caught:
        chain.service.submit_review(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            reviewer_harness_id=REVIEWER,
            verdict="approve",
            reviewed_commit=chain.head_commit,
            review_revision=1,
            findings=[{"contract_id": "A-1", "message": "checked"}],
            review_id=str(claimed["review_id"]),
        )
    assert getattr(caught.value, "code", None) == "review.contract_missing"


def test_approval_accepts_explicit_no_evidence_for_a_contract(tmp_path):
    chain = build_chain(
        tmp_path,
        acceptance=[
            {"id": "A-1", "check": "human", "description": "human check"},
            {"id": "A-2", "check": "human", "description": "another check"},
        ],
    )
    claimed = chain.service.review_claim(
        task_id=chain.task_id, attempt_id=chain.attempt_id, reviewer_harness_id=REVIEWER
    )
    result = chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="approve",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[
            {"contract_id": "A-1", "message": "checked"},
            {"contract_id": "A-2", "no_evidence": True, "message": "仍需人工查验"},
        ],
        review_id=str(claimed["review_id"]),
    )
    assert result["verdict"] == "approve"


def test_approval_rejects_unknown_contract_ids(tmp_path):
    chain = build_chain(
        tmp_path,
        acceptance=[{"id": "A-1", "check": "human", "description": "human check"}],
    )
    claimed = chain.service.review_claim(
        task_id=chain.task_id, attempt_id=chain.attempt_id, reviewer_harness_id=REVIEWER
    )
    with pytest.raises(Conflict) as caught:
        chain.service.submit_review(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            reviewer_harness_id=REVIEWER,
            verdict="approve",
            reviewed_commit=chain.head_commit,
            review_revision=1,
            findings=[
                {"contract_id": "A-1", "message": "checked"},
                {"contract_id": "A-unknown", "message": "not in the plan"},
            ],
            review_id=str(claimed["review_id"]),
        )
    assert caught.value.code == "review.contract_unknown"


def test_request_changes_does_not_require_contract_coverage(tmp_path):
    chain = build_chain(
        tmp_path,
        acceptance=[{"id": "A-1", "check": "human", "description": "human check"}],
    )
    claimed = chain.service.review_claim(
        task_id=chain.task_id, attempt_id=chain.attempt_id, reviewer_harness_id=REVIEWER
    )
    result = chain.service.submit_review(
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        reviewer_harness_id=REVIEWER,
        verdict="request_changes",
        reviewed_commit=chain.head_commit,
        review_revision=1,
        findings=[{"message": "please add evidence"}],
        review_id=str(claimed["review_id"]),
    )
    assert result["verdict"] == "request_changes"
