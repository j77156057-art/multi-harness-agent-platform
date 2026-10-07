"""HUMAN-OPS-001: the R10 §18 human decision surface, first slice.

Six operations over ``task_decisions`` (service matrix, HTTP routes, operator
CLI), plus the two reminder sweeps that keep a human wait visible without ever
releasing it (R10 §13). Rigs seed the parked/escalated states directly -- the
producers of ``awaiting_human`` / drift / ``escalated`` live in the sweeps and
worker lines and are tested in their own files -- but everything else goes
through a real entry point: the service for the matrix, the HTTP surface for
routes and the harness verb, ``main(client=...)`` for the CLI.

Clocks: ``task_events`` is append-only (trigger-enforced), so tests never
backdate anchors; they move ``now`` forward instead -- the sweeper family's
convention.
"""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.adapter.http import HttpAdapter
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.claim.service import ClaimRequest, ClaimService
from app.cli.main import EXIT_API_ERROR, EXIT_OK, main
from app.human.service import HumanOpsService
from app.lease.service import grant_lease, parse_iso
from app.scheduler import sweep_escalation_reminder, sweep_human_wait_timeout
from app.tasks.service import Conflict, SchedulerService
from tests.api_support import bootstrap, make_client, publish_plan, worktree_manager
from tests.claim_support import make_database, seed_project, seed_task

NOW = datetime.now(UTC)


# ----------------------------------------------------------------------- rigs


def _rig(
    tmp_path,
    *,
    status: str,
    attempt: bool = True,
    owner: str | None = "harness-1",
    epoch: int = 1,
    lease: bool = False,
    waiting_since: datetime | None = None,
    escalated_at: datetime | None = None,
):
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database, task_id="T-1", status=status)
    with database.transaction() as conn:
        conn.execute(
            "UPDATE tasks SET owner = ?, current_attempt_id = ?, current_epoch = ?,"
            " waiting_since = ?, escalated_at = ? WHERE task_id = 'T-1'",
            (
                owner,
                "T-1.a1" if attempt else None,
                epoch,
                waiting_since.isoformat() if waiting_since else None,
                escalated_at.isoformat() if escalated_at else None,
            ),
        )
        if attempt:
            conn.execute(
                "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner, fencing_epoch,"
                " base_commit, base_locked_seq, head_commit, status, created_at)"
                " VALUES ('T-1.a1', 'T-1', 1, ?, ?, NULL, NULL, NULL, ?, ?)",
                (owner or "unknown", epoch, status, NOW.isoformat()),
            )
        if lease:
            grant_lease(
                conn,
                task_id="T-1",
                attempt_id="T-1.a1",
                owner=owner or "unknown",
                fencing_epoch=epoch,
            )
    return database


def _service(database) -> HumanOpsService:
    return HumanOpsService(database)


def _task(database) -> dict:
    return dict(database.read("SELECT * FROM tasks WHERE task_id = 'T-1'")[0])


def _event_names(database, task_id: str = "T-1") -> list[str]:
    rows = database.read(
        "SELECT event_name FROM task_events WHERE aggregate_id = ? ORDER BY seq", (task_id,)
    )
    return [str(row["event_name"]) for row in rows]


def _decisions(database, task_id: str = "T-1") -> list[dict]:
    return [
        dict(row)
        for row in database.read(
            "SELECT * FROM task_decisions WHERE task_id = ? ORDER BY rowid", (task_id,)
        )
    ]


def _active_leases(database, task_id: str = "T-1") -> list[dict]:
    return [
        dict(row)
        for row in database.read(
            "SELECT * FROM leases WHERE task_id = ? AND released_at IS NULL", (task_id,)
        )
    ]


# ------------------------------------------------------------------ ask_human


def test_ask_human_parks_releases_the_lease_and_opens_a_decision(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)

    result = _service(database).ask_human(
        task_id="T-1", question="要不要回滚？", options=["是", "否"], actor="harness-1"
    )

    row = _task(database)
    assert row["status"] == "awaiting_human"
    assert row["waiting_since"] is not None, "the wait clock starts on entry"
    assert row["next_reminder_at"] is None
    assert _active_leases(database) == [], "§18: the owner lease clock pauses on entry"
    (decision,) = _decisions(database)
    assert decision["decision_id"] == result["decision_id"]
    assert decision["question"] == "要不要回滚？"
    assert json.loads(decision["options_json"]) == ["是", "否"]
    assert decision["scope"] == "task" and decision["actor"] == "harness-1"
    assert decision["attempt_id"] == "T-1.a1"
    assert decision["answer"] is None and decision["decided_seq"] is None, "未决行保持 NULL"
    assert _event_names(database) == ["human.question"]


