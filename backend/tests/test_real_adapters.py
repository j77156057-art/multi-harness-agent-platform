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
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from app.adapter.harnesses import (
    CLAUDE_CODE_REGISTRATION,
    CODEBUDDY_REGISTRATION,
    CODEX_REGISTRATION,
    OPENCODE_REGISTRATION,
    QODER_REGISTRATION,
    ClaudeCodeAdapter,
    CliHarnessWorker,
    CliRunResult,
    CliUnavailable,
    CodeBuddyAdapter,
    CodexAdapter,
    HarnessTimeout,
    OpenCodeAdapter,
    QoderAdapter,
    SubprocessRunner,
    WorkRefused,
)
from app.adapter.http import AdapterError
from app.adapter.protocol import HarnessRegistration
from app.adapter.registry import HarnessRegistry
from app.config import Settings, settings
from app.git.repo import CanonicalRepo, run_git
from app.git.worktree import WorktreeManager
from app.merge.worker import MergeWorker
from app.tasks.service import SchedulerService
from app.verify.worker import VerifyWorker
from tests.api_support import bootstrap, make_client, publish_plan, verify_policy
from tests.claim_support import make_database
from tests.merge_support import PROFILE, register_harness
from tests.merge_support import policy as merge_policy
from tests.test_adapter import section_7_verbs

CODEX_OUT = '{"type":"session.created","session_id":"cx-1"}\n{"msg":"done: flag.txt"}\n'
CLAUDE_OUT = '{"session_id":"cc-1","result":"done: flag.txt","total_cost_usd":0.012}'
OPENCODE_OUT = '{"session_id":"oc-1","text":"done: flag.txt"}'
QODER_OUT = '{"session_id":"qo-1","result":"done: flag.txt"}'
CODEBUDDY_OUT = '{"session_id":"cb-1","result":"done: flag.txt"}'


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
QODER_CASE = HarnessCase(
    name="qoder",
    cls=QoderAdapter,
    registration=QODER_REGISTRATION,
    flags=["-p", "--output-format", "json", "--dangerously-skip-permissions", "-w"],
    stdout=QODER_OUT,
    session_id="qo-1",
)
CODEBUDDY_CASE = HarnessCase(
    name="codebuddy",
    cls=CodeBuddyAdapter,
    registration=CODEBUDDY_REGISTRATION,
    flags=["-p", "--output-format", "json", "--permission-mode", "bypassPermissions"],
    stdout=CODEBUDDY_OUT,
    session_id="cb-1",
)

CASES = [
    pytest.param(CODEX_CASE, id="codex"),
    pytest.param(CLAUDE_CASE, id="claude-code"),
    pytest.param(OPENCODE_CASE, id="opencode"),
    pytest.param(QODER_CASE, id="qoder"),
    pytest.param(CODEBUDDY_CASE, id="codebuddy"),
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
    # rules-tighten F5: the reviewer face needs a registry row with a provider
    # distinct from the case's own (openai / sst / anthropic).
    register_harness(database, "r-1", provider="local")
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
        self.stdin_text: str | None = None

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout_seconds: float | None,
        on_tick: Any = None,
        tick_interval_seconds: float | None = None,
        stdin_text: str | None = None,
    ) -> CliRunResult:
        self.argv = list(argv)
        self.stdin_text = stdin_text
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
    unwired = [
        verb
        for verb in verbs
        if not {"_post", "_get"} & set(getattr(case.cls, verb).__code__.co_names)
    ]
    assert not unwired, f"{case.name} has §7 verbs with no transport call: {unwired}"


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


def new_adapter(case: HarnessCase, *, platform: str | None = None):
    """Construct the case's adapter with the platform injected.

    The posix contract is the historical base contract, so the verbatim test
    below pins it explicitly instead of inheriting the host's platform
    (adapter-hardening gave both CLIs per-platform argv).
    """
    if case.cls is CodexAdapter:
        return CodexAdapter(client=None, platform=platform or "posix")
    if case.cls is OpenCodeAdapter:
        return OpenCodeAdapter(client=None, platform=platform or "posix", bash_path="/usr/bin/bash")
    return case.cls(client=None)


