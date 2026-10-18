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

from fastapi.testclient import TestClient

from app.adapter.formal_worker import build_goal
from app.api.app import create_app
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
