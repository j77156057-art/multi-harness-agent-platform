"""The administrator-led three-Harness dispatch and review loop."""

from __future__ import annotations

from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth.tokens import TokenService
from app.config import settings
from tests.api_support import (
    advance_to_awaiting_review,
    make_client,
    scheduler,
    worktree_manager,
)


def _register(database, harness_id: str, provider: str) -> None:
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


def test_admin_dispatches_three_harnesses_and_reviews_each_result(tmp_path, monkeypatch):
    """MVP-ADMIN-REVIEW-001: three independent attempts reach human approval."""
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    harnesses = [("codex", "openai"), ("opencode", "stepfun"), ("claude", "anthropic")]
    for harness_id, provider in harnesses:
        _register(database, harness_id, provider)

    admin_id = "administrator"
    admin_token = TokenService(database).issue(subject_type="human", subject_id=admin_id)
    tokens = {
        harness_id: TokenService(database).issue(subject_type="harness", subject_id=harness_id)
        for harness_id, _ in harnesses
    }
    admin_headers = _bearer(admin_token)

    assert (
        client.post(
            "/api/v1/projects",
            json={"project_id": "P-MULTI", "name": "three harnesses"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/projects/P-MULTI/groups",
            json={"group_id": "G-MULTI", "name": "parallel lanes"},
            headers=admin_headers,
        ).status_code
        == 200
    )
    tasks = [{"id": f"T-{harness_id}"} for harness_id, _ in harnesses]
    assert (
        client.post(
            "/api/v1/groups/G-MULTI/plans",
            json={"plan_id": "PL-MULTI", "snapshot": {"tasks": tasks}},
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/plans/PL-MULTI/approve",
            json={"decided_by": admin_id},
            headers=admin_headers,
        ).status_code
        == 200
    )

    manager = worktree_manager(tmp_path, database)
    submitted: list[tuple[str, str, str, int]] = []
    for harness_id, _ in harnesses:
        task_id = f"T-{harness_id}"
        headers = _bearer(tokens[harness_id])
        claimed = client.post(
            "/api/v1/adapter/claim",
            json={
                "claim_request_id": f"claim-{harness_id}",
                "harness_id": harness_id,
                "task_ids": [task_id],
            },
            headers=headers,
        )
        assert claimed.status_code == 200, claimed.text
        item = claimed.json()["items"][0]
        scheduler(database).start_task(
            task_id=task_id,
            attempt_id=item["attempt_id"],
            fencing_epoch=item["fencing_epoch"],
            worktrees=manager,
        )
        commit = advance_to_awaiting_review(
            client,
            database,
            tmp_path,
            task_id=task_id,
            attempt_id=item["attempt_id"],
            fencing_epoch=item["fencing_epoch"],
            manager=manager,
            filename=f"{harness_id}.txt",
            headers=headers,
        )
        assert client.get(f"/api/v1/tasks/{task_id}").json()["status"] == "awaiting_review"
        submitted.append((task_id, item["attempt_id"], commit, item["fencing_epoch"]))

    # A Harness cannot impersonate the administrator review face.
    denied = client.post(
        "/api/v1/human/review/claim",
        json={
            "task_id": submitted[0][0],
            "attempt_id": submitted[0][1],
            "decided_by": "administrator",
        },
        headers=_bearer(tokens["codex"]),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "auth.permission_denied"

    for task_id, attempt_id, commit, _epoch in submitted:
        claimed_review = client.post(
            "/api/v1/human/review/claim",
            json={"task_id": task_id, "attempt_id": attempt_id, "decided_by": admin_id},
            headers=admin_headers,
        )
        assert claimed_review.status_code == 200, claimed_review.text
        review = claimed_review.json()
        assert review["reviewed_commit"] == commit
        verdict = client.post(
            "/api/v1/human/review/submit",
            json={
                "task_id": task_id,
                "attempt_id": attempt_id,
                "decided_by": admin_id,
                "review_id": review["review_id"],
                "verdict": "approve",
                "reviewed_commit": commit,
            },
            headers=admin_headers,
        )
        assert verdict.status_code == 200, verdict.text
        assert verdict.json()["event"] == "approve"
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        assert task["status"] == "approved"

    events = database.read(
        "SELECT actor_type, actor_id, event_name FROM task_events"
        " WHERE event_name = 'approve' ORDER BY seq"
    )
    assert [(row["actor_type"], row["actor_id"]) for row in events] == [
        ("human", "administrator"),
        ("human", "administrator"),
        ("human", "administrator"),
    ]
