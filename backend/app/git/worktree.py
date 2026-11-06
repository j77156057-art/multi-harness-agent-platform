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
import json
import os
import re
import shutil
import sqlite3
import stat
import tarfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path

from app.config import settings
from app.db.connection import Database
from app.git.branch import agent_branch, parse_branch
from app.git.repo import CanonicalRepo, run_git


class WorktreeError(RuntimeError):
    """Base class for worktree failures."""


class WorktreeCapacityExceeded(WorktreeError):
    """The concurrent worktree cap is reached (R10 §14: queue the task).

    Carries the same shape the admin-side capacity report uses
    (``unavailability_reasons`` / ``earliest_available_at``) so a release
    payload can tell a human *why* the slot is missing and when one frees,
    instead of only "capacity".
    """

    def __init__(
        self,
        message: str,
        *,
        limit: int | None = None,
        active: int | None = None,
        earliest_available_at: str | None = None,
        unavailability_reasons: list[str] | tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.limit = limit
        self.active = active
        self.earliest_available_at = earliest_available_at
        self.unavailability_reasons = list(unavailability_reasons)


class WorktreeArchiveVerificationError(WorktreeError):
    """The archive does not prove every file survived; the tree must stay."""


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


#: 归档脏工作时必须挡住的敏感文件。07 现场：worktree 里留着成员自己造的
#: ``.env`` / 私钥时，归档 tar 会把它们原样带进 ``data/archives`` —— 归档目录
#: 是平台长期保留、可被人翻看的地方，不能变成凭证泄漏的二次入口。
_SECRET_PATTERNS = (".env*", "*.key", "*.pem", "id_rsa*", "*.p12")


def _is_secret_path(path: str) -> bool:
    """Match the secret patterns against the file name (not the directory)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return any(fnmatchcase(name, pattern) for pattern in _SECRET_PATTERNS)


def _unquote_path(path: str) -> str:
    """Undo git's C-style quoting of a path (same rule as the scope check)."""
    text = path.strip()
    if len(text) < 2 or not text.startswith('"') or not text.endswith('"'):
        return text
    body = text[1:-1]
    out = bytearray()
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\" and body[index + 1 : index + 4].isdigit():
            try:
                out.append(int(body[index + 1 : index + 4], 8))
                index += 4
                continue
            except ValueError:
                pass
        out.extend(char.encode("utf-8"))
        index += 1
    return out.decode("utf-8", errors="replace")


def _artifact_id(attempt_id: str, kind: str) -> str:
    """Deterministic id so a timeout snapshot and a later recycle dedupe."""
    digest = hashlib.sha256(f"{attempt_id}:{kind}".encode("utf-8")).hexdigest()
    return "art-" + digest[:32]


def _reconcile_vanished_worktrees(conn: sqlite3.Connection) -> list[str]:
    """Archive active rows whose directory *and* separate gitdir are both gone.

    A crashed cleanup can leave an ``active`` row pointing at nothing.  That row
    holds no writable resource, so keeping it active would eat a slot forever.
    A remaining directory (even empty) is deliberately left alone: it may still
    hold content a human needs to recover.
    """
    vanished: list[str] = []
    rows = conn.execute(
        "SELECT workspace_id, attempt_id, path FROM workspaces WHERE status = 'active'"
    ).fetchall()
    for row in rows:
        path = Path(str(row["path"]))
        gitdir = path.parent / f"{path.name}.git"
        if path.exists() or gitdir.exists():
            continue
        conn.execute(
            "UPDATE workspaces SET status = 'archived' WHERE workspace_id = ?",
            (str(row["workspace_id"]),),
        )
        vanished.append(str(row["attempt_id"]))
    return vanished


def worktree_capacity(
    conn: sqlite3.Connection,
    *,
    now: datetime | None = None,
    limit: int | None = None,
    exclude_attempt_id: str | None = None,
) -> dict[str, object]:
    """Count worktree slots that a *live* development lease actually holds.

    The old count followed the disk (``path.exists() or gitdir.exists()``),
    which meant a row left behind by a dead run kept a slot forever: the drill
    had 8 historical directories, the cap was 8, and every new claim failed
    with ``WorktreeCapacityExceeded`` even though no attempt was running.

    The three exclusions requested by the 2026-11-05 fix:
    * expired lease      -> ``l.lease_until > now``;
    * released lease     -> ``l.released_at IS NULL``;
    * recycled workspace -> no *archived* workspace may keep the slot.  A live
      lease on an attempt whose tree was already Archived is data drift; the
      archived row is authoritative (an attempt with no workspace row at all
      is the in-flight reservation we *do* count).

    **Counting the lease, not the workspace, is the 2026-11-06 fix.**  The join
    through ``workspaces`` missed the window between ``claim`` granting the
    lease and ``start_task`` creating the directory: with cap=1 two claims
    both saw zero holders (E2E: 17:23:00.443/.588), the loser then died at
    provision leaving ``close_reason NULL``.  A live development lease *is*
    the reservation -- it is inserted inside the claim transaction, so the
    next claim's check (same transaction on the same connection for a batch)
    sees it.  ``exclude_attempt_id`` is for ``provision`` itself: the caller's
    own reservation must not lock it out of the slot it already holds.

    ``earliest_available_at`` is the smallest live ``lease_until`` among the
    holders, so a queued task can say when a slot is expected back.
    """
    moment = now or datetime.now(UTC)
    stamp = moment.isoformat() if isinstance(moment, datetime) else str(moment)
    _reconcile_vanished_worktrees(conn)
    rows = conn.execute(
        "SELECT l.attempt_id AS attempt_id,"
        "       MAX(l.owner) AS owner,"
        "       MAX(l.lease_until) AS lease_until,"
        "       MAX(w.path) AS path"
        "  FROM leases l"
        "  LEFT JOIN workspaces w"
        "    ON w.attempt_id = l.attempt_id AND w.status = 'active'"
        " WHERE l.kind = 'development'"
        "   AND l.released_at IS NULL"
        "   AND l.lease_until > ?"
        "   AND (? IS NULL OR l.attempt_id <> ?)"
        "   AND (w.workspace_id IS NOT NULL"
        "        OR NOT EXISTS (SELECT 1 FROM workspaces x WHERE x.attempt_id = l.attempt_id))"
        " GROUP BY l.attempt_id"
        " ORDER BY MIN(l.lease_until)",
        (stamp, exclude_attempt_id, exclude_attempt_id),
    ).fetchall()
    cap = settings.concurrent_worktree if limit is None else limit
    active = len(rows)
    at_capacity = active >= cap
    earliest = str(rows[0]["lease_until"]) if at_capacity and rows else None
    reasons = [
        f"{row['attempt_id']} ({row['owner'] or 'unknown'}) holds"
        f" {row['path'] or 'a slot awaiting provisioning'}"
        f" until {row['lease_until']}"
        for row in rows
    ] if at_capacity else []
    return {
        "limit": cap,
        "active": active,
        "available_slots": max(0, cap - active),
        "at_capacity": at_capacity,
        "earliest_available_at": earliest,
        "unavailability_reasons": reasons,
    }


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
            report = worktree_capacity(conn, limit=limit, exclude_attempt_id=attempt_id)
            if report["at_capacity"]:
                reasons = list(report["unavailability_reasons"])
                detail = "; ".join(reasons) if reasons else "no live lease detail"
                raise WorktreeCapacityExceeded(
                    f"concurrent worktree cap reached ({report['active']}/{limit});"
                    f" earliest available at {report['earliest_available_at']}; {detail}",
                    limit=int(report["limit"]),
                    active=int(report["active"]),
                    earliest_available_at=(
                        str(report["earliest_available_at"])
                        if report["earliest_available_at"] is not None
                        else None
                    ),
                    unavailability_reasons=reasons,
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
        # GIT-ACL-002 checks the boundary of. newline="\n" is load-bearing:
        # git does not strip a trailing CR from an alternates entry, and the
        # R8 harness drill observed every worktree git command failing with
        # `object directory ... does not exist` while this file was CRLF
        # (same treatment as the pre-receive hook).
        alternates.write_text(str(self.repo.objects_dir) + "\n", encoding="utf-8", newline="\n")
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

    def reconcile_missing_workspace(self, *, attempt_id: str) -> bool:
        """Close an active row whose writable tree is already gone.

        A crashed cleanup can leave the database row active after the physical
        worktree and its separate gitdir have both disappeared.  There is no
        branch content left to archive in that case, so keeping the row active
        would permanently consume capacity.  This method is deliberately
        limited to the both-missing case; a remaining path or gitdir still
        requires the normal archive path and human-visible failure handling.
        """
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT workspace_id, path FROM workspaces"
                " WHERE attempt_id = ? AND status = 'active'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return False
            path = Path(str(row["path"]))
            gitdir = path.parent / f"{path.name}.git"
            # An interrupted removal may leave an empty directory behind.  It
            # has no writable content once the separate gitdir is gone, but a
            # non-empty directory must remain visible for human recovery.
            path_has_content = path.exists() and any(path.iterdir())
            if path_has_content or gitdir.exists():
                return False
            conn.execute(
                "UPDATE workspaces SET status = 'archived' WHERE workspace_id = ?",
                (str(row["workspace_id"]),),
            )
            return True

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
            self._heal_config(path)
            base = self._base_commit(path)

            run_git("bundle", "create", str(bundle), branch, cwd=path)
            verify = run_git("bundle", "verify", str(bundle), cwd=path, check=False)
            if verify.returncode != 0:
                raise WorktreeArchiveVerificationError(
                    f"bundle verification failed for {attempt_id}: {verify.stderr.strip()}"
                )
            head = run_git("rev-parse", branch, cwd=path).stdout.strip()
            diff = run_git("diff", f"{base}..{head}", cwd=path).stdout
            patch.write_text(diff, encoding="utf-8")

            # 07 现场：成员写出的文件停在 worktree 里没提交，回收前只打了
            # base..head，四条任务的 patch 全是 0 字节。删除之前先把未提交与
            # 未跟踪的工作快照下来（manifest 标 deliverable=false），否则回收
            # 就等于把唯一的证据一起删掉。
            snapshot = self._snapshot_uncommitted(
                row=row,
                path=path,
                attempt_id=attempt_id,
                target_root=target_root,
                bundle=bundle,
                patch=patch,
            )
            # 回收的最后一道门槛：bundle 能还原提交，快照里的每个字节都要和
            # 磁盘上的一致；校验不过就直接抛错，行还是 active、目录还在。
            self._verify_archive(
                path=path,
                attempt_id=attempt_id,
                bundle=bundle,
                patch=patch,
                snapshot=snapshot,
            )

            conn.execute(
                "UPDATE workspaces SET status = 'archived' WHERE workspace_id = ?",
                (str(row["workspace_id"]),),
            )
            artifacts: list[tuple[str, Path, str]] = [
                ("git_bundle", bundle, f"art-{uuid.uuid4().hex}"),
                ("patch", patch, f"art-{uuid.uuid4().hex}"),
            ]
            if snapshot is not None:
                artifacts.extend(snapshot["artifacts"])
            for kind, uri, artifact_id in artifacts:
                payload = uri.read_bytes()
                conn.execute(
                    "INSERT OR IGNORE INTO artifacts(artifact_id, task_id, attempt_id,"
                    " kind, uri, hash, size_bytes, redaction_state)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'clear')",
                    (
                        artifact_id,
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

    # ----------------------------------------------------- uncommitted snapshot

    def checkpoint(
        self, *, attempt_id: str, archive_root: Path | None = None
    ) -> dict[str, object] | None:
        """Snapshot uncommitted work but keep the worktree (timeout path).

        超时的那条路故意保留 attempt 和 worktree（租约内可以重跑继续）。
        但保留不等于不用留证据：如果租约到期后清扫器回收，脏工作就会跟着
        消失。这里在超时的一刻先落一份快照，回收时再落一份（同名覆盖，
        artifact 行按确定性 id 去重）。
        """
        with self.database.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE attempt_id = ? AND status = 'active'",
                (attempt_id,),
            ).fetchone()
            if row is None:
                return None
            path = Path(str(row["path"]))
            target_root = (
                Path(archive_root)
                if archive_root is not None
                else Path(settings.data_dir) / "archives"
            )
            if not path.exists():
                return None
            self._heal_config(path)
            snapshot = self._snapshot_uncommitted(
                row=row, path=path, attempt_id=attempt_id, target_root=target_root
            )
            if snapshot is None:
                return None
            for kind, uri, artifact_id in snapshot["artifacts"]:
                payload = uri.read_bytes()
                conn.execute(
                    "INSERT OR IGNORE INTO artifacts(artifact_id, task_id, attempt_id,"
                    " kind, uri, hash, size_bytes, redaction_state)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, 'clear')",
                    (
                        artifact_id,
                        str(row["task_id"]),
                        attempt_id,
                        kind,
                        str(uri),
                        "sha256:" + hashlib.sha256(payload).hexdigest(),
                        len(payload),
                    ),
                )
            return {
                "status": "archived",
                "deliverable": False,
                "reason": "uncommitted_worktree",
                "dirty_diff": str(snapshot["dirty_diff"]),
                "modified_archive": (
                    str(snapshot["modified_archive"]) if snapshot["modified_archive"] else None
                ),
                "modified_files": snapshot["modified_files"],
                "deleted_files": snapshot["deleted_files"],
                "untracked_archive": (
                    str(snapshot["untracked_archive"]) if snapshot["untracked_archive"] else None
                ),
                "untracked_files": snapshot["untracked_files"],
                "manifest": str(snapshot["manifest"]),
                "excluded_secret_paths": snapshot["excluded_secret_paths"],
            }

    def _snapshot_uncommitted(
        self,
        *,
        row: sqlite3.Row,
        path: Path,
        attempt_id: str,
        target_root: Path,
        bundle: Path | None = None,
        patch: Path | None = None,
    ) -> dict[str, object] | None:
        """Write the dirty diff / untracked tar / manifest of an attempt.

        Returns ``None`` when the worktree is clean: a clean attempt needs no
        non-deliverable evidence, and the existing archive test asserts the
        artifact set is exactly ``git_bundle`` + ``patch`` for that case.
        """
        status_lines = [
            line
            for line in run_git("status", "--porcelain", cwd=path).stdout.splitlines()
            if line.strip()
        ]
        if not status_lines:
            return None

        suffix = attempt_id.rsplit(".", 1)[-1] if "." in attempt_id else attempt_id
        root = target_root / attempt_id
        root.mkdir(parents=True, exist_ok=True)
        dirty_diff = root / f"checkpoint-{suffix}.dirty.diff"
        modified_tar = root / f"checkpoint-{suffix}.modified.tar"
        untracked_tar = root / f"checkpoint-{suffix}.untracked.tar"
        manifest_path = root / "manifest.json"

        tracked_changes: list[str] = []
        excluded: list[str] = []
        for line in status_lines:
            if line[:2].strip() == "??":
                continue  # untracked is enumerated file-by-file below
            item = line[3:].strip()
            if " -> " in item:
                item = item.rsplit(" -> ", 1)[1]
            item = _unquote_path(item).replace("\\", "/")
            if not item or item.endswith("/"):
                continue
            if _is_secret_path(item):
                excluded.append(item)
                continue
            tracked_changes.append(item)

        # 完整 diff（staged + unstaged 对 HEAD）。未跟踪文件不在 diff 里，
        # 因此另有 untracked tar + manifest 清单。
        dirty_diff.write_text(
            run_git("diff", "HEAD", cwd=path).stdout, encoding="utf-8"
        )

        modified_files: list[str] = []
        deleted_files: list[str] = []
        for item in sorted(set(tracked_changes)):
            source = path / item
            if source.is_file() or source.is_symlink():
                modified_files.append(item)
            else:
                deleted_files.append(item)
        if modified_files:
            # 文件快照而不只是 diff：二进制文件、换行、diff 无法表达的字节
            # 都要能原样还原。
            with tarfile.open(modified_tar, "w") as archive:
                for item in modified_files:
                    source = path / item
                    if source.is_file() or source.is_symlink():
                        archive.add(source, arcname=item)

        untracked = [
            _unquote_path(line).replace("\\", "/")
            for line in run_git(
                "ls-files", "--others", "--exclude-standard", cwd=path
            ).stdout.splitlines()
            if line.strip()
        ]
        untracked_files = [item for item in untracked if not _is_secret_path(item)]
        excluded.extend(item for item in untracked if _is_secret_path(item))
        if untracked_files:
            # 排除在**打包时**生效：敏感文件根本不进 tar，不是打包后再删。
            with tarfile.open(untracked_tar, "w") as archive:
                for item in sorted(set(untracked_files)):
                    source = path / item
                    if source.is_file():
                        archive.add(source, arcname=item)

        changed_files = sorted(set(tracked_changes) | set(untracked_files))
        entries: list[dict[str, object]] = []
        if bundle is not None:
            entries.append(
                {"kind": "git_bundle", "path": str(bundle), "deliverable": True}
            )
        if patch is not None:
            entries.append({"kind": "patch", "path": str(patch), "deliverable": True})
        entries.append(
            {
                "kind": "checkpoint_dirty",
                "path": str(dirty_diff),
                "deliverable": False,
                "reason": "uncommitted_worktree",
                "bytes": dirty_diff.stat().st_size,
            }
        )
        if modified_tar.exists():
            entries.append(
                {
                    "kind": "checkpoint_modified",
                    "path": str(modified_tar),
                    "deliverable": False,
                    "reason": "uncommitted_worktree",
                    "files": modified_files,
                }
            )
        if untracked_tar.exists():
            entries.append(
                {
                    "kind": "checkpoint_untracked",
                    "path": str(untracked_tar),
                    "deliverable": False,
                    "reason": "uncommitted_worktree",
                    "files": sorted(set(untracked_files)),
                }
            )
        manifest = {
            "attempt_id": attempt_id,
            "task_id": str(row["task_id"]),
            "branch": str(row["branch"]),
            "created_at": datetime.now(UTC).isoformat(),
            "deliverable": False,
            "reason": "uncommitted_worktree",
            "entries": entries,
            "changed_files": changed_files,
            "modified_files": modified_files,
            "deleted_files": deleted_files,
            "untracked_files": sorted(set(untracked_files)),
            "excluded_secret_paths": sorted(set(excluded)),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        artifacts: list[tuple[str, Path, str]] = []
        if dirty_diff.stat().st_size > 0:
            artifacts.append(
                (
                    "checkpoint_dirty",
                    dirty_diff,
                    _artifact_id(attempt_id, "checkpoint_dirty"),
                )
            )
        if modified_tar.exists():
            artifacts.append(
                (
                    "checkpoint_modified",
                    modified_tar,
                    _artifact_id(attempt_id, "checkpoint_modified"),
                )
            )
        if untracked_tar.exists():
            artifacts.append(
                (
                    "checkpoint_untracked",
                    untracked_tar,
                    _artifact_id(attempt_id, "checkpoint_untracked"),
                )
            )
        return {
            "dirty_diff": dirty_diff,
            "modified_archive": modified_tar if modified_tar.exists() else None,
            "modified_files": modified_files,
            "deleted_files": deleted_files,
            "changed_files": changed_files,
            "untracked_archive": untracked_tar if untracked_tar.exists() else None,
            "untracked_files": sorted(set(untracked_files)),
            "manifest": manifest_path,
            "excluded_secret_paths": sorted(set(excluded)),
            "artifacts": artifacts,
        }

    def _verify_archive(
        self,
        *,
        path: Path,
        attempt_id: str,
        bundle: Path,
        patch: Path,
        snapshot: dict[str, object] | None,
    ) -> None:
        """Prove the archive can restore the tree before anything is deleted.

        Checks, in order:
        1. ``git bundle verify`` (commits are restorable);
        2. every tracked modified file has a byte-identical entry in
           ``checkpoint_modified.tar``;
        3. every untracked file has a byte-identical entry in
           ``checkpoint_untracked.tar``;
        4. every deleted file has a non-empty diff (its content is gone from
           disk, so the diff is the only record).

        Any mismatch raises :class:`WorktreeArchiveVerificationError`; the
        caller has not updated the row or removed the directory at that point.
        """
        if not bundle.is_file():
            raise WorktreeArchiveVerificationError(
                f"bundle missing for {attempt_id}: {bundle}"
            )
        verify = run_git("bundle", "verify", str(bundle), cwd=path, check=False)
        if verify.returncode != 0:
            raise WorktreeArchiveVerificationError(
                f"bundle verification failed for {attempt_id}: {verify.stderr.strip()}"
            )
        if snapshot is None:
            return

        manifest_path = Path(str(snapshot["manifest"]))
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WorktreeArchiveVerificationError(
                f"manifest unreadable for {attempt_id}: {exc}"
            ) from exc
        if manifest.get("attempt_id") != attempt_id:
            raise WorktreeArchiveVerificationError(
                f"manifest belongs to {manifest.get('attempt_id')}, not {attempt_id}"
            )

        checks = (
            ("checkpoint_modified", snapshot.get("modified_archive"), manifest.get("modified_files", [])),
            ("checkpoint_untracked", snapshot.get("untracked_archive"), manifest.get("untracked_files", [])),
        )
        for kind, archive_path, files in checks:
            expected = [str(item) for item in files]
            if not expected:
                continue
            if archive_path is None or not Path(str(archive_path)).is_file():
                raise WorktreeArchiveVerificationError(
                    f"{kind} missing {len(expected)} file(s) for {attempt_id}"
                )
            with tarfile.open(str(archive_path), "r") as archive:
                members = {
                    name.replace("\\", "/"): member for name, member in
                    ((name, archive.getmember(name)) for name in archive.getnames())
                }
                for item in expected:
                    if _is_secret_path(item):
                        raise WorktreeArchiveVerificationError(
                            f"{kind} unexpectedly contains secret path {item}"
                        )
                    member = members.get(item.replace("\\", "/"))
                    if member is None:
                        raise WorktreeArchiveVerificationError(
                            f"{kind} does not contain {item} for {attempt_id}"
                        )
                    source = path / item
                    if not source.exists():
                        raise WorktreeArchiveVerificationError(
                            f"{item} vanished before archive verification"
                        )
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise WorktreeArchiveVerificationError(
                            f"{kind} member {item} is not a regular file"
                        )
                    archived_bytes = extracted.read()
                    disk_bytes = source.read_bytes()
                    if hashlib.sha256(archived_bytes).hexdigest() != hashlib.sha256(
                        disk_bytes
                    ).hexdigest():
                        raise WorktreeArchiveVerificationError(
                            f"{kind} bytes differ for {item} in {attempt_id}"
                        )

        for item in [str(entry) for entry in manifest.get("deleted_files", [])]:
            deleted = run_git("diff", "HEAD", "--", item, cwd=path, check=False)
            if deleted.returncode != 0 or not deleted.stdout.strip():
                raise WorktreeArchiveVerificationError(
                    f"deleted file {item} has no diff in {attempt_id}"
                )

    @staticmethod
    def _heal_config(path: Path) -> str | None:
        """工作树的 git 配置坏了就修好它。

        成员 CLI 会在工作树里跑自己的 git 命令。有的 CLI 把
        ``git config core.autocrlf --global`` 当成了「设值」（``--global`` 本该在
        键名**前面**），于是 ``core.autocrlf = --global`` 被写进这个工作树的本地
        配置 —— 而 git 读到一个非法布尔值，就让**这个仓库的所有 git 命令**全废
        （连 ``git rev-parse --git-dir`` 都报 "bad boolean config value"）。

        2026-10-29 实测的代价：平台回收工作树的那一步因此抛错，把整个 sweeper
        带走了 —— 这一群的周期维护（回收工位、救合入中的任务、升级提醒）全停，
        而且没人知道。

        这个配置是**平台自己的**工作树（平台建的、平台回收的），坏了平台可以修：
        把那几行去掉，其余配置照旧生效。修不了就返回说明，由调用方决定怎么办。
        """
        config = None
        gitdir = path / ".git"
        try:
            if gitdir.is_file():
                pointer = gitdir.read_text(encoding="utf-8", errors="replace").strip()
                if not pointer.startswith("gitdir:"):
                    return None
                config = Path(pointer.split(":", 1)[1].strip()) / "config"
            elif gitdir.is_dir():
                # ``git init --separate-git-dir`` 的布局：git 目录在同级 <name>.git。
                sibling = path.parent / f"{path.name}.git"
                if sibling.is_dir():
                    config = sibling / "config"
            if config is None or not config.is_file():
                return None
            # 探测要用**真的 git 命令**：``git config --file … --list`` 不会校验布尔值
            # （它照样列得出 ``core.autocrlf=--global``），坏值是在 git **要用**这个值
            # 时才炸。
            probe = run_git("rev-parse", "--git-dir", cwd=path, check=False)
            if probe.returncode == 0:
                return None
            detail = probe.stderr or ""
            match = re.search(
                r"bad boolean config value '([^']+)' for '([^']+)'", detail
            )
            if match is None:
                return None
            value, key = match.group(1), match.group(2)
            leaf = key.rsplit(".", 1)[-1]
            lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
            kept = [
                line
                for line in lines
                if not re.match(rf"\s*{re.escape(leaf)}\s*=\s*{re.escape(value)}\s*$", line)
            ]
            if len(kept) == len(lines):
                return None
            config.write_text("\n".join(kept) + "\n", encoding="utf-8")
            return f"removed the broken {key} = {value} from the worktree config"
        except OSError:
            return None

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
