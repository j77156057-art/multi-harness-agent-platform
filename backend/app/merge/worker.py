"""Merge Worker: the only writer of the integration branch (R10 §21).

Two things this module is careful about:

* nothing is written to the integration branch until the gate passes. The gate
  runs first and every rejection happens before the first ``update-ref``, so a
  refused merge leaves no trace in the repository.
* a conflict is classified by *who can resolve it*, not by how it looks. A clash
  in files the task itself changed needs its owner (``merge_conflict_needs_owner``
  -> changes_requested); a clash in files it never touched is the integration
  moving under the branch, which the Merge Worker re-runs (``merge_conflict``
  -> approved). R10 §10 gives those two edges different ``next`` states, so a
  single "conflict" outcome would have to pick one and lose the distinction.

Revert recovery is deliberately mechanical: the tree assertion from §21 either
holds or the recovery is invalid and escalates, because "the revert did not
actually undo the merge" is not something a human should have to notice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.claim.transitions import apply_transition
from app.db.connection import Database
from app.events.store import EventStore
from app.git.repo import CanonicalRepo, GitError, run_git
from app.merge.gate import evaluate_merge_gate
from app.spec import EventKind, TaskStatus
from app.verify.overlay import Overlay, OverlayError, OverlayManager
from app.verify.profile import VerifyPolicy, VerifyProfile
from app.verify.worker import CommandRunner, default_runner

HEALTH_GREEN = "green"
HEALTH_RED = "red"
HEALTH_RETESTING = "retesting"
HEALTH_ESCALATED = "escalated"


class MergeError(RuntimeError):
    """The merge could not be completed."""


@dataclass(frozen=True, slots=True)
class MergeOutcome:
    status: str
    task_status: TaskStatus
    merge_commit: str | None
    event: str
    conflicts: tuple[str, ...] = ()
    revert_commit: str | None = None
    detail: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "task_status": self.task_status.value,
            "merge_commit": self.merge_commit,
            "revert_commit": self.revert_commit,
            "event": self.event,
            "conflicts": list(self.conflicts),
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HealthRecheck:
    """The result of re-running the integration profile at the canonical tip."""

    project_id: str
    status: str
    commit: str | None
    profile: str | None
    result: str
    detail: str | None = None
    applied: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "project_id": self.project_id,
            "status": self.status,
            "commit": self.commit,
            "profile": self.profile,
            "result": self.result,
            "detail": self.detail,
            "applied": self.applied,
        }


class MergeWorker:
    def __init__(
        self,
        database: Database,
        repo: CanonicalRepo,
        *,
        policy: VerifyPolicy | None = None,
        overlay_root: Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.database = database
        self.repo = repo
        self.policy = policy
        self.overlays = OverlayManager(repo, overlay_root)
        self.runner: CommandRunner = runner or default_runner
        self.store = EventStore(database)

    # ---------------------------------------------------------------- merge

    def merge(self, *, task_id: str, attempt_id: str, commit: str, branch: str) -> MergeOutcome:
        gate = evaluate_merge_gate(
            self.database,
            self.repo,
            task_id=task_id,
            attempt_id=attempt_id,
            commit=commit,
            branch=branch,
        )
        gate.raise_for_failures()  # nothing is written before this line

        self._transition(task_id, current=TaskStatus.APPROVED, event=EventKind.MERGE_START)
        integration = self.repo.ref_commit(self.repo.integration_ref)
        if integration is None:
            raise MergeError("canonical has no integration branch to merge into")

        try:
            overlay = self.overlays.create(commit=integration, job_id=f"merge-{task_id}")
        except OverlayError as exc:
            self._transition(task_id, current=TaskStatus.MERGING, event=EventKind.MERGE_CONFLICT)
            raise MergeError(f"could not prepare a merge tree: {exc}") from exc

        try:
            run_git("fetch", "--no-tags", str(self.repo.path), commit, cwd=overlay.path)
            merged = run_git(
                "merge",
                "--no-ff",
                "--no-edit",
                "-m",
                f"merge {task_id} attempt {attempt_id}",
                "FETCH_HEAD",
                cwd=overlay.path,
                check=False,
            )
            if merged.returncode != 0:
                return self._handle_conflict(
                    overlay, task_id=task_id, attempt_id=attempt_id, commit=commit
                )
            merge_commit = run_git("rev-parse", "HEAD", cwd=overlay.path).stdout.strip()
            self.repo.import_objects(overlay.path, merge_commit)
            self._record_intent("merge", task_id=task_id, idempotency_key=f"merge:{merge_commit}")
            self.repo.update_ref(self.repo.integration_ref, merge_commit)
            self._set_integration_commit(task_id, merge_commit)
            post_merge = self._run_integration_check(overlay, merge_commit)

            if post_merge is None:
                self.overlays.recycle(overlay)
                self._transition(task_id, current=TaskStatus.MERGING, event=EventKind.MERGE_SUCCESS)
                return MergeOutcome(
                    status="merged",
                    task_status=TaskStatus.MERGED,
                    merge_commit=merge_commit,
                    event=EventKind.MERGE_SUCCESS.value,
                )
            return self._revert_recovery(
                overlay,
                task_id=task_id,
                merge_commit=merge_commit,
                reason=post_merge,
            )
        finally:
            if overlay.path.exists():
                self.overlays.recycle(overlay)

    def _handle_conflict(
        self, overlay: Overlay, *, task_id: str, attempt_id: str, commit: str
    ) -> MergeOutcome:
        conflicts = tuple(
            line.strip()
            for line in run_git(
                "diff", "--name-only", "--diff-filter=U", cwd=overlay.path, check=False
            ).stdout.splitlines()
            if line.strip()
        )
        run_git("merge", "--abort", cwd=overlay.path, check=False)
        task = self.database.read("SELECT base_commit FROM tasks WHERE task_id = ?", (task_id,))[0]
        changed = {
            line.strip()
            for line in run_git(
                "diff",
                "--name-only",
                f"{task['base_commit']}..{commit}",
                cwd=overlay.path,
                check=False,
            ).stdout.splitlines()
            if line.strip()
        }
        owner_must_resolve = bool(conflicts) and set(conflicts) <= changed
        event = (
            EventKind.MERGE_CONFLICT_NEEDS_OWNER if owner_must_resolve else EventKind.MERGE_CONFLICT
        )
        # 冲突要说清楚**冲在哪些文件上**。事件原来只写一个空 payload —— 界面上
        # 只有「合并冲突（需人工决定）」四个字，主人不知道要看哪儿，也就无法裁决
        # （2026-10-29 的真实演练：两条工单都写同一个落点，冲突后只有主人自己
        # 从仓库里翻才知道是撞了路径）。
        conflict_files = list(conflicts)
        next_status = self._transition(
            task_id,
            current=TaskStatus.MERGING,
            event=event,
            payload={
                "conflicts": conflict_files,
                "conflict_scope": "own_files" if owner_must_resolve else "other_files",
                "detail": (
                    "冲突在任务自己的文件里："
                    + "、".join(conflict_files)
                    + "。这份落点已经有别的交付合进来了，要主人决定是覆盖还是改落点。"
                    if owner_must_resolve
                    else "冲突在别的任务的文件里：" + "、".join(conflict_files)
                ),
            },
        )
        return MergeOutcome(
            status="conflict",
            task_status=next_status,
            merge_commit=None,
            event=event.value,
            conflicts=conflicts,
            detail="conflict is inside the task's own files"
            if owner_must_resolve
            else "conflict is outside the task's files",
        )

    # --------------------------------------------------------------- revert

    def _revert_recovery(
        self, overlay: Overlay, *, task_id: str, merge_commit: str, reason: str
    ) -> MergeOutcome:
        """R10 §21: the only write allowed while integration_health is red."""
        self._set_health(task_id, HEALTH_RED)
        run_git("checkout", "--detach", merge_commit, cwd=overlay.path, check=False)
        reverted = run_git(
            "revert", "--no-edit", "-m", "1", merge_commit, cwd=overlay.path, check=False
        )
        if reverted.returncode != 0:
            run_git("revert", "--abort", cwd=overlay.path, check=False)
            self._transition(
                task_id,
                current=TaskStatus.MERGING,
                event=EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED,
            )
            return MergeOutcome(
                status="revert_failed",
                task_status=TaskStatus.ESCALATED,
                merge_commit=merge_commit,
                event=EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED.value,
                detail=f"revert could not be created: {reason}",
            )

        revert_commit = run_git("rev-parse", "HEAD", cwd=overlay.path).stdout.strip()
        problem = _revert_assertion_problem(overlay.path, merge_commit, revert_commit)
        if problem is not None:
            self._transition(
                task_id,
                current=TaskStatus.MERGING,
                event=EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED,
            )
            return MergeOutcome(
                status="revert_invalid",
                task_status=TaskStatus.ESCALATED,
                merge_commit=merge_commit,
                event=EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED.value,
                detail=problem,
            )

        self.repo.import_objects(overlay.path, revert_commit)
        self._record_intent(
            "revert_merge", task_id=task_id, idempotency_key=f"revert:{revert_commit}"
        )
        self.repo.update_ref(self.repo.integration_ref, revert_commit)
        self._set_integration_commit(task_id, revert_commit)
        self._append_event(
            "merge_reverted",
            task_id=task_id,
            payload={"reverted_commit": merge_commit, "revert_commit": revert_commit},
        )

        self._set_health(task_id, HEALTH_RETESTING)
        still_failing = self._run_integration_check(overlay, revert_commit)
        if still_failing is not None:
            self._set_health(task_id, HEALTH_ESCALATED)
            self._transition(
                task_id,
                current=TaskStatus.MERGING,
                event=EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED,
            )
            return MergeOutcome(
                status="revert_red",
                task_status=TaskStatus.ESCALATED,
                merge_commit=merge_commit,
                revert_commit=revert_commit,
                event=EventKind.MERGE_POST_COMMIT_FAILURE_ESCALATED.value,
                detail="integration tests still fail after the revert",
            )

        self._set_health(task_id, HEALTH_GREEN)
        next_status = self._transition(
            task_id, current=TaskStatus.MERGING, event=EventKind.MERGE_POST_COMMIT_FAILURE
        )
        return MergeOutcome(
            status="reverted",
            task_status=next_status,
            merge_commit=merge_commit,
            revert_commit=revert_commit,
            event=EventKind.MERGE_POST_COMMIT_FAILURE.value,
            detail=f"post-merge check failed: {reason}",
        )

    # ------------------------------------------------------------- internals

    def _run_integration_check(self, overlay: Overlay, commit: str) -> str | None:
        """Run the integration profile at ``commit``; None means it passed."""
        _, profile = self._integration_profile()
        if profile is None:
            return None
        run_git("checkout", "--detach", commit, cwd=overlay.path, check=False)
        result = self.runner(profile, overlay.path)
        if result.timed_out:
            return "integration check timed out"
        if result.env_error is not None:
            return f"integration check could not run: {result.env_error}"
        if result.exit_code != 0:
            return f"integration check exited {result.exit_code}"
        return None

    def _integration_profile(self) -> tuple[str | None, VerifyProfile | None]:
        """The one profile the merge gate runs, and its name.

        The merge policy is narrowed to the resolved integration profile (see
        ``RuntimePolicy.merge_policy``), so the first profile *is* the
        integration profile. Keeping the name next to the profile means the
        recheck reports which command it actually ran.
        """
        if self.policy is None:
            return None, None
        name = next(iter(self.policy.profiles), None)
        if name is None:
            return None, None
        return name, self.policy.profiles[name]

    def _set_health_for_project(self, project_id: str, status: str, *, commit: str | None) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE integration_health SET status = ?,"
                " integration_commit = COALESCE(?, integration_commit), updated_seq = NULL"
                " WHERE project_id = ?",
                (status, commit, project_id),
            )

    # ----------------------------------------------------------- health recheck

    def recheck_integration_health(self, project_id: str, *, apply: bool = False) -> HealthRecheck:
        """Re-run the integration profile at the canonical tip.

        R10 §21 makes a non-green ``integration_health`` a stop-the-line for the
        whole merge queue of that project, and the only documented way back to
        green is a merge (or revert) round. A project whose integration profile
        was misconfigured therefore deadlocks: the queue is stopped *and* the
        only thing that could restart it is a merge the stop-the-line forbids.

        This is the operator-side way out, and it is deliberately narrow:

        * the check runs the *configured* integration profile at the current
          canonical tip -- the same command the merge gate would have run;
        * without ``apply`` nothing is written (a pure check);
        * with ``apply`` the projection is set to green **only** when the check
          passed; a failing check leaves the row exactly as it was.

        No event is written: ``integration_health`` is a projection, and §21
        gives it no event of its own.
        """
        rows = self.database.read(
            "SELECT status FROM integration_health WHERE project_id = ?", (project_id,)
        )
        if not rows:
            raise MergeError(f"no integration_health row for project {project_id}")
        status = str(rows[0]["status"])

        name, profile = self._integration_profile()
        if profile is None:
            raise MergeError(
                "no integration profile is configured; refusing to declare the queue healthy"
                " without running the check"
            )

        tip = self.repo.ref_commit(self.repo.integration_ref)
        if tip is None:
            raise MergeError("canonical has no integration branch to check")

        try:
            overlay = self.overlays.create(commit=tip, job_id=f"recheck-{project_id}")
        except OverlayError as exc:
            raise MergeError(f"could not prepare a recheck tree: {exc}") from exc
        try:
            problem = self._run_integration_check(overlay, tip)
        finally:
            if overlay.path.exists():
                self.overlays.recycle(overlay)

        if problem is not None:
            return HealthRecheck(
                project_id=project_id,
                status=status,
                commit=tip,
                profile=name,
                result="failed",
                detail=problem,
                applied=False,
            )

        if apply:
            self._set_health_for_project(project_id, HEALTH_GREEN, commit=tip)
        return HealthRecheck(
            project_id=project_id,
            status=status,
            commit=tip,
            profile=name,
            result="passed",
            detail=None,
            applied=apply,
        )

    def _transition(
        self,
        task_id: str,
        *,
        current: TaskStatus,
        event: EventKind,
        payload: dict[str, Any] | None = None,
    ) -> TaskStatus:
        with self.database.transaction() as conn:
            task = conn.execute(
                "SELECT current_epoch FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise MergeError(f"unknown task: {task_id}")
            record = self.store.append(
                event_name=event.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="merge_worker",
                actor_id="merge-worker-1",
                payload=payload or {},
                connection=conn,
            )
            return apply_transition(
                conn,
                task_id=task_id,
                current=current,
                event=event,
                expected_epoch=int(task["current_epoch"]),
                state_seq=record.seq,
            )

    def _append_event(self, name: str, *, task_id: str, payload: dict) -> None:
        self.store.append(
            event_name=name,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="merge_worker",
            actor_id="merge-worker-1",
            payload=payload,
        )

    def _record_intent(self, kind: str, *, task_id: str, idempotency_key: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO side_effect_intents(intent_id, type, task_id, status,"
                " idempotency_key, fencing_epoch, created_seq)"
                " VALUES (?, ?, ?, 'done', ?, NULL, NULL)",
                (f"int-{uuid.uuid4().hex}", kind, task_id, idempotency_key),
            )

    def _set_integration_commit(self, task_id: str, commit: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE integration_health SET integration_commit = ?, updated_seq = NULL"
                " WHERE project_id = (SELECT g.project_id FROM tasks t"
                "   JOIN groups g ON g.group_id = t.group_id WHERE t.task_id = ?)",
                (commit, task_id),
            )

    def _set_health(self, task_id: str, status: str) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                "UPDATE integration_health SET status = ? WHERE project_id ="
                " (SELECT g.project_id FROM tasks t JOIN groups g ON g.group_id = t.group_id"
                "  WHERE t.task_id = ?)",
                (status, task_id),
            )


def _revert_assertion_problem(path: Path, merge_commit: str, revert_commit: str) -> str | None:
    """R10 §21's mechanical revert assertions.

    ``git revert -m 1`` restores the first parent's tree by construction, which is
    exactly why the assertion is worth making: if it ever fails, the revert is not
    the revert we think it is.
    """
    try:
        revert_tree = run_git("rev-parse", f"{revert_commit}^{{tree}}", cwd=path).stdout.strip()
        parent_tree = run_git("rev-parse", f"{merge_commit}^1^{{tree}}", cwd=path).stdout.strip()
        revert_parent = run_git("rev-parse", f"{revert_commit}^", cwd=path).stdout.strip()
        carried = run_git(
            "rev-list", "--count", f"{merge_commit}..{revert_commit}", cwd=path
        ).stdout.strip()
    except GitError as exc:  # pragma: no cover - defensive
        return f"could not read the revert: {exc}"
    if revert_tree != parent_tree:
        return "tree(revert_commit) != tree(first_parent(failed_merge_commit))"
    if revert_parent != merge_commit:
        return "the revert commit is not a direct child of the failed merge"
    if carried != "1":
        return f"the revert history carries {carried} commits beyond the revert itself"
    return None
