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
import os
import shutil
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
from app.adapter.harnesses.codebuddy import _codebuddy_binary
from app.adapter.harnesses.codex import _codex_binary
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
#: claude-code 用 ``--output-format stream-json``：一行一个事件，最后一行是
#: ``result`` 事件。``parse_output`` 要能从这串里读出最终消息。
CLAUDE_OUT = "\n".join(
    [
        '{"type":"system","subtype":"init","session_id":"cc-1"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}',
        '{"type":"result","subtype":"success","is_error":false,'
        '"session_id":"cc-1","result":"done: flag.txt","total_cost_usd":0.012}',
    ]
)
OPENCODE_OUT = '{"session_id":"oc-1","text":"done: flag.txt"}'
QODER_OUT = '{"session_id":"qo-1","result":"done: flag.txt"}'
#: qoder 的流式输出（真机实测，qoderclicn 1.1.71）：一行一个事件，最后一行是
#: ``result`` 事件；CLI 自己的提示（"Skill command ... was renamed"）是纯文本行，
#: 解析必须跳过而不是报错。
QODER_STREAM_OUT = "\n".join(
    [
        '{"type":"system","subtype":"init","qodercli_version":"1.1.71"}',
        "Skill command '/goal' was renamed to '/goal1' because it conflicts with built-in command.",
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}',
        '{"type":"result","subtype":"success","is_error":false,"session_id":"qo-1",'
        '"result":"done: flag.txt","total_cost_usd":0.02}',
    ]
)
CODEBUDDY_OUT = '{"session_id":"cb-1","result":"done: flag.txt"}'
#: codebuddy 用 ``--output-format stream-json``：一行一个事件，最后一行是
#: ``result`` 事件（实测 12 行 / 16 KB，事件类型与 claude 相同）。
CODEBUDDY_STREAM_OUT = "\n".join(
    [
        '{"type":"system","subtype":"init","session_id":"cb-1"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}',
        '{"type":"result","subtype":"success","is_error":false,"session_id":"cb-1",'
        '"result":"done: flag.txt"}',
    ]
)


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
    flags=["exec", "--json", "--skip-git-repo-check", "--full-auto"],
    stdout=CODEX_OUT,
    session_id="cx-1",
)
CLAUDE_CASE = HarnessCase(
    name="claude-code",
    cls=ClaudeCodeAdapter,
    registration=CLAUDE_CODE_REGISTRATION,
    flags=["-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"],
    stdout=CLAUDE_OUT,
    session_id="cc-1",
)
OPENCODE_CASE = HarnessCase(
    name="opencode",
    cls=OpenCodeAdapter,
    registration=OPENCODE_REGISTRATION,
    flags=["run", "--standalone", "--format", "json", "--auto"],
    stdout=OPENCODE_OUT,
    session_id="oc-1",
)
QODER_CASE = HarnessCase(
    name="qoder",
    cls=QoderAdapter,
    registration=QODER_REGISTRATION,
    flags=["-p", "--output-format", "stream-json", "--dangerously-skip-permissions", "-w"],
    stdout=QODER_OUT,
    session_id="qo-1",
)
CODEBUDDY_CASE = HarnessCase(
    name="codebuddy",
    cls=CodeBuddyAdapter,
    registration=CODEBUDDY_REGISTRATION,
    flags=["-p", "--output-format", "stream-json", "--permission-mode", "bypassPermissions"],
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
        self.log_path: Path | None = None
        self.err_log_path: Path | None = None

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        timeout_seconds: float | None,
        on_tick: Any = None,
        tick_interval_seconds: float | None = None,
        stdin_text: str | None = None,
        log_path: Path | None = None,
        err_log_path: Path | None = None,
    ) -> CliRunResult:
        self.argv = list(argv)
        self.stdin_text = stdin_text
        self.log_path = log_path
        self.err_log_path = err_log_path
        if self.timeout:
            raise HarnessTimeout("scripted deadline reached")
        if on_tick is not None:
            self.ticks.append(on_tick())
        if log_path is not None:
            # The scripted runner stands in for the real child: it writes the
            # same bytes the child would have written, so the live log path is
            # exercised the same way.
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(self.stdout, encoding="utf-8")
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
    if case.name in {"qoder", "codebuddy", "codex"}:
        # These adapters resolve the installed executable so a worker started
        # before a PATH refresh can still use the local CLI installation.
        assert Path(command[0]).stem.lower() in {
            case.name,
            "qoderclicn",
            "qodercn",
            "cbc",
            "codex",
        }
        if case.name == "qoder":
            assert command[1:-2] == case.flags
            assert command[-2:] == [str(tmp_path), order]
        elif case.name == "codebuddy":
            # CodeBuddy takes the order on stdin, so its argv is flags plus the
            # worktree path -- no positional at all (a positional multi-line
            # order is truncated at the first newline by the real CLI).
            assert command[1:-2] == case.flags
            assert command[-2:] == ["--add-dir", str(tmp_path)]
        else:
            # codex: the executable is resolved (PATH or the Windows install
            # directory), the rest of the argv is the pinned contract -- and the
            # work order travels on **stdin** ("-"), because Windows caps the
            # command line at 32,767 characters and the administrator's review
            # prompt (with the whole diff) does not fit in an argument.
            assert command[1:] == [*case.flags, "-"]
            assert adapter.prompt_via_stdin is True
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