def test_ask_human_refuses_a_task_that_is_not_in_progress(tmp_path):
    database = _rig(tmp_path, status="claimed", lease=True)

    with pytest.raises(Conflict):
        _service(database).ask_human(task_id="T-1", question="?")

    assert _event_names(database) == [], "a refused ask writes nothing"
    assert _decisions(database) == []


# --------------------------------------------------------------------- resume


def test_resume_reissues_the_same_attempt_and_epoch_and_closes_the_decision(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)
    service = _service(database)
    asked = service.ask_human(task_id="T-1", question="?", actor="harness-1")

    resumed = service.resume(task_id="T-1", decided_by="ops", answer="继续")

    row = _task(database)
    assert row["status"] == "in_progress" and row["waiting_since"] is None
    assert row["current_attempt_id"] == "T-1.a1", "a resume is not a new attempt"
    assert row["current_epoch"] == 1, "no epoch bump"
    (active,) = _active_leases(database)
    assert active["attempt_id"] == "T-1.a1" and active["fencing_epoch"] == 1
    (decision,) = _decisions(database)
    assert decision["decision_id"] == asked["decision_id"]
    assert decision["answer"] == "继续"
    assert decision["decided_seq"] == resumed["seq"]
    assert _event_names(database) == ["human.question", "human.resume"]


def test_a_resume_without_an_answer_leaves_the_decision_open(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)
    service = _service(database)
    service.ask_human(task_id="T-1", question="?")

    service.resume(task_id="T-1", decided_by="ops")

    (decision,) = _decisions(database)
    assert decision["answer"] is None and decision["decided_seq"] is None
    assert _task(database)["status"] == "in_progress"


def test_resume_from_drift_with_an_owner_keeps_the_attempt_and_one_lease(tmp_path):
    """The drift sweep froze the attempt but left its lease running; the resume
    must leave exactly one active lease behind, still on the same epoch."""
    database = _rig(tmp_path, status="plan_drift", lease=True)

    _service(database).resume(task_id="T-1", decided_by="ops")

    row = _task(database)
    assert row["status"] == "in_progress"
    assert row["current_attempt_id"] == "T-1.a1" and row["current_epoch"] == 1
    (active,) = _active_leases(database)
    assert active["fencing_epoch"] == 1
    assert _event_names(database) == ["human.resume"]


def test_resume_from_drift_without_an_owner_asks_for_a_fresh_claim(tmp_path):
    database = _rig(tmp_path, status="impl_drift", attempt=False, owner=None)

    _service(database).resume(task_id="T-1", decided_by="ops")

    row = _task(database)
    assert row["status"] == "ready"
    assert row["owner"] is None and row["current_attempt_id"] is None
    assert _event_names(database) == ["human.reopen_no_owner"]
    # "Fresh claim needed" must really be claimable: claim rejects a task whose
    # current_attempt_id is still set, so the reset above is load-bearing.
    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-2", harness_id="harness-2", task_ids=("T-1",)))
        .items[0]
    )
    assert item.outcome == "claimed"


def test_resume_refuses_a_task_that_is_neither_parked_nor_drifted(tmp_path):
    database = _rig(tmp_path, status="blocked", lease=True)

    with pytest.raises(Conflict):
        _service(database).resume(task_id="T-1", decided_by="ops")

    assert _event_names(database) == []


# --------------------------------------------------------------------- reopen


@pytest.mark.parametrize("status", ["awaiting_human", "plan_drift", "impl_drift"])
def test_reopen_returns_a_parked_or_drifted_task_to_ready(tmp_path, status):
    database = _rig(tmp_path, status=status, lease=False)

    _service(database).reopen(task_id="T-1", decided_by="ops", answer="重来")

    row = _task(database)
    assert row["status"] == "ready"
    assert row["owner"] is None and row["current_attempt_id"] is None
    assert _event_names(database) == ["human.reopen"]


def test_reopen_revives_an_abandoned_task(tmp_path):
    """abandoned's only exit (R10-02): the coverage map points here."""
    database = _rig(tmp_path, status="abandoned", attempt=False, owner=None)

    _service(database).reopen(task_id="T-1", decided_by="ops")

    assert _task(database)["status"] == "ready"
    assert _event_names(database) == ["reopen"]


