"""The worker that drives a real harness CLI for one task attempt (MVP-10b).

R10 §7 is the interface; a real harness is a CLI process started in the
attempt's worktree. This module owns the loop around that process:

    claim -> [CLI runs, heartbeat on tick] -> read HEAD -> publish -> submit

The CLI process itself is behind :class:`CliRunner`, so the loop is testable
without a model: tests inject a scripted runner, production uses
:class:`SubprocessRunner`. The argv is not built here -- it is each harness
adapter's ``command()``, because "how to invoke codex/claude/opencode" is
exactly the integration fact a per-harness adapter exists to own.

A timed-out CLI is killed; the attempt and its worktree are kept (R10-fix-15):
within the lease the same harness replays its claim and resumes in the same
worktree. A partial commit still never reaches review on its own -- the run
just does not destroy the work that was already on disk. The kill also carries
the child's partial stdout/stderr so the failure evidence can show where it
stopped.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.adapter import cli_output
from app.adapter.http import AdapterError, HttpAdapter
from app.config import settings
from app.git.repo import run_git


class HarnessTimeout(RuntimeError):
    """The CLI exceeded its deadline and was killed.

    The child's partial stdout/stderr is carried along: a 30-60 minute run that
    dies at the deadline is expensive, and without the transcript the only fact
    left is "it timed out". The worker writes these tails into its failure
    evidence so the next attempt can see where the CLI stopped.
    """

    def __init__(
        self,
        message: str,
        *,
        stdout: str = "",
        stderr: str = "",
        duration_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.stdout = stdout
        self.stderr = stderr
        self.duration_seconds = duration_seconds


def _tail(handle: Any, limit: int = 4000) -> str:
    """Best-effort read of what a killed child already wrote to its temp file."""
    try:
        handle.flush()
        handle.seek(0)
        data = handle.read()
    except (OSError, ValueError):
        return ""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else str(data)
    return text[-limit:]


@contextlib.contextmanager
def _stream_log(path: Path | None) -> Any:
    """Open the live log for one stream, or a no-op sink when there is none.

    Append mode: a replayed attempt keeps the earlier attempt's transcript and
    adds to it, which is what an operator wants to read.
    """
    if path is None:
        yield None
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("ab")
    except OSError:
        # A log we cannot write is not a reason to fail the run: the receipt
        # and the failure evidence still record what happened.
        yield None
        return
    try:
        yield handle
    finally:
        with contextlib.suppress(OSError):
            handle.close()


def _drain(source: Any, sink: Any | None, offset: int) -> int:
    """Copy what the child wrote since ``offset`` into ``sink``.

    Returns the new offset. The temp file is read at its current end, so the
    copy is incremental and never rewrites what is already in the log.
    """
    if sink is None:
        return offset
    try:
        source.flush()
        source.seek(offset)
        chunk = source.read()
    except (OSError, ValueError):
        return offset
    if not chunk:
        return offset
    try:
        sink.write(chunk)
        sink.flush()
    except (OSError, ValueError):
        return offset
    return offset + len(chunk)


class CliUnavailable(RuntimeError):
    """The harness binary is not on PATH."""


class CliFailed(RuntimeError):
    """The CLI exited non-zero; the attempt has been abandoned."""

    def __init__(self, task_id: str, result: CliRunResult) -> None:
        super().__init__(f"{task_id}: harness CLI exited {result.returncode}")
        self.result = result


class HarnessLeaseLost(RuntimeError):
    """A heartbeat was refused; the platform no longer considers us the owner."""

    def __init__(self, error: AdapterError) -> None:
        super().__init__(f"lease lost: {error}")
        self.error = error


class WorkRefused(RuntimeError):
    """The claim did not yield this task; the worker has nothing to do."""

    def __init__(self, task_id: str, outcome: str, item: dict[str, Any]) -> None:
        super().__init__(f"{task_id} claim outcome was {outcome!r}, not 'claimed'")
        self.task_id = task_id
        self.outcome = outcome
        self.item = item


@dataclass(frozen=True, slots=True)
class CliRunResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float


class CliRunner(Protocol):
    """What the worker needs from "run the harness CLI once"."""

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
        env: dict[str, str] | None = None,
    ) -> CliRunResult: ...


def runner_accepts(runner: Any, name: str) -> bool:
    """Whether this runner can take the given keyword argument.

    The production runner writes the child's output to the given log paths. A
    scripted runner (in a unit test) may not know them, and a runner that
    cannot be introspected is given the keyword anyway -- it would raise, which
    is louder than quietly losing the transcript.
    """
    try:
        return name in inspect.signature(runner.run).parameters
    except (TypeError, ValueError):  # pragma: no cover - exotic callables
        return True


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """Where an attempt's model calls go when they do not go to the CLI's own
    provider.

    The platform's quota pool: the CLI keeps doing the work (it is still the
    execution slot, in its own worktree), but the model behind it is served by
    the platform -- from another harness's quota, because this window's own
    provider ran out.
    """

    #: The pool's OpenAI-compatible base (…/api/v1/pool/v1) or, for a CLI
    #: that speaks Anthropic, the Anthropic-compatible one (…/api/v1/pool).
    base_url: str
    #: The calling harness's own platform token: the pool must know *who* is
    #: spending the quota.
    token: str
    #: The model to ask the pool for.
    model: str
    #: "openai" or "anthropic" -- the protocol the CLI speaks.
    protocol: str = "openai"


class CliHarness(HttpAdapter):
    """An :class:`HttpAdapter` that also knows how to drive its own CLI.

    This is where the per-harness integration facts live: the binary, the argv
    contract for one unattended run, and how to read the CLI's output. It is a
    subclass rather than an extension of the §7 interface -- §7's 16 verbs are
    the surface every adapter must have; how a CLI is invoked is not part of
    that surface and must not be added to it.
    """

    binary: str = ""

    #: True when this CLI must receive the work order on stdin instead of argv.
    #: CodeBuddy keeps only the first line of a positional prompt (measured: the
    #: CLI put ``<user_query>Line1 alpha</user_query>`` in its transcript for a
    #: four-line prompt), so its order travels through the pipe.
    prompt_via_stdin: bool = False

    #: Set by the worker when the platform routes this attempt through the
    #: quota pool.  ``None`` means "use the CLI's own provider" (the normal
    #: case).
    provider_route: ProviderRoute | None = None

    #: Which protocol this CLI speaks to a model provider.  The pool serves
    #: both, and each CLI has to be pointed at the right one.
    pool_protocol: str = "openai"

    def command(self, work_order: str, worktree: Path) -> list[str]:
        raise NotImplementedError

    def review_command(self, prompt: str, workdir: Path) -> list[str]:
        """The command for a **review** call (a plan, not the work).

        A review must not be able to *do* anything: the reviewer gives an
        opinion, it does not edit files, run commands or post messages.  The
        default is the normal command -- adapters whose CLI can be put into a
        read-only mode override this.
        """
        return self.command(prompt, workdir)

    def route_environment(self) -> dict[str, str]:
        """The environment the CLI needs to reach the pool.

        Empty when the attempt runs on the CLI's own provider.
        """
        return {}

    def prepare_work_order(self, work_order: str, worktree: Path) -> str:
        """Return the text passed to :meth:`command`.

        Adapters whose platform launcher cannot safely carry arbitrary text may
        persist the order in a short-lived transport file instead.  The worker
        always calls :meth:`cleanup_work_order` after the child exits.
        """
        return work_order

    def cleanup_work_order(self) -> None:
        """Remove any temporary transport files created for the last command."""
        return None

    def process_environment(self) -> dict[str, str] | None:
        """Optional environment for the child CLI process.

        Some adapters need their own configuration (Pi isolates its agent
        directory so it cannot read the user's real config).  The values are
        **added to** the worker's environment, never replacing it.
        """
        return None

    def parse_output(self, stdout: str) -> dict[str, Any]:
        return {}

    # ---------------------------------------------------------------- 平台工具

    #: 这一轮属于哪次尝试。worker 在跑 CLI 之前填，适配器据此拼平台工具的地址。
    #: 不放进 ``command`` 的签名里：那是每个适配器都要实现的 CLI argv 契约，
    #: 加参数等于让每个 CLI 适配器都知道"平台工具"这件事。
    task_context: dict[str, str] | None = None

    def mcp_binding(self) -> dict[str, Any] | None:
        """这次尝试可用的平台工具端点；没有就是 ``None``。

        基类返回 ``None``：只有**确认过怎么把 MCP 配置交给这个 CLI** 的适配器才
        实现它。没确认就假装支持，会让窗口以为自己有工具而实际没有。
        """
        return None

    def transient_paths(self) -> tuple[str, ...]:
        """适配器**自己**写进工作树、跑完要删掉的文件（相对工作树）。

        ``ScopedRunner`` 会把它们从"脏文件/越界"里排除，并在跑完删除。这条
        通道只给适配器自己的配置文件用：一个真实交付物躲在里面会被判成越界。
        """
        return ()


class SubprocessRunner:
    """The production runner: a real child process, killed at the deadline.

    Output goes to temp files rather than pipes -- a verbose CLI that fills a
    pipe buffer while the poll loop is between reads would otherwise deadlock.
    """

    def __init__(self, *, poll_seconds: float = 0.25) -> None:
        self.poll_seconds = poll_seconds

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
        env: dict[str, str] | None = None,
    ) -> CliRunResult:
        """Run one CLI child.

        ``stdin_text`` exists because some CLIs (CodeBuddy) only keep the *first
        line* of a positional prompt: everything after the first ``\\n`` is
        dropped before the model sees it. Those CLIs read a piped prompt instead
        (``-p`` with stdin), which preserves the whole work order. When
        ``stdin_text`` is None the child still gets ``DEVNULL`` so no CLI can
        block waiting for input.

        ``log_path`` / ``err_log_path`` make the child's own words available
        *while it runs*: the bytes are copied out of the temp files while the
        child is still going, so the office can show a harness's live output
        instead of only its result. The temp files stay the source of truth for
        the final result.

        ``env`` adds to (never replaces) the worker's own environment: it is
        how an attempt that runs on the platform's quota pool tells its CLI
        where the pool is.
        """
        started = time.monotonic()
        spawn_kwargs: dict[str, Any] = {}
        if env:
            # The child gets the worker's environment plus the pool's
            # settings -- it never loses what the worker had.
            child_env = dict(os.environ)
            child_env.update({str(key): str(value) for key, value in env.items()})
            spawn_kwargs["env"] = child_env
        if os.name != "nt":
            # POSIX: children get their own process group so _kill can signal
            # the whole tree, not just the CLI.
            spawn_kwargs["start_new_session"] = True
        with (
            tempfile.TemporaryFile() as out,
            tempfile.TemporaryFile() as err,
            _stream_log(log_path) as out_log,
            _stream_log(err_log_path) as err_log,
        ):
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    **spawn_kwargs,
                )
            except FileNotFoundError as exc:
                raise CliUnavailable(f"{argv[0]}: not found on PATH") from exc
            if stdin_text is not None:
                try:
                    process.stdin.write(stdin_text.encode("utf-8"))
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    # The CLI exited before reading its prompt; the exit code
                    # and stderr below are the real diagnosis.
                    with contextlib.suppress(Exception):
                        process.stdin.close()

            deadline = started + timeout_seconds if timeout_seconds else None
            next_tick = (
                started + tick_interval_seconds
                if on_tick is not None and tick_interval_seconds
                else None
            )
            offsets = {"out": 0, "err": 0}
            while True:
                now = time.monotonic()
                # Copy whatever the child has written so far into the live log
                # files. The temp files stay the source of truth; these copies
                # only exist so a reader can watch the run.
                offsets["out"] = _drain(out, out_log, offsets["out"])
                offsets["err"] = _drain(err, err_log, offsets["err"])
                moments = [moment for moment in (deadline, next_tick) if moment is not None]
                if moments and now >= min(moments):
                    if deadline is not None and now >= deadline:
                        _kill(process)
                        offsets["out"] = _drain(out, out_log, offsets["out"])
                        offsets["err"] = _drain(err, err_log, offsets["err"])
                        raise HarnessTimeout(
                            f"{argv[0]} exceeded {timeout_seconds:.0f}s",
                            stdout=_tail(out),
                            stderr=_tail(err),
                            duration_seconds=time.monotonic() - started,
                        )
                    next_tick = now + float(tick_interval_seconds or 1)
                    try:
                        on_tick()
                    except Exception:
                        _kill(process)
                        raise
                    continue
                slice_end = min(moments + [now + self.poll_seconds])
                try:
                    process.wait(timeout=slice_end - now)
                    break
                except subprocess.TimeoutExpired:
                    continue
            # The child is gone: take whatever it wrote in its last breath
            # (a buffered child flushes at exit) before the final collection.
            offsets["out"] = _drain(out, out_log, offsets["out"])
            offsets["err"] = _drain(err, err_log, offsets["err"])
            return _collect(argv, process, out, err, started)


def _kill(process: subprocess.Popen) -> None:
    """Kill the CLI and its children (TRAE's R2 on the MVP-10b verification).

    A harness CLI spawns its own tool processes; killing only the direct child
    (the 10b behaviour) leaves orphans that keep writing into the worktree. On
    Windows ``taskkill /T`` walks the tree; on POSIX the child was spawned in
    its own session and the whole group is signalled.
    """
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                check=False,
            )
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    process.wait()


def _collect(
    argv: list[str],
    process: subprocess.Popen,
    out: Any,
    err: Any,
    started: float,
) -> CliRunResult:
    out.seek(0)
    err.seek(0)
    return CliRunResult(
        argv=list(argv),
        returncode=int(process.returncode if process.returncode is not None else -1),
        stdout=out.read().decode("utf-8", errors="replace"),
        stderr=err.read().decode("utf-8", errors="replace"),
        duration_seconds=time.monotonic() - started,
    )


@dataclass(frozen=True, slots=True)
class WorkOutcome:
    task_id: str
    attempt_id: str
    commit: str
    branch: str
    changed_files: list[str]
    publish: dict[str, Any]
    submit: dict[str, Any]
    cli: CliRunResult
    parsed: dict[str, Any]


#: How much of the CLI's own output may go into the **event log**. The event
#: log is an audit trail, not a transcript: one attempt's output was 1.3 MB, which
#: bloated the task's history and made the office's read routes answer 502 (the
#: gateway refuses oversized upstream responses). The full words live in the
#: transcript (``data/formal-runs/<task>.<attempt>.log``) and in the run
#: evidence; the event keeps the beginning.
MAX_EVENT_OUTPUT_CHARS = 4000


def _summarize_output(parsed: dict[str, Any]) -> dict[str, Any]:
    """The harness's parsed output, small enough for an event payload."""
    summary: dict[str, Any] = {}
    for key, value in parsed.items():
        if isinstance(value, str):
            if len(value) > MAX_EVENT_OUTPUT_CHARS:
                summary[key] = (
                    value[:MAX_EVENT_OUTPUT_CHARS]
                    + f"…[truncated {len(value) - MAX_EVENT_OUTPUT_CHARS} chars]"
                )
            else:
                summary[key] = value
        elif isinstance(value, int | float | bool) or value is None:
            summary[key] = value
        else:
            text = json.dumps(value, ensure_ascii=False, default=str)
            summary[key] = (
                value
                if len(text) <= MAX_EVENT_OUTPUT_CHARS
                else {"truncated": True, "bytes": len(text)}
            )
    return summary


class CliHarnessWorker:
    """One task attempt, start to submit, via the harness's own CLI.

    Everything goes through the adapter's HTTP surface; the worker never
    touches the database. It reads the worktree's own HEAD for the commit and
    branch, the same way a harness that was handed a worktree would.

    The claim and the worktree are two steps bound together by the platform:
    the harness claims, the scheduler provisions the worktree, the harness
    works. ``on_claimed`` is where that hand-over happens -- it receives the
    claim item and returns the worktree path. Replayed claims skip it: the
    worktree from the first, crashed run is still there, and since F9 the
    replay response itself carries its path (``worktree_path``).
    """

    def __init__(
        self,
        adapter: CliHarness,
        *,
        worktree: Path | None = None,
        runner: CliRunner | None = None,
        heartbeat_seconds: int | None = None,
    ) -> None:
        self.adapter = adapter
        self.worktree = Path(worktree) if worktree is not None else None
        self.runner = runner or SubprocessRunner()
        self.heartbeat_seconds = heartbeat_seconds or settings.heartbeat_seconds

    # ------------------------------------------------------------------ work

    def take(self, *, task_id: str, claim_request_id: str) -> dict[str, Any]:
        """Claim one task; refuse with :class:`WorkRefused` if it is not ours.

        Reusing a ``claim_request_id`` replays instead of re-claiming: that is
        the documented recovery path, and the replay re-issues a live lease
        token (MVP-10b rotation).
        """
        claimed = self.adapter.claim(claim_request_id=claim_request_id, task_ids=[task_id])
        item = next((i for i in claimed.get("items", ()) if i.get("task_id") == task_id), None)
        if item is None or item.get("outcome") != "claimed":
            outcome = "missing" if item is None else str(item.get("outcome"))
            raise WorkRefused(task_id, outcome, dict(item or {}))
        return dict(item)

    def work_on(
        self,
        *,
        task_id: str,
        claim_request_id: str,
        on_claimed: Callable[[dict[str, Any]], Path] | None = None,
        timeout_seconds: float | None = None,
        goal: str | None = None,
        on_error_attempt: Callable[[str | None], None] | None = None,
        abandon_on_failure: bool = True,
        env: dict[str, str] | None = None,
    ) -> WorkOutcome:
        """claim -> [CLI runs, heartbeat on tick] -> publish -> submit.

        ``abandon_on_failure=False`` leaves the "this attempt is dead" decision
        to the caller: a CLI that failed because its provider is down should put
        the task back in the queue (retry), not end it.
        """
        item = self.take(task_id=task_id, claim_request_id=claim_request_id)
        if on_error_attempt is not None:
            # The claim succeeded; provisioning has not. A Git/provisioning
            # exception here must still leave the caller a way to name the
            # attempt it has to close, otherwise the task stays claimed.
            on_error_attempt(str(item.get("attempt_id") or "") or None)
        # A replay normally hands the first run's worktree back (F9) and skips
        # provisioning. If the first run died *before* provisioning (worktree
        # capacity, a Git error), there is no worktree to hand back: run the
        # same hand-over again for the *same* attempt. `start_task` is keyed on
        # attempt + fencing epoch and only accepts a `claimed` task, so this
        # recovers the attempt instead of creating a second claim.
        if on_claimed is not None and (not item.get("replayed") or not item.get("worktree_path")):
            self.worktree = Path(on_claimed(item))
        if self.worktree is None and item.get("worktree_path"):
            # F9 (adapter-hardening): the replay response carries the live
            # worktree path, so a restarted process continues in the first
            # run's workspace without a side channel.
            self.worktree = Path(str(item["worktree_path"]))
        if self.worktree is None:
            raise RuntimeError("no worktree: pass one, or an on_claimed that provisions it")

        context = self.adapter.attempts[task_id]
        work_order = self.work_order(
            task_id=task_id,
            attempt_id=context.attempt_id,
            base_commit=item.get("base_commit"),
            goal=goal,
        )
        prepared_order = self.adapter.prepare_work_order(work_order, self.worktree)
        # 这一轮属于哪次尝试：适配器据此把「平台工具」的地址拼进它自己的配置。
        # 只在这里填，命令签名不变 —— 每个 CLI 适配器都不该知道平台工具的存在。
        self.adapter.task_context = {"task_id": task_id, "attempt_id": context.attempt_id}
        argv = self.adapter.command(prepared_order, self.worktree)

        # Only mention stdin when this adapter actually uses it: the keyword is
        # additive on the protocol, and a third-party runner written against the
        # older signature must keep working for every argv-only harness.
        run_kwargs: dict[str, Any] = {}
        if self.adapter.prompt_via_stdin:
            run_kwargs["stdin_text"] = prepared_order
        # The child's own words, streamed while it runs: the office shows this
        # tail, so "what is this harness doing" is answered by the harness
        # itself instead of by our summary of it.
        stdout_log, stderr_log = cli_output.log_paths(
            settings.data_dir, task_id, context.attempt_id
        )
        run_kwargs["log_path"] = stdout_log
        run_kwargs["err_log_path"] = stderr_log
        # 适配器自己要的环境（比如 Pi 隔离自己的 agent 目录）与额度池给的 route env
        # 一起给子进程：两者都是**加到**worker 自己的环境上，不是替换它。
        adapter_env = self.adapter.process_environment()
        merged_env = {**(env or {}), **(adapter_env or {})}
        try:
            cli = self.runner.run(
                argv,
                cwd=self.worktree,
                timeout_seconds=timeout_seconds,
                on_tick=self._tick(task_id),
                tick_interval_seconds=float(self.heartbeat_seconds),
                **({"env": merged_env} if merged_env else {}),
                **run_kwargs,
            )
        except HarnessTimeout:
            # 超时不再 abandon：attempt 与工作区都保留，租约内用同一个
            # claim_request_id 重跑会 replay 回同一个 worktree 继续（R10-fix-15）。
            # 半成品仍然不提交；租约过期后由现有 stale/reclaiming sweep 接管。
            raise
        finally:
            self.adapter.cleanup_work_order()
        if cli.returncode != 0:
            # 谁来"放弃"这条活，是**调用方**的决定：CLI 挂了可能只是它的
            # provider 出了问题（可重试 → 放回队列），也可能是平台自己拒绝了
            # 越界产物（不可重试 → 终态）。默认保持旧行为（abandon），需要自己
            # 判定的调用方（成员 worker）传 abandon_on_failure=False。
            if abandon_on_failure:
                self.adapter.abandon(task_id=task_id, reason=f"harness CLI exited {cli.returncode}")
            raise CliFailed(task_id, cli)

        commit = self._git("rev-parse", "HEAD")
        branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        changed = self._changed_files(str(item.get("base_commit") or ""))
        publish = self.adapter.publish_task_branch(
            task_id=task_id,
            branch=branch,
            commit=commit,
            idempotency_key=f"pub:{task_id}:{commit[:8]}",
        )
        parsed = self.adapter.parse_output(cli.stdout)
        completion: dict[str, Any] = {
            "commit": commit,
            "task_id": task_id,
            "attempt_id": context.attempt_id,
            "fencing_epoch": context.fencing_epoch,
            "changed_files": changed,
            "harness": self.adapter.registration.harness_id,
            "cli": {"adapter_version": self.adapter.registration.adapter_version},
        }
        if parsed:
            completion["harness_output"] = _summarize_output(parsed)
        submit = self.adapter.submit_result(task_id=task_id, completion=completion)
        return WorkOutcome(
            task_id=task_id,
            attempt_id=context.attempt_id,
            commit=commit,
            branch=branch,
            changed_files=changed,
            publish=publish,
            submit=submit,
            cli=cli,
            parsed=parsed,
        )

    # -------------------------------------------------------------- internals

    def _tick(self, task_id: str) -> Callable[[], dict[str, Any]]:
        def tick() -> dict[str, Any]:
            try:
                return self.adapter.heartbeat(task_id=task_id)
            except AdapterError as exc:
                raise HarnessLeaseLost(exc) from exc

        return tick

    def _git(self, *args: str) -> str:
        return run_git(*args, cwd=self.worktree).stdout.strip()

    def _changed_files(self, base_commit: str) -> list[str]:
        """HEAD against the base; a NULL base falls back to the branch start.

        A project without an integration head yields ``base_commit = None``
        (the seed state); the worktree's own root commit is then the honest
        left side, the same convention ``WorktreeManager._base_commit`` uses.
        """
        base = base_commit or self._root_commit()
        if not base:
            return []
        proc = run_git("diff", "--name-only", f"{base}..HEAD", cwd=self.worktree, check=False)
        if proc.returncode != 0:
            return []
        return sorted(line for line in proc.stdout.splitlines() if line.strip())

    def _root_commit(self) -> str | None:
        proc = run_git("rev-list", "--max-parents=0", "HEAD", cwd=self.worktree, check=False)
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return proc.stdout.strip().splitlines()[-1]

    def work_order(
        self,
        *,
        task_id: str,
        attempt_id: str,
        base_commit: str | None,
        goal: str | None,
    ) -> str:
        """The deterministic instruction the CLI receives.

        Deliberately terse: the card in the repository is the task's own truth
        source, so the work order points at it instead of duplicating it.
        """
        lines = [
            f"You are harness {self.adapter.registration.harness_id}, "
            f"working task {task_id} (attempt {attempt_id}) in this worktree.",
            "Your branch is checked out here; the platform publishes and submits for you.",
        ]
        if base_commit:
            lines.append(f"Worktree base commit: {base_commit}.")
        if goal:
            lines.append(f"Goal: {goal}")
        lines.append(
            "Read the task card for this task in TASKS.md (or docs/claims/) and do the work. "
            "Commit your changes on the current branch. Do not try to push anywhere."
        )
        lines.append(
            "Before writing anything, check what already exists: when your channel has network "
            "access, use it to consult official documentation, upstream repositories and prior "
            "art -- including the rest of this repository -- and reuse or adapt a proven approach "
            "instead of rebuilding it from scratch. Working in isolation and reinventing an "
            "existing wheel is a defect, not diligence."
        )
        lines.append(
            "If the goal is ambiguous, or you cannot tell which approach the group owner wants, "
            "do not guess: state the open question in your report and stop short of inventing a "
            "requirement nobody asked for."
        )
        return "\n".join(lines)


__all__ = [
    "CliFailed",
    "CliHarness",
    "CliHarnessWorker",
    "CliRunResult",
    "CliRunner",
    "CliUnavailable",
    "HarnessLeaseLost",
    "HarnessTimeout",
    "SubprocessRunner",
    "WorkOutcome",
    "WorkRefused",
]
