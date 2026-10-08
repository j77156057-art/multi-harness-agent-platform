"""CORE-REWORK-001 (rework-loop): the ``claim_for_revision`` path.

Before this card a verify failure parked a task in ``changes_requested``
forever: re-claiming hit ALREADY_OWNED because ``current_attempt_id`` stays
set (the e2e drill's F1). R10 §10 L449/L523 defines the way out -- a fresh
attempt, epoch and lease under the same checks as a first claim.

Pinned here: the revived loop end to end (fail -> rework -> merge), replay
instead of burning a third attempt, the two-harness race, the attempt-budget
and resource guards, the budget reservation linkage, and the pull-model
visibility of a rework.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.claim.service import (
    OUTCOME_CLAIMED,
    OUTCOME_REJECTED,
    ClaimRequest,
    ClaimService,
    RejectCode,
)
from app.config import settings
from app.git.publish import PublishProxy
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.merge.worker import MergeWorker
from app.tasks.service import SchedulerService
from app.verify.worker import VerifyWorker
from tests.claim_support import count, hold_resource, make_database, seed_project, seed_task
from tests.git_support import commit_in
from tests.merge_support import CHECK_FLAG, PROFILE, build_chain, policy

LOOPBACK = "http://127.0.0.1"
FAIL_COMMAND = ["python", "-c", "raise SystemExit(1)"]


def _status(database) -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])


def _event_names(database) -> list[str]:
    rows = database.read(
        "SELECT event_name FROM task_events WHERE aggregate_id = 'T-1' ORDER BY seq"
    )
    return [str(row["event_name"]) for row in rows]


def _failed_chain(tmp_path: Path):
    """A first attempt whose verify fails -> the task parks in changes_requested."""
    chain = build_chain(tmp_path, verify_command=FAIL_COMMAND)
    assert _status(chain.database) == "changes_requested"
    return chain


def _rework_attempt(
    chain, tmp_path: Path, *, request_id: str = "cr-rework", harness: str = "harness-2"
):
    """Claim the revision, work it in a fresh worktree, submit, verify green."""
    item = (
        ClaimService(chain.database)
        .claim(ClaimRequest(claim_request_id=request_id, harness_id=harness, task_ids=("T-1",)))
        .items[0]
    )
    assert item.outcome == OUTCOME_CLAIMED, item
    chain.service.start_task(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        group_short="g1",
        worktrees=chain.manager,
    )
    worktree = chain.manager.workspace_for_attempt(str(item.attempt_id))
    assert worktree is not None
    head = commit_in(worktree, filename="flag.txt", content="ok\n", message="fix: flag")
    PublishProxy(chain.database, chain.repo).publish_task_branch(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        branch=worktree.branch,
        commit=head,
        fencing_epoch=int(item.fencing_epoch),
        idempotency_key=f"pub-{request_id}",
    )
    chain.service.submit_result(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        completion={"commit": head},
    )
    VerifyWorker(
        chain.database,
        chain.repo,
        policy(command=CHECK_FLAG),
        overlay_root=tmp_path / f"ov-{request_id}",
        evidence_root=tmp_path / f"ev-{request_id}",
    ).run(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        commit=head,
        base_commit=chain.base_commit,
        verify_profile=PROFILE,
        fencing_epoch=int(item.fencing_epoch),
    )
    return item, worktree, head


def _review_and_merge(chain, tmp_path: Path, *, item, head: str, branch: str) -> None:
    chain.service.review_claim(
        task_id="T-1", attempt_id=str(item.attempt_id), reviewer_harness_id="reviewer-2"
    )
    chain.service.submit_review(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        reviewer_harness_id="reviewer-2",
        verdict="approve",
        reviewed_commit=head,
        review_revision=1,
        findings=[],
    )
    outcome = MergeWorker(
        chain.database,
        chain.repo,
        policy=policy(command=CHECK_FLAG),
        overlay_root=tmp_path / "merge-ov",
    ).merge(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        commit=head,
        branch=branch,
    )
    assert outcome.status == "merged", outcome.detail


# ------------------------------------------------------------ the revived loop


def test_a_verify_failure_is_no_longer_a_dead_end(tmp_path):
    chain = _failed_chain(tmp_path)
    assert "verify_code_failed" in _event_names(chain.database)

    item, worktree, head = _rework_attempt(chain, tmp_path)

    assert item.attempt_no == 2
    assert item.fencing_epoch == 2, "the revision advances the epoch exactly once"
    assert item.attempt_id == "T-1.a2"
    assert item.lease_token, "the revision grants a fresh development lease"
    events = _event_names(chain.database)
    assert "claim_for_revision" in events
    payload = json.loads(
        str(
            chain.database.read(
                "SELECT payload_json FROM task_events WHERE event_name = 'claim_for_revision'"
            )[0]["payload_json"]
        )
    )
    assert payload["revised_from"] == "T-1.a1"
    assert payload["attempt_id"] == "T-1.a2"
    # The old attempt stays readable history; the new one is a separate row.
    assert count(chain.database, "SELECT COUNT(*) FROM task_attempts WHERE task_id = 'T-1'") == 2

    assert _status(chain.database) == "awaiting_review"
    _review_and_merge(chain, tmp_path, item=item, head=head, branch=worktree.branch)
    assert _status(chain.database) == "merged"


def test_the_revision_claim_replays_instead_of_burning_a_third_attempt(tmp_path):
    chain = _failed_chain(tmp_path)
    first = _rework_attempt(chain, tmp_path)

    replay = (
        ClaimService(chain.database)
        .claim(
            ClaimRequest(claim_request_id="cr-rework", harness_id="harness-2", task_ids=("T-1",))
        )
        .items[0]
    )

    assert replay.replayed is True
    assert replay.attempt_id == first[0].attempt_id
    assert (
        count(chain.database, "SELECT COUNT(*) FROM task_attempts WHERE task_id = 'T-1'") == 2
    ), "a replay must not create a third attempt"


def test_two_harnesses_racing_the_rework_exactly_one_wins(tmp_path):
    chain = _failed_chain(tmp_path)
    barrier = threading.Barrier(2)

    def attempt(request_id: str) -> object:
        service = ClaimService(chain.database)
        barrier.wait(timeout=10)
        try:
            return service.claim(
                ClaimRequest(
                    claim_request_id=request_id,
                    harness_id=f"harness-{request_id}",
                    task_ids=("T-1",),
                )
            )
        except Exception as exc:  # pragma: no cover - surfaced by the assertion
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, rid) for rid in ("cr-r1", "cr-r2")]
        results = [future.result(timeout=30) for future in futures]

    for result in results:
        assert not isinstance(result, Exception), f"a racer raised: {result!r}"
    winners = [r for r in results if getattr(r, "outcome", None) == OUTCOME_CLAIMED]
    losers = [r for r in results if getattr(r, "outcome", None) == OUTCOME_REJECTED]
    assert len(winners) == 1 and len(losers) == 1
    assert losers[0].items[0].error_code in {
        RejectCode.ALREADY_OWNED.value,
        RejectCode.CONCURRENT_CLAIM.value,
    }
    row = chain.database.read(
        "SELECT status, current_epoch, attempt_no, current_attempt_id FROM tasks WHERE task_id='T-1'"
    )[0]
    assert row["status"] == "claimed"
    assert row["current_epoch"] == 2, "the epoch advances exactly once"
    assert row["attempt_no"] == 2
    assert count(chain.database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 2


# ------------------------------------------------------------------- guards


def test_attempt_budget_guards_the_rework(tmp_path):
    chain = _failed_chain(tmp_path)
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET attempt_no = max_attempts WHERE task_id = 'T-1'")

    denied = (
        ClaimService(chain.database)
        .claim(ClaimRequest(claim_request_id="cr-broke", harness_id="harness-2", task_ids=("T-1",)))
        .items[0]
    )
    assert denied.outcome == OUTCOME_REJECTED
    assert denied.error_code == RejectCode.ATTEMPT_BUDGET_EXHAUSTED.value
    assert _status(chain.database) == "changes_requested", "a denied claim writes no state"

    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET budget_override_by = 'human-1' WHERE task_id = 'T-1'")
    allowed = (
        ClaimService(chain.database)
        .claim(
            ClaimRequest(
                claim_request_id="cr-humansaidyes", harness_id="harness-2", task_ids=("T-1",)
            )
        )
        .items[0]
    )
    assert allowed.outcome == OUTCOME_CLAIMED, "the human override unblocks the rework"


def _resource_chain(tmp_path: Path):
    """A first attempt on a task with an exclusive resource, failing verify."""
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    seed_project(database, integration_commit=base)
    seed_task(database, task_id="T-1", exclusive_resources=["proc:rework"])
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-rr-1", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    service.start_task(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        group_short="g1",
        worktrees=manager,
    )
    worktree = manager.workspace_for_attempt(str(item.attempt_id))
    assert worktree is not None
    head = commit_in(worktree, filename="x.txt", content="x\n", message="feat: x")
    PublishProxy(database, repo).publish_task_branch(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        branch=worktree.branch,
        commit=head,
        fencing_epoch=int(item.fencing_epoch),
        idempotency_key="pub-rr-1",
    )
    service.submit_result(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        completion={"commit": head},
    )
    VerifyWorker(
        database,
        repo,
        policy(command=FAIL_COMMAND),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "ev",
    ).run(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        commit=head,
        base_commit=base,
        verify_profile=PROFILE,
        fencing_epoch=int(item.fencing_epoch),
    )
    assert _status(database) == "changes_requested"
    return database


def test_a_blocked_resource_refuses_the_rework_without_side_effects(tmp_path):
    database = _resource_chain(tmp_path)
    future = (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
    hold_resource(database, "proc:rework", attempt_id="other-task.a1", lease_until=future)

    denied = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-rr-2", harness_id="harness-2", task_ids=("T-1",)))
        .items[0]
    )
    assert denied.outcome == OUTCOME_REJECTED
    assert denied.error_code == RejectCode.RESOURCE_UNAVAILABLE.value
    assert _status(database) == "changes_requested"
    assert "claim_for_revision" not in _event_names(database)
    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id='T-1'") == 1
    assert (
        count(
            database,
            "SELECT COUNT(*) FROM task_claims WHERE task_id='T-1' AND released_at IS NULL",
        )
        == 0
    ), "the refusal leaves no claimed row"


def test_the_rework_takes_over_its_own_previous_resource_hold(tmp_path):
    """The dead attempt's still-unreleased resource rows must not self-block."""
    database = _resource_chain(tmp_path)
    held = database.read(
        "SELECT COUNT(*) AS n FROM resource_leases"
        " WHERE resource_id = 'proc:rework' AND released_at IS NULL AND attempt_id = 'T-1.a1'"
    )[0]["n"]
    assert held == 1, "the first attempt's hold lingers (release_lease frees the dev lease only)"

    granted = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-rr-3", harness_id="harness-2", task_ids=("T-1",)))
        .items[0]
    )
    assert granted.outcome == OUTCOME_CLAIMED, granted
    old_hold = database.read(
        "SELECT release_reason FROM resource_leases"
        " WHERE resource_id = 'proc:rework' AND attempt_id = 'T-1.a1'"
    )[0]
    assert str(old_hold["release_reason"]) == "revision"
    new_hold = database.read(
        "SELECT attempt_id FROM resource_leases"
        " WHERE resource_id = 'proc:rework' AND released_at IS NULL"
    )
    assert [str(row["attempt_id"]) for row in new_hold] == ["T-1.a2"]


