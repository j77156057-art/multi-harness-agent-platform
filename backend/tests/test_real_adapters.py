"""MVP-ADAPTER-002 / R10 §25 phase 5: the real harness adapters.

What is real here and what is substituted, stated plainly:

* Real: the adapter classes (registration, argv contract, output parsing), the
  worker loop, the HTTP surface, worktrees, the push proxy, verify, review and
  merge.
* Substituted: the model process. Most tests drive the worker with a
  :class:`ScriptedRunner` that performs in the worktree what the CLI would;
  the CLI invocation itself is pinned by the argv-contract tests, and the
  production :class:`SubprocessRunner`'s kill-on-deadline path is tested
  against a real child process. The contracts were checked against installed
  CLIs (claude 2.1.274, opencode v2.0.22); ``codex`` was not installed here
  and its module docstring says its contract is documentation-only.
"""

from __future__ import annotations

import inspect
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.adapter.harnesses import (
    CLAUDE_CODE_REGISTRATION,
    CODEX_REGISTRATION,
    OPENCODE_REGISTRATION,
    ClaudeCodeAdapter,
    CliHarnessWorker,
    CliRunResult,
    CliUnavailable,
    CodexAdapter,
    HarnessTimeout,
    OpenCodeAdapter,
    SubprocessRunner,
    WorkRefused,
)
from app.adapter.http import AdapterError, UnsupportedVerb
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeManager
from app.merge.worker import MergeWorker
from app.tasks.service import SchedulerService
from app.verify.worker import VerifyWorker
from tests.api_support import bootstrap, make_client, publish_plan, verify_policy
from tests.claim_support import make_database
from tests.merge_support import PROFILE
from tests.merge_support import policy as merge_policy
from tests.test_adapter import section_7_verbs

CODEX_OUT = '{"type":"session.created","session_id":"cx-1"}\n{"msg":"done: flag.txt"}\n'
CLAUDE_OUT = '{"session_id":"cc-1","result":"done: flag.txt","total_cost_usd":0.012}'
OPENCODE_OUT = '{"session_id":"oc-1","text":"done: flag.txt"}'


@dataclass(frozen=True)
class HarnessCase:
    name: str
    cls: type
    registration: HarnessRegistration
    flags: list[str]
    stdout: str
    session_id: str


CODEX_CASE = HarnessCase(
    name="codex",
    cls=CodexAdapter,
    registration=CODEX_REGISTRATION,
    flags=["exec", "--json", "--full-auto"],
    stdout=CODEX_OUT,
    session_id="cx-1",
)
CLAUDE_CASE = HarnessCase(
    name="claude-code",
    cls=ClaudeCodeAdapter,
    registration=CLAUDE_CODE_REGISTRATION,
    flags=["-p", "--output-format", "json", "--permission-mode", "acceptEdits"],
    stdout=CLAUDE_OUT,
    session_id="cc-1",
)
OPENCODE_CASE = HarnessCase(
    name="opencode",
    cls=OpenCodeAdapter,
    registration=OPENCODE_REGISTRATION,
    flags=["run", "--format", "json", "--auto"],
    stdout=OPENCODE_OUT,
    session_id="oc-1",
)

CASES = [
    pytest.param(CODEX_CASE, id="codex"),
    pytest.param(CLAUDE_CASE, id="claude-code"),
    pytest.param(OPENCODE_CASE, id="opencode"),
]


@dataclass
class Rig:
    client: Any
    database: Any
    repo: CanonicalRepo
    manager: WorktreeManager
    service: SchedulerService
    case: HarnessCase


def build_rig(tmp_path: Path, case: HarnessCase) -> Rig:
    client, database = make_client(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    manager = WorktreeManager(database, repo, root=tmp_path / "wt")
    service = SchedulerService(database)
    HarnessRegistry(database).register(case.registration)
    bootstrap(client)
    publish_plan(client, plan_id="PL-1", tasks=[{"id": "T-1"}])
    response = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})
    assert response.status_code == 200, response.text
    return Rig(client, database, repo, manager, service, case)


def adapter_for(rig: Rig):
    return rig.case.cls(rig.client)


def status_of(rig: Rig) -> str:
    return str(rig.database.read("SELECT status FROM tasks WHERE task_id = 'T-1'")[0]["status"])


def provision(rig: Rig, item: dict[str, Any]) -> Path:
    """What the platform does between claim and work: hand over a worktree."""
    rig.service.start_task(
        task_id="T-1",
        attempt_id=str(item["attempt_id"]),
        fencing_epoch=int(item["fencing_epoch"]),
        group_short="g1",
        worktrees=rig.manager,
    )
    workspace = rig.manager.workspace_for_attempt(str(item["attempt_id"]))
    assert workspace is not None
    return workspace.path


