"""MVP-11 / CORE-STATE-COVERAGE-001 support: the R10 §17 secret boundary.

The contract from the MVP-08 prep, asserted here rather than assumed:

* a body the API accepts (completion, findings, message text) is scanned
  **before** anything is persisted -- the test proves it by searching the
  whole database for the canary after the rejection;
* the rejection is ``security.secret_scan_blocked`` / ``message.secret_scan_blocked``
  (422) with ``details`` limited to exactly ``scan_id`` and ``rule_name``
  (prep D-8 -- no fragments, no line numbers, no context);
* the ``secret_scan_blocked`` event carries position, rule and value hash;
* a recorded block stops the Merge Gate (``merge.secret_scan_blocking``),
  fail-closed, before anything is written to the integration branch.
"""

from __future__ import annotations

import json

import pytest

from app.claim.service import ClaimRequest, ClaimService
from app.events.store import EventStore
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.merge.gate import MergeGateRejected, evaluate_merge_gate
from app.security.scan import scan_payload, scan_text
from app.tasks.service import SchedulerService
from tests.api_support import bootstrap, make_client, publish_plan
from tests.merge_support import approve, build_chain
from tests.test_merge import worker_for

CANARY_AWS = "AKIAIOSFODNN7EXAMPLE"
CANARY_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
CANARY_PK = "-----BEGIN RSA PRIVATE KEY-----"


# ---------------------------------------------------------------- the scanner


def test_the_scanner_catches_high_signal_tokens_and_skips_hashes():
    for canary, rule in (
        (CANARY_AWS, "aws_access_key_id"),
        (CANARY_GH, "github_token"),
        (CANARY_PK, "private_key_block"),
        ('api_key = "s3cr3t-material-abcdefghijkl"', "generic_credential"),
    ):
        findings = scan_text(f"prefix {canary} suffix", location="body.text")
        assert [f.rule_name for f in findings] == [rule], canary
        finding = findings[0]
        assert finding.location == "body.text"
        assert canary not in finding.value_hash, "the hash must not carry the value"
        assert len(finding.value_hash) == 64

    # A pure-hex digest is not a credential; commit shas must not trip the scan.
    assert scan_text("commit 0123456789abcdef0123456789abcdef01234567", location="x") == []
    assert scan_text("all clean here", location="x") == []


def test_the_scanner_walks_nested_payloads_with_dotted_locations():
    payload = {"summary": "ok", "nested": [{"note": f"uses {CANARY_AWS}"}]}
    findings = scan_payload(payload, location="completion")
    assert [f.location for f in findings] == ["completion.nested[0].note"]


# ------------------------------------------------------------------ the API


def build_rig(tmp_path):
    client, database = make_client(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    bootstrap(client)
    publish_plan(client, plan_id="PL-1", tasks=[{"id": "T-1"}])
    response = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})
    assert response.status_code == 200, response.text
    item = (
        ClaimService(database)
        .claim(ClaimRequest(claim_request_id="cr-1", harness_id="harness-1", task_ids=("T-1",)))
        .items[0]
    )
    service.start_task(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=manager,
    )
    return client, database, service, item


def assert_canary_nowhere(database, canary: str) -> None:
    events = database.read("SELECT COUNT(*) AS n FROM task_events")
    for table, column in (("task_events", "payload_json"), ("task_messages", "body")):
        rows = database.read(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {column} LIKE ?", (f"%{canary}%",)
        )
        assert rows[0]["n"] == 0, f"canary reached {table}.{column} (events total {events[0]['n']})"


def test_a_secret_in_a_completion_is_rejected_before_anything_is_persisted(tmp_path):
    client, database, service, item = build_rig(tmp_path)
    response = client.post(
        "/api/v1/adapter/submit_result",
        json={
            "task_id": "T-1",
            "attempt_id": item.attempt_id,
            "fencing_epoch": item.fencing_epoch,
            "completion": {"commit": "abc123", "note": f"deploy key {CANARY_AWS}"},
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "security.secret_scan_blocked"
    assert error["retryable"] is False
    assert error["task_id"] == "T-1" and isinstance(error["seq"], int)
    assert set(error["details"]) == {"scan_id", "rule_name"}
    assert error["details"]["rule_name"] == "aws_access_key_id"
    assert CANARY_AWS not in response.text, "the error body must not echo the match"

    # Nothing was persisted: no submit event, status unchanged, no canary.
    status = database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"]
    assert status == "in_progress"
    submits = database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'submit_result'"
    )
    assert submits[0]["n"] == 0
    assert_canary_nowhere(database, CANARY_AWS)

    blocked = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = 'secret_scan_blocked'"
    )
    assert len(blocked) == 1
    payload = json.loads(str(blocked[0]["payload_json"]))
    assert payload["surface"] == "completion"
    assert payload["attempt_id"] == item.attempt_id
    section = payload["findings"][0]
    assert section["rule_name"] == "aws_access_key_id"
    assert section["location"] == "completion.note"
    assert len(section["value_hash"]) == 64