def test_reopen_refuses_a_live_task(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)

    with pytest.raises(Conflict):
        _service(database).reopen(task_id="T-1", decided_by="ops")

    assert _event_names(database) == []


# -------------------------------------------------------------------- release


def test_release_returns_an_escalated_task_to_ready(tmp_path):
    database = _rig(tmp_path, status="escalated", attempt=False, owner=None, escalated_at=NOW)

    _service(database).release(task_id="T-1", decided_by="ops")

    row = _task(database)
    assert row["status"] == "ready"
    assert row["escalated_at"] is None, "the escalation anchor goes with the state"
    assert _event_names(database) == ["human.release"]


def test_release_refuses_a_task_that_is_not_escalated(tmp_path):
    database = _rig(tmp_path, status="awaiting_human", attempt=False, owner=None)

    with pytest.raises(Conflict):
        _service(database).release(task_id="T-1", decided_by="ops")

    assert _event_names(database) == []


# ----------------------------------------------------------------- adjudicate


def test_adjudicate_parks_an_escalated_task_with_a_decision(tmp_path):
    database = _rig(tmp_path, status="escalated", lease=False, escalated_at=NOW)

    result = _service(database).adjudicate(
        task_id="T-1", decided_by="ops", question="选哪个方案？", options=["A", "B"]
    )

    row = _task(database)
    assert row["status"] == "awaiting_human"
    assert row["waiting_since"] is not None and row["escalated_at"] is None
    (decision,) = _decisions(database)
    assert decision["decision_id"] == result["decision_id"]
    assert decision["scope"] == "escalation"
    assert decision["question"] == "选哪个方案？"
    assert json.loads(decision["options_json"]) == ["A", "B"]
    assert _event_names(database) == ["human.adjudicate"]


def test_adjudicate_refuses_a_task_that_is_not_escalated(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)

    with pytest.raises(Conflict):
        _service(database).adjudicate(task_id="T-1", decided_by="ops", question="?")

    assert _event_names(database) == []


# --------------------------------------------------------------------- cancel


@pytest.mark.parametrize(
    ("status", "expected_event"),
    [
        ("queued", "queue_cancel"),
        ("stale", "human.cancel"),
        ("reclaiming", "human.cancel"),
        ("awaiting_human", "human.cancel"),
        ("in_progress", "cancel"),
        ("draft", "cancel"),
        ("merging", "cancel"),
        ("approved", "cancel"),
    ],
)
def test_cancel_picks_the_event_by_current_state(tmp_path, status, expected_event):
    live = status in ("in_progress", "merging", "approved")
    database = _rig(tmp_path, status=status, attempt=live, lease=False)

    result = _service(database).cancel(task_id="T-1", decided_by="ops", reason="stop")

    assert _task(database)["status"] == "cancelled"
    assert result["event"] == expected_event
    assert _event_names(database) == [expected_event]
    if status == "queued":
        assert _task(database)["queue_reason"] is None


@pytest.mark.parametrize(
    "status", ["escalated", "merged", "cancelled", "abandoned", "draft_rejected"]
)
def test_cancel_refuses_states_off_every_cancel_edge(tmp_path, status):
    database = _rig(tmp_path, status=status, attempt=False, owner=None)

    with pytest.raises(Conflict):
        _service(database).cancel(task_id="T-1", decided_by="ops", reason="stop")

    assert _event_names(database) == []


def test_cancel_releases_leases_resources_and_expires_a_claimed_review(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)
    lease_until = (NOW + timedelta(minutes=30)).isoformat()
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO resource_leases(resource_lease_id, resource_id, attempt_id,"
            " lease_until, epoch, released_at, release_reason)"
            " VALUES ('rl-1', 'port:8188', 'T-1.a1', ?, 1, NULL, NULL)",
            (lease_until,),
        )
        conn.execute(
            "INSERT INTO reviews(review_id, task_id, attempt_id, reviewer_harness_id,"
            " reviewer_provider, status, lease_until, fencing_epoch, reviewed_commit,"
            " verdict, findings_hash, review_revision, created_seq)"
            " VALUES ('rv-1', 'T-1', 'T-1.a1', 'harness-2', 'unknown', 'claimed', ?, 1,"
            " 'deadbeef', NULL, NULL, 1, NULL)",
            (lease_until,),
        )

    _service(database).cancel(task_id="T-1", decided_by="ops", reason="stop")

    assert _active_leases(database) == []
    (resource,) = database.read("SELECT * FROM resource_leases WHERE resource_lease_id = 'rl-1'")
    assert resource["released_at"] is not None and resource["release_reason"] == "cancel"
    (review,) = database.read("SELECT * FROM reviews WHERE review_id = 'rv-1'")
    assert review["status"] == "expired"


