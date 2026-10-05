"""MVP-08 acceptance: the whole path is drivable over HTTP.

Checklist MVP-08 asks for "建群 -> 发方案 -> 领取 -> 提交 -> 审查" through the API
only. Two transitions in that chain are owned by tasks that come *after* MVP-08
in the dependency graph:

* ``claimed -> in_progress`` is gated on "worktree ready" (R10 §10), and worktree
  provisioning is MVP-05.
* ``verifying -> awaiting_review`` is gated on a passing verification receipt
  (R10 §16), and Verify is MVP-06.

So the test drives those two through the scheduler service, marks them clearly,
and asserts the HTTP surface for everything else. No verification receipt is
fabricated anywhere.
"""

from __future__ import annotations

from tests.api_support import (
    advance_to_awaiting_review,
    bootstrap,
    make_client,
    publish_plan,
    scheduler,
    worktree_manager,
)


def test_full_path_over_http_with_two_scaffolded_transitions(tmp_path):
    client, database = make_client(tmp_path)
    bootstrap(client)

    published = publish_plan(client, tasks=[{"id": "T-1"}, {"id": "T-2"}])
    assert published["plan_hash"].startswith("sha256:")

    # T-2 depends on T-1, so approval must split them across the two edges.
    client_plan = {
        "plan_id": "PL-2",
        "snapshot": {"tasks": [{"id": "A"}, {"id": "B", "depends_on": ["A"]}]},
    }
    assert client.post("/api/v1/groups/G-1/plans", json=client_plan).status_code == 200
    approval = client.post("/api/v1/plans/PL-2/approve", json={"decided_by": "human-1"})
    assert approval.status_code == 200, approval.text
    assert approval.json() == {"plan_id": "PL-2", "ready": ["A"], "waiting": ["B"]}

    # 领取
    claimed = client.post(
        "/api/v1/adapter/claim",
        json={
            "claim_request_id": "cr-1",
            "harness_id": "harness-1",
            "task_ids": ["A"],
        },
    )
    assert claimed.status_code == 200, claimed.text
    item = claimed.json()["items"][0]
    assert item["outcome"] == "claimed"
    assert item["owner"] == "harness-1"
    epoch = item["fencing_epoch"]
    attempt = item["attempt_id"]
    token = item["lease_token"]

    # 心跳走 HTTP
    beat = client.post(
        "/api/v1/adapter/heartbeat",
        json={
            "task_id": "A",
            "attempt_id": attempt,
            "lease_token": token,
            "fencing_epoch": epoch,
        },
    )
    assert beat.status_code == 200, beat.text
    assert beat.json()["lease_until"] > item["lease_until"]

    # claimed -> in_progress provisions the worktree. R10 §7 defines no
    # harness-facing verb for it, so the scheduler drives this one step.
    manager = worktree_manager(tmp_path, database)
    scheduler(database).start_task(
        task_id="A", attempt_id=attempt, fencing_epoch=epoch, worktrees=manager
    )

    # 提交 + 验证：真实 commit、真实发布、真实 receipt。MVP-06 之后这段没有脚手架。
    commit = advance_to_awaiting_review(
        client,
        database,
        tmp_path,
        task_id="A",
        attempt_id=attempt,
        fencing_epoch=epoch,
        manager=manager,
    )
    assert client.get("/api/v1/tasks/A").json()["status"] == "awaiting_review"

    # 审查
    reviewed = client.post(
        "/api/v1/review/submit",
        json={
            "task_id": "A",
            "attempt_id": attempt,
            "reviewer_harness_id": "harness-2",
            "verdict": "request_changes",
            "reviewed_commit": commit,
            "review_revision": 1,
            "findings": [{"severity": "high", "message": "needs work"}],
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["event"] == "request_changes"
    assert client.get("/api/v1/tasks/A").json()["status"] == "changes_requested"
    assert client.get("/api/v1/tasks/A").json()["review_round"] == 1


def test_approve_then_review_approval(tmp_path):
    client, database = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-3", tasks=[{"id": "T-9"}])
    client.post("/api/v1/plans/PL-3/approve", json={"decided_by": "human-1"})

    claimed = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-9", "harness_id": "h1", "task_ids": ["T-9"]},
    ).json()["items"][0]
    service = scheduler(database)
    manager = worktree_manager(tmp_path, database)
    service.start_task(
        task_id="T-9",
        attempt_id=claimed["attempt_id"],
        fencing_epoch=claimed["fencing_epoch"],
        worktrees=manager,
    )
    commit = advance_to_awaiting_review(
        client,
        database,
        tmp_path,
        task_id="T-9",
        attempt_id=claimed["attempt_id"],
        fencing_epoch=claimed["fencing_epoch"],
        manager=manager,
    )

    approved = client.post(
        "/api/v1/review/submit",
        json={
            "task_id": "T-9",
            "attempt_id": claimed["attempt_id"],
            "reviewer_harness_id": "reviewer-1",
            "verdict": "approve",
            "reviewed_commit": commit,
        },
    )
    assert approved.status_code == 200, approved.text
    task = client.get("/api/v1/tasks/T-9").json()
    assert task["status"] == "approved"
    assert task["last_review_id"] is not None


def test_owner_actions_over_http(tmp_path):
    client, database = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-4", tasks=[{"id": "T-1"}])
    client.post("/api/v1/plans/PL-4/approve", json={"decided_by": "h"})

    claimed = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-4", "harness_id": "h1", "task_ids": ["T-1"]},
    ).json()["items"][0]
    scheduler(database).start_task(
        task_id="T-1",
        attempt_id=claimed["attempt_id"],
        fencing_epoch=claimed["fencing_epoch"],
        worktrees=worktree_manager(tmp_path, database),
    )

    helped = client.post(
        "/api/v1/adapter/request_help",
        json={
            "task_id": "T-1",
            "attempt_id": claimed["attempt_id"],
            "fencing_epoch": claimed["fencing_epoch"],
            "reason": "stuck on schema",
        },
    )
    assert helped.status_code == 200, helped.text
    assert client.get("/api/v1/tasks/T-1").json()["status"] == "needs_help"

    released = client.post(
        "/api/v1/adapter/release",
        json={
            "task_id": "T-1",
            "attempt_id": claimed["attempt_id"],
            "fencing_epoch": claimed["fencing_epoch"],
            "reason": "handing back",
        },
    )
    assert released.status_code == 200, released.text
    task = client.get("/api/v1/tasks/T-1").json()
    assert task["status"] == "ready"
    assert task["owner"] is None


def test_reject_plan_leaves_tasks_draft_rejected(tmp_path):
    client, _ = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-5", tasks=[{"id": "T-1"}])

    rejected = client.post(
        "/api/v1/plans/PL-5/reject", json={"decided_by": "human-1", "reason": "too broad"}
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["rejected"] == ["T-1"]
    assert client.get("/api/v1/tasks/T-1").json()["status"] == "draft_rejected"
