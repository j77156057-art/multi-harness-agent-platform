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
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.db.connection import _lock_file, _unlock_file
from app.git.branch import is_integration_ref

INTEGRATION_BRANCH = "integration"

#: Windows 上 ``open("a+b")`` 在另一进程已持有/正在锁该文件时会先抛
#: PermissionError——和 ``app/db/connection.py::_lock_file`` 注释里写的是
#: 同一个坑，那里只在 ``msvcrt.locking`` 阶段重试；重试必须补在 open 这一层。
#: 所以这里同样在调用侧做有界重试（deadline + 轮询），超时后抛清晰异常。
_ENSURE_LOCK_OPEN_TIMEOUT_S = 5.0
_ENSURE_LOCK_OPEN_POLL_S = 0.1

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
    # ``core.quotepath=false``: by default git *quotes* non-ASCII paths in
    # status/diff output ("docs/\350\207\250...") and quotes paths with spaces.
    # The scope check then compared an escaped path against the raw authorized
    # one and refused every task whose path was not plain ASCII -- a member that
    # wrote exactly the right file was told it had written outside its scope.
    # Appending to any GIT_CONFIG_* the caller already set keeps both.
    config_count = 0
    try:
        config_count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    except ValueError:
        config_count = 0
    env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    env[f"GIT_CONFIG_KEY_{config_count}"] = "core.quotepath"
    env[f"GIT_CONFIG_VALUE_{config_count}"] = "false"
    if os.name == "nt":
        # 临时 init/clone 出来的仓库不继承任何 per-repo 配置；深根下对象路径
        # 会超 MAX_PATH，统一在环境层开扩展长度路径。
        env[f"GIT_CONFIG_KEY_{config_count + 1}"] = "core.longpaths"
        env[f"GIT_CONFIG_VALUE_{config_count + 1}"] = "true"
        env["GIT_CONFIG_COUNT"] = str(config_count + 2)
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
        # Windows 坑：另一进程持有该锁文件时，open("a+b") 本身就先抛
        # PermissionError，根本轮不到 ``msvcrt.locking`` 的重试阶段。原因与
        # ``app/db/connection.py::_lock_file`` 的注释一致——必须在调用侧重试。
        deadline = time.monotonic() + _ENSURE_LOCK_OPEN_TIMEOUT_S
        while True:
            try:
                handle = lock_path.open("a+b")
                break
            except PermissionError as exc:
                if time.monotonic() >= deadline:
                    raise GitError(
                        f"could not open the ensure lock {lock_path} within "
                        f"{_ENSURE_LOCK_OPEN_TIMEOUT_S}s: it appears to be "
                        f"held by another process ({exc})"
                    ) from exc
                time.sleep(_ENSURE_LOCK_OPEN_POLL_S)
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
            # fetch 会派生 upload-pack 子进程读这个临时库的对象；环境层配置
            # 到不了那条路，per-repo 配置才是 worktree 已验证的生效方式。
            run_git("config", "core.longpaths", "true", cwd=work)
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

    def is_initialized(self) -> bool:
        """Whether the bare repository actually exists on disk.

        The same predicate :meth:`ensure` uses to decide it must create the
        repository; a misconfigured ``AP_CANONICAL_REPO`` reads as "not there".
        """
        return (self.path / "HEAD").exists()

    def has_object(self, commit: str) -> bool:
        """Whether the commit object can actually be consulted.

        A git that cannot run against its own directory (a misconfigured
        canonical path makes ``subprocess`` fail before git starts) answers
        False, not an exception: "cannot see it" is exactly "not reachable"
        as far as every caller can act on it.
        """
        try:
            proc = run_git("cat-file", "-e", f"{commit}^{{commit}}", cwd=self.path, check=False)
        except OSError:
            return False
        return proc.returncode == 0

    def read_file(self, ref: str, path: str) -> str | None:
        """Read a blob from ``ref``'s tree, or return ``None`` when absent.

        The runtime policy lives in the repository it governs, so the runtime
        reads it from ``integration`` instead of assuming the platform checkout
        is the project under test.
        """
        proc = run_git("show", f"{ref}:{path}", cwd=self.path, check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout

    def update_ref(self, ref: str, commit: str) -> None:
        """Point ``ref`` at ``commit``. Scheduler-internal, so no hook runs."""
        run_git("update-ref", ref, commit, cwd=self.path)

    def import_objects(self, agent_gitdir: Path, commit: str) -> None:
        """Fetch objects from an agent gitdir into canonical (ADR-059 step 2)."""
        run_git("fetch", "--no-tags", str(agent_gitdir), commit, cwd=self.path)

    def is_integration_write_attempt(self, ref: str) -> bool:
        return is_integration_ref(ref)