# ------------------------------------------------------------------ reminders


def test_the_first_reminder_fires_after_the_window(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)
    _service(database).ask_human(task_id="T-1", question="?")
    anchor = parse_iso(str(_task(database)["waiting_since"]))

    early = sweep_human_wait_timeout(database, anchor + timedelta(minutes=29))
    assert early.actions == 0, "inside the window nothing fires"

    report = sweep_human_wait_timeout(database, anchor + timedelta(minutes=30))
    assert report.detail["reminded"] == ["T-1"]
    assert _task(database)["status"] == "awaiting_human", "a reminder never releases (§13)"
    (event,) = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = 'human_wait_timeout'"
    )
    payload = json.loads(event["payload_json"])
    assert payload["reminder_no"] == 1 and payload["interval_minutes"] == 60


def test_the_reminder_schedule_backs_off_and_caps(tmp_path):
    """Gaps: base (30), then base x mult^1.. capped at reminder_cap_minutes."""
    database = _rig(tmp_path, status="awaiting_human", attempt=False, owner=None, waiting_since=NOW)

    fired_at = []
    for minutes in (30, 90, 210, 450, 690):
        report = sweep_human_wait_timeout(database, NOW + timedelta(minutes=minutes))
        assert report.actions == 1, f"expected a reminder at +{minutes}m"
        fired_at.append(minutes)

    intervals = [
        json.loads(row["payload_json"])["interval_minutes"]
        for row in database.read(
            "SELECT payload_json FROM task_events WHERE event_name = 'human_wait_timeout'"
            " ORDER BY seq"
        )
    ]
    assert intervals == [60, 120, 240, 240, 240], "base x2, capped at 240"
    assert _task(database)["next_reminder_at"] == (NOW + timedelta(minutes=690 + 240)).isoformat()


def test_a_reminder_never_moves_the_task_or_its_waiting_clock(tmp_path):
    database = _rig(tmp_path, status="in_progress", lease=True)
    _service(database).ask_human(task_id="T-1", question="?")
    before = _task(database)
    assert before["state_changed_seq"] is not None
    anchor = parse_iso(str(before["waiting_since"]))

    sweep_human_wait_timeout(database, anchor + timedelta(minutes=30))

    after = _task(database)
    assert after["status"] == before["status"] == "awaiting_human"
    assert after["waiting_since"] == before["waiting_since"], "R10-fix-11: no advance"
    assert after["state_changed_seq"] == before["state_changed_seq"], "self loops never write it"
    assert after["next_reminder_at"] is not None, "the reminder field is the only write"


def test_the_escalation_reminder_backs_off_with_the_event_count(tmp_path):
    database = _rig(tmp_path, status="escalated", attempt=False, owner=None, escalated_at=NOW)

    assert sweep_escalation_reminder(database, NOW + timedelta(minutes=29)).actions == 0
    assert sweep_escalation_reminder(database, NOW + timedelta(minutes=30)).actions == 1
    assert sweep_escalation_reminder(database, NOW + timedelta(minutes=89)).actions == 0
    assert sweep_escalation_reminder(database, NOW + timedelta(minutes=90)).actions == 1

    intervals = [
        json.loads(row["payload_json"])["interval_minutes"]
        for row in database.read(
            "SELECT payload_json FROM task_events WHERE event_name = 'escalation_reminder'"
            " ORDER BY seq"
        )
    ]
    assert intervals == [60, 120]
    assert _task(database)["status"] == "escalated", "提醒不放行；human.release/adjudicate 才行"


def test_each_reminder_sweep_watches_only_its_own_state(tmp_path):
    database = _rig(tmp_path, status="escalated", attempt=False, owner=None, escalated_at=NOW)

    assert sweep_human_wait_timeout(database, NOW + timedelta(hours=2)).actions == 0
    assert _event_names(database) == [], "an escalated task is not a human-wait task"

    second = tmp_path / "second"
    second.mkdir()
    database2 = _rig(second, status="awaiting_human", attempt=False, owner=None, waiting_since=NOW)
    assert sweep_escalation_reminder(database2, NOW + timedelta(hours=2)).actions == 0
    assert _event_names(database2) == [], "an awaiting_human task is not an escalation"


