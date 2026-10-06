"""Per-attempt worktrees: provisioning, isolation, archiving (R10 §14).

One attempt, one writable directory. The branch name and the directory are
created together and recorded in ``workspaces``, because the isolation claim in
the checklist ("两个任务不得共享可写 worktree") is only checkable if the tree
knows which directory belongs to which attempt.

The physical path uses a short id (R10 §14: "物理目录使用短 ID，例如 wt/a3f9");
the full branch name lives in the database, not in the path.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.db.connection import Database
from app.git.branch import agent_branch, parse_branch
from app.git.repo import CanonicalRepo, run_git


class WorktreeError(RuntimeError):
    """Base class for worktree failures."""


class WorktreeCapacityExceeded(WorktreeError):
    """The concurrent worktree cap is reached (R10 §14: queue the task)."""


class WorktreeIsolationError(WorktreeError):
    """Two attempts would share a writable directory."""


@dataclass(frozen=True, slots=True)
class Worktree:
    workspace_id: str
    task_id: str
    attempt_id: str
    branch: str
    path: Path
    gitdir: Path


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    attempt_id: str
    bundle: Path
    patch: Path
    size_bytes: int


def short_id(attempt_id: str, *, length: int = 4) -> str:
    """A short, stable directory id for an attempt (R10 §14)."""
    digest = hashlib.sha256(attempt_id.encode("utf-8")).hexdigest()
    return digest[:length]


def _force_rmtree(path: Path) -> None:
    """Remove a git directory on Windows.

    Git writes object files read-only, and ``shutil.rmtree`` silently skips
    those on Windows -- with ``ignore_errors=True`` the recycle looked like it
    had worked while the directory stayed behind. Clear the attribute first.
    """
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
    shutil.rmtree(path, ignore_errors=True)


class WorktreeManager:
    """Provision, archive and recycle attempt worktrees."""

    def __init__(self, database: Database, repo: CanonicalRepo, root: Path | None = None) -> None:
        self.database = database
        self.repo = repo
        self.root = Path(root) if root is not None else Path(settings.worktree_root)

    # ------------------------------------------------------------- provision

    def provision(
        self,
        *,
        task_id: str,
        attempt_id: str,
        group_short: str,
        harness: str,
        base_commit: str,
        max_concurrent: int | None = None,
    ) -> Worktree:
        """Create the attempt's worktree, or return the existing one.

        Re-provisioning the same attempt is a no-op: retrying a provisioning
        request must not produce a second writable directory for one attempt.
        """
        branch = agent_branch(
            group_short=group_short,
            harness=harness,
            task_id=task_id,
            attempt_id=attempt_id,
        )
        limit = settings.concurrent_worktree if max_concurrent is None else max_concurrent
        with self.database.transaction() as conn:
            existing = self._find_active(conn, attempt_id=attempt_id)
            if existing is not None:
                return existing
            active = self._active_count(conn)
            if active >= limit:
                raise WorktreeCapacityExceeded(
                    f"concurrent worktree cap reached ({active}/{limit})"
                )

            self.root.mkdir(parents=True, exist_ok=True)
            path, gitdir = self._unique_paths(conn, attempt_id)
            self._create(path, gitdir, branch, base_commit)
            workspace_id = f"ws-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO workspaces(workspace_id, task_id, attempt_id, branch, path,"
                " status, created_at) VALUES (?, ?, ?, ?, ?, 'active', datetime('now'))",
                (workspace_id, task_id, attempt_id, branch, str(path)),
            )
            self.assert_isolated(conn)
            return Worktree(
                workspace_id=workspace_id,
                task_id=task_id,
                attempt_id=attempt_id,
                branch=branch,
                path=path,
                gitdir=gitdir,
            )

    def _unique_paths(self, conn: sqlite3.Connection, attempt_id: str) -> tuple[Path, Path]:
        """Pick a short directory id that no other attempt holds."""
        for length in range(4, 12):
            candidate = short_id(attempt_id, length=length)
            path = self.root / candidate
            gitdir = self.root / f"{candidate}.git"
            taken = conn.execute(
                "SELECT 1 FROM workspaces WHERE path = ? AND status = 'active'", (str(path),)
            ).fetchone()
            if taken is None:
                return path, gitdir
        raise WorktreeError(f"could not find a free short id for {attempt_id}")

    def _create(self, path: Path, gitdir: Path, branch: str, base_commit: str) -> None:
        if path.exists():
            # A leftover directory from a crashed run. R10 §14 wants the archive
            # before the recycle, so this is not silently reused.
            raise WorktreeError(f"{path} already exists and is not a tracked workspace")
        run_git("init", "--separate-git-dir", str(gitdir), str(path))
        # R10 §14 shortens the physical directory, but refs/heads/<branch>
        # under the gitdir still carries the full branch name; a long task id
        # in a deep root exceeds Windows' MAX_PATH when git creates the loose
        # ref. Extended-length paths are what lets the §14 layout survive it.
        run_git("config", "core.longpaths", "true", cwd=path)
        alternates = gitdir / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        # ADR-059: objects are read through the canonical store, which is what
        # GIT-ACL-002 checks the boundary of.
        alternates.write_text(str(self.repo.objects_dir) + "\n", encoding="utf-8")
        run_git("fetch", "--no-tags", str(self.repo.path), base_commit, cwd=path)
        run_git("checkout", "-b", branch, "FETCH_HEAD", cwd=path)

    # ------------------------------------------------------------ isolation

    @staticmethod
    def assert_isolated(conn: sqlite3.Connection) -> None:
        """Every active workspace owns a distinct directory.

        This is the mechanical form of "两个任务不得共享可写 worktree".
        """
        rows = conn.execute(
            "SELECT path, COUNT(*) AS n FROM workspaces WHERE status = 'active'"
            " GROUP BY path HAVING n > 1"
        ).fetchall()
        if rows:
            shared = ", ".join(str(row["path"]) for row in rows)
            raise WorktreeIsolationError(f"writable worktree shared by several attempts: {shared}")

    @staticmethod
    def _active_count(conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM workspaces WHERE status = 'active'"
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    @staticmethod
    def _find_active(conn: sqlite3.Connection, *, attempt_id: str) -> Worktree | None:
        row = conn.execute(
            "SELECT * FROM workspaces WHERE attempt_id = ? AND status = 'active'",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        path = Path(str(row["path"]))
        return Worktree(
            workspace_id=str(row["workspace_id"]),
            task_id=str(row["task_id"]),
            attempt_id=str(row["attempt_id"]),
            branch=str(row["branch"]),
            path=path,
            gitdir=path.parent / f"{path.name}.git",
        )

    def workspace_for_attempt(self, attempt_id: str) -> Worktree | None:
        with self.database.connect() as conn:
            return self._find_active(conn, attempt_id=attempt_id)

    # --------------------------------------------------------- archive/recycle

    def archive_and_recycle(
        self, *, attempt_id: str, archive_root: Path | None = None
    ) -> ArchiveResult:
        """Bundle and patch the attempt branch, verify, then remove the tree.

        R10 §14: "归档校验通过后才回收 worktree". The bundle is verified before
        anything is deleted, so a failed verification leaves the worktree in
        place for a human rather than losing the only copy.
        """
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE attempt_id = ? AND status = 'active'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                raise WorktreeError(f"no active workspace for attempt {attempt_id}")
            path = Path(str(row["path"]))
            branch = str(row["branch"])
            target_root = (
                Path(archive_root)
                if archive_root is not None
                else Path(settings.data_dir) / "archives"
            )
            target_root.mkdir(parents=True, exist_ok=True)
            bundle = target_root / f"{attempt_id}.bundle"
            patch = target_root / f"{attempt_id}.patch"
            base = self._base_commit(path)

            run_git("bundle", "create", str(bundle), branch, cwd=path)
            verify = run_git("bundle", "verify", str(bundle), cwd=path, check=False)
            if verify.returncode != 0:
                raise WorktreeError(
                    f"bundle verification failed for {attempt_id}: {verify.stderr.strip()}"
                )
            head = run_git("rev-parse", branch, cwd=path).stdout.strip()
            diff = run_git("diff", f"{base}..{head}", cwd=path).stdout
            patch.write_text(diff, encoding="utf-8")

            conn.execute(
                "UPDATE workspaces SET status = 'archived' WHERE workspace_id = ?",
                (str(row["workspace_id"]),),
            )
            for kind, uri, digest_source in (
                ("git_bundle", bundle, bundle),
                ("patch", patch, patch),
            ):
                payload = digest_source.read_bytes()
                conn.execute(
                    "INSERT OR IGNORE INTO artifacts(artifact_id, task_id, attempt_id,"
                    " kind, uri, hash, size_bytes, redaction_state)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'clear')",
                    (
                        f"art-{uuid.uuid4().hex}",
                        str(row["task_id"]),
                        attempt_id,
                        kind,
                        str(uri),
                        "sha256:" + hashlib.sha256(payload).hexdigest(),
                        len(payload),
                    ),
                )

        gitdir = path.parent / f"{path.name}.git"
        if path.exists():
            run_git("worktree", "prune", cwd=path, check=False)
            _force_rmtree(path)
        if gitdir.exists():
            _force_rmtree(gitdir)
        return ArchiveResult(
            attempt_id=attempt_id, bundle=bundle, patch=patch, size_bytes=bundle.stat().st_size
        )

    @staticmethod
    def _base_commit(path: Path) -> str:
        """The commit the attempt branched from, for the patch's left side."""
        proc = run_git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
            cwd=path,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
        return (
            run_git("rev-list", "--max-parents=0", "HEAD", cwd=path).stdout.strip().splitlines()[-1]
        )


def branch_of_workspace(row: sqlite3.Row) -> str:
    """Re-parse a stored branch, proving it still satisfies R10 §14."""
    return parse_branch(str(row["branch"])).name
