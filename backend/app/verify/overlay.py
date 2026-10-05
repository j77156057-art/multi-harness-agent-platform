"""Per-job verify overlays: a fresh detached worktree at one exact commit.

R10 §16 requires the check to run "在要合并的精确 commit" in a "全新 detached
worktree", with a per-worktree gitdir whose writable part is only the overlay
(HEAD, logs, index) while objects come read-only through alternates.

One overlay per job id, never reused: §16 says a requeued job must get a new
overlay, because a dirty overlay is how a "pass" from a previous attempt leaks
into a new one.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.git.repo import CanonicalRepo, run_git


class OverlayError(RuntimeError):
    """The overlay could not be created or verified."""


@dataclass(frozen=True, slots=True)
class Overlay:
    overlay_id: str
    path: Path
    gitdir: Path
    commit: str


def _force_rmtree(path: Path) -> None:
    """See app.git.worktree: git marks objects read-only on Windows."""
    for root, dirs, files in os.walk(path, topdown=False):
        for name in files + dirs:
            with contextlib.suppress(OSError):
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
    shutil.rmtree(path, ignore_errors=True)


class OverlayManager:
    def __init__(
        self, repo: CanonicalRepo, root: Path | None = None, *, objects_dir: Path | None = None
    ) -> None:
        self.repo = repo
        self.root = Path(root) if root is not None else Path(settings.data_dir) / "verify"
        self.objects_dir = objects_dir if objects_dir is not None else repo.objects_dir

    def create(self, *, commit: str, job_id: str) -> Overlay:
        overlay_id = f"ov-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{job_id}-{overlay_id[3:11]}"
        gitdir = self.root / f"{path.name}.git"
        if path.exists() or gitdir.exists():
            raise OverlayError(f"overlay path for job {job_id} already exists")

        run_git("init", "--separate-git-dir", str(gitdir), str(path))
        alternates = gitdir / "objects" / "info" / "alternates"
        alternates.parent.mkdir(parents=True, exist_ok=True)
        alternates.write_text(str(self.objects_dir) + "\n", encoding="utf-8")
        run_git("fetch", "--no-tags", str(self.repo.path), commit, cwd=path)
        run_git("checkout", "--detach", "FETCH_HEAD", cwd=path)

        overlay = Overlay(overlay_id=overlay_id, path=path, gitdir=gitdir, commit=commit)
        if not self.is_clean(overlay):
            self.recycle(overlay)
            raise OverlayError(f"overlay for job {job_id} is not a clean checkout of {commit}")
        return overlay

    @staticmethod
    def is_clean(overlay: Overlay) -> bool:
        """R10 §16: ``git status`` must be empty before the check runs."""
        proc = run_git("status", "--porcelain", cwd=overlay.path, check=False)
        return proc.returncode == 0 and not proc.stdout.strip()

    @staticmethod
    def head_commit(overlay: Overlay) -> str:
        return run_git("rev-parse", "HEAD", cwd=overlay.path).stdout.strip()

    @staticmethod
    def changed_files(overlay: Overlay, base_commit: str | None) -> list[tuple[str, str]]:
        """``(status, path)`` pairs for ``base..HEAD``; empty without a base."""
        if not base_commit:
            return []
        proc = run_git(
            "diff",
            "--name-status",
            f"{base_commit}..{overlay.commit}",
            cwd=overlay.path,
            check=False,
        )
        if proc.returncode != 0:
            return []
        rows: list[tuple[str, str]] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                rows.append((parts[0].strip(), parts[1].strip()))
        return rows

    def recycle(self, overlay: Overlay) -> None:
        if overlay.path.exists():
            run_git("worktree", "prune", cwd=overlay.path, check=False)
            _force_rmtree(overlay.path)
        if overlay.gitdir.exists():
            _force_rmtree(overlay.gitdir)