@pytest.mark.parametrize("case", CASES)
def test_each_cli_contract_is_pinned(case, tmp_path):
    """The argv is the integration fact: assert it verbatim, flag by flag."""
    adapter = new_adapter(case, platform="posix")
    order = "do the thing"
    command = adapter.command(order, tmp_path)
    if case.name in {"qoder", "codebuddy"}:
        # These adapters resolve the installed executable so a worker started
        # before a PATH refresh can still use the local CLI installation.
        assert Path(command[0]).stem.lower() in {case.name, "qoderclicn", "qodercn", "cbc"}
        if case.name == "qoder":
            assert command[1:-2] == case.flags
            assert command[-2:] == [str(tmp_path), order]
        else:
            # CodeBuddy takes the order on stdin, so its argv is flags plus the
            # worktree path -- no positional at all (a positional multi-line
            # order is truncated at the first newline by the real CLI).
            assert command[1:-2] == case.flags
            assert command[-2:] == ["--add-dir", str(tmp_path)]
    else:
        assert command == [adapter.binary, *case.flags, order]
    parsed = adapter.parse_output(case.stdout)
    assert parsed.get("session_id") == case.session_id
    assert parsed["final_message"] == "done: flag.txt"


#: The CodeBuddy CLI's own argument grammar, as far as the adapter uses it.
#: Kept here as data because the bug being pinned is *about* arity: `--add-dir`
#: takes `<directories...>`, so it consumes every following non-flag token.
_CODEBUDDY_BOOL_FLAGS = {"-p"}
_CODEBUDDY_VALUE_FLAGS = {"--output-format", "--permission-mode"}
_CODEBUDDY_VARIADIC_FLAGS = {"--add-dir"}


def split_cli_argv(tokens):
    """Replay a CLI argument grammar into (positionals, add_dir_directories)."""
    positionals: list[str] = []
    directories: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _CODEBUDDY_BOOL_FLAGS:
            index += 1
        elif token in _CODEBUDDY_VALUE_FLAGS:
            index += 2
        elif token in _CODEBUDDY_VARIADIC_FLAGS:
            index += 1
            while index < len(tokens) and not tokens[index].startswith("-"):
                directories.append(tokens[index])
                index += 1
        elif token.startswith("-"):
            raise AssertionError(f"argv carries a flag this test does not know: {token}")
        else:
            positionals.append(token)
            index += 1
    return positionals, directories


def test_codebuddy_argv_carries_no_prompt_and_the_order_goes_on_stdin(tmp_path):
    """Regression for FD-8438d596ff9225e6: the CLI saw only the first line.

    The real CLI truncates a *multi-line positional* prompt at the first newline
    (its transcript contained `<user_query>Line1 alpha</user_query>` for a
    four-line prompt), so the model never saw the task specification and
    refused to invent one — an attempt burned for a defect in our argv.
    CodeBuddy's pipe mode preserves the text, therefore:

    * the argv must contain **no positional at all** (nothing can be swallowed
      by the variadic ``--add-dir``, and nothing can be truncated), and
    * the adapter must declare ``prompt_via_stdin`` so the worker pipes the
      whole order in.

    The paired test below proves the assertion can fail on a positional argv.
    """
    from app.adapter.harnesses.codebuddy import CodeBuddyAdapter, codebuddy_command

    command = codebuddy_command(tmp_path)

    positionals, directories = split_cli_argv(command[1:])
    assert (
        positionals == []
    ), "no positional prompt: a positional one is truncated at the first newline"
    assert directories == [str(tmp_path)], "--add-dir must see exactly the worktree"
    assert CodeBuddyAdapter.prompt_via_stdin is True


def test_a_multiline_positional_would_lose_everything_after_line_one(tmp_path):
    """The stdin test only proves something if argv prompting really breaks.

    This reconstructs the shape that failed and asserts the *reason*: a
    newline-bearing positional cannot be the transport for a work order, so the
    adapter must not use one.
    """
    from app.adapter.harnesses.codebuddy import _codebuddy_binary

    order = "line one\nline two\nline three"
    positional_argv = [
        _codebuddy_binary(),
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        order,
        "--add-dir",
        str(tmp_path),
    ]

    positionals, directories = split_cli_argv(positional_argv[1:])
    # The flag parser itself would keep the newline, which is exactly why the
    # defect was invisible here and only showed up in the CLI's own transcript;
    # what this test pins is that the prompt is a *positional* at all, i.e. the
    # transport the real CLI truncates.
    assert positionals == [order]
    assert (
        "\n" in positionals[0]
    ), "a positional order is multi-line: the real CLI keeps only line one"
    assert directories == [str(tmp_path)]