# ------------------------------------------------------------------ HTTP e2e


def _http_rig(tmp_path):
    """Claim + start a real task over the real surface (start has no route yet:
    MVP-10a gap, so it is the one in-process call, exactly as test_cli_flow)."""
    client, database = make_client(tmp_path)
    bootstrap(client)
    publish_plan(client)
    plan_id = database.read("SELECT plan_id FROM plans LIMIT 1")[0]["plan_id"]
    approved = client.post(f"/api/v1/plans/{plan_id}/approve", json={"decided_by": "ops"})
    assert approved.status_code == 200, approved.text
    registration = HarnessRegistration(
        harness_id="harness-1",
        name="H",
        provider="fake",
        adapter="http",
        adapter_version="1.0",
        capabilities=["implementation"],
        registration_version="reg-1",
    )
    HarnessRegistry(database).register(registration)
    adapter = HttpAdapter(client, registration)
    claimed = adapter.claim(claim_request_id="cr-1", task_ids=["T-1"])
    assert claimed["items"][0]["outcome"] == "claimed", claimed
    context = adapter.attempts["T-1"]
    SchedulerService(database).start_task(
        task_id="T-1",
        attempt_id=context.attempt_id,
        fencing_epoch=context.fencing_epoch,
        worktrees=worktree_manager(tmp_path, database),
    )
    return client, database, adapter, context


def test_the_human_surface_over_http(tmp_path):
    client, database, adapter, context = _http_rig(tmp_path)

    asked = adapter.ask_human(task_id="T-1", question="接口形状确认")
    assert _task(database)["status"] == "awaiting_human"

    # §13 guard on the harness verb: a stale owner cannot park the task.
    stale = client.post(
        "/api/v1/adapter/ask_human",
        json={
            "task_id": "T-1",
            "attempt_id": context.attempt_id,
            "fencing_epoch": context.fencing_epoch + 1,
            "question": "again",
            "harness_id": "harness-1",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "fencing.epoch_stale"

    resumed = client.post(
        "/api/v1/human/resume",
        json={"task_id": "T-1", "decided_by": "ops", "answer": "按接口表"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "in_progress"
    (decision,) = _decisions(database)
    assert decision["decision_id"] == asked["decision_id"]
    assert decision["answer"] == "按接口表"

    cancelled = client.post(
        "/api/v1/human/cancel",
        json={"task_id": "T-1", "decided_by": "ops", "reason": "方向变了"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["event"] == "cancel"
    assert _task(database)["status"] == "cancelled"

    refused = client.post("/api/v1/human/release", json={"task_id": "T-1", "decided_by": "ops"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "request.conflict"


# ------------------------------------------------------------------- CLI e2e


def _run_cli(client, *argv: str) -> tuple[int, object, str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv), client=client)
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None), err.getvalue().strip()


def test_the_operator_cli_drives_the_human_surface(tmp_path):
    client, database, adapter, _context = _http_rig(tmp_path)
    adapter.ask_human(task_id="T-1", question="先问一句")

    code, body, err = _run_cli(
        client, "human-resume", "--task", "T-1", "--by", "ops", "--answer", "继续"
    )
    assert code == EXIT_OK, err
    assert body["status"] == "in_progress"
    assert _decisions(database)[0]["answer"] == "继续"

    # A second task, seeded escalated: the CLI adjudicates it into a decision.
    seed_task(database, task_id="T-2", status="escalated")
    code, body, err = _run_cli(
        client,
        "human-adjudicate",
        "--task",
        "T-2",
        "--by",
        "ops",
        "--question",
        "选哪个？",
        "--option",
        "A",
        "--option",
        "B",
    )
    assert code == EXIT_OK, err
    assert body["decision_id"]
    assert str(database.read("SELECT status FROM tasks WHERE task_id = 'T-2'")[0]["status"]) == (
        "awaiting_human"
    )

    code, body, err = _run_cli(client, "human-release", "--task", "T-2", "--by", "ops")
    assert code == EXIT_API_ERROR, "resume would need awaiting_human; release needs escalated"
    assert "request.conflict" in err

    code, body, err = _run_cli(
        client, "human-cancel", "--task", "T-1", "--by", "ops", "--reason", "收工"
    )
    assert code == EXIT_OK, err
    assert body["event"] == "cancel"
