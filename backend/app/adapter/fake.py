"""The Fake Harness: a client that behaves like a harness (MVP-10a).

Its value is that it drives the platform the way a real one would -- over the
API, committing in its own worktree, publishing through the proxy -- so a green
run says something about the platform rather than about this file. It never
touches the database; the only state it keeps is the attempt context the claim
returned and its own worktree.

The branch is read from the worktree's HEAD rather than computed. R10 §14 derives
it from group-short / harness / task-id / attempt-id, and group-short is not in
the database yet (MVP-05 recorded that gap); reading it back is what a harness
that was handed a worktree would actually do.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.adapter.http import AdapterError, HttpAdapter
from app.git.repo import run_git


@dataclass(frozen=True, slots=True)
class WorkResult:
    task_id: str
    attempt_id: str
    commit: str
    branch: str
    publish: dict[str, Any]
    submit: dict[str, Any]


class FakeHarness:
    def __init__(
        self,
        adapter: HttpAdapter,
        *,
        worktree: Path,
        name: str = "fake-harness",
    ) -> None:
        self.adapter = adapter
        self.worktree = Path(worktree)
        self.name = name
        self.taken: set[str] = set()

    # ------------------------------------------------------------------ work

    def probe(self, *, host_mode: str = "native_cli") -> dict[str, Any]:
        return self.adapter.probe(host_mode=host_mode)

    def take(self, *, task_id: str, claim_request_id: str) -> dict[str, Any]:
        """Claim one task and keep the attempt context the response carried."""
        result = self.adapter.claim(claim_request_id=claim_request_id, task_ids=[task_id])
        for item in result.get("items", ()):
            if item.get("outcome") == "claimed":
                self.taken.add(task_id)
        return result

    def heartbeat(self, *, task_id: str, progress_hash: str | None = None) -> dict[str, Any]:
        return self.adapter.heartbeat(task_id=task_id, progress_hash=progress_hash)

    def commit(self, *, files: dict[str, str], message: str) -> str:
        for name, content in files.items():
            target = self.worktree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            run_git("add", name, cwd=self.worktree)
        run_git("commit", "-m", message, cwd=self.worktree)
        return run_git("rev-parse", "HEAD", cwd=self.worktree).stdout.strip()

    def branch(self) -> str:
        return run_git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.worktree).stdout.strip()

    def work(
        self,
        *,
        task_id: str,
        claim_request_id: str,
        files: dict[str, str],
        message: str = "feat: work",
    ) -> WorkResult:
        """claim -> heartbeat -> commit -> publish -> submit, all over the API."""
        self.take(task_id=task_id, claim_request_id=claim_request_id)
        self.heartbeat(task_id=task_id)
        commit = self.commit(files=files, message=message)
        branch = self.branch()
        context = self.adapter.attempts[task_id]
        publish = self.adapter.publish_task_branch(
            task_id=task_id,
            branch=branch,
            commit=commit,
            idempotency_key=f"pub:{task_id}:{commit[:8]}",
        )
        submit = self.adapter.submit_result(
            task_id=task_id,
            completion={
                "commit": commit,
                "task_id": task_id,
                "attempt_id": context.attempt_id,
                "fencing_epoch": context.fencing_epoch,
                "changed_files": sorted(files),
            },
        )
        return WorkResult(
            task_id=task_id,
            attempt_id=context.attempt_id,
            commit=commit,
            branch=branch,
            publish=publish,
            submit=submit,
        )

    # -------------------------------------------------------------- yielding

    def give_up(self, *, task_id: str, reason: str = "fake harness giving up") -> dict[str, Any]:
        return self.adapter.abandon(task_id=task_id, reason=reason)

    def hand_back(self, *, task_id: str) -> dict[str, Any]:
        return self.adapter.release(task_id=task_id)

    def ask_for_help(self, *, task_id: str, reason: str) -> dict[str, Any]:
        return self.adapter.request_help(task_id=task_id, reason=reason)

    def last_error_code(self, action) -> str | None:
        """Run an action and report the wire code it was refused with."""
        try:
            action()
        except AdapterError as exc:
            return exc.code
        return None