def test_a_secret_in_a_message_is_rejected_and_the_body_is_not_persisted(tmp_path):
    client, database, _service, _item = build_rig(tmp_path)
    response = client.post(
        "/api/v1/groups/G-1/messages",
        json={
            "author_kind": "harness",
            "author_id": "harness-1",
            "body": f"here is the token {CANARY_GH}",
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "message.secret_scan_blocked"
    assert set(error["details"]) == {"scan_id", "rule_name"}
    assert CANARY_GH not in response.text

    messages = database.read("SELECT COUNT(*) AS n FROM task_messages")
    assert messages[0]["n"] == 0, "the body must not be persisted"
    assert_canary_nowhere(database, CANARY_GH)
    assert (
        database.read(
            "SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'secret_scan_blocked'"
        )[0]["n"]
        == 1
    )


def test_a_secret_in_review_findings_is_rejected_before_the_state_lookup(tmp_path):
    """Scanning happens before validation -- a blocked body is never trusted.

    The call is made from a clean rig (task still ``in_progress``), so the
    state check would also refuse it; the scan must fire first, which is what
    fail-closed means here.
    """
    client, database, _service, item = build_rig(tmp_path)
    response = client.post(
        "/api/v1/review/submit",
        json={
            "task_id": "T-1",
            "attempt_id": item.attempt_id,
            "reviewer_harness_id": "reviewer-1",
            "verdict": "approve",
            "reviewed_commit": "abc123",
            "review_revision": 1,
            "findings": [{"note": f"see {CANARY_PK}"}],
        },
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "security.secret_scan_blocked"
    assert database.read("SELECT COUNT(*) AS n FROM reviews")[0]["n"] == 0
    assert_canary_nowhere(database, CANARY_PK)


# --------------------------------------------------------------- merge gate


def test_a_recorded_secret_block_stops_the_merge(tmp_path):
    chain = build_chain(tmp_path)
    approve(chain)

    # Control: without a recorded block the gate is green.
    control = evaluate_merge_gate(
        chain.database,
        chain.repo,
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )
    assert control.ok is True, control.failures
    assert control.code is None

    EventStore(chain.database).append(
        event_name="secret_scan_blocked",
        aggregate_type="task",
        aggregate_id=chain.task_id,
        actor_type="harness",
        actor_id="harness-1",
        payload={
            "scan_id": "scan-planted",
            "surface": "completion",
            "findings": [
                {
                    "rule_name": "aws_access_key_id",
                    "location": "completion.note",
                    "value_hash": "0" * 64,
                }
            ],
            "task_id": chain.task_id,
            "attempt_id": chain.attempt_id,
        },
    )

    blocked = evaluate_merge_gate(
        chain.database,
        chain.repo,
        task_id=chain.task_id,
        attempt_id=chain.attempt_id,
        commit=chain.head_commit,
        branch=chain.branch,
    )
    assert blocked.ok is False
    assert blocked.code == "merge.secret_scan_blocking"
    assert any("secret scan scan-planted" in failure for failure in blocked.failures)

    # And the worker refuses with the same code, before anything is written.
    integration_before = chain.repo.ref_commit(chain.repo.integration_ref)
    with pytest.raises(MergeGateRejected) as caught:
        worker_for(chain).merge(
            task_id=chain.task_id,
            attempt_id=chain.attempt_id,
            commit=chain.head_commit,
            branch=chain.branch,
        )
    assert caught.value.code == "merge.secret_scan_blocking"
    assert chain.repo.ref_commit(chain.repo.integration_ref) == integration_before
    status = chain.database.read("SELECT status FROM tasks WHERE task_id = ?", (chain.task_id,))[0]
    assert str(status["status"]) == "approved", "a refused merge changes nothing"
