"""MVP-ADAPTER-001 (adapter-verbs): the last three §7 verbs over real routes.

``accept_help`` / ``reject_help`` / ``split_task`` were the only §7 verbs with
no route; this file proves them on the service matrix and end-to-end over the
HTTP surface (real ``HttpAdapter`` objects against a ``TestClient``). Rigs seed
the owned/parked states directly -- the producers live elsewhere -- and every
other hop goes through a real entry point.

The declared gaps (no ``help_artifacts`` table, no collaborator lease on
accept, no child lifecycle / ``all_children_merged`` wake-up, "budget
sufficient" not evaluable) are recorded in the adapter-verbs card; the tests
here pin what the slice *does* promise and refuse the alternatives loudly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.adapter.http import AdapterError, HttpAdapter
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.claim.guarded_write import StaleFencingEpoch
from app.tasks.service import Conflict, SchedulerService
from tests.api_support import bootstrap, make_client, publish_plan, worktree_manager
from tests.claim_support import make_database, seed_project, seed_task

NOW = datetime.now(UTC)


# ----------------------------------------------------------------------- rigs


def _rig(tmp_path, *, status: str = "in_progress", epoch: int = 1):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=status)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET owner = 'harness-1', current_attempt_id = 'T-1.a1',"
            " current_epoch = ? WHERE task_id = 'T-1'",
            (epoch,),
        )
        conn.execute(
            "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            " base_commit, base_locked_seq, head_commit, status, created_at)"
            " VALUES ('T-1.a1', 'T-1', 1, 'harness-1', ?, NULL, NULL, NULL, ?, ?)",
            (epoch, status, NOW.isoformat()),
        )
    return database


def _service(database) -> SchedulerService:
    return SchedulerService(database)


def _task(database, task_id: str = "T-1") -> dict:
    return dict(database.read("SELECT * FROM tasks WHERE task_id = ?", (task_id,))[0])


def _event_names(database, task_id: str = "T-1") -> list[str]:
    rows = database.read(
        "SELECT event_name FROM task_events WHERE aggregate_id = ? ORDER BY seq", (task_id,)
    )
    return [str(row["event_name"]) for row in rows]


# ------------------------------------------------------------------- help


def test_request_help_mints_the_help_id_it_validates_against(tmp_path):
    database = _rig(tmp_path)

    result = _service(database).request_help(
        task_id="T-1", attempt_id="T-1.a1", fencing_epoch=1, reason="schema 不确定"
    )

    help_id = str(result["help_id"])
    assert help_id.startswith("help-"), "R10's guard says the id is created here"
    row = _task(database)
    assert row["status"] == "needs_help"
    assert row["owner"] == "harness-1" and row["current_attempt_id"] == "T-1.a1"
    (event,) = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = 'request_help'"
    )
    payload = json.loads(event["payload_json"])
    assert payload["help_id"] == help_id and payload["reason"] == "schema 不确定"


def test_accept_help_records_the_collaborator_without_moving_the_task(tmp_path):
    database = _rig(tmp_path)
    service = _service(database)
    help_id = str(
        service.request_help(task_id="T-1", attempt_id="T-1.a1", fencing_epoch=1, reason="?")[
            "help_id"
        ]
    )
    before = _task(database)

    result = service.accept_help(task_id="T-1", help_id=help_id, collaborator="harness-2")

    after = _task(database)
    assert result["event"] == "help_accepted"
    assert after["status"] == before["status"] == "needs_help"
    assert after["owner"] == before["owner"], "R10-fix-11: no owner fields on the loop"
    assert after["current_attempt_id"] == before["current_attempt_id"]
    assert after["state_changed_seq"] == before["state_changed_seq"], "self loops never write it"
    (event,) = database.read("SELECT * FROM task_events WHERE event_name = 'help_accepted'")
    payload = json.loads(event["payload_json"])
    assert payload == {"help_id": help_id, "collaborator": "harness-2"}
    assert event["actor_id"] == "harness-2"


def test_reject_help_self_loops(tmp_path):
    database = _rig(tmp_path)
    service = _service(database)
    help_id = str(
        service.request_help(task_id="T-1", attempt_id="T-1.a1", fencing_epoch=1, reason="?")[
            "help_id"
        ]
    )

    result = service.reject_help(task_id="T-1", help_id=help_id, collaborator="harness-3")

    assert result["event"] == "help_rejected"
    assert _task(database)["status"] == "needs_help"
    assert "help_rejected" in _event_names(database)


def test_help_votes_need_the_open_help_id_on_a_parked_task(tmp_path):
    database = _rig(tmp_path)
    service = _service(database)
    with pytest.raises(Conflict):
        service.accept_help(task_id="T-1", help_id="help-nope", collaborator="harness-2")

    service.request_help(task_id="T-1", attempt_id="T-1.a1", fencing_epoch=1, reason="?")
    with pytest.raises(Conflict):
        service.accept_help(task_id="T-1", help_id="help-stale", collaborator="harness-2")

    assert "help_accepted" not in _event_names(database)


# ------------------------------------------------------------------ split


def test_split_creates_child_drafts_and_blocks_the_parent(tmp_path):
    database = _rig(tmp_path)

    result = _service(database).split_task(
        task_id="T-1",
        child_specs=[{"id": "C-1", "title": "前端"}, {"id": "C-2", "title": "后端"}],
        idempotency_key="sp-1",
        attempt_id="T-1.a1",
        fencing_epoch=1,
    )

    assert result["child_ids"] == ["C-1", "C-2"]
    assert result["parent_status"] == "blocked"
    assert result["replayed"] is False
    assert _task(database)["status"] == "blocked"
    children = database.read("SELECT task_id, status, group_id FROM tasks WHERE task_id LIKE 'C-%'")
    assert sorted(str(row["task_id"]) for row in children) == ["C-1", "C-2"]
    assert all(str(row["status"]) == "draft" and str(row["group_id"]) == "G-1" for row in children)
    deps = database.read(
        "SELECT child_task_id, relation FROM task_dependencies WHERE parent_task_id = 'T-1'"
    )
    assert sorted(str(row["child_task_id"]) for row in deps) == ["C-1", "C-2"]
    assert all(str(row["relation"]) == "depends_on" for row in deps)
    assert _event_names(database) == ["split", "split_parent_blocked"]
    (event,) = database.read("SELECT payload_json FROM task_events WHERE event_name = 'split'")
    assert json.loads(event["payload_json"])["child_ids"] == ["C-1", "C-2"]


def test_split_is_idempotent(tmp_path):
    database = _rig(tmp_path)
    service = _service(database)
    first = service.split_task(
        task_id="T-1",
        child_specs=[{"id": "C-1"}],
        idempotency_key="sp-1",
        attempt_id="T-1.a1",
        fencing_epoch=1,
    )
    events_after_first = _event_names(database)

    replay = service.split_task(
        task_id="T-1",
        child_specs=[{"id": "C-1"}],
        idempotency_key="sp-1",
        attempt_id="T-1.a1",
        fencing_epoch=1,
    )

    assert replay["replayed"] is True
    assert replay["child_ids"] == first["child_ids"] and replay["seq"] == first["seq"]
    assert _event_names(database) == events_after_first, "a replay writes nothing new"
    count = database.read("SELECT COUNT(*) AS n FROM tasks WHERE task_id = 'C-1'")[0]["n"]
    assert count == 1


def test_split_from_blocked_keeps_the_parent_blocked(tmp_path):
    database = _rig(tmp_path, status="blocked")

    result = _service(database).split_task(
        task_id="T-1",
        child_specs=[{"id": "C-1"}],
        idempotency_key="sp-2",
        attempt_id="T-1.a1",
        fencing_epoch=1,
    )

    assert result["parent_status"] == "blocked"
    assert _task(database)["status"] == "blocked"
    assert _event_names(database) == ["split"], "child_created changes no status"


def test_split_is_owner_fenced(tmp_path):
    database = _rig(tmp_path)

    with pytest.raises(StaleFencingEpoch):
        _service(database).split_task(
            task_id="T-1",
            child_specs=[{"id": "C-1"}],
            idempotency_key="sp-3",
            attempt_id="T-1.a1",
            fencing_epoch=2,
        )

    assert _event_names(database) == []


def test_split_refuses_bad_child_specs(tmp_path):
    database = _rig(tmp_path)
    seed_task(database, task_id="T-8")
    service = _service(database)
    kwargs = {"task_id": "T-1", "attempt_id": "T-1.a1", "fencing_epoch": 1}

    with pytest.raises(Conflict):
        service.split_task(child_specs=[], idempotency_key="sp-4", **kwargs)
    with pytest.raises(Conflict):
        service.split_task(child_specs=[{"id": "  "}], idempotency_key="sp-5", **kwargs)
    with pytest.raises(Conflict):
        service.split_task(
            child_specs=[{"id": "C-1"}, {"id": "C-1"}], idempotency_key="sp-6", **kwargs
        )
    with pytest.raises(Conflict):
        service.split_task(child_specs=[{"id": "T-1"}], idempotency_key="sp-7", **kwargs)
    with pytest.raises(Conflict):
        service.split_task(child_specs=[{"id": "T-8"}], idempotency_key="sp-8", **kwargs)
    assert _event_names(database) == [], "no refusal may write"
    assert not database.read("SELECT 1 FROM tasks WHERE task_id = 'C-1'")


# --------------------------------------------------------------- HTTP e2e


def _http_rig(tmp_path):
    client, database = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client, tasks=[{"id": "T-1"}, {"id": "T-2"}])
    plan_id = database.read("SELECT plan_id FROM plans LIMIT 1")[0]["plan_id"]
    approved = client.post(f"/api/v1/plans/{plan_id}/approve", json={"decided_by": "ops"})
    assert approved.status_code == 200, approved.text
    registrations = [
        HarnessRegistration(
            harness_id=f"harness-{n}",
            name=f"H{n}",
            provider="fake",
            adapter="http",
            adapter_version="1.0",
            capabilities=["implementation"],
            registration_version="reg-1",
        )
        for n in (1, 2)
    ]
    registry = HarnessRegistry(database)
    for registration in registrations:
        registry.register(registration)
    adapters = [HttpAdapter(client, registration) for registration in registrations]
    manager = worktree_manager(tmp_path, database)
    service = SchedulerService(database)
    for adapter, task_id in zip(adapters, ("T-1", "T-2"), strict=True):
        claimed = adapter.claim(claim_request_id=f"cr-{task_id}", task_ids=[task_id])
        assert claimed["items"][0]["outcome"] == "claimed", claimed
        context = adapter.attempts[task_id]
        service.start_task(
            task_id=task_id,
            attempt_id=context.attempt_id,
            fencing_epoch=context.fencing_epoch,
            worktrees=manager,
        )
    return client, database, adapters


def test_the_three_verbs_over_http(tmp_path):
    _client, database, (owner, collaborator) = _http_rig(tmp_path)

    asked = owner.request_help(task_id="T-1", reason="接口形状需要帮助")
    help_id = str(asked["help_id"])
    assert _task(database, "T-1")["status"] == "needs_help"

    accepted = collaborator.accept_help(task_id="T-1", help_id=help_id)
    assert accepted["event"] == "help_accepted"
    rejected = collaborator.reject_help(task_id="T-1", help_id=help_id)
    assert rejected["event"] == "help_rejected"

    wrong = None
    try:
        collaborator.accept_help(task_id="T-1", help_id="help-nope")
    except AdapterError as exc:
        wrong = exc.code
    assert wrong == "request.conflict", "the wire refuses a stale help_id"

    split = collaborator.split_task(
        task_id="T-2",
        child_specs=[{"id": "C-HTTP-1"}, {"id": "C-HTTP-2"}],
        idempotency_key="sp-http-1",
    )
    assert split["parent_status"] == "blocked"
    assert _task(database, "T-2")["status"] == "blocked"
    replay = collaborator.split_task(
        task_id="T-2",
        child_specs=[{"id": "C-HTTP-1"}, {"id": "C-HTTP-2"}],
        idempotency_key="sp-http-1",
    )
    assert replay["replayed"] is True
