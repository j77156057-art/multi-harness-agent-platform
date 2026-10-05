"""MVP-08 error contract: one envelope, codes that mean something, and the
idempotency replay header.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.app import create_app
from app.api.errors import envelope
from tests.api_support import bootstrap, make_client, publish_plan
from tests.claim_support import make_database


def _error(response) -> dict:
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert {"code", "message", "retryable"} <= set(error), error
    return error


def test_unknown_task_is_resource_not_found(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.get("/api/v1/tasks/NOPE")
    assert response.status_code == 404
    assert _error(response)["code"] == "resource.not_found"
    assert _error(response)["retryable"] is False


def test_malformed_body_reports_fields_but_not_values(tmp_path):
    client, _ = make_client(tmp_path)
    response = client.post("/api/v1/projects", json={"name": 42})
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "request.invalid"
    assert "fields" in error["details"]
    assert "42" not in response.text, "an error body must not echo submitted values"


def test_invalid_plan_snapshot_is_rejected_with_violations(tmp_path):
    client, _ = make_client(tmp_path)
    bootstrap(client)
    response = client.post(
        "/api/v1/groups/G-1/plans",
        json={"plan_id": "PL-bad", "snapshot": {"tasks": [{"id": "A", "depends_on": ["ghost"]}]}},
    )
    assert response.status_code == 422
    assert _error(response)["code"] == "request.invalid"


def test_claim_replay_sets_the_idempotency_header(tmp_path):
    client, _ = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-1", tasks=[{"id": "T-1"}])
    client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "h"})
    body = {"claim_request_id": "cr-1", "harness_id": "h1", "task_ids": ["T-1"]}

    first = client.post("/api/v1/adapter/claim", json=body)
    assert first.status_code == 200
    assert "Idempotency-Replayed" not in first.headers
    assert first.json()["replayed"] is False

    second = client.post("/api/v1/adapter/claim", json=body)
    assert second.status_code == 200
    assert second.headers["Idempotency-Replayed"] == "true"
    assert second.json()["replayed"] is True
    assert second.json()["items"][0]["attempt_id"] == first.json()["items"][0]["attempt_id"]


def test_same_request_id_with_a_different_payload_is_a_conflict(tmp_path):
    client, _ = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-2", tasks=[{"id": "T-1"}, {"id": "T-2"}])
    client.post("/api/v1/plans/PL-2/approve", json={"decided_by": "h"})

    client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-x", "harness_id": "h1", "task_ids": ["T-1"]},
    )
    clash = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-x", "harness_id": "h1", "task_ids": ["T-2"]},
    )
    assert clash.status_code == 409
    assert _error(clash)["code"] == "idempotency.conflict"
    assert client.get("/api/v1/tasks/T-2").json()["status"] == "ready"


def test_stale_fencing_epoch_is_refused(tmp_path):
    client, _ = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-3", tasks=[{"id": "T-1"}])
    client.post("/api/v1/plans/PL-3/approve", json={"decided_by": "h"})
    item = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-3", "harness_id": "h1", "task_ids": ["T-1"]},
    ).json()["items"][0]

    response = client.post(
        "/api/v1/adapter/heartbeat",
        json={
            "task_id": "T-1",
            "attempt_id": item["attempt_id"],
            "lease_token": item["lease_token"],
            "fencing_epoch": item["fencing_epoch"] - 1,
        },
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "fencing.epoch_stale"


def test_submit_from_a_non_owner_state_is_refused(tmp_path):
    client, _ = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-4", tasks=[{"id": "T-1"}])
    client.post("/api/v1/plans/PL-4/approve", json={"decided_by": "h"})

    response = client.post(
        "/api/v1/adapter/submit_result",
        json={
            "task_id": "T-1",
            "attempt_id": "T-1.a1",
            "fencing_epoch": 0,
            "completion": {"commit": "c"},
        },
    )
    assert response.status_code == 409
    assert _error(response)["code"] in {"fencing.epoch_stale", "submit.attempt_mismatch"}


def test_review_refuses_self_review(tmp_path):
    client, database = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, plan_id="PL-5", tasks=[{"id": "T-1"}])
    client.post("/api/v1/plans/PL-5/approve", json={"decided_by": "h"})
    item = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-5", "harness_id": "owner-1", "task_ids": ["T-1"]},
    ).json()["items"][0]
    from tests.api_support import scheduler

    service = scheduler(database)
    service.start_task(
        task_id="T-1", attempt_id=item["attempt_id"], fencing_epoch=item["fencing_epoch"]
    )
    client.post(
        "/api/v1/adapter/submit_result",
        json={
            "task_id": "T-1",
            "attempt_id": item["attempt_id"],
            "fencing_epoch": item["fencing_epoch"],
            "completion": {"commit": "c1"},
        },
    )
    service.record_verify_passed(task_id="T-1", commit="c1", actor_id="vw")

    response = client.post(
        "/api/v1/review/submit",
        json={
            "task_id": "T-1",
            "attempt_id": item["attempt_id"],
            "reviewer_harness_id": "owner-1",
            "verdict": "approve",
            "reviewed_commit": "c1",
        },
    )
    assert response.status_code == 409
    assert _error(response)["code"] == "review.self_review_forbidden"


def test_non_loopback_host_header_is_rejected(tmp_path):
    database = make_database(tmp_path)
    app = create_app(database)
    client = TestClient(app, base_url="http://evil.example")
    response = client.get("/api/v1/tasks/anything")
    assert response.status_code == 400
    assert _error(response)["code"] == "auth.host_header_rejected"


def test_envelope_shape_is_stable():
    from app.api.errors import ApiError

    body = envelope(ApiError("request.invalid", 400, "bad", details={"a": 1}))
    assert body == {
        "error": {
            "code": "request.invalid",
            "message": "bad",
            "retryable": False,
            "details": {"a": 1},
        }
    }