def test_the_variadic_replay_fails_on_the_old_argv(tmp_path):
    """The test above only proves something if it can fail on the broken order.

    The pre-fix argv is reconstructed here verbatim and must be rejected --
    otherwise `split_cli_argv` would be a test that agrees with anything.
    """
    from app.adapter.harnesses.codebuddy import _codebuddy_binary

    order = "do the thing"
    broken = [
        _codebuddy_binary(),
        "-p",
        "--output-format",
        "json",
        "--permission-mode",
        "bypassPermissions",
        "--add-dir",
        str(tmp_path),
        order,
    ]

    positionals, directories = split_cli_argv(broken[1:])
    assert positionals == [], "the broken order really does lose the prompt"
    assert directories == [str(tmp_path), order], "and it really does eat it as a directory"


#: Trimmed from a **real** CodeBuddy CLI transcript for `--output-format json`:
#: the answer arrives inside one pretty-printed JSON array, not one object per
#: line. Field names and nesting are kept as captured.
CODEBUDDY_ARRAY_TRANSCRIPT = json.dumps(
    [
        {
            "type": "message",
            "role": "user",
            "status": "completed",
            "content": [
                {
                    "type": "input_text",
                    "text": '<system-reminder data-role="memory"><memory>\n'
                    "# auto memory\n\nYou have a persistent, file-based memory system.\n"
                    "</memory></system-reminder>\n\n"
                    "<user_query>Reply with exactly OK and do not modify any file.</user_query>",
                }
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "OK"}],
        },
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": "OK",
            "session_id": "01a148f2-7e2f-746e-8b0e-b2de3c8444e3",
            "duration_ms": 4151,
            "num_turns": 3,
        },
    ],
    indent=2,
)


def test_codebuddy_reads_the_pretty_printed_array_transcript():
    """Second real defect on the same channel: the answer must be the answer.

    Parsing line by line never saw the `result` object, so `final_message` fell
    back to the raw tail -- the adapter reported a slice of JSON syntax as the
    harness's message and lost `session_id` entirely.
    """
    adapter = CodeBuddyAdapter(client=None)
    parsed = adapter.parse_output(CODEBUDDY_ARRAY_TRANSCRIPT)
    assert parsed["final_message"] == "OK"
    assert parsed["session_id"] == "01a148f2-7e2f-746e-8b0e-b2de3c8444e3"


def test_the_array_transcript_really_is_not_line_delimited():
    """Why the fix was needed at all: no single line of it carries the result."""
    lines = CODEBUDDY_ARRAY_TRANSCRIPT.splitlines()
    assert len(lines) > 5, "the fixture must actually be multi-line"

    per_line: list[Any] = []
    for line in lines:
        try:
            per_line.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    assert not [
        payload
        for payload in per_line
        if isinstance(payload, dict) and payload.get("result") == "OK"
    ], "if a single line carried the result, this fixture would not pin the bug"


def test_codebuddy_still_reads_one_object_per_line():
    """The other shape stays working: accepting both is the contract."""
    adapter = CodeBuddyAdapter(client=None)
    parsed = adapter.parse_output('{"session_id":"cb-1","result":"done: flag.txt"}')
    assert parsed["final_message"] == "done: flag.txt"
    assert parsed["session_id"] == "cb-1"


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


# ------------------------------------------------- adapter-hardening (F3/F4)


def test_codex_sandbox_argv_per_platform(tmp_path, monkeypatch):
    """F3: Windows keeps the drill-proven danger-full-access argv; POSIX keeps
    --full-auto; the setting overrides both, and both spellings stay pinned."""
    order = "do the thing"
    posix = CodexAdapter(client=None, platform="posix")
    nt = CodexAdapter(client=None, platform="nt")

    assert posix.command(order, tmp_path) == ["codex", "exec", "--json", "--full-auto", order]
    assert nt.command(order, tmp_path) == [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "danger-full-access",
        order,
    ]
    assert nt.sandbox_mode() == "danger-full-access"

    monkeypatch.setattr(settings, "codex_sandbox", "workspace-write")
    assert nt.sandbox_mode() == "workspace-write"
    assert nt.command(order, tmp_path) == ["codex", "exec", "--json", "--full-auto", order]

    monkeypatch.setattr(settings, "codex_sandbox", "danger-full-access")
    assert posix.command(order, tmp_path) == [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "danger-full-access",
        order,
    ]


