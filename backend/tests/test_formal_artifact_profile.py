"""`formal-artifact` has one resolver, shared by the worker and the runtime.

Before this, the formal worker built the profile inline and the runtime only
knew the static registry, so the same snapshot was verified by one path and
skipped (silently, memory-only) by the other.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.adapter import formal_worker
from app.db.connection import Database
from app.runtime.driver import (
    VERIFY_PROFILE_UNRESOLVED_EVENT,
    RuntimeDriver,
    RuntimePolicy,
)
from app.verify.formal_artifact import (
    PROFILE_NAME,
    TIMEOUT_SECONDS,
    VERIFY_SCRIPT,
    FormalArtifactUnavailable,
    build_formal_artifact_policy,
)
from app.verify.profile import VerifyPolicy
from tests.merge_support import CHECK_FLAG, PROFILE, build_chain, status_of


def runtime_policy() -> RuntimePolicy:
    policy = VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {PROFILE: {"command": CHECK_FLAG, "timeout_seconds": 60}},
        }
    )
    return RuntimePolicy(verify=policy, integration_profile=PROFILE, source=None)


def driver_for(chain) -> RuntimeDriver:
    return RuntimeDriver(
        chain.database,
        chain.repo,
        runtime_policy(),
        overlay_root=chain.tmp_path / "runtime-ov",
        evidence_root=chain.tmp_path / "runtime-evidence",
    )


def link_contract(
    database: Database,
    task_id: str,
    profile: str,
    exclusive_paths: list[object] | None,
) -> None:
    """Point a task at a snapshot that names ``profile`` and its scope."""
    now = datetime.now(UTC).isoformat()
    spec: dict[str, object] = {"id": task_id, "verify_profile": profile}
    if exclusive_paths is not None:
        spec["exclusive_paths"] = exclusive_paths
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO plans(plan_id, group_id, status, plan_hash, created_at)"
            " VALUES (?, 'G-1', 'approved', ?, ?)",
            (f"plan-{task_id}", f"hash-{task_id}", now),
        )
        conn.execute(
            "INSERT INTO plan_snapshots(snapshot_id, plan_id, snapshot_hash,"
            " content_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                f"snap-{task_id}",
                f"plan-{task_id}",
                f"hash-{task_id}",
                json.dumps({"tasks": [spec]}),
                now,
            ),
        )
        conn.execute(
            "UPDATE tasks SET plan_snapshot_id = ? WHERE task_id = ?",
            (f"snap-{task_id}", task_id),
        )


def skip_events(database: Database) -> list[dict[str, object]]:
    rows = database.read(
        "SELECT payload_json FROM task_events WHERE event_name = ?",
        (VERIFY_PROFILE_UNRESOLVED_EVENT,),
    )
    return [json.loads(str(row["payload_json"])) for row in rows]


# --------------------------------------------------------------- shared resolver


def test_the_resolver_is_the_formal_worker_command(tmp_path):
    """(c) the worker's policy is byte-for-byte what it built before.

    The old inline construction is restated here on purpose: if the shared
    function ever drifts, this fingerprint stops matching.
    """
    exclusive_paths = ["flag.txt", "docs/x.md"]
    resolved = build_formal_artifact_policy(exclusive_paths)
    expected = VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                PROFILE_NAME: {
                    "command": [
                        "python",
                        str(VERIFY_SCRIPT),
                        json.dumps(exclusive_paths),
                    ],
                    "timeout_seconds": TIMEOUT_SECONDS,
                }
            },
        }
    )

    assert resolved.fingerprint() == expected.fingerprint()
    assert resolved.profile(PROFILE_NAME).command == expected.profile(PROFILE_NAME).command
    assert resolved.profile(PROFILE_NAME).timeout_seconds == 30
    # The worker really uses the shared function, not a second copy.
    assert formal_worker.build_formal_artifact_policy is build_formal_artifact_policy
    assert formal_worker.FORMAL_ARTIFACT_PROFILE == PROFILE_NAME


def test_the_resolver_refuses_paths_it_cannot_scope():
    for bad in ([], ["/abs/path"], ["../up"], ["C:/drive"], ["a\\b"], [""], ["ok", 3], "flag.txt"):
        with pytest.raises(FormalArtifactUnavailable):
            build_formal_artifact_policy(bad)


# ------------------------------------------------------------------- runtime


def test_a_declared_formal_artifact_profile_runs_instead_of_skipping(tmp_path):
    """(a) resolvable from the snapshot -> a real job and receipt, no skip event."""
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_contract(chain.database, "T-1", PROFILE_NAME, ["flag.txt"])
    driver = driver_for(chain)

    scan = driver.scan()
    assert scan.verify_skipped == ()
    assert len(scan.verify_candidates) == 1
    assert scan.verify_candidates[0].policy is not None

    report = driver.run_once()

    assert report.actions == 1
    assert report.verify[0].status == "passed"
    assert report.verify[0].next_status == "awaiting_review"
    assert status_of(chain) == "awaiting_review"
    receipts = chain.database.read(
        "SELECT baseline_exit_code, head_exit_code, verify_profile"
        " FROM verification_receipts WHERE task_id = 'T-1'"
    )
    assert len(receipts) == 1
    assert receipts[0]["verify_profile"] == PROFILE_NAME
    assert receipts[0]["baseline_exit_code"] == 1
    assert receipts[0]["head_exit_code"] == 0
    assert skip_events(chain.database) == []


def test_an_unresolvable_profile_is_skipped_once_and_recorded(tmp_path):
    """(b) empty scope -> one durable event, and only one across rounds."""
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_contract(chain.database, "T-1", PROFILE_NAME, [])
    driver = driver_for(chain)

    # --check calls scan(): it must stay read-only.
    scan = driver.scan()
    assert scan.verify_candidates == ()
    assert scan.verify_skipped[0].durable is True
    assert skip_events(chain.database) == []

    first = driver.run_once()
    second = driver.run_once()

    assert first.actions == 0
    assert second.actions == 0
    assert status_of(chain) == "verifying"
    events = skip_events(chain.database)
    assert len(events) == 1
    assert events[0]["task_id"] == "T-1"
    assert events[0]["attempt_id"] == chain.attempt_id
    assert events[0]["verify_profile"] == PROFILE_NAME
    assert "exclusive_paths" in str(events[0]["reason"])