class ScriptedRunner:
    """The model process stand-in: heartbeat once, then do the worktree work."""

    def __init__(
        self,
        *,
        files: dict[str, str] | None = None,
        message: str = "feat: adapter work",
        stdout: str = "",
        returncode: int = 0,
        timeout: bool = False,
    ) -> None:
        self.files = files or {}
        self.message = message
        self.stdout = stdout
        self.returncode = returncode
        self.timeout = timeout
        self.ticks: list[dict[str, Any]] = []
        self.argv: list[str] = []

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout_seconds: float | None,
        on_tick: Any = None,
        tick_interval_seconds: float | None = None,
    ) -> CliRunResult:
        self.argv = list(argv)
        if self.timeout:
            raise HarnessTimeout("scripted deadline reached")
        if on_tick is not None:
            self.ticks.append(on_tick())
        if self.returncode == 0:
            for name, content in self.files.items():
                target = Path(cwd) / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
                run_git("add", name, cwd=cwd)
            run_git("commit", "-m", self.message, cwd=cwd)
        return CliRunResult(
            argv=list(argv),
            returncode=self.returncode,
            stdout=self.stdout,
            stderr="",
            duration_seconds=0.01,
        )


def run_to_merged(
    rig: Rig,
    tmp_path: Path,
    *,
    adapter: Any,
    commit: str,
    branch: str,
) -> None:
    """verify -> review -> merge, the scheduler-side steps after the submit."""
    attempt = adapter.attempts["T-1"]
    base_commit = rig.database.read("SELECT base_commit FROM tasks WHERE task_id = 'T-1'")[0][
        "base_commit"
    ]
    VerifyWorker(
        rig.database,
        rig.repo,
        merge_policy(),
        overlay_root=tmp_path / "ov",
        evidence_root=tmp_path / "evidence",
    ).run(
        task_id="T-1",
        attempt_id=attempt.attempt_id,
        commit=commit,
        base_commit=base_commit,
        verify_profile=PROFILE,
        fencing_epoch=attempt.fencing_epoch,
    )
    assert status_of(rig) == "awaiting_review"

    rig.service.review_claim(
        task_id="T-1", attempt_id=attempt.attempt_id, reviewer_harness_id="r-1"
    )
    rig.service.submit_review(
        task_id="T-1",
        attempt_id=attempt.attempt_id,
        reviewer_harness_id="r-1",
        verdict="approve",
        reviewed_commit=commit,
        review_revision=1,
        findings=[],
    )
    assert status_of(rig) == "approved"

    outcome = MergeWorker(
        rig.database, rig.repo, policy=verify_policy(), overlay_root=tmp_path / "merge-ov"
    ).merge(task_id="T-1", attempt_id=attempt.attempt_id, commit=commit, branch=branch)
    assert outcome.status == "merged", outcome.detail
    assert status_of(rig) == "merged"


# --------------------------------------------------------------- the surface


@pytest.mark.parametrize("case", CASES)
def test_each_adapter_declares_the_full_section_7_surface(case, project_root):
    """R10 §7 is the truth source; each real adapter must satisfy all of it."""
    verbs = section_7_verbs(project_root / "docs" / "spec" / "0.3-R10.md")
    assert len(verbs) == 16, verbs
    assert not inspect.isabstract(case.cls)
    missing = [verb for verb in verbs if not hasattr(case.cls, verb)]
    assert not missing, f"{case.name} is missing §7 verbs: {missing}"
    unsupported = {
        verb for verb in verbs if "UnsupportedVerb" in getattr(case.cls, verb).__code__.co_names
    }
    assert unsupported == {"accept_help", "reject_help", "split_task", "ask_human"}, unsupported


@pytest.mark.parametrize("case", CASES)
def test_each_registration_is_traceable(case, tmp_path):
    """R10 §6: registration history keeps the version a claim pinned."""
    database = make_database(tmp_path)
    registry = HarnessRegistry(database)
    registry.register(case.registration)
    current = registry.current(case.registration.harness_id)
    assert current is not None
    assert current["registration_version"] == case.registration.registration_version

    bumped = HarnessRegistration(
        harness_id=case.registration.harness_id,
        name=case.registration.name,
        provider=case.registration.provider,
        adapter=case.registration.adapter,
        adapter_version="1.1.0",
        capabilities=case.registration.capabilities,
        registration_version="reg-002",
    )
    registry.register(bumped)
    pinned = registry.pinned(case.registration.harness_id, "reg-001")
    assert pinned is not None, "the version a claim pinned must stay resolvable"
    assert pinned["adapter_version"] == "1.0.0"
    assert str(pinned["superseded_by"]) == "reg-002"


