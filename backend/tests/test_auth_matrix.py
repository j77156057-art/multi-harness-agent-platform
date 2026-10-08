"""The API-layer permission matrix under AP_AUTH_MODE=enforce (auth-layer).

Every refusal must be one 403/401 *and* exactly one ``permission_denied``
audit event with zero business side effects (R10 §17 L1057; R10-fix-12 made
the event name legal). The subject check runs before the fact checks -- an
unauthenticated caller cannot probe which resources exist; the ordering is
pinned below.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.auth.tokens import TokenService
from app.config import settings
from tests.api_support import bootstrap, make_client

OPERATOR = "operator"


def register_harness(database, harness_id: str) -> None:
    HarnessRegistry(database).register(
        HarnessRegistration(
            harness_id=harness_id,
            name=harness_id,
            provider="openai",
            adapter="codex",
            adapter_version="1.0",
            capabilities=("implementation",),
            registration_version="reg-1",
        )
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def denials(database) -> list[dict[str, object]]:
    rows = database.read(
        "SELECT payload_json FROM task_events"
        " WHERE event_name = 'permission_denied' ORDER BY seq"
    )
    return [json.loads(str(row["payload_json"])) for row in rows]


def status_of(database, task_id: str = "T-1") -> str:
    return str(database.read("SELECT status FROM tasks WHERE task_id = ?", (task_id,))[0]["status"])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    register_harness(database, "h-A")
    register_harness(database, "h-B")
    monkeypatch.setattr(settings, "auth_mode", "enforce")

    human = TokenService(database).issue(subject_type="human", subject_id=OPERATOR)
    token_a = TokenService(database).issue(subject_type="harness", subject_id="h-A")
    token_b = TokenService(database).issue(subject_type="harness", subject_id="h-B")

    response = client.post(
        "/api/v1/projects",
        json={"project_id": "P-1", "name": "project"},
        headers=bearer(human),
    )
    assert response.status_code == 200, response.text
    client.post(
        "/api/v1/projects/P-1/groups",
        json={"group_id": "G-1", "name": "group"},
        headers=bearer(human),
    )
    client.post(
        "/api/v1/groups/G-1/plans",
        json={"plan_id": "PL-1", "snapshot": {"tasks": [{"id": "T-1"}]}},
        headers=bearer(human),
    )
    approved = client.post(
        "/api/v1/plans/PL-1/approve", json={"decided_by": OPERATOR}, headers=bearer(human)
    )
    assert approved.status_code == 200, approved.text

    return SimpleNamespace(
        client=client,
        database=database,
        human=human,
        token_a=token_a,
        token_b=token_b,
    )


def claim_task(env, token: str, *, harness_id: str, request_id: str) -> dict[str, object]:
    response = env.client.post(
        "/api/v1/adapter/claim",
        json={
            "claim_request_id": request_id,
            "harness_id": harness_id,
            "task_ids": ["T-1"],
        },
        headers=bearer(token),
    )
    assert response.status_code == 200, response.text
    return response.json()["items"][0]


# ------------------------------------------------------------------ 401 side


def test_missing_token_is_401_and_audited_without_side_effects(env):
    before = status_of(env.database)

    response = env.client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-A", "task_ids": ["T-1"]},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.token_invalid"
    audit = denials(env.database)
    assert len(audit) == 1
    assert audit[0]["reason"] == "token_missing"
    assert audit[0]["permission"] == "task:claim"
    assert audit[0]["path"] == "/api/v1/adapter/claim"
    assert status_of(env.database) == before  # nothing moved


def test_invalid_and_revoked_tokens_are_distinguishable_in_the_audit(env):
    response = env.client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-A", "task_ids": ["T-1"]},
        headers=bearer("apt_deadbeef"),
    )
    assert response.status_code == 401

    service = TokenService(env.database)
    revoked = service.issue(subject_type="harness", subject_id="h-A")
    service.revoke(raw=revoked, reason="rotated")
    response = env.client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-A", "task_ids": ["T-1"]},
        headers=bearer(revoked),
    )
    assert response.status_code == 401

    assert [item["reason"] for item in denials(env.database)] == [
        "token_invalid",
        "token_revoked",
    ]


# ------------------------------------------------------------- matrix cells


def test_harness_token_cannot_touch_the_human_face(env):
    response = env.client.post(
        "/api/v1/human/resume",
        json={"task_id": "T-1", "decided_by": OPERATOR},
        headers=bearer(env.token_a),
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "auth.permission_denied"
    audit = denials(env.database)
    assert len(audit) == 1
    assert audit[0]["reason"] == "role_not_allowed"
    assert audit[0]["permission"] == "task:resume"


def test_human_token_cannot_touch_the_adapter_face(env):
    response = env.client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-A", "task_ids": ["T-1"]},
        headers=bearer(env.human),
    )

    assert response.status_code == 403
    assert denials(env.database)[0]["reason"] == "role_not_allowed"


def test_harness_token_cannot_approve_plans(env):
    response = env.client.post(
        "/api/v1/plans/PL-1/approve",
        json={"decided_by": "h-A"},
        headers=bearer(env.token_a),
    )

    assert response.status_code == 403
    assert denials(env.database)[0]["permission"] == "plan:approve"


def test_human_decisions_require_the_token_subject_to_match(env):
    impersonation = env.client.post(
        "/api/v1/human/resume",
        json={"task_id": "T-1", "decided_by": "someone-else"},
        headers=bearer(env.human),
    )

    assert impersonation.status_code == 403
    assert denials(env.database)[0]["reason"] == "identity_mismatch"


# --------------------------------------------------------- identity binding


def test_claiming_as_another_harness_is_refused(env):
    impersonation = env.client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-B", "task_ids": ["T-1"]},
        headers=bearer(env.token_a),
    )

    assert impersonation.status_code == 403
    assert denials(env.database)[0]["reason"] == "identity_mismatch"
    assert status_of(env.database) == "ready"  # the impersonation changed nothing

    # the honest claim then goes through
    item = claim_task(env, env.token_a, harness_id="h-A", request_id="cr-2")
    assert item["outcome"] == "claimed"
    assert status_of(env.database) == "claimed"


def test_owner_scoped_verbs_require_the_task_owner(env):
    item = claim_task(env, env.token_a, harness_id="h-A", request_id="cr-1")

    stolen = env.client.post(
        "/api/v1/adapter/heartbeat",
        json={
            "task_id": "T-1",
            "attempt_id": item["attempt_id"],
            "lease_token": item["lease_token"],
            "fencing_epoch": item["fencing_epoch"],
        },
        headers=bearer(env.token_b),
    )
    assert stolen.status_code == 403
    assert denials(env.database)[0]["reason"] == "not_task_owner"
    assert denials(env.database)[0]["permission"] == "task:heartbeat"

    honest = env.client.post(
        "/api/v1/adapter/heartbeat",
        json={
            "task_id": "T-1",
            "attempt_id": item["attempt_id"],
            "lease_token": item["lease_token"],
            "fencing_epoch": item["fencing_epoch"],
        },
        headers=bearer(env.token_a),
    )
    assert honest.status_code == 200, honest.text


def test_publish_branch_must_carry_the_token_subjects_name(env):
    claim_task(env, env.token_a, harness_id="h-A", request_id="cr-1")

    response = env.client.post(
        "/api/v1/adapter/publish_task_branch",
        json={
            "task_id": "T-1",
            "attempt_id": "T-1.a1",
            "branch": "agent/G-1/h-B/T-1/T-1.a1",
            "commit": "0" * 40,
            "fencing_epoch": 1,
            "idempotency_key": "pub-1",
        },
        headers=bearer(env.token_a),
    )

    assert response.status_code == 403
    assert denials(env.database)[0]["reason"] == "branch_identity_mismatch"


# --------------------------------------------------------------- ordering


def test_subject_checks_run_before_fact_checks(env):
    """No token + unknown resource -> 401, not 404: nothing leaks existence."""
    anonymous = env.client.post(
        "/api/v1/plans/DOES-NOT-EXIST/approve", json={"decided_by": OPERATOR}
    )
    assert anonymous.status_code == 401

    # authorized subject, unknown resource -> the fact check answers (404)
    honest = env.client.post(
        "/api/v1/plans/DOES-NOT-EXIST/approve",
        json={"decided_by": OPERATOR},
        headers=bearer(env.human),
    )
    assert honest.status_code == 404


# ------------------------------------------------------------------- mode


def test_off_mode_is_unchanged_and_writes_no_audit(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    register_harness(database, "h-A")
    monkeypatch.setattr(settings, "auth_mode", "off")

    bootstrap(client)  # no tokens anywhere
    client.post(
        "/api/v1/groups/G-1/plans",
        json={"plan_id": "PL-1", "snapshot": {"tasks": [{"id": "T-1"}]}},
    )
    client.post("/api/v1/plans/PL-1/approve", json={"decided_by": OPERATOR})
    response = client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": "cr-1", "harness_id": "h-A", "task_ids": ["T-1"]},
    )

    assert response.status_code == 200
    assert denials(database) == []


def test_policy_table_is_resource_action_shaped(env):
    from app.auth.policy import POLICY

    for key, roles in POLICY.items():
        parts = key.split(":")
        assert len(parts) == 2 and all(parts), key
        assert roles and roles <= {"harness", "human"}, key


# ------------------------------------------------------------------ whoami


def test_whoami_four_states_without_refusing_or_auditing(env):
    """frontend-token: 自省四态；刻意不拒绝、不写审计（浏览器加载探针）。"""
    anonymous = env.client.get("/api/v1/auth/whoami")
    assert anonymous.status_code == 200
    assert anonymous.json() == {
        "mode": "enforce",
        "subject": None,
        "token_reason": "token_missing",
    }

    bad = env.client.get("/api/v1/auth/whoami", headers=bearer("apt_deadbeef"))
    assert bad.status_code == 200
    assert bad.json()["token_reason"] == "token_invalid"

    service = TokenService(env.database)
    revoked = service.issue(subject_type="human", subject_id=OPERATOR)
    service.revoke(raw=revoked)
    gone = env.client.get("/api/v1/auth/whoami", headers=bearer(revoked))
    assert gone.json()["token_reason"] == "token_revoked"

    mine = env.client.get("/api/v1/auth/whoami", headers=bearer(env.token_a))
    assert mine.json() == {
        "mode": "enforce",
        "subject": {"type": "harness", "id": "h-A"},
        "token_reason": None,
    }

    assert denials(env.database) == []  # introspection never manufactures audit noise


def test_whoami_reports_off_mode(tmp_path, monkeypatch):
    client, database = make_client(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "off")

    response = client.get("/api/v1/auth/whoami")

    assert response.json() == {"mode": "off", "subject": None, "token_reason": None}
