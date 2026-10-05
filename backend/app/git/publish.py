"""publish_task_branch: the only path from an agent gitdir into canonical.

R10 §13 / ADR-059: the Harness never runs ``git push`` at canonical. It calls
this API, and the scheduler validates, imports objects with a local ``fetch``,
then writes the ref itself. Because the scheduler's write does not pass through
the receive port, the canonical pre-receive hook can stay strict about *client*
pushes without getting in the way.

Validation order matters and follows ADR-059: task and attempt, then fencing
epoch, then branch whitelist, then alternates, then commit reachability, then the
idempotency key. Every rejection happens before any object or ref is written.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.claim.guarded_write import GuardError, guarded_write
from app.db.connection import Database
from app.git.branch import BranchNameError, is_integration_ref, parse_branch
from app.git.repo import CanonicalRepo, GitError, run_git


class PublishRejected(RuntimeError):
    """The publish is refused. ``code`` is the wire code from the MVP-08 table."""

    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True, slots=True)
class PublishResult:
    task_id: str
    attempt_id: str
    branch: str
    commit: str
    fencing_epoch: int
    intent_id: str
    intent_status: str
    replayed: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "branch": self.branch,
            "commit": self.commit,
            "fencing_epoch": self.fencing_epoch,
            "intent_id": self.intent_id,
            "intent_status": self.intent_status,
            "replayed": self.replayed,
        }


def _alternates_of(gitdir: Path) -> list[str]:
    path = gitdir / "objects" / "info" / "alternates"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class PublishProxy:
    """Validated object transfer and ref update into the canonical repository."""

    def __init__(self, database: Database, repo: CanonicalRepo | None = None) -> None:
        self.database = database
        self.repo = repo if repo is not None else CanonicalRepo.from_settings()

    def publish_task_branch(
        self,
        *,
        task_id: str,
        attempt_id: str,
        branch: str,
        commit: str,
        fencing_epoch: int,
        idempotency_key: str,
    ) -> PublishResult:
        self._reject_integration_target(branch)

        with self.database.transaction() as conn:
            replayed = self._replay(conn, idempotency_key)
            if replayed is not None:
                return replayed

            self._validate_epoch(conn, task_id=task_id, attempt_id=attempt_id, epoch=fencing_epoch)
            workspace = self._workspace(conn, attempt_id=attempt_id)
            self._validate_branch(branch, str(workspace["branch"]))

            gitdir = Path(str(workspace["path"])).parent / (
                Path(str(workspace["path"])).name + ".git"
            )
            self._validate_alternates(gitdir)
            self._validate_commit(gitdir, commit)

            intent_id = f"int-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO side_effect_intents(intent_id, type, task_id, status,"
                " idempotency_key, fencing_epoch, created_seq)"
                " VALUES (?, 'push', ?, 'pending', ?, ?, NULL)",
                (intent_id, task_id, idempotency_key, fencing_epoch),
            )

            self.repo.import_objects(gitdir, commit)
            if not self.repo.has_object(commit):
                raise PublishRejected(
                    "publish.commit_unreachable",
                    f"commit {commit} did not arrive in the canonical object store",
                )
            ref = f"refs/heads/{branch}"
            self.repo.update_ref(ref, commit)
            conn.execute(
                "UPDATE side_effect_intents SET status = 'done' WHERE intent_id = ?",
                (intent_id,),
            )
            conn.execute(
                "UPDATE task_attempts SET head_commit = ? WHERE attempt_id = ?",
                (commit, attempt_id),
            )

            result = PublishResult(
                task_id=task_id,
                attempt_id=attempt_id,
                branch=branch,
                commit=commit,
                fencing_epoch=fencing_epoch,
                intent_id=intent_id,
                intent_status="done",
            )
            conn.execute(
                "INSERT INTO command_idempotency(operation, idempotency_key, payload_hash,"
                " first_result_json, created_seq) VALUES ('publish', ?, ?, ?, NULL)",
                (idempotency_key, _hash(commit), json.dumps(result.as_json())),
            )
            return result

    # ------------------------------------------------------------ validation

    @staticmethod
    def _reject_integration_target(branch: str) -> None:
        if is_integration_ref(f"refs/heads/{branch}") or branch in {"integration", "main"}:
            raise PublishRejected(
                "publish.branch_not_allowed",
                f"{branch!r} may only be written by the Merge Worker",
                http_status=403,
            )

    @staticmethod
    def _validate_branch(requested: str, stored: str) -> None:
        """The whitelist is the branch recorded for this attempt, not a pattern.

        ``stored`` was produced by app.git.branch, so re-parsing it here is what
        keeps §14, the permission matrix and this check on one convention.
        """
        try:
            parsed = parse_branch(stored)
        except BranchNameError as exc:
            raise PublishRejected(
                "publish.branch_name_invalid",
                f"the recorded branch for this attempt is not a legal agent branch: {exc}",
            ) from exc
        if parsed.name != stored:
            raise PublishRejected(
                "publish.branch_name_invalid", f"recorded branch is not canonical: {stored!r}"
            )
        if requested != stored:
            raise PublishRejected(
                "publish.branch_not_allowed",
                f"{requested!r} is not the branch recorded for this attempt",
                http_status=403,
            )

    @staticmethod
    def _validate_epoch(
        conn: sqlite3.Connection, *, task_id: str, attempt_id: str, epoch: int
    ) -> None:
        try:
            guarded_write(conn, task_id=task_id, fencing_epoch=epoch, attempt_id=attempt_id)
        except GuardError as exc:
            raise PublishRejected("fencing.epoch_stale", str(exc)) from exc

    @staticmethod
    def _workspace(conn: sqlite3.Connection, *, attempt_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM workspaces WHERE attempt_id = ? AND status = 'active'",
            (attempt_id,),
        ).fetchone()
        if row is None:
            raise PublishRejected(
                "publish.commit_unreachable",
                f"attempt {attempt_id} has no active worktree to publish from",
                http_status=404,
            )
        return row

    def _validate_alternates(self, gitdir: Path) -> None:
        """ADR-059 step 1: alternates may point only inside this project's store.

        GIT-ACL-002's boundary test drives exactly this method's failure path.
        """
        allowed = str(self.repo.objects_dir)
        for entry in _alternates_of(gitdir):
            if Path(entry) != Path(allowed):
                raise PublishRejected(
                    "publish.alternates_invalid",
                    f"alternates entry {entry!r} is outside the canonical object store",
                    http_status=403,
                )

    @staticmethod
    def _validate_commit(gitdir: Path, commit: str) -> None:
        proc = run_git(
            "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}", cwd=gitdir, check=False
        )
        if proc.returncode != 0:
            raise PublishRejected(
                "publish.commit_unreachable",
                f"commit {commit} is not reachable in the agent gitdir",
            )

    # ------------------------------------------------------------- idempotency

    @staticmethod
    def _replay(conn: sqlite3.Connection, idempotency_key: str) -> PublishResult | None:
        row = conn.execute(
            "SELECT first_result_json FROM command_idempotency"
            " WHERE operation = 'publish' AND idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None or row["first_result_json"] is None:
            return None
        stored = json.loads(str(row["first_result_json"]))
        return PublishResult(
            task_id=str(stored["task_id"]),
            attempt_id=str(stored["attempt_id"]),
            branch=str(stored["branch"]),
            commit=str(stored["commit"]),
            fencing_epoch=int(stored["fencing_epoch"]),
            intent_id=str(stored["intent_id"]),
            intent_status=str(stored["intent_status"]),
            replayed=True,
        )


def _hash(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


__all__ = [
    "GitError",
    "PublishProxy",
    "PublishRejected",
    "PublishResult",
]