@pytest.mark.parametrize("case", CASES)
def test_each_cli_contract_is_pinned(case, tmp_path):
    """The argv is the integration fact: assert it verbatim, flag by flag."""
    adapter = case.cls(client=None)
    order = "do the thing"
    assert adapter.command(order, tmp_path) == [adapter.binary, *case.flags, order]
    parsed = adapter.parse_output(case.stdout)
    assert parsed.get("session_id") == case.session_id
    assert parsed["final_message"] == "done: flag.txt"


@pytest.mark.parametrize("case", CASES)
def test_each_adapter_reads_its_surface_over_http(case, tmp_path):
    """probe -> ready list -> claim -> context -> release -> ready list again."""
    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)

    probe = adapter.probe()
    assert probe["degraded"] is False
    caps = adapter.capabilities()
    assert caps["harness_id"] == case.name
    assert tuple(caps["capabilities"]) == ("coding",)

    ready = adapter.list_ready_tasks()
    assert [task["task_id"] for task in ready["tasks"]] == ["T-1"]

    claimed = adapter.claim(claim_request_id=f"cr-reads-{case.name}", task_ids=["T-1"])
    assert claimed["items"][0]["outcome"] == "claimed"

    context = adapter.read_context(task_id="T-1")
    assert any(event["event_name"] == "claim" for event in context["events"])

    adapter.release(task_id="T-1")
    assert status_of(rig) == "ready"
    again = adapter.list_ready_tasks(group_id="G-1", limit=10)
    assert [task["task_id"] for task in again["tasks"]] == ["T-1"]


def test_redacted_visibility_is_refused_not_guessed(tmp_path):
    """R10 never defines what `redacted` hides; answering would be a lie."""
    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)
    with pytest.raises(AdapterError) as caught:
        adapter.list_ready_tasks(visibility="redacted")
    assert caught.value.code == "request.invalid"


# ---------------------------------------------------------------- the loop


@pytest.mark.parametrize("case", CASES)
def test_each_adapter_completes_claim_to_merged_over_http(case, tmp_path):
    """MVP-ADAPTER-002: a real adapter Claims / Heartbeats / Submits / Reviews."""
    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)
    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=case.stdout)
    worker = CliHarnessWorker(adapter, runner=runner)

    outcome = worker.work_on(
        task_id="T-1",
        claim_request_id=f"cr-work-{case.name}",
        on_claimed=lambda item: provision(rig, item),
    )

    assert runner.ticks and runner.ticks[0].get("lease_until"), "heartbeat must land"
    assert runner.argv[0] == adapter.binary
    assert outcome.changed_files == ["flag.txt"]
    assert outcome.parsed["session_id"]
    assert status_of(rig) == "verifying"
    published = rig.database.read(
        "SELECT head_commit FROM task_attempts WHERE attempt_id = ?", (outcome.attempt_id,)
    )[0]["head_commit"]
    assert published == outcome.commit, "the push proxy recorded the attempt head"

    run_to_merged(rig, tmp_path, adapter=adapter, commit=outcome.commit, branch=outcome.branch)


@pytest.mark.parametrize("case", CASES)
def test_each_adapter_abandons_a_timed_out_cli(case, tmp_path):
    """§25: a timeout is loss of contact -- abandon, never submit."""
    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)
    runner = ScriptedRunner(timeout=True)
    worker = CliHarnessWorker(adapter, runner=runner)

    with pytest.raises(HarnessTimeout):
        worker.work_on(
            task_id="T-1",
            claim_request_id=f"cr-timeout-{case.name}",
            on_claimed=lambda item: provision(rig, item),
        )
    assert status_of(rig) == "abandoned"
    assert "T-1" not in adapter.attempts, "the harness forgets the attempt it lost"


@pytest.mark.parametrize("case", CASES)
def test_each_adapter_refuses_dangerous_operations(case, tmp_path):
    """§25: the refusals that matter, per adapter, on the wire."""
    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)
    claimed = adapter.claim(claim_request_id=f"cr-danger-{case.name}", task_ids=["T-1"])
    item = claimed["items"][0]
    worktree = provision(rig, item)
    branch = run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=worktree).stdout.strip()
    commit = run_git("rev-parse", "HEAD", cwd=worktree).stdout.strip()

    with pytest.raises(AdapterError) as caught:
        adapter.publish_task_branch(
            task_id="T-1", branch="main", commit=commit, idempotency_key="pub-main"
        )
    assert caught.value.code == "publish.branch_not_allowed"

    with pytest.raises(AdapterError) as caught:
        adapter.publish_task_branch(
            task_id="T-1",
            branch=branch,
            commit="0" * 40,
            idempotency_key="pub-bogus",
        )
    assert caught.value.code == "publish.commit_unreachable"

    with pytest.raises(UnsupportedVerb):
        adapter.accept_help(task_id="T-1", help_id="h-1")
    with pytest.raises(UnsupportedVerb):
        adapter.ask_human(task_id="T-1", question="?")


