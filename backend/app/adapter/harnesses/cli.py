"""The worker that drives a real harness CLI for one task attempt (MVP-10b).

R10 §7 is the interface; a real harness is a CLI process started in the
attempt's worktree. This module owns the loop around that process:

    claim -> [CLI runs, heartbeat on tick] -> read HEAD -> publish -> submit

The CLI process itself is behind :class:`CliRunner`, so the loop is testable
without a model: tests inject a scripted runner, production uses
:class:`SubprocessRunner`. The argv is not built here -- it is each harness
adapter's ``command()``, because "how to invoke codex/claude/opencode" is
exactly the integration fact a per-harness adapter exists to own.

A timed-out CLI is killed and the attempt is abandoned, not submitted: a
partial commit must not reach review on its own.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.adapter.http import AdapterError, HttpAdapter
from app.config import settings
from app.git.repo import run_git


class HarnessTimeout(RuntimeError):
    """The CLI exceeded its deadline and was killed."""


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
    ) -> CliRunResult: ...


class CliHarness(HttpAdapter):
    """An :class:`HttpAdapter` that also knows how to drive its own CLI.

    This is where the per-harness integration facts live: the binary, the argv
    contract for one unattended run, and how to read the CLI's output. It is a
    subclass rather than an extension of the §7 interface -- §7's 16 verbs are
    the surface every adapter must have; how a CLI is invoked is not part of
    that surface and must not be added to it.
    """

    binary: str = ""

    def command(self, work_order: str, worktree: Path) -> list[str]:
        raise NotImplementedError

    def parse_output(self, stdout: str) -> dict[str, Any]:
        return {}


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
    ) -> CliRunResult:
        started = time.monotonic()
        spawn_kwargs: dict[str, Any] = {}
        if os.name != "nt":
            # POSIX: children get their own process group so _kill can signal
            # the whole tree, not just the CLI.
            spawn_kwargs["start_new_session"] = True
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            try:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd,
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    **spawn_kwargs,
                )
            except FileNotFoundError as exc:
                raise CliUnavailable(f"{argv[0]}: not found on PATH") from exc

            deadline = started + timeout_seconds if timeout_seconds else None
            next_tick = (
                started + tick_interval_seconds
                if on_tick is not None and tick_interval_seconds
                else None
            )
            while True:
                now = time.monotonic()
                moments = [moment for moment in (deadline, next_tick) if moment is not None]
                if moments and now >= min(moments):
                    if deadline is not None and now >= deadline:
                        _kill(process)
                        raise HarnessTimeout(f"{argv[0]} exceeded {timeout_seconds:.0f}s")
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


class CliHarnessWorker:
    """One task attempt, start to submit, via the harness's own CLI.

    Everything goes through the adapter's HTTP surface; the worker never
    touches the database. It reads the worktree's own HEAD for the commit and
    branch, the same way a harness that was handed a worktree would.

    The claim and the worktree are two steps bound together by the platform:
    the harness claims, the scheduler provisions the worktree, the harness
    works. ``on_claimed`` is where that hand-over happens -- it receives the
    claim item and returns the worktree path. Replayed claims skip it: the
    worktree from the first, crashed run is still there.
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
    ) -> WorkOutcome:
        """claim -> [CLI runs, heartbeat on tick] -> publish -> submit."""
        item = self.take(task_id=task_id, claim_request_id=claim_request_id)
        if not item.get("replayed") and on_claimed is not None:
            self.worktree = Path(on_claimed(item))
        if self.worktree is None:
            raise RuntimeError("no worktree: pass one, or an on_claimed that provisions it")

        context = self.adapter.attempts[task_id]
        work_order = self.work_order(
            task_id=task_id,
            attempt_id=context.attempt_id,
            base_commit=item.get("base_commit"),
            goal=goal,
        )
        argv = self.adapter.command(work_order, self.worktree)

        try:
            cli = self.runner.run(
                argv,
                cwd=self.worktree,
                timeout_seconds=timeout_seconds,
                on_tick=self._tick(task_id),
                tick_interval_seconds=float(self.heartbeat_seconds),
            )
        except HarnessTimeout as exc:
            self.adapter.abandon(task_id=task_id, reason=f"harness timeout: {exc}")
            raise
        if cli.returncode != 0:
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
            completion["harness_output"] = parsed
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
