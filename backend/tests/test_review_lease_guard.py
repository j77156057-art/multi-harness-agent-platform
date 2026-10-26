"""OFFICE-UI-43: the review guard's stable conflict codes.

`FORMAL-MULTI-HARNESS-02`'s second completion standard says: a human review must
carry a **review lease** and the **claimed reviewed commit**; a missing lease or a
mismatched commit must answer with a *stable* code.  The three codes exist in
``SchedulerService.submit_review``:

* ``review.lease_required`` -- no live lease for this reviewer;
* ``review.commit_mismatch`` -- the commit handed in is not the claimed one;
* ``review.lease_expired``  -- the lease ran out (covered elsewhere).

Only the third had a test.  These two are pinned here -- over real HTTP, a real
database and a real worktree, with one Harness claiming and publishing and the
administrator taking the review.
"""

from __future__ import annotations

import pytest

from app.adapter.registry import HarnessRegistration, HarnessRegistry
from app.auth.tokens import TokenService
from app.config import settings
from tests.api_support import advance_to_awaiting_review, make_client, worktree_manager

HARNESS = "codex"
PROVIDER = "openai"
ADMIN = "administrator"


def _register(database, harness_id: str, provider: str) -> None:
    """Put one harness in the registry (the same shape the real CLI uses)."""
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=harness_id,
            name=harness_id,
            provider=provider,
            adapter=harness_id,
            adapter_version="1",
            capabilities=("implementation",),
            registration_version="r1",
        )
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reviewed_attempt(tmp_path, monkeypatch):
    """One attempt that reached ``awaiting_review``, and the admin's review claim.

    Returns ``(client, database, task_id, attempt_id, commit, review_id)``.
    """
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    client, database = make_client(tmp_path)
    _register(database, HARNESS, PROVIDER)
    admin_token = TokenService(database).issue(subject_type="human", subject_id=ADMIN)
    harness_token = TokenService(database).issue(subject_type="harness", subject_id=HARNESS)
    admin_headers = _bearer(admin_token)
    harness_headers = _bearer(harness_token)

    client.post(
        "/api/v1/projects",
        json={"project_id": "P-GUARD", "name": "review guard"},
        headers=admin_headers,
    )
    client.post(
        "/api/v1/projects/P-GUARD/groups",
        json={"group_id": "G-GUARD", "name": "guard"},
        headers=admin_headers,
    )
    client.post(
        "/api/v1/groups/G-GUARD/plans",
        json={"plan_id": "PL-GUARD", "snapshot": {"tasks": [{"id": "T-GUARD"}]}},
        headers=admin_headers,
    )
    client.post(
        "/api/v1/plans/PL-GUARD/approve",
        json={"decided_by": ADMIN},
        headers=admin_headers,
    )

    manager = worktree_manager(tmp_path, database)
    claimed = client.post(
        "/api/v1/adapter/claim",
        json={
            "claim_request_id": "claim-guard",
            "harness_id": HARNESS,
            "task_ids": ["T-GUARD"],
        },
        headers=harness_headers,
    )
    assert claimed.status_code == 200, claimed.text
    item = claimed.json()["items"][0]
    from app.tasks.service import SchedulerService

    SchedulerService(database).start_task(
        task_id="T-GUARD",
        attempt_id=item["attempt_id"],
        fencing_epoch=item["fencing_epoch"],
        worktrees=manager,
    )
    commit = advance_to_awaiting_review(
        client,
        database,
        tmp_path,
        task_id="T-GUARD",
        attempt_id=item["attempt_id"],
        fencing_epoch=item["fencing_epoch"],
        manager=manager,
        filename="guard.txt",
        headers=harness_headers,
    )
    assert client.get("/api/v1/tasks/T-GUARD").json()["status"] == "awaiting_review"

    review = client.post(
        "/api/v1/human/review/claim",
        json={
            "task_id": "T-GUARD",
            "attempt_id": item["attempt_id"],
            "decided_by": ADMIN,
        },
        headers=admin_headers,
    )
    assert review.status_code == 200, review.text
    body = review.json()
    return client, database, "T-GUARD", item["attempt_id"], commit, body, admin_token


def test_a_review_without_a_lease_is_refused_with_a_stable_code(reviewed_attempt):
    """No live lease for this reviewer -> ``review.lease_required`` (409)."""
    client, _database, task_id, attempt_id, commit, _review, admin_token = reviewed_attempt

    response = client.post(
        "/api/v1/human/review/submit",
        json={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "decided_by": ADMIN,
            "review_id": "rv-does-not-exist",
            "verdict": "approve",
            "reviewed_commit": commit,
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "review.lease_required"


def test_a_review_with_a_different_commit_is_refused_with_a_stable_code(reviewed_attempt):
    """The commit handed in is not the claimed one -> ``review.commit_mismatch`` (409)."""
    client, _database, task_id, attempt_id, commit, review, admin_token = reviewed_attempt

    response = client.post(
        "/api/v1/human/review/submit",
        json={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "decided_by": ADMIN,
            "review_id": review["review_id"],
            "verdict": "approve",
            "reviewed_commit": "0" * 40,
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "review.commit_mismatch"


def test_the_same_review_still_works_with_the_claimed_commit(reviewed_attempt):
    """The guard refuses the wrong calls, not the right one."""
    client, _database, task_id, attempt_id, commit, review, admin_token = reviewed_attempt

    response = client.post(
        "/api/v1/human/review/submit",
        json={
            "task_id": task_id,
            "attempt_id": attempt_id,
            "decided_by": ADMIN,
            "review_id": review["review_id"],
            "verdict": "approve",
            "reviewed_commit": commit,
        },
        headers=_bearer(admin_token),
    )
    assert response.status_code == 200, response.text
    assert response.json()["event"] == "approve"
    assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "approved"
