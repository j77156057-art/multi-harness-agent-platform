"""MVP-ADAPTER-001: the Fake Harness and the surface it proves.

The first test is the important one: it parses R10 §7 and asserts the adapter has
a method for every verb the spec lists. A fake harness that invented its own shape
would pass every other test here while proving nothing about what a real adapter
(10b) has to satisfy.
"""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapter.fake import FakeHarness
from app.adapter.http import HttpAdapter
from app.adapter.protocol import HarnessAdapter, HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.merge.worker import MergeWorker
from app.tasks.service import SchedulerService
from app.verify.worker import VerifyWorker
from tests.api_support import bootstrap, make_client, publish_plan, verify_policy
from tests.merge_support import PROFILE, register_harness
from tests.merge_support import policy as merge_policy

HARNESS = "fake-1"
REGISTRATION = HarnessRegistration(
    harness_id=HARNESS,
    name="Fake Harness",
    provider="fake",
    adapter="fake-cli",
    adapter_version="1.0.0",
    capabilities=("backend", "testing"),
    registration_version="reg-001",
)


# ----------------------------------------------------- the surface itself


def section_7_verbs(spec_path: Path) -> list[str]:
    text = spec_path.read_text(encoding="utf-8")
    start = text.index("## 7. Adapter 接口")
    end = text.index("## 8. ", start)
    block = re.search(r"~~~text\n(.*?)~~~", text[start:end], flags=re.S)
    assert block is not None
    verbs = []
    for line in block.group(1).splitlines():
        match = re.match(r"^([a-z_]+)\(", line.strip())
        if match:
            verbs.append(match.group(1))
    return verbs


def test_the_adapter_implements_every_section_7_verb(project_root):
    """R10 §7 is the truth source for the interface, not this package."""
    verbs = section_7_verbs(project_root / "docs" / "spec" / "0.3-R10.md")
    assert len(verbs) == 16, verbs
    missing = [verb for verb in verbs if not hasattr(HarnessAdapter, verb)]
    assert not missing, f"adapter surface is missing §7 verbs: {missing}"

    # The base class declares them abstract -- that is the surface. The concrete
    # adapter is what must not leave any of them unimplemented, which is what
    # "instantiable" checks.
    assert inspect.isabstract(HarnessAdapter), "the interface must declare the surface"
    assert not inspect.isabstract(HttpAdapter), "HttpAdapter leaves §7 verbs unimplemented"


def test_every_section_7_verb_speaks_http():
    """adapter-verbs closed the last three routes: every §7 verb now wires to
    the transport, and ``UnsupportedVerb`` is gone from the http module."""
    verbs = section_7_verbs(Path(__file__).resolve().parents[2] / "docs" / "spec" / "0.3-R10.md")
    unwired = [
        verb
        for verb in verbs
        if not {"_post", "_get"} & set(getattr(HttpAdapter, verb).__code__.co_names)
    ]
    assert not unwired, f"§7 verbs with no transport call: {unwired}"
    import app.adapter.http as http_module

    assert not hasattr(http_module, "UnsupportedVerb")


# ---------------------------------------------------------------- fixture


@dataclass(frozen=True, slots=True)
class Rig:
    client: Any
    database: Any
    repo: CanonicalRepo
    manager: WorktreeManager
    service: SchedulerService
    adapter: HttpAdapter
    harness: FakeHarness
    task_id: str


def build_rig(tmp_path: Path, *, register: bool = True) -> Rig:
    client, database = make_client(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    if register:
        HarnessRegistry(database).register(REGISTRATION)
        # rules-tighten F5: the review face needs both sides in the registry
        # with distinct providers; fake-1 (fake) owns, reviewer-1 (local) reviews.
        register_harness(database, "reviewer-1", provider="local")

    bootstrap(client)
    publish_plan(client, plan_id="PL-1", tasks=[{"id": "T-1"}])
    client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})

    adapter = HttpAdapter(client, REGISTRATION)
    claimed = adapter.claim(claim_request_id="cr-1", task_ids=["T-1"])
    item = claimed["items"][0]
    service.start_task(
        task_id="T-1",
        attempt_id=item["attempt_id"],
        fencing_epoch=item["fencing_epoch"],
        group_short="g1",
        worktrees=manager,
    )
    worktree = manager.workspace_for_attempt(item["attempt_id"])
    assert worktree is not None
    # The harness already claimed through the adapter; keep the context it needs.
    adapter.attempts["T-1"] = type(adapter.attempts["T-1"])(
        attempt_id=item["attempt_id"],
        fencing_epoch=item["fencing_epoch"],
        lease_token=item["lease_token"],
    )
    harness = FakeHarness(adapter, worktree=worktree.path)
    return Rig(
        client=client,
        database=database,
        repo=repo,
        manager=manager,
        service=service,
        adapter=adapter,
        harness=harness,
        task_id="T-1",
    )