def test_claude_code_reads_the_final_result_event_of_the_stream():
    """`--output-format stream-json` 是一行一个事件；最终消息在最后一个 result 事件里。

    换成流式输出后，CLI 在**跑的过程中**就有话可说（办公室屏幕显示的就是它），
    但解析仍然必须拿到与从前一样的字段：最终消息、session、费用。
    """
    adapter = ClaudeCodeAdapter(client=None)

    parsed = adapter.parse_output(CLAUDE_OUT)

    assert parsed["final_message"] == "done: flag.txt"
    assert parsed["session_id"] == "cc-1"
    assert parsed["cost_usd"] == 0.012
    assert "is_error" not in parsed


def test_claude_code_still_accepts_a_single_json_object():
    """老格式（只有一个对象）也要能读：CLI 版本或只输出一行的情形。"""
    adapter = ClaudeCodeAdapter(client=None)

    parsed = adapter.parse_output(
        '{"session_id":"cc-0","result":"done","total_cost_usd":0.5,"is_error":true}'
    )

    assert parsed["final_message"] == "done"
    assert parsed["session_id"] == "cc-0"
    assert parsed["cost_usd"] == 0.5
    assert parsed["is_error"] is True


def test_claude_code_skips_lines_it_cannot_parse():
    """流是 harness 自己的：多一行看不懂的东西不能把解析弄死。"""
    adapter = ClaudeCodeAdapter(client=None)

    parsed = adapter.parse_output(
        "not json at all\n"
        '{"type":"assistant","message":{"content":[]}}\n'
        '{"type":"result","session_id":"cc-2","result":"ok","total_cost_usd":0.01}\n'
    )

    assert parsed["final_message"] == "ok"
    assert parsed["session_id"] == "cc-2"


def test_qoder_reads_the_final_result_event_of_the_stream():
    """qoder 的流式输出：最后一个 ``result`` 事件是答案，CLI 自己的纯文本提示要跳过。

    真机实测（qoderclicn 1.1.71）：13 行里 3 行是 "Skill command ... was
    renamed" 这种纯文本通知——它们不是 JSON，解析必须跳过而不是把整份丢掉。
    """
    adapter = QoderAdapter(client=None)

    parsed = adapter.parse_output(QODER_STREAM_OUT)

    assert parsed["final_message"] == "done: flag.txt"
    assert parsed["session_id"] == "qo-1"


def test_codebuddy_reads_the_final_result_event_of_the_stream():
    """codebuddy 的流式输出：最后一个 ``result`` 事件就是答案。

    实测（真 CLI，`--output-format stream-json`）：12 行 / 16 KB，事件类型
    system / assistant / user / result，与 claude 的流式格式一致。
    """
    adapter = CodeBuddyAdapter(client=None)

    parsed = adapter.parse_output(CODEBUDDY_STREAM_OUT)

    assert parsed["final_message"] == "done: flag.txt"
    assert parsed["session_id"] == "cb-1"