def test_opencode_launch_shim_per_platform(tmp_path, monkeypatch):
    """F4: Windows goes through the bash shim (the drill's recipe); POSIX runs
    direct; a missing bash falls back to direct instead of failing at launch."""
    order = "do the thing"
    shim = ["bash", "-lc", 'exec "$0" "$@"']
    direct = ["opencode", "run", "--format", "json", "--auto", order]

    nt = OpenCodeAdapter(client=None, platform="nt", bash_path="/usr/bin/bash")
    assert nt.command(order, tmp_path) == [*shim, *direct]
    assert nt.use_shell_shim() is True

    posix = OpenCodeAdapter(client=None, platform="posix", bash_path="/usr/bin/bash")
    assert posix.command(order, tmp_path) == direct

    no_bash = OpenCodeAdapter(client=None, platform="nt", bash_path="")
    assert no_bash.use_shell_shim() is False
    assert no_bash.command(order, tmp_path) == direct

    monkeypatch.setattr(settings, "opencode_launch", "bash")
    assert no_bash.use_shell_shim() is False, "no bash on PATH -> direct, not a crash"
    assert nt.use_shell_shim() is True

    monkeypatch.setattr(settings, "opencode_launch", "direct")
    assert nt.use_shell_shim() is False, "explicit override wins on Windows too"


def test_cli_settings_are_fail_closed(monkeypatch):
    """A typo in either knob refuses to start instead of silently degrading."""
    monkeypatch.setenv("AP_CODEX_SANDBOX", "yolo")
    with pytest.raises(ValueError, match="AP_CODEX_SANDBOX"):
        Settings()

    monkeypatch.delenv("AP_CODEX_SANDBOX")
    monkeypatch.setenv("AP_OPENCODE_LAUNCH", "sneaky")
    with pytest.raises(ValueError, match="AP_OPENCODE_LAUNCH"):
        Settings()


def test_claim_replay_carries_the_worktree_path(tmp_path):
    """F9: the replay response hands the live worktree back to a restarted
    harness; before `start_task` (no workspace yet) it is honestly NULL."""
    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)

    first = adapter.claim(claim_request_id="cr-f9", task_ids=["T-1"])["items"][0]
    assert first["outcome"] == "claimed"
    assert first["worktree_path"] is None, "no worktree exists before start_task"

    path = provision(rig, first)

    replay = adapter.claim(claim_request_id="cr-f9", task_ids=["T-1"])["items"][0]
    assert replay["replayed"] is True
    assert replay["worktree_path"] == str(path)
    assert replay["attempt_id"] == first["attempt_id"], "same attempt, not a new one"


def test_replayed_claim_resumes_without_a_side_channel(tmp_path):
    """F9 end to end: the restarted worker needs neither a pre-known worktree
    nor an on_claimed callback -- the replay response carries the path."""
    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)

    first = adapter.claim(claim_request_id="cr-f9b", task_ids=["T-1"])["items"][0]
    provision(rig, first)

    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=CODEX_OUT)
    worker = CliHarnessWorker(adapter, runner=runner)

    outcome = worker.work_on(task_id="T-1", claim_request_id="cr-f9b")

    assert outcome.attempt_id == first["attempt_id"]
    assert outcome.changed_files == ["flag.txt"]
    assert status_of(rig) == "verifying"


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
    expected_head = adapter.binary
    if isinstance(adapter, OpenCodeAdapter) and adapter.use_shell_shim():
        # WindowsApps' WSL alias drops argv after ``-c``; production uses
        # cmd.exe there, while a real POSIX/Git Bash install still uses bash.
        expected_head = "cmd.exe" if runner.argv[0].lower() == "cmd.exe" else "bash"
    if case.name in {"qoder", "codebuddy"}:
        assert Path(runner.argv[0]).stem.lower() in {case.name, "qoderclicn", "qodercn", "cbc"}
    else:
        assert runner.argv[0] == expected_head
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

    # A help vote needs a needs_help task with an open help_id; this task is
    # claimed, so the real route refuses it on the wire (no UnsupportedVerb
    # left to hide behind).
    with pytest.raises(AdapterError) as caught:
        adapter.accept_help(task_id="T-1", help_id="h-1")
    assert caught.value.code == "request.conflict"


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


@pytest.mark.parametrize("case", CASES)
def test_each_adapter_delivers_the_work_order_on_the_channel_its_cli_reads(case, tmp_path):
    """The order reaches the CLI intact: argv for most, stdin for CodeBuddy.

    CodeBuddy keeps only the first line of a multi-line positional prompt
    (measured against the real binary: a four-line prompt arrived as
    ``<user_query>Line1 alpha</user_query>``), so its order travels through the
    pipe. Every other adapter keeps the argv contract, and a runner written
    against the older signature must still work for those.
    """
    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)
    claimed = adapter.claim(claim_request_id="cr-channel", task_ids=["T-1"])
    worktree = provision(rig, claimed["items"][0])

    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=case.stdout)
    worker = CliHarnessWorker(adapter, worktree=worktree, runner=runner)
    worker.work_on(task_id="T-1", claim_request_id="cr-channel")

    if case.cls.prompt_via_stdin:
        assert runner.stdin_text, "the order must travel on stdin for this CLI"
        assert "T-1" in runner.stdin_text
        assert "\n" in runner.stdin_text, "the whole multi-line order, not only line one"
        assert all(
            "T-1" not in argument for argument in runner.argv[1:]
        ), "no positional prompt to truncate"
    else:
        # Not every argv-only harness puts the order in argv (opencode passes it
        # through its prepared transport file), so the property pinned here is
        # the transport boundary: no pipe unless the CLI needs one.
        assert runner.stdin_text is None, "argv-only harnesses must not be piped to"
        assert case.cls.prompt_via_stdin is False


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