def status_of(rig: Rig) -> str:
    rows = rig.database.read("SELECT status FROM tasks WHERE task_id = ?", (rig.task_id,))
    return str(rows[0]["status"])


# ------------------------------------------------------------ probe / caps


def test_an_unregistered_harness_probe_is_degraded(tmp_path):
    """R10 §6: a runtime self-report cannot on its own authorise a claim."""
    rig = build_rig(tmp_path, register=False)
    result = rig.harness.probe()
    assert result["degraded"] is True
    assert result["effective_capabilities"] == []
    assert any("registry" in warning for warning in result["warnings"])

    probes = rig.database.read("SELECT * FROM capability_probes WHERE harness_id = ?", (HARNESS,))
    assert len(probes) == 1, "the self-report is still recorded, for alarms and degradation"


def test_a_registered_harness_probe_is_effective(tmp_path):
    rig = build_rig(tmp_path)
    result = rig.harness.probe()
    assert result["degraded"] is False
    assert set(result["effective_capabilities"]) == {"backend", "testing"}
    assert result["registration_version"] == "reg-001"

    caps = rig.adapter.capabilities()
    assert caps["harness_id"] == HARNESS
    assert set(caps["capabilities"]) == {"backend", "testing"}


def test_registration_history_keeps_the_pinned_version(tmp_path):
    """A claim pins registration_version; the history is what makes it resolvable."""
    rig = build_rig(tmp_path)
    registry = HarnessRegistry(rig.database)
    registry.register(
        HarnessRegistration(
            harness_id=HARNESS,
            name="Fake Harness",
            provider="fake",
            adapter="fake-cli",
            adapter_version="1.1.0",
            capabilities=("backend", "testing", "docs"),
            registration_version="reg-002",
        )
    )
    pinned = registry.pinned(HARNESS, "reg-001")
    assert pinned is not None
    assert pinned["adapter_version"] == "1.0.0"
    assert str(pinned["superseded_by"]) == "reg-002"


# ------------------------------------------------------------- the loop


def test_the_fake_harness_runs_the_whole_loop(tmp_path):
    rig = build_rig(tmp_path)
    result = rig.harness.work(
        task_id=rig.task_id,
        claim_request_id="cr-work",
        files={"flag.txt": "ok\n"},
        message="feat: make the check pass",
    )

    assert result.branch.startswith("agent/g1/")
    assert rig.adapter.attempts[rig.task_id].attempt_id == result.attempt_id
    assert result.submit["event"] == "submit_result"
    assert status_of(rig) == "verifying"

    VerifyWorker(
        rig.database,
        rig.repo,
        merge_policy(),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "evidence",
    ).run(
        task_id=rig.task_id,
        attempt_id=result.attempt_id,
        commit=result.commit,
        base_commit=rig.database.read(
            "SELECT base_commit FROM tasks WHERE task_id = ?", (rig.task_id,)
        )[0]["base_commit"],
        verify_profile=PROFILE,
        fencing_epoch=rig.adapter.attempts[rig.task_id].fencing_epoch,
    )
    assert status_of(rig) == "awaiting_review"

    rig.service.review_claim(
        task_id=rig.task_id, attempt_id=result.attempt_id, reviewer_harness_id="reviewer-1"
    )
    rig.service.submit_review(
        task_id=rig.task_id,
        attempt_id=result.attempt_id,
        reviewer_harness_id="reviewer-1",
        verdict="approve",
        reviewed_commit=result.commit,
        review_revision=1,
        findings=[],
    )
    assert status_of(rig) == "approved"

    outcome = MergeWorker(
        rig.database, rig.repo, policy=verify_policy(), overlay_root=tmp_path / "merge-ov"
    ).merge(
        task_id=rig.task_id,
        attempt_id=result.attempt_id,
        commit=result.commit,
        branch=result.branch,
    )
    assert outcome.status == "merged", outcome.detail
    assert status_of(rig) == "merged"