def test_codebuddy_resolves_an_executable_on_windows(tmp_path, monkeypatch):
    """npm 会装一个无扩展名的 bash shim；Windows 起不来那种文件。

    `shutil.which` 可能先命中无扩展名的那个，于是 child 直接 "system cannot
    find the file"。解析时优先用同名的 .cmd / .exe。
    """
    shim = tmp_path / "codebuddy"
    shim.write_text("#!/bin/sh\n", encoding="utf-8")
    cmd = tmp_path / "codebuddy.cmd"
    cmd.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(shutil, "which", lambda name: str(shim))
    monkeypatch.setattr(os, "name", "nt")

    assert _codebuddy_binary() == str(cmd)


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

    # The work order travels on stdin ("-"), not in argv: Windows caps the
    # command line at 32,767 characters and the administrator's review prompt
    # (which carries the whole diff) does not fit.
    assert posix.command(order, tmp_path) == [
        _codex_binary(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--full-auto",
        "-",
    ]
    assert nt.command(order, tmp_path) == [
        _codex_binary(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-",
    ]
    assert nt.sandbox_mode() == "danger-full-access"

    monkeypatch.setattr(settings, "codex_sandbox", "workspace-write")
    assert nt.sandbox_mode() == "workspace-write"
    assert nt.command(order, tmp_path) == [
        _codex_binary(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--full-auto",
        "-",
    ]

    monkeypatch.setattr(settings, "codex_sandbox", "danger-full-access")
    assert posix.command(order, tmp_path) == [
        _codex_binary(),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-",
    ]


def test_opencode_launch_shim_per_platform(tmp_path, monkeypatch):
    """F4: Windows goes through the bash shim (the drill's recipe); POSIX runs
    direct; a missing bash falls back to direct instead of failing at launch."""
    order = "do the thing"
    shim = ["bash", "-lc", 'exec "$0" "$@"']
    # `--standalone`: the CLI must run its own server, not attach to whatever
    # background service happens to be running (a newer one makes every run die
    # with "Database is not empty and has no session table").
    direct = ["opencode", "run", "--standalone", "--format", "json", "--auto", order]

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


def test_the_worker_streams_the_cli_output_into_the_data_dir(tmp_path, monkeypatch):
    """worker → runner → 日志文件：CLI 的话在平台数据目录里落成了文字。

    这是"办公室屏幕能显示 harness 原话"的后端半边：worker 交出日志路径，
    runner 把 child 的字节抄进去，平台的只读路由再把尾巴交出去。
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)

    first = adapter.claim(claim_request_id="cr-log", task_ids=["T-1"])["items"][0]
    provision(rig, first)

    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=CODEX_OUT)
    worker = CliHarnessWorker(adapter, runner=runner)

    worker.work_on(task_id="T-1", claim_request_id="cr-log")

    attempt_id = first["attempt_id"]
    expected = tmp_path / "formal-runs" / f"T-1.{attempt_id}.log"
    assert runner.log_path == expected, "the worker names the log file by attempt"
    assert expected.exists(), "the CLI output is on disk"
    assert CODEX_OUT.strip()[:40] in expected.read_text(encoding="utf-8")


def test_a_replay_without_a_worktree_provisions_the_same_attempt(tmp_path):
    """A claim that died before provisioning must be recoverable by replay.

    The replay response hands back a worktree only when one was provisioned.
    With none, the worker used to raise "no worktree" and (after the worker
    error path) abandon the task. It now re-runs the hand-over for the same
    attempt, which is exactly the attempt that is already claimed.
    """
    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)

    first = adapter.claim(claim_request_id="cr-f9c", task_ids=["T-1"])["items"][0]
    assert first["worktree_path"] is None

    runner = ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=CODEX_OUT)
    worker = CliHarnessWorker(adapter, runner=runner)
    handovers: list[str] = []

    def handover(item):
        handovers.append(item["attempt_id"])
        return provision(rig, item)

    outcome = worker.work_on(task_id="T-1", claim_request_id="cr-f9c", on_claimed=handover)

    assert handovers == [first["attempt_id"]], "same attempt, provisioned once"
    assert outcome.attempt_id == first["attempt_id"]
    assert status_of(rig) == "verifying"


# ---------------------------------------------------------------- the loop


def test_a_provisioning_failure_still_names_the_claimed_attempt(tmp_path):
    """A claim followed by a Git failure must expose the attempt to clean up.

    Before this, ``on_claimed`` raised before ``captured["attempt_id"]`` was
    set, so the worker left the task ``claimed`` with an open claim and lease.
    """
    rig = build_rig(tmp_path, CODEX_CASE)
    adapter = adapter_for(rig)
    worker = CliHarnessWorker(
        adapter, runner=ScriptedRunner(files={"flag.txt": "ok\n"}, stdout=CODEX_OUT)
    )
    seen: list[str | None] = []

    def explode(item):
        raise RuntimeError("worktree provisioning failed")

    with pytest.raises(RuntimeError, match="worktree provisioning failed"):
        worker.work_on(
            task_id="T-1",
            claim_request_id="cr-provision-fail",
            on_claimed=explode,
            on_error_attempt=seen.append,
        )

    assert seen and seen[0]
    assert seen[0].startswith("T-1."), "the callback receives the claimed attempt id"


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
    if case.name in {"qoder", "codebuddy", "codex"}:
        assert Path(runner.argv[0]).stem.lower() in {
            case.name,
            "qoderclicn",
            "qodercn",
            "cbc",
            "codex",
        }
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
def test_each_adapter_keeps_a_timed_out_attempt_for_replay(case, tmp_path):
    """R10-fix-15: a timeout keeps the attempt and worktree -- never the commit.

    Loss of contact still stops the half-written work from being submitted, but
    the attempt is no longer destroyed: within the lease the same harness
    replays its claim and continues in the same workspace.
    """
    rig = build_rig(tmp_path, case)
    adapter = adapter_for(rig)
    runner = ScriptedRunner(timeout=True)
    worker = CliHarnessWorker(adapter, runner=runner)
    claim_id = f"cr-timeout-{case.name}"

    with pytest.raises(HarnessTimeout):
        worker.work_on(
            task_id="T-1",
            claim_request_id=claim_id,
            on_claimed=lambda item: provision(rig, item),
        )
    assert status_of(rig) == "in_progress", "the attempt is kept, not abandoned"
    assert "T-1" in adapter.attempts, "the harness still owns the attempt"
    assert worker.worktree is not None and Path(worker.worktree).exists()
    assert status_of(rig) != "verifying", "a timed-out CLI never submits"

    # The process restarts knowing only the persisted claim_request_id; the
    # replay reissues the lease and hands back the same worktree (F9).
    adapter2 = adapter_for(rig)
    replayed = adapter2.claim(claim_request_id=claim_id, task_ids=["T-1"])["items"][0]
    assert replayed["replayed"] is True
    assert replayed["attempt_id"] == adapter.attempts["T-1"].attempt_id
    assert Path(replayed["worktree_path"]) == Path(worker.worktree)
    assert replayed["lease_token"], "the resumed run gets a live lease token"


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


def test_the_subprocess_runner_keeps_partial_output_on_timeout(tmp_path):
    """A deadline kill keeps what the CLI printed, not just "timed out".

    The 30-60 minute attempts are the expensive ones; the failure evidence is
    the only place an operator can see where the CLI stopped.
    """
    runner = SubprocessRunner(poll_seconds=0.05)
    with pytest.raises(HarnessTimeout) as caught:
        runner.run(
            [
                sys.executable,
                "-u",
                "-c",
                "import sys, time\n"
                "print('editing scripts/verify-opening-host.mjs')\n"
                "print('warning: eslint config missing', file=sys.stderr)\n"
                "time.sleep(30)\n",
            ],
            cwd=tmp_path,
            timeout_seconds=0.8,
        )
    exc = caught.value
    assert "editing scripts/verify-opening-host.mjs" in exc.stdout
    assert "eslint config missing" in exc.stderr
    assert exc.duration_seconds is not None
    assert exc.duration_seconds >= 0.5


def test_the_harness_timeout_is_configurable(monkeypatch):
    """CLI flag > env > default; a non-positive value never disables the deadline."""
    from app.adapter.formal_worker import resolve_timeout_seconds

    assert resolve_timeout_seconds(42) == 42
    assert resolve_timeout_seconds(0) == 3600.0
    assert resolve_timeout_seconds(-1) == 3600.0

    import app.adapter.formal_worker as worker_module

    monkeypatch.setattr(worker_module.settings, "harness_timeout_seconds", 600)
    assert resolve_timeout_seconds() == 600

    monkeypatch.setenv("AP_HARNESS_TIMEOUT_SECONDS", "5400")
    from app.config import Settings

    assert Settings().harness_timeout_seconds == 5400


def test_the_subprocess_runner_reports_a_missing_binary(tmp_path):
    with pytest.raises(CliUnavailable):
        SubprocessRunner().run(
            ["definitely-not-a-real-binary-qoder"],
            cwd=tmp_path,
            timeout_seconds=1,
        )


def test_codex_reads_the_nested_agent_message_not_thread_started():
    """Real codex 0.130 wraps the answer in item.completed/item.agent_message.

    Reading only top-level fields left ``final_message`` empty, so the admin
    caller fell back to raw stdout and parsed ``thread.started`` as the verdict.
    """
    adapter = CodexAdapter(client=None)
    parsed = adapter.parse_output(
        '{"type":"thread.started","thread_id":"cx-1"}\n'
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":" "}}\n'
        '{"type":"item.completed","item":{"id":"item_1","type":"agent_message",'
        '"text":"{\\"verdict\\":\\"approve\\",\\"findings\\":[]}"}}\n'
        '{"type":"turn.completed"}\n'
    )
    assert parsed["final_message"] == '{"verdict":"approve","findings":[]}'
    assert parsed["session_id"] == "cx-1"


def test_the_subprocess_runner_pipes_the_whole_multi_line_prompt(tmp_path):
    """Regression for FD-8438d596ff9225e6 at the transport layer.

    CodeBuddy keeps only the first line of a positional prompt, so the order is
    piped in. This asserts the runner really delivers every byte to the child's
    stdin (a newline-free argv could still be paired with a truncated pipe).
    """
    reader = tmp_path / "reader.py"
    # Read bytes, not text: the runner's contract is byte delivery. Decoding
    # with the child's locale codec (cp936 on a Chinese Windows) would turn the
    # runner's UTF-8 bytes into surrogates and blame the wrong layer.
    reader.write_text(
        "import pathlib, sys\n" "pathlib.Path('order.txt').write_bytes(sys.stdin.buffer.read())\n",
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
    assert (tmp_path / "order.txt").read_bytes() == order.encode("utf-8")


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