# ------------------------------------------------------- budget-guard linkage


def test_a_budgeted_rework_still_reserves(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "budget_reservation_tokens", 50_000)
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    base = repo.ref_commit(repo.integration_ref)
    assert base is not None
    seed_project(database, integration_commit=base)
    seed_task(database, task_id="T-1", budget_tokens=100_000)
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-b-1", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    service.start_task(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        group_short="g1",
        worktrees=manager,
    )
    worktree = manager.workspace_for_attempt(str(item.attempt_id))
    assert worktree is not None
    head = commit_in(worktree, filename="x.txt", content="x\n", message="feat: x")
    PublishProxy(database, repo).publish_task_branch(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        branch=worktree.branch,
        commit=head,
        fencing_epoch=int(item.fencing_epoch),
        idempotency_key="pub-b-1",
    )
    service.submit_result(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        completion={"commit": head},
    )
    VerifyWorker(
        database,
        repo,
        policy(command=FAIL_COMMAND),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "ev",
    ).run(
        task_id="T-1",
        attempt_id=str(item.attempt_id),
        commit=head,
        base_commit=base,
        verify_profile=PROFILE,
        fencing_epoch=int(item.fencing_epoch),
    )
    assert _status(database) == "changes_requested"
    assert count(database, "SELECT COUNT(*) FROM budget_reservations WHERE task_id='T-1'") == 1

    granted = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-b-2", harness_id="harness-2", task_ids=("T-1",)))
        .items[0]
    )
    assert granted.outcome == OUTCOME_CLAIMED, granted
    rows = database.read(
        "SELECT attempt_id, state, amount_tokens FROM budget_reservations"
        " WHERE task_id='T-1' ORDER BY rowid"
    )
    assert [(str(r["attempt_id"]), str(r["state"])) for r in rows] == [
        ("T-1.a1", "settled"),
        ("T-1.a2", "held"),
    ], "the rework places its own hold under the same admission rules"


# --------------------------------------------------------------- pull model


def test_list_ready_tasks_surfaces_a_rework(tmp_path):
    chain = _failed_chain(tmp_path)
    client = TestClient(create_app(chain.database, chain.repo), base_url=LOOPBACK)

    listed = client.get("/api/v1/adapter/list_ready_tasks").json()

    assert listed["count"] == 1
    assert listed["tasks"][0]["task_id"] == "T-1"
    assert listed["tasks"][0]["status"] == "changes_requested"