def test_a_failing_result_never_reaches_review(tmp_path):
    rig = build_rig(tmp_path)
    result = rig.harness.work(
        task_id=rig.task_id,
        claim_request_id="cr-fail",
        files={"not-the-flag.txt": "nope\n"},
    )
    VerifyWorker(
        rig.database,
        rig.repo,
        merge_policy(),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "evidence",
    ).run(
        task_id=rig.task_id,
        attempt_id=result.attempt_id,
        commit=result.commit,
        base_commit=rig.database.read(
            "SELECT base_commit FROM tasks WHERE task_id = ?", (rig.task_id,)
        )[0]["base_commit"],
        verify_profile=PROFILE,
        fencing_epoch=rig.adapter.attempts[rig.task_id].fencing_epoch,
    )
    assert status_of(rig) == "changes_requested"


def test_abandon_ends_the_attempt(tmp_path):
    rig = build_rig(tmp_path)
    rig.harness.take(task_id=rig.task_id, claim_request_id="cr-abandon")
    rig.harness.give_up(task_id=rig.task_id, reason="cannot proceed")

    assert status_of(rig) == "abandoned"
    assert rig.task_id not in rig.adapter.attempts, "the harness forgets its attempt"


def test_a_stale_attempt_can_be_taken_over(tmp_path):
    """The lease expiring is what makes a task recoverable, not a lost cause."""
    rig = build_rig(tmp_path)
    rig.harness.take(task_id=rig.task_id, claim_request_id="cr-stale")
    attempt = rig.adapter.attempts[rig.task_id]

    from datetime import UTC, datetime, timedelta

    from app.claim.service import ClaimService
    from app.lease.service import expire_stale_leases

    expire_stale_leases(rig.database, now=datetime.now(UTC) + timedelta(hours=1))
    assert status_of(rig) == "stale"

    recovery = ClaimService(rig.database)
    recovery.reclaim(rig.task_id, grace_elapsed=True)
    assert status_of(rig) == "reclaiming"

    epoch = rig.database.read("SELECT current_epoch FROM tasks WHERE task_id = ?", (rig.task_id,))[
        0
    ]["current_epoch"]
    taken = recovery.take_over(rig.task_id, harness_id="fake-2", fencing_epoch=epoch)
    assert status_of(rig) == "claimed"
    assert taken.owner == "fake-2"
    assert taken.fencing_epoch == attempt.fencing_epoch + 1


def test_a_stale_harness_cannot_keep_working(tmp_path):
    """After a takeover the old epoch is refused, which is what fencing is for."""
    rig = build_rig(tmp_path)
    rig.harness.take(task_id=rig.task_id, claim_request_id="cr-stale2")
    old = rig.adapter.attempts[rig.task_id]

    from datetime import UTC, datetime, timedelta

    from app.claim.service import ClaimService
    from app.lease.service import expire_stale_leases

    expire_stale_leases(rig.database, now=datetime.now(UTC) + timedelta(hours=1))
    recovery = ClaimService(rig.database)
    recovery.reclaim(rig.task_id, grace_elapsed=True)
    epoch = rig.database.read("SELECT current_epoch FROM tasks WHERE task_id = ?", (rig.task_id,))[
        0
    ]["current_epoch"]
    recovery.take_over(rig.task_id, harness_id="fake-2", fencing_epoch=epoch)

    code = rig.harness.last_error_code(
        lambda: rig.adapter.heartbeat(task_id=rig.task_id, fencing_epoch=old.fencing_epoch)
    )
    assert code == "fencing.epoch_stale"


def test_help_request_moves_the_task_without_losing_the_attempt(tmp_path):
    rig = build_rig(tmp_path)
    rig.harness.take(task_id=rig.task_id, claim_request_id="cr-help")
    rig.harness.ask_for_help(task_id=rig.task_id, reason="stuck on the schema")
    assert status_of(rig) == "needs_help"
    row = rig.database.read(
        "SELECT current_attempt_id FROM tasks WHERE task_id = ?", (rig.task_id,)
    )[0]
    assert row["current_attempt_id"] is not None, "the owner keeps the attempt while asking"


def test_release_returns_the_task_to_the_pool(tmp_path):
    rig = build_rig(tmp_path)
    rig.harness.take(task_id=rig.task_id, claim_request_id="cr-release")
    rig.harness.hand_back(task_id=rig.task_id)
    assert status_of(rig) == "ready"
