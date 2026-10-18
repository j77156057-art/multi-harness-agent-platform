"""CORE-RUNTIME-001 (runtime-driver): the run loop that was missing (F6).

The drill proved the gap this module closes: ``verifying`` and ``approved``
were states nothing in the live platform ever moved out of -- the workers
existed, but only tests drove them. These tests drive them the same way the
platform now does: through the driver's scan/run_once and the CLI, on real
chains (claim -> start -> commit -> publish -> submit, real git, real
overlays), never by constructing state the platform could not have produced.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.config import settings
from app.db.connection import Database
from app.git.repo import CanonicalRepo, run_git
from app.runtime import __main__ as runtime_cli
from app.runtime.driver import (
    RuntimeDriver,
    RuntimePolicy,
    RuntimePolicyError,
    load_runtime_policy,
)
from app.verify.profile import VerifyPolicy
from tests.claim_support import seed_task
from tests.merge_support import (
    CHECK_FLAG,
    HARNESS,
    PROFILE,
    approve,
    build_chain,
    status_of,
)


def runtime_policy(*, integration_profile: str | None = PROFILE) -> RuntimePolicy:
    policy = VerifyPolicy.from_mapping(
        {
            "command_roots": ["python"],
            "profiles": {
                PROFILE: {"command": CHECK_FLAG, "timeout_seconds": 60},
                "lint-only": {
                    "command": ["python", "-c", "import sys; sys.exit(0)"],
                    "timeout_seconds": 60,
                },
            },
        }
    )
    return RuntimePolicy(verify=policy, integration_profile=integration_profile, source=None)


def driver_for(chain, *, concurrent_verify: int = 3, policy: RuntimePolicy | None = None):
    return RuntimeDriver(
        chain.database,
        chain.repo,
        policy or runtime_policy(),
        concurrent_verify=concurrent_verify,
        overlay_root=chain.tmp_path / "runtime-ov",
        evidence_root=chain.tmp_path / "runtime-evidence",
    )


def link_snapshot(database: Database, task_id: str, profile: str | None) -> None:
    """Point a task at a plan snapshot, exactly where the driver reads profiles.

    ``seed_task`` only writes a snapshot when a budget or resource is declared,
    and never includes ``verify_profile``; the driver refuses to guess a
    command, so a candidate needs this row to exist for real.
    """
    now = datetime.now(UTC).isoformat()
    spec: dict[str, object] = {"id": task_id}
    if profile is not None:
        spec["verify_profile"] = profile
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


def spawn_task(chain, task_id: str) -> tuple[str, str]:
    """One more real chain on an existing env: claim -> ... -> submit (verifying)."""
    from app.claim.service import ClaimRequest, ClaimService
    from app.git.publish import PublishProxy

    database, repo, manager = chain.database, chain.repo, chain.manager
    seed_task(database, task_id=task_id)
    item = (
        ClaimService(database)
        .claim(
            ClaimRequest(claim_request_id=f"cr-{task_id}", harness_id=HARNESS, task_ids=(task_id,))
        )
        .items[0]
    )
    chain.service.start_task(
        task_id=task_id,
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        group_short="g1",
        worktrees=manager,
    )
    worktree = manager.workspace_for_attempt(str(item.attempt_id))
    assert worktree is not None
    # flag.txt satisfies CHECK_FLAG for verify; the per-task content makes a
    # later merge of a second chain conflict deterministically (add/add with
    # different bytes).
    (worktree.path / "flag.txt").write_text(f"ok {task_id}\n", encoding="utf-8")
    (worktree.path / f"{task_id}.txt").write_text("ok\n", encoding="utf-8")
    run_git("add", "flag.txt", f"{task_id}.txt", cwd=worktree.path)
    run_git("commit", "-m", f"feat: {task_id}", cwd=worktree.path)
    head = run_git("rev-parse", "HEAD", cwd=worktree.path).stdout.strip()
    PublishProxy(database, repo).publish_task_branch(
        task_id=task_id,
        attempt_id=str(item.attempt_id),
        branch=worktree.branch,
        commit=head,
        fencing_epoch=int(item.fencing_epoch),
        idempotency_key=f"pub-{task_id}",
    )
    chain.service.submit_result(
        task_id=task_id,
        attempt_id=str(item.attempt_id),
        fencing_epoch=int(item.fencing_epoch),
        completion={"commit": head},
    )
    return str(item.attempt_id), head


def review_count(database: Database) -> int:
    return int(database.read("SELECT COUNT(*) AS n FROM reviews")[0]["n"])


# --------------------------------------------------------------------- scan


def test_scan_sources_every_field_from_the_real_state(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", PROFILE)

    scan = driver_for(chain).scan()

    assert status_of(chain) == "verifying"
    assert scan.merge_candidates == ()
    assert len(scan.verify_candidates) == 1
    candidate = scan.verify_candidates[0]
    assert candidate.task_id == "T-1"
    assert candidate.attempt_id == chain.attempt_id
    assert candidate.commit == chain.head_commit
    assert candidate.base_commit == chain.base_commit
    assert candidate.verify_profile == PROFILE
    assert candidate.fencing_epoch == chain.fencing_epoch


def test_scan_refuses_to_guess_a_missing_profile(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", None)

    scan = driver_for(chain).scan()

    assert scan.verify_candidates == ()
    assert len(scan.verify_skipped) == 1
    assert "no verify_profile" in scan.verify_skipped[0].reason


def test_scan_skips_a_profile_the_policy_does_not_declare(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", "ghost-check")

    scan = driver_for(chain).scan()

    assert scan.verify_candidates == ()
    assert "not in the runtime policy" in scan.verify_skipped[0].reason


def test_scan_skips_when_a_passing_receipt_already_exists(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=True)
    # auto_verify left the task at awaiting_review; put it back at verifying
    # without touching the receipt -- the exact crash-window shape the skip is
    # for (though the worker writes receipt+transition in one transaction).
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status='verifying' WHERE task_id='T-1'")
    link_snapshot(chain.database, "T-1", PROFILE)

    scan = driver_for(chain).scan()

    assert scan.verify_candidates == ()
    assert "passing receipt already exists" in scan.verify_skipped[0].reason


# ---------------------------------------------------------------- execution


def test_run_once_drives_verify_to_awaiting_review(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", PROFILE)

    report = driver_for(chain).run_once()

    assert report.actions == 1
    assert len(report.verify) == 1
    run = report.verify[0]
    assert run.status == "passed"
    assert run.next_status == "awaiting_review"
    assert run.replayed is False
    assert status_of(chain) == "awaiting_review"
    receipts = chain.database.read("SELECT COUNT(*) AS n FROM verification_receipts")
    assert int(receipts[0]["n"]) == 1
    # The job row now has a producer (the sweep's disclosure said it had none).
    jobs = chain.database.read("SELECT status FROM verify_jobs")
    assert [str(row["status"]) for row in jobs] == ["passed"]


def test_a_failing_verify_lands_in_changes_requested(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", PROFILE)
    failing = RuntimePolicy(
        verify=VerifyPolicy.from_mapping(
            {
                "command_roots": ["python"],
                "profiles": {
                    PROFILE: {
                        "command": ["python", "-c", "import sys; sys.exit(1)"],
                        "timeout_seconds": 60,
                    }
                },
            }
        ),
        integration_profile=PROFILE,
        source=None,
    )

    report = driver_for(chain, policy=failing).run_once()

    assert report.verify[0].status == "failed"
    assert report.verify[0].detail == "code_failed"
    assert status_of(chain) == "changes_requested"


def test_second_round_is_a_noop(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", PROFILE)
    driver = driver_for(chain)

    first = driver.run_once()
    second = driver.run_once()

    assert first.actions == 1
    assert second.actions == 0
    assert second.verify == ()
    assert second.scan.verify_candidates == ()
    receipts = chain.database.read("SELECT COUNT(*) AS n FROM verification_receipts")
    assert int(receipts[0]["n"]) == 1


def test_verify_concurrency_stops_at_the_cap(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    for task_id in ("T-2", "T-3", "T-4"):
        spawn_task(chain, task_id)
    for task_id in ("T-1", "T-2", "T-3", "T-4"):
        link_snapshot(chain.database, task_id, PROFILE)
    driver = driver_for(chain, concurrent_verify=3)

    first = driver.run_once()
    assert len(first.verify) == 3
    remaining = [
        str(row["task_id"])
        for row in chain.database.read(
            "SELECT task_id FROM tasks WHERE status='verifying' ORDER BY task_id"
        )
    ]
    assert len(remaining) == 1

    second = driver.run_once()
    assert len(second.verify) == 1
    assert str(second.verify[0].task_id) == remaining[0]
    assert (
        chain.database.read("SELECT COUNT(*) AS n FROM tasks WHERE status='awaiting_review'")[0][
            "n"
        ]
        == 4
    )


def test_merge_round_merges_one_and_leaves_the_rest_for_the_next(tmp_path):
    """§21 backpressure: one successful integration write per round."""
    first = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(first.database, "T-1", PROFILE)
    attempt2, head2 = spawn_task(first, "T-2")
    link_snapshot(first.database, "T-2", PROFILE)
    second = replace(first, task_id="T-2", attempt_id=attempt2, head_commit=head2)
    driver = driver_for(first)

    assert driver.run_once().actions == 2  # both verifying -> awaiting_review
    assert status_of(first) == "awaiting_review"
    assert status_of(second) == "awaiting_review"
    approve(first)
    approve(second)

    round_one = driver.run_once()
    assert len(round_one.merge) == 1
    assert round_one.merge[0].task_id == "T-1"
    assert round_one.merge[0].status == "merged"
    # The budget is spent: the second approval is untouched this round.
    assert status_of(second) == "approved"

    round_two = driver.run_once()
    assert len(round_two.merge) == 1
    assert round_two.merge[0].task_id == "T-2"
    # Both chains added flag.txt (different bytes) -> deterministic add/add
    # conflict, and it travels through the driver as an outcome, not a crash.
    assert round_two.merge[0].status == "conflict"
    assert status_of(second) != "approved"


def test_merge_run_reaches_merged(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=True)
    approve(chain)
    reviews_before = review_count(chain.database)
    integration_before = chain.repo.ref_commit(chain.repo.integration_ref)

    report = driver_for(chain).run_once()

    assert status_of(chain) == "merged"
    assert len(report.merge) == 1
    run = report.merge[0]
    assert run.status == "merged"
    assert run.next_status == "merged"
    assert run.merge_commit is not None
    integration_after = chain.repo.ref_commit(chain.repo.integration_ref)
    assert integration_after == run.merge_commit
    assert integration_after != integration_before
    # The boundary in the card: the runtime never drives review.
    assert review_count(chain.database) == reviews_before


def test_merge_gate_refusal_is_reported_not_raised(tmp_path):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=True)
    # approved, but no review was ever recorded -- the gate must say so.
    with chain.database.transaction() as conn:
        conn.execute("UPDATE tasks SET status='approved' WHERE task_id='T-1'")

    report = driver_for(chain).run_once()

    assert len(report.merge) == 1
    assert report.merge[0].status == "blocked"
    assert "no approving review" in str(report.merge[0].detail)
    assert status_of(chain) == "approved"


# ------------------------------------------------------------------- policy


def test_policy_file_round_trip_and_refusals(tmp_path):
    good = tmp_path / "policy.json"
    good.write_text(
        json.dumps(
            {
                "command_roots": ["python"],
                "profiles": {PROFILE: {"command": CHECK_FLAG}},
                "integration_profile": PROFILE,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_runtime_policy(good)
    assert loaded.source == good
    assert loaded.effective_integration_profile() == PROFILE
    narrowed = loaded.merge_policy()
    assert narrowed is not None and list(narrowed.profiles) == [PROFILE]

    with pytest.raises(RuntimePolicyError, match="not found"):
        load_runtime_policy(tmp_path / "nope.json")

    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(RuntimePolicyError, match="not valid JSON"):
        load_runtime_policy(bad)

    unknown = tmp_path / "unknown.json"
    unknown.write_text(
        json.dumps(
            {
                "command_roots": ["python"],
                "profiles": {PROFILE: {"command": CHECK_FLAG}},
                "integration_profile": "ghost",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimePolicyError, match="ghost"):
        load_runtime_policy(unknown)


def _commit_repo_policy(work, repo: CanonicalRepo) -> None:
    run_git("add", "verify-profiles.json", cwd=work)
    run_git(
        "-c",
        "user.name=test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "repo policy",
        cwd=work,
    )
    commit = run_git("rev-parse", "HEAD", cwd=work).stdout.strip()
    repo.import_objects(work, commit)
    repo.update_ref(repo.integration_ref, commit)


def test_repo_policy_follows_the_repository(tmp_path):
    """A repo carries its own verify profiles; the platform keeps the roots.

    The merge gate used to run the platform's ``backend-default`` (pytest) for
    every project, so a JavaScript repository could never merge. The profiles
    now come from the repository under test, while the command-root allowlist
    stays human-maintained in the platform policy.
    """
    platform = tmp_path / "policy.json"
    platform.write_text(
        json.dumps(
            {
                "command_roots": ["python"],
                "profiles": {PROFILE: {"command": CHECK_FLAG}},
                "integration_profile": PROFILE,
            }
        ),
        encoding="utf-8",
    )

    repo = CanonicalRepo(tmp_path / "canonical.git")
    run_git("init", "--bare", "--initial-branch", "integration", str(repo.path))
    work = tmp_path / "work"
    run_git("init", "--initial-branch", "integration", str(work))
    policy_file = work / "verify-profiles.json"
    policy_file.write_text(
        json.dumps(
            {
                "profiles": {"office-preview": {"command": CHECK_FLAG}},
                "integration_profile": "office-preview",
            }
        ),
        encoding="utf-8",
    )
    _commit_repo_policy(work, repo)

    loaded = load_runtime_policy(platform, repo=repo)
    assert loaded.repo_source == "integration:verify-profiles.json"
    assert loaded.effective_integration_profile() == "office-preview"
    # The repo adds a profile; the platform's profiles and roots stay.
    assert set(loaded.verify.profiles) == {PROFILE, "office-preview"}
    assert loaded.verify.command_roots == frozenset({"python"})

    # A repo may name a command, never widen the human allowlist.
    policy_file.write_text(
        json.dumps(
            {
                "profiles": {"office-preview": {"command": ["bash", "-c", "true"]}},
                "integration_profile": "office-preview",
            }
        ),
        encoding="utf-8",
    )
    _commit_repo_policy(work, repo)
    with pytest.raises(RuntimePolicyError, match="allowlist"):
        load_runtime_policy(platform, repo=repo)


# ---------------------------------------------------------------------- CLI


def test_cli_check_is_read_only(tmp_path, monkeypatch, capsys):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", PROFILE)
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "command_roots": ["python"],
                "profiles": {PROFILE: {"command": CHECK_FLAG}},
                "integration_profile": PROFILE,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_policy_file", policy_file)
    monkeypatch.setattr(settings, "db_path", chain.database.path)
    monkeypatch.setattr(settings, "canonical_repo", chain.repo.path)

    code = runtime_cli.main(["--check"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["policy"]["integration_profile"] == PROFILE
    assert [item["task_id"] for item in payload["scan"]["verify"]] == ["T-1"]
    # read-only: nothing moved and nothing was written
    assert status_of(chain) == "verifying"
    assert chain.database.read("SELECT COUNT(*) AS n FROM verification_receipts")[0]["n"] == 0


def test_cli_refuses_without_a_usable_policy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(settings, "runtime_policy_file", tmp_path / "missing.json")

    code = runtime_cli.main(["--check"])

    assert code == 2
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "refused" in payload
    assert "not found" in payload["refused"]


def test_cli_once_runs_a_round(tmp_path, monkeypatch, capsys):
    chain = build_chain(tmp_path, task_id="T-1", auto_verify=False)
    link_snapshot(chain.database, "T-1", PROFILE)
    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "command_roots": ["python"],
                "profiles": {PROFILE: {"command": CHECK_FLAG}},
                "integration_profile": PROFILE,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "runtime_policy_file", policy_file)
    monkeypatch.setattr(settings, "db_path", chain.database.path)
    monkeypatch.setattr(settings, "canonical_repo", chain.repo.path)

    code = runtime_cli.main(["--once"])

    assert code == 0
    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["actions"] == 1
    assert payload["verify"][0]["status"] == "passed"
    assert status_of(chain) == "awaiting_review"