def test_a_stale_epoch_is_refused_through_a_real_adapter(tmp_path):
    """fencing after a takeover, over the adapter surface (10a proved the core)."""
    from datetime import UTC, datetime, timedelta

    from app.claim.service import ClaimService
    from app.lease.service import expire_stale_leases

    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)
    adapter.claim(claim_request_id="cr-stale", task_ids=["T-1"])
    old = adapter.attempts["T-1"]

    expire_stale_leases(rig.database, now=datetime.now(UTC) + timedelta(hours=1))
    recovery = ClaimService(rig.database)
    recovery.reclaim("T-1", grace_elapsed=True)
    epoch = rig.database.read("SELECT current_epoch FROM tasks WHERE task_id = 'T-1'")[0][
        "current_epoch"
    ]
    recovery.take_over("T-1", harness_id="codex-2", fencing_epoch=epoch)

    with pytest.raises(AdapterError) as caught:
        adapter.heartbeat(task_id="T-1", fencing_epoch=old.fencing_epoch)
    assert caught.value.code == "fencing.epoch_stale"


# ------------------------------------------------------- recovery / refusal


@pytest.mark.parametrize("case", CASES)
def test_a_restarted_adapter_recovers_its_attempt(case, tmp_path):
    """The deferred verify-mvp-04 item, end to end: lose the token, replay, go on."""
    rig = build_rig(tmp_path, case)
    adapter1 = adapter_for(rig)
    claimed = adapter1.claim(claim_request_id="cr-rec", task_ids=["T-1"])
    item = claimed["items"][0]
    worktree = provision(rig, item)
    lost_token = adapter1.attempts["T-1"].lease_token

    # The harness process dies; a fresh one knows only the persisted
    # claim_request_id. Replay must return the same attempt and a live token.
    adapter2 = adapter_for(rig)
    replay = adapter2.claim(claim_request_id="cr-rec", task_ids=["T-1"])
    replayed = replay["items"][0]
    assert replayed["replayed"] is True
    assert replayed["attempt_id"] == item["attempt_id"], "no new attempt is burned"
    assert replayed["lease_token"] and replayed["lease_token"] != lost_token

    with pytest.raises(AdapterError) as caught:
        adapter1.heartbeat(task_id="T-1", lease_token=lost_token)
    assert caught.value.code == "lease.wrong_owner", "rotation invalidates the old token"

    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=case.stdout)
    worker = CliHarnessWorker(adapter2, worktree=worktree, runner=runner)
    outcome = worker.work_on(task_id="T-1", claim_request_id="cr-rec")

    assert outcome.attempt_id == item["attempt_id"]
    assert status_of(rig) == "verifying"
    assert (
        rig.database.read("SELECT attempt_no FROM tasks WHERE task_id = 'T-1'")[0]["attempt_no"]
        == 1
    ), "recovery continues attempt 1; it does not start attempt 2"


def test_a_worker_refuses_a_task_owned_by_someone_else(tmp_path):
    rig = build_rig(tmp_path, CODEX_CASE)
    owner = adapter_for(rig)
    owner.claim(claim_request_id="cr-owner", task_ids=["T-1"])

    other = adapter_for(rig)
    worker = CliHarnessWorker(other, runner=ScriptedRunner())
    with pytest.raises(WorkRefused) as caught:
        worker.work_on(task_id="T-1", claim_request_id="cr-other")
    assert caught.value.item["error_code"] == "claim.already_owned"


# ----------------------------------------------------- the real subprocess


def test_the_subprocess_runner_kills_at_the_deadline(tmp_path):
    """The production runner, against a real child process."""
    runner = SubprocessRunner(poll_seconds=0.05)
    ticks: list[int] = []
    started = time.monotonic()
    with pytest.raises(HarnessTimeout):
        runner.run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=0.5,
            on_tick=lambda: ticks.append(1),
            tick_interval_seconds=0.1,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 15, "kill must not wait for the child"
    assert ticks, "the tick callback fires while the child runs"


def test_the_subprocess_runner_reports_a_missing_binary(tmp_path):
    with pytest.raises(CliUnavailable):
        SubprocessRunner().run(
            ["definitely-not-a-real-binary-qoder"],
            cwd=tmp_path,
            timeout_seconds=1,
        )
