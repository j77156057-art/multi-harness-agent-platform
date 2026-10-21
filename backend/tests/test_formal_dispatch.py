"""MVP-API-001 / CORE-CLAIM-001/002: real dispatch, identity and replay."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry, RegistrationConflict
from app.auth.tokens import TokenService
from app.config import settings
from app.git.repo import run_git
from app.spec import TaskStatus
from tests.api_support import make_client


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, db = make_client(tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    human = TokenService(db).issue(subject_type="human", subject_id="operator")
    client.headers["Authorization"] = f"Bearer {human}"
    assert (
        client.post("/api/v1/projects", json={"project_id": "P", "name": "project"}).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/projects/P/groups", json={"group_id": "G", "name": "group"}
        ).status_code
        == 200
    )
    for name in ("codex", "other"):
        response = client.post(
            "/api/v1/harnesses",
            json={
                "registered_by": "operator",
                "harness_id": name,
                "name": name,
                "provider": "openai",
                "adapter": "codex-exec",
                "adapter_version": "1",
                "capabilities": ["coding"],
                "registration_version": "reg-1",
            },
        )
        assert response.status_code == 200, response.text
    tokens = {
        name: TokenService(db).issue(subject_type="harness", subject_id=name)
        for name in ("codex", "other")
    }
    return SimpleNamespace(client=client, db=db, tokens=tokens)


def body(key="request-1"):
    return {
        "request_id": key,
        "title": "Write documentation",
        "goal": "Document startup",
        "harness_id": "codex",
        "decided_by": "operator",
        "exclusive_paths": ["docs/startup.md"],
        "acceptance": "Explain startup and execution boundaries",
    }


def claim(env, task_id, name="codex", key="claim-1"):
    response = env.client.post(
        "/api/v1/adapter/claim",
        json={"claim_request_id": key, "harness_id": name, "task_ids": [task_id]},
        headers={"Authorization": f"Bearer {env.tokens[name]}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_atomic_dispatch_replay_and_conflict(env):
    response = env.client.post("/api/v1/groups/G/dispatch", json=body())
    assert response.status_code == 200, response.text
    first = response.json()
    assert first["status"] == TaskStatus.READY.value and first["owner"] is None
    replay = env.client.post("/api/v1/groups/G/dispatch", json=body()).json()
    assert replay["task_id"] == first["task_id"] and replay["replayed"]
    bad = env.client.post("/api/v1/groups/G/dispatch", json={**body(), "goal": "different"})
    assert bad.status_code == 409
    assert len(env.db.read("SELECT * FROM tasks")) == 1
    assert (
        len(env.db.read("SELECT * FROM task_events WHERE aggregate_id = ?", (first["task_id"],)))
        == 1
    )


def test_task_reads_project_title_from_plan_snapshot(env):
    task_id = env.client.post("/api/v1/groups/G/dispatch", json=body()).json()["task_id"]

    detail = env.client.get(f"/api/v1/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["title"] == "Write documentation"

    listed = env.client.get("/api/v1/groups/G/tasks")
    assert listed.status_code == 200
    assert listed.json()["tasks"][0]["title"] == "Write documentation"


def test_http_claim_freezes_actual_git_head_without_health_projection(env, tmp_path):
    repo = env.client.app.state.canonical_repo
    old = repo.ref_commit(repo.integration_ref)
    assert env.db.read("SELECT integration_commit FROM integration_health")[0][0] is None
    first = env.client.post("/api/v1/groups/G/dispatch", json=body()).json()["task_id"]
    item = claim(env, first)["items"][0]
    assert item["base_commit"] == old
    assert env.db.read("SELECT base_commit FROM task_attempts")[0][0] == old
    # Advance the actual scheduler ref while leaving the health projection NULL.
    clone = tmp_path / "advance"
    run_git("clone", str(repo.path), str(clone))
    run_git("commit", "--allow-empty", "-m", "advance", cwd=clone)
    new = run_git("rev-parse", "HEAD", cwd=clone).stdout.strip()
    repo.import_objects(clone, new)
    repo.update_ref(repo.integration_ref, new)
    assert claim(env, first)["items"][0]["base_commit"] == old
    second = env.client.post(
        "/api/v1/groups/G/dispatch", json={**body("new-head"), "harness_id": "other"}
    ).json()["task_id"]
    assert claim(env, second, name="other", key="claim-new-head")["items"][0]["base_commit"] == new


def test_wrong_target_not_listed_or_claimed_and_version_pinned(env):
    dispatched = env.client.post("/api/v1/groups/G/dispatch", json=body()).json()
    task = dispatched["task_id"]
    listing = env.client.get(
        "/api/v1/adapter/list_ready_tasks",
        headers={"Authorization": f"Bearer {env.tokens['other']}"},
    ).json()
    assert listing["tasks"] == []
    refusal = claim(env, task, "other", "wrong")
    assert refusal["outcome"] == "rejected"
    assert refusal["items"][0]["error_code"].endswith("wrong_target")
    before = env.client.get("/api/v1/tasks/" + task).json()
    assert before["owner"] is None and before["attempt_no"] == 0
    granted = claim(env, task)["items"][0]
    assert granted["owner"] == "codex" and granted["lease_token"]
    replay = claim(env, task)["items"][0]
    assert replay["attempt_id"] == granted["attempt_id"] and replay["replayed"]
    assert len(env.db.read("SELECT * FROM task_attempts")) == 1
    assert env.db.read("SELECT registration_version FROM task_attempts")[0][0] == "reg-1"
    assert env.client.get("/api/v1/tasks/" + task).json()["registration_version"] == "reg-1"
    heartbeat = env.client.post(
        "/api/v1/adapter/heartbeat",
        json={
            "task_id": task,
            "attempt_id": replay["attempt_id"],
            "fencing_epoch": replay["fencing_epoch"],
            "lease_token": replay["lease_token"],
        },
        headers={"Authorization": f"Bearer {env.tokens['codex']}"},
    )
    assert heartbeat.status_code == 200


def test_capacity_and_offline_refused(env):
    first = env.client.post("/api/v1/groups/G/dispatch", json=body()).json()["task_id"]
    assert claim(env, first)["outcome"] == "claimed"
    second = env.client.post("/api/v1/groups/G/dispatch", json=body("second")).json()["task_id"]
    assert claim(env, second, key="second")["items"][0]["error_code"].endswith("parallel_limit")
    registry = HarnessRegistry(env.db)
    registry.register(
        HarnessRegistration(
            "codex",
            "codex",
            "openai",
            "codex-exec",
            "1",
            ("coding",),
            status="offline",
            registration_version="reg-2",
        )
    )
    assert claim(env, second, key="offline")["items"][0]["error_code"].endswith(
        "offline_or_degraded"
    )
    assert env.client.get("/api/v1/tasks/" + first).json()["registration_version"] == "reg-1"


def test_dispatch_to_a_window_without_a_credential_is_refused(env):
    """没有平台凭据的窗口领不了任务：派单要**当场拒绝**，不能事后让人干等。

    实测过一次：管理员把两个子任务派给了 ``codebuddy`` 与 ``qoder``——它们
    登记在册，却没有 worker 令牌，那两条任务永远停在 ``ready``。
    """
    registry = HarnessRegistry(env.db)
    registry.register(
        HarnessRegistration(
            "codebuddy", "codebuddy", "tencent", "codex-exec", "1", ("coding",),
            registration_version="reg-1",
        )
    )
    payload = body("no-credential")
    payload["harness_id"] = "codebuddy"

    response = env.client.post("/api/v1/groups/G/dispatch", json=payload)

    assert response.status_code == 422, response.text
    assert "credential" in response.text
    # Nothing was created: no task, no plan.
    assert env.client.get("/api/v1/groups/G/tasks").json()["tasks"] == []


def test_registration_replay_history_conflict_and_authority(env):
    registry = HarnessRegistry(env.db)
    original = HarnessRegistration(
        "codex", "codex", "openai", "codex-exec", "1", ("coding",), registration_version="reg-1"
    )
    assert registry.register(original)["replayed"]
    with pytest.raises(RegistrationConflict):
        registry.register(replace(original, capabilities=("coding", "review")))
    registry.register(replace(original, registration_version="reg-2"))
    with pytest.raises(RegistrationConflict):
        registry.register(original)
    assert registry.pinned("codex", "reg-1")["superseded_by"] == "reg-2"
    assert registry.current("codex")["registration_version"] == "reg-2"
    refusal = env.client.post(
        "/api/v1/groups/G/dispatch",
        json=body(),
        headers={"Authorization": f"Bearer {env.tokens['codex']}"},
    )
    assert refusal.status_code == 403 and not env.db.read("SELECT * FROM tasks")


@pytest.mark.parametrize(
    "changes", [{"exclusive_paths": ["../outside.md"]}, {"harness_id": "unknown"}]
)
def test_invalid_dispatch_leaves_no_partial_plan(env, changes):
    response = env.client.post("/api/v1/groups/G/dispatch", json={**body(), **changes})
    assert response.status_code == 422
    assert env.db.read("SELECT * FROM plans") == [] and env.db.read("SELECT * FROM tasks") == []


def test_probe_is_not_registration(env):
    registry = HarnessRegistry(env.db)
    registry.record_probe(
        harness_id="probe-only",
        reported_capabilities=["coding"],
        effective_capabilities=[],
        degraded=True,
    )
    refused = env.client.post(
        "/api/v1/groups/G/dispatch", json={**body(), "harness_id": "probe-only"}
    )
    assert refused.status_code == 422