def test_the_subprocess_runner_pipes_the_whole_multi_line_prompt(tmp_path):
    """Regression for FD-8438d596ff9225e6 at the transport layer.

    CodeBuddy keeps only the first line of a positional prompt, so the order is
    piped in. This asserts the runner really delivers every byte to the child's
    stdin (a newline-free argv could still be paired with a truncated pipe).
    """
    reader = tmp_path / "reader.py"
    reader.write_text(
        "import pathlib, sys\n"
        "stdin = sys.stdin.read()\n"
        "pathlib.Path('order.txt').write_text(stdin, encoding='utf-8')\n",
        encoding="utf-8",
    )
    order = "line one\nline two\n\nline four — 中文"
    result = SubprocessRunner(poll_seconds=0.05).run(
        [sys.executable, str(reader)],
        cwd=tmp_path,
        timeout_seconds=30,
        stdin_text=order,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "order.txt").read_text(encoding="utf-8") == order


def test_the_subprocess_runner_still_gives_devnull_without_a_prompt(tmp_path):
    """No prompt means no pipe: a CLI that reads stdin must see EOF, not hang."""
    result = SubprocessRunner(poll_seconds=0.05).run(
        [sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"],
        cwd=tmp_path,
        timeout_seconds=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0"


def test_the_subprocess_runner_kills_the_whole_process_tree(tmp_path):
    """TRAE's R2: killing only the direct child leaves grandchildren running.

    The child spawns a grandchild that keeps writing a marker file; after the
    deadline kill, the marker must stop changing. Without a tree kill the
    grandchild survives and the marker keeps advancing.
    """
    marker = tmp_path / "marker.txt"
    grand = tmp_path / "grand.py"
    grand.write_text(
        "import pathlib, sys, time\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "for i in range(600):\n"
        "    p.write_text(str(i))\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    child = tmp_path / "child.py"
    child.write_text(
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, {str(grand)!r}, {str(marker)!r}])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    runner = SubprocessRunner(poll_seconds=0.05)
    with pytest.raises(HarnessTimeout):
        runner.run(
            [sys.executable, str(child)],
            cwd=tmp_path,
            timeout_seconds=0.6,
            on_tick=lambda: None,
            tick_interval_seconds=0.2,
        )

    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists(), "the grandchild never started"
    time.sleep(0.3)  # let any in-flight write land
    frozen = marker.read_text(encoding="utf-8")
    time.sleep(0.8)
    assert (
        marker.read_text(encoding="utf-8") == frozen
    ), "the grandchild is still writing; the tree was not killed"


@pytest.mark.parametrize("case", CASES)
def test_a_refused_heartbeat_stops_the_run_without_abandoning(case, tmp_path):
    """TRAE's R3: the lease-lost path had no test.

    The lease is expired for real (not mocked): the next heartbeat is refused
    by the platform, which must surface as ``HarnessLeaseLost`` and must not
    be turned into an abandon -- there is no live lease left to abandon with.
    """
    from datetime import UTC, datetime, timedelta

    from app.adapter.harnesses import HarnessLeaseLost

    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)
    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=case.stdout)
    worker = CliHarnessWorker(adapter, runner=runner)

    def expire(item):
        past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        with rig.database.transaction() as conn:
            conn.execute("UPDATE leases SET lease_until = ? WHERE task_id = 'T-1'", (past,))
        return provision(rig, item)

    with pytest.raises(HarnessLeaseLost) as caught:
        worker.work_on(
            task_id="T-1",
            claim_request_id=f"cr-lost-{case.name}",
            on_claimed=expire,
        )
    assert caught.value.error.code == "lease.expired"
    assert status_of(rig) == "in_progress", "a lost lease is not an abandon"
    abandoned = rig.database.read(
        "SELECT COUNT(*) AS n FROM task_events WHERE event_name = 'abandon'"
    )
    assert abandoned[0]["n"] == 0
