"""Canonical bare repository, the git runner and the client-push guard.

R10 §13: the canonical repository is owned by the scheduler; Harnesses never
push to it. Object transfer happens the other way round -- the scheduler runs
``git fetch <agent-gitdir> <commit>`` to import objects, then ``update-ref``
(ADR-059). Because that path never goes through the receive port, a pre-receive
hook is safe to use as the guard for *client* pushes, which is what makes
"直接写集成分支的测试失败" a real rejection rather than a convention.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.db.connection import _lock_file, _unlock_file
from app.git.branch import is_integration_ref

INTEGRATION_BRANCH = "integration"

#: Git needs an identity to create the repository's first commit. Scoped to this
#: process, never written to any config file.
_IDENTITY = (
    "-c",
    "user.name=agent-platform-scheduler",
    "-c",
    "user.email=scheduler@localhost",
)


class GitError(RuntimeError):
    """A git invocation failed."""


def run_git(
    *args: str, cwd: Path | None = None, check: bool = True, env_extra: dict | None = None
) -> subprocess.CompletedProcess[str]:
    """Run git with a sanitised environment.

    The ambient git configuration is deliberately not trusted: a developer's
    ``commit.gpgsign`` or ``core.autocrlf`` would otherwise change what the
    tester asserts about.
    """
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "agent-platform-scheduler",
            "GIT_AUTHOR_EMAIL": "scheduler@localhost",
            "GIT_COMMITTER_NAME": "agent-platform-scheduler",
            "GIT_COMMITTER_EMAIL": "scheduler@localhost",
        }
    )
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc


def pre_receive_hook() -> str:
    """The canonical repository's client-push guard.

    Rejects everything except agent branches, and rejects those too on the two
    refs the Merge Worker owns. The scheduler's own publishes use ``update-ref``
    and never reach this hook.
    """
    return """#!/bin/sh
# Installed by the scheduler (R10 §13 / ADR-059). Harnesses publish through
# publish_task_branch, which imports objects with a local fetch and writes the
# ref from inside the scheduler -- that path never reaches this hook. So every
# client push is refused; integration and main get their own message because
# "you may only write the scheduler's refs" and "you may not write at all" are
# different mistakes.
status=0
while read -r old new ref; do
  case "$ref" in
    refs/heads/integration|refs/heads/main)
      echo "rejected: $ref may only be written by the Merge Worker" >&2
      status=1
      ;;
    *)
      echo "rejected: client pushes are not accepted; use publish_task_branch" >&2
      status=1
      ;;
  esac
done
exit $status
"""


@dataclass(frozen=True, slots=True)
class CanonicalRepo:
    """A bare repository the scheduler owns."""

    path: Path

    @classmethod
    def from_settings(cls) -> CanonicalRepo:
        return cls(Path(settings.canonical_repo))

    @property
    def objects_dir(self) -> Path:
        return self.path / "objects"

    @property
    def integration_ref(self) -> str:
        return f"refs/heads/{INTEGRATION_BRANCH}"

    def ensure(self) -> None:
        """Create the bare repository, seed ``integration`` and install the hook."""
        with self._ensure_lock():
            if not (self.path / "HEAD").exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
                run_git("init", "--bare", "--initial-branch", INTEGRATION_BRANCH, str(self.path))
                # The Merge Worker and the push proxy write refs/heads/<branch>
                # here, and a long branch name can exceed Windows' MAX_PATH; the
                # same extended-length-path switch as worktrees (R10 §14).
                run_git("config", "core.longpaths", "true", cwd=self.path)
            self.install_hook()
            if self.ref_commit(self.integration_ref) is None:
                self._seed_integration()

    @contextmanager
    def _ensure_lock(self) -> Iterator[None]:
        """Serialize first-run repository creation across worker processes.

        Several formal workers can start at once. Without a lock they all see
        the empty bare repository and race through ``git init``/seed commit,
        leaving ``HEAD.lock`` or the temporary seed repository behind.
        """
        lock_path = self.path.with_name(self.path.name + ".ensure.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+b")
        try:
            _lock_file(handle)
            yield
        finally:
            _unlock_file(handle)
            handle.close()

    def _seed_integration(self) -> None:
        """Create the root commit ``integration`` starts from.

        An empty repository has no HEAD for a first claim to freeze, and R10 §11
        requires base_commit to be the integration HEAD at claim time.

        Seeding deliberately does not ``git push``: the pre-receive hook installed
        by :meth:`ensure` refuses client writes to ``integration``, and the
        scheduler is supposed to be the exception that writes it from inside.
        This is that same path -- import objects, then ``update-ref``.
        """
        # Keep the unique suffix, but use a deliberately short prefix: this
        # directory is nested below the canonical path and Windows Git object
        # names can otherwise exceed MAX_PATH in the deep-root regression.
        work = Path(tempfile.mkdtemp(prefix=".s-", dir=self.path.parent))
        try:
            run_git("init", "--initial-branch", INTEGRATION_BRANCH, str(work))
            readme = work / "README.md"
            readme.write_text(
                "# canonical integration seed\n\n"
                "Created by the scheduler so the first claim has an integration HEAD "
                "to freeze (R10 §11).\n",
                encoding="utf-8",
            )
            run_git("add", "README.md", cwd=work)
            run_git(*_IDENTITY, "commit", "-m", "chore: seed integration", cwd=work)
            commit = run_git("rev-parse", "HEAD", cwd=work).stdout.strip()
            self.import_objects(work, commit)
            self.update_ref(self.integration_ref, commit)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def install_hook(self) -> None:
        hooks = self.path / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "pre-receive"
        hook.write_text(pre_receive_hook(), encoding="utf-8", newline="\n")
        hook.chmod(0o755)

    def ref_commit(self, ref: str) -> str | None:
        proc = run_git("rev-parse", "--verify", "--quiet", ref, cwd=self.path, check=False)
        commit = proc.stdout.strip()
        return commit or None

    def has_object(self, commit: str) -> bool:
        proc = run_git("cat-file", "-e", f"{commit}^{{commit}}", cwd=self.path, check=False)
        return proc.returncode == 0

    def update_ref(self, ref: str, commit: str) -> None:
        """Point ``ref`` at ``commit``. Scheduler-internal, so no hook runs."""
        run_git("update-ref", ref, commit, cwd=self.path)

    def import_objects(self, agent_gitdir: Path, commit: str) -> None:
        """Fetch objects from an agent gitdir into canonical (ADR-059 step 2)."""
        run_git("fetch", "--no-tags", str(agent_gitdir), commit, cwd=self.path)

    def is_integration_write_attempt(self, ref: str) -> bool:
        return is_integration_ref(ref)
