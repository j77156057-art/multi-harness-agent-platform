"""The Verify Worker: baseline differential and the append-only receipt (R10 §16).

Two properties this module exists to guarantee:

* a task cannot reach Review without a passing receipt for the *exact* commit
  that would be merged, so "测试未通过不能进入 Review" is structural rather than
  a convention between components;
* ``failure_reason`` distinguishes ``code_failed`` from ``env_failed`` and
  ``timeout``. R10 §28 calls mixing them out as an invariant violation because an
  environment failure recorded as a code failure sends the work back through
  rework that cannot possibly fix it.

The worker never transitions a task on its own authority: the receipt is written
first and the status change is derived from it.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.claim.transitions import apply_transition
from app.config import settings
from app.db.connection import Database
from app.events.store import EventStore
from app.git.repo import CanonicalRepo, run_git
from app.spec import EventKind, TaskStatus
from app.verify.overlay import Overlay, OverlayError, OverlayManager
from app.verify.profile import VerifyPolicy, VerifyProfile

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_ABORTED = "aborted"

FAILURE_CODE = "code_failed"
FAILURE_ENV = "env_failed"
FAILURE_TIMEOUT = "timeout"

#: R10 §16 step 5 vs the checklist's MVP simplification. ``required`` enforces
#: the head half now and records the baseline exit code; the "baseline must also
#: fail" half is phase 2 and must not be silently treated as satisfied.
MODE_REQUIRED = "required"
MODE_NOT_APPLICABLE = "not_applicable"


class VerifyError(RuntimeError):
    """The job could not be started (configuration, not code)."""


@dataclass(frozen=True, slots=True)
class RunResult:
    exit_code: int | None
    output: str
    timed_out: bool = False
    env_error: str | None = None


@dataclass(frozen=True, slots=True)
class VerifyOutcome:
    job_id: str
    status: str
    failure_reason: str | None
    receipt_id: str | None
    receipt_hash: str | None
    baseline_exit_code: int | None
    head_exit_code: int | None
    new_test_files: tuple[str, ...]
    replayed: bool
    next_status: TaskStatus

    def as_json(self) -> dict[str, object]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "receipt_id": self.receipt_id,
            "receipt_hash": self.receipt_hash,
            "baseline_exit_code": self.baseline_exit_code,
            "head_exit_code": self.head_exit_code,
            "new_test_files": list(self.new_test_files),
            "replayed": self.replayed,
            "next_status": self.next_status.value,
        }


class CommandRunner(Protocol):
    def __call__(self, profile: VerifyProfile, cwd: Path) -> RunResult: ...


def default_runner(profile: VerifyProfile, cwd: Path) -> RunResult:
    """Run the profile's argv in a stripped environment.

    R10 §16 forbids injecting git and scheduler credentials, mounting state.db,
    and any secret not declared in both the task and the profile. The environment
    is built from nothing rather than filtered, so a variable added elsewhere
    cannot leak in by default.
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
        "TEMP": str(cwd),
        "TMP": str(cwd),
        "LANG": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    try:
        proc = subprocess.run(
            list(profile.command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=profile.timeout_seconds,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(partial, bytes):  # pragma: no cover - encoding dependent
            partial = partial.decode("utf-8", "replace")
        return RunResult(exit_code=None, output=partial, timed_out=True)
    except (FileNotFoundError, PermissionError, OSError) as exc:
        return RunResult(exit_code=None, output="", env_error=str(exc))
    return RunResult(exit_code=proc.returncode, output=(proc.stdout or "") + (proc.stderr or ""))


@dataclass(frozen=True, slots=True)
class _Job:
    """Everything one verification run needs, so no method takes 18 parameters."""

    job_id: str
    task_id: str
    attempt_id: str
    commit: str
    base_commit: str | None
    verify_profile: str
    fencing_epoch: int
    differential_mode: str
    profile: VerifyProfile


class VerifyWorker:
    def __init__(
        self,
        database: Database,
        repo: CanonicalRepo,
        policy: VerifyPolicy,
        *,
        overlay_root: Path | None = None,
        evidence_root: Path | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.database = database
        self.repo = repo
        self.policy = policy
        self.overlays = OverlayManager(repo, overlay_root)
        self.evidence_root = (
            Path(evidence_root)
            if evidence_root is not None
            else Path(settings.data_dir) / "evidence"
        )
        self.runner: CommandRunner = runner or default_runner
        self.store = EventStore(database)

    # ------------------------------------------------------------------ run

    def run(
        self,
        *,
        task_id: str,
        attempt_id: str,
        commit: str,
        base_commit: str | None,
        verify_profile: str,
        fencing_epoch: int,
        differential_mode: str = MODE_REQUIRED,
    ) -> VerifyOutcome:
        profile = self.policy.profile(verify_profile)  # raises VerifyPolicyError
        job = _Job(
            job_id=f"vj-{uuid.uuid4().hex}",
            task_id=task_id,
            attempt_id=attempt_id,
            commit=commit,
            base_commit=base_commit,
            verify_profile=verify_profile,
            fencing_epoch=fencing_epoch,
            differential_mode=differential_mode,
            profile=profile,
        )

        with self.database.transaction() as conn:
            existing = self._existing_receipt(conn, job)
            if existing is not None:
                return self._replay(conn, existing)
        self._open_job(job)

        outcome = self._execute(job)
        return outcome

    def _execute(self, job: _Job) -> VerifyOutcome:
        baseline_exit: int | None = None
        head_exit: int | None = None
        baseline_output = ""
        head_output = ""
        changed: list[tuple[str, str]] = []
        failure_reason: str | None = None
        head_overlay: Overlay | None = None

        try:
            if job.differential_mode == MODE_REQUIRED and job.base_commit:
                with self._overlay(job, commit=job.base_commit) as baseline:
                    run = self.runner(job.profile, baseline.path)
                    baseline_exit = run.exit_code
                    baseline_output = run.output

            head_overlay = self.overlays.create(commit=job.commit, job_id=job.job_id)
            run = self.runner(job.profile, head_overlay.path)
            head_exit = run.exit_code
            head_output = run.output
            changed = self.overlays.changed_files(head_overlay, job.base_commit)
            if run.timed_out:
                failure_reason = FAILURE_TIMEOUT
            elif run.env_error is not None:
                failure_reason = FAILURE_ENV
            elif head_exit != 0:
                failure_reason = FAILURE_CODE
        except OverlayError as exc:
            failure_reason = FAILURE_ENV
            head_output = str(exc)

        if failure_reason is None and head_overlay is not None:
            self.overlays.recycle(head_overlay)
            head_overlay = None

        status = {
            None: STATUS_PASSED,
            FAILURE_CODE: STATUS_FAILED,
            FAILURE_ENV: STATUS_FAILED,
            FAILURE_TIMEOUT: STATUS_TIMEOUT,
        }[failure_reason]
        new_test_files = tuple(
            path for kind, path in changed if kind == "A" and _looks_like_test(path)
        )
        return self._settle(
            job,
            status=status,
            failure_reason=failure_reason,
            baseline_exit_code=baseline_exit,
            head_exit_code=head_exit,
            new_test_files=new_test_files,
            head_output=head_output,
            baseline_output=baseline_output,
            changed=changed,
        )

    # ------------------------------------------------------------- internals

    def _open_job(self, job: _Job) -> None:
        with self.database.transaction() as conn:
            conn.execute(
                'INSERT INTO verify_jobs(job_id, task_id, attempt_id, "commit", base_commit,'
                " status, queue_reason, fencing_epoch, verify_profile, created_seq)"
                " VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL)",
                (
                    job.job_id,
                    job.task_id,
                    job.attempt_id,
                    job.commit,
                    job.base_commit,
                    STATUS_RUNNING,
                    job.fencing_epoch,
                    job.verify_profile,
                ),
            )

    def _overlay(self, job: _Job, *, commit: str) -> _OverlayContext:
        return _OverlayContext(self.overlays, commit=commit, job_id=job.job_id)

    @staticmethod
    def _existing_receipt(conn, job: _Job):
        return conn.execute(
            "SELECT * FROM verification_receipts WHERE task_id = ? AND attempt_id = ?"
            ' AND "commit" = ? AND verify_profile = ?',
            (job.task_id, job.attempt_id, job.commit, job.verify_profile),
        ).fetchone()

    def _replay(self, conn, row) -> VerifyOutcome:
        """R10 §16: a repeat for the same attempt returns the first receipt."""
        payload = json.loads(str(row["receipt_json"]))
        task = conn.execute(
            "SELECT status FROM tasks WHERE task_id = ?", (str(row["task_id"]),)
        ).fetchone()
        return VerifyOutcome(
            job_id=str(payload.get("job_id", "")),
            status=str(payload.get("status", STATUS_PASSED)),
            failure_reason=row["failure_reason"],
            receipt_id=str(row["receipt_id"]),
            receipt_hash=str(row["receipt_hash"]),
            baseline_exit_code=row["baseline_exit_code"],
            head_exit_code=row["head_exit_code"],
            new_test_files=tuple(str(item) for item in payload.get("new_test_files", ())),
            replayed=True,
            next_status=TaskStatus(str(task["status"])) if task else TaskStatus.VERIFYING,
        )

    def _settle(
        self,
        job: _Job,
        *,
        status: str,
        failure_reason: str | None,
        baseline_exit_code: int | None,
        head_exit_code: int | None,
        new_test_files: tuple[str, ...],
        head_output: str,
        baseline_output: str,
        changed: list[tuple[str, str]],
    ) -> VerifyOutcome:
        receipt = {
            "receipt_id": f"rc-{uuid.uuid4().hex}",
            "task_id": job.task_id,
            "attempt_id": job.attempt_id,
            "job_id": job.job_id,
            "commit": job.commit,
            "base_commit": job.base_commit,
            "baseline_exit_code": baseline_exit_code,
            "head_exit_code": head_exit_code,
            "new_test_files": list(new_test_files),
            "differential_mode": job.differential_mode,
            "verify_profile": job.verify_profile,
            "verify_env_hash": self.policy.fingerprint(),
            "toolchain": _toolchain(),
            "fencing_epoch": job.fencing_epoch,
            "failure_reason": failure_reason,
            "log_hash": _hash_text(head_output + baseline_output),
            "diff_hash": _hash_text("\n".join(f"{kind}\t{path}" for kind, path in changed)),
            "status": status,
            # Phase 2 (checklist MVP-06 simplification). R10 §21 blocks a merge
            # when diff_intersection is missing, so an empty registry here means
            # flaky passing is unavailable rather than quietly assumed.
            "flaky_registry_ids": [],
            "diff_intersection": None,
            "failed_test_case_result_ids": [],
            "input_decision_ids": [],
            "injected_secret_ids": [],
            "created_at": datetime.now(UTC).isoformat(),
        }
        receipt_hash = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
        )
        receipt["receipt_hash"] = receipt_hash

        with self.database.transaction() as conn:
            existing = self._existing_receipt(conn, job)
            if existing is not None:
                return self._replay(conn, existing)
            conn.execute(
                'INSERT INTO verification_receipts(receipt_id, task_id, attempt_id, "commit",'
                " base_commit, fencing_epoch, receipt_hash, differential_mode, verify_profile,"
                " created_seq, baseline_exit_code, head_exit_code, failure_reason, log_hash,"
                " receipt_json)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    receipt["receipt_id"],
                    job.task_id,
                    job.attempt_id,
                    job.commit,
                    job.base_commit,
                    job.fencing_epoch,
                    receipt_hash,
                    job.differential_mode,
                    job.verify_profile,
                    baseline_exit_code,
                    head_exit_code,
                    failure_reason,
                    receipt["log_hash"],
                    json.dumps(receipt, sort_keys=True),
                ),
            )
            next_status = self._transition(
                conn,
                job,
                status=status,
                failure_reason=failure_reason,
                receipt_id=str(receipt["receipt_id"]),
            )
            conn.execute(
                "UPDATE verify_jobs SET status = ?, failure_reason = ?, baseline_exit_code = ?,"
                " head_exit_code = ?, receipt_hash = ?, receipt_id = ?, worker_id = ?"
                " WHERE job_id = ?",
                (
                    status,
                    failure_reason,
                    baseline_exit_code,
                    head_exit_code,
                    receipt_hash,
                    receipt["receipt_id"],
                    "verify-worker-1",
                    job.job_id,
                ),
            )
            self._write_evidence(
                conn,
                job,
                receipt=receipt,
                head_output=head_output,
                baseline_output=baseline_output,
            )
        return VerifyOutcome(
            job_id=job.job_id,
            status=status,
            failure_reason=failure_reason,
            receipt_id=str(receipt["receipt_id"]),
            receipt_hash=receipt_hash,
            baseline_exit_code=baseline_exit_code,
            head_exit_code=head_exit_code,
            new_test_files=new_test_files,
            replayed=False,
            next_status=next_status,
        )

    def _transition(
        self, conn, job: _Job, *, status: str, failure_reason: str | None, receipt_id: str
    ) -> TaskStatus:
        """Derive the task's next status from the receipt, never the reverse."""
        task = conn.execute(
            "SELECT status, current_epoch FROM tasks WHERE task_id = ?", (job.task_id,)
        ).fetchone()
        if task is None:
            raise VerifyError(f"unknown task: {job.task_id}")
        current = TaskStatus(str(task["status"]))
        if current is not TaskStatus.VERIFYING:
            raise VerifyError(f"{job.task_id} is {current.value}; verify needs verifying")
        if status == STATUS_ABORTED:
            # No R10 §10 edge takes an aborted verify out of `verifying`; the
            # worker crash path is intent reconciliation (R10 §16), not a transition.
            return current

        if status == STATUS_PASSED:
            event = EventKind.VERIFY_PASSED
        elif failure_reason == FAILURE_TIMEOUT:
            event = EventKind.VERIFY_EXECUTION_TIMEOUT
        elif failure_reason == FAILURE_ENV:
            event = EventKind.VERIFY_ENV_FAILED
        else:
            event = EventKind.VERIFY_CODE_FAILED
        record = self.store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=job.task_id,
            actor_type="verify_worker",
            actor_id="verify-worker-1",
            payload={
                "attempt_id": job.attempt_id,
                "receipt_id": receipt_id,
                "failure_reason": failure_reason,
            },
            connection=conn,
        )
        return apply_transition(
            conn,
            task_id=job.task_id,
            current=current,
            event=event,
            expected_epoch=int(task["current_epoch"]),
            state_seq=record.seq,
        )

    # -------------------------------------------------------------- evidence

    def _write_evidence(
        self,
        conn,
        job: _Job,
        *,
        receipt: dict,
        head_output: str,
        baseline_output: str,
    ) -> None:
        """Repo-side read-only views; the database row stays authoritative."""
        root = self.evidence_root / job.task_id / job.attempt_id
        (root / "verification").mkdir(parents=True, exist_ok=True)
        files = {
            root / "completion.md": _completion_markdown(job, receipt),
            root / "test-results.md": _test_results_markdown(baseline_output, head_output, receipt),
            root / "verification" / "receipt.json": json.dumps(receipt, indent=2, sort_keys=True),
        }
        for path, text in files.items():
            path.write_text(text, encoding="utf-8")
            payload = text.encode("utf-8")
            conn.execute(
                "INSERT OR IGNORE INTO artifacts(artifact_id, task_id, attempt_id, kind, uri,"
                " hash, size_bytes, redaction_state)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 'clear')",
                (
                    f"art-{uuid.uuid4().hex}",
                    job.task_id,
                    job.attempt_id,
                    f"evidence_{path.name}",
                    str(path),
                    "sha256:" + hashlib.sha256(payload).hexdigest(),
                    len(payload),
                ),
            )


class _OverlayContext:
    """Create an overlay for the baseline run; always recycle it afterwards."""

    def __init__(self, manager: OverlayManager, *, commit: str, job_id: str) -> None:
        self.manager = manager
        self.commit = commit
        self.job_id = job_id
        self.overlay: Overlay | None = None

    def __enter__(self) -> Overlay:
        self.overlay = self.manager.create(commit=self.commit, job_id=f"{self.job_id}-base")
        return self.overlay

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.overlay is not None:
            self.manager.recycle(self.overlay)
        return None


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _toolchain() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "git": run_git("--version", check=False).stdout.strip(),
        "verified_at": datetime.now(UTC).isoformat(),
    }


def _looks_like_test(path: str) -> bool:
    lowered = path.lower()
    return "test" in lowered.rsplit("/", 1)[-1] or lowered.startswith("tests/")


def _completion_markdown(job: _Job, receipt: dict) -> str:
    return (
        f"# completion — {job.task_id}\n\n"
        f"- attempt: `{job.attempt_id}`\n"
        f"- commit: `{job.commit}`\n"
        f"- verify profile: `{job.verify_profile}`\n"
        f"- status: **{receipt['status']}**\n"
        f"- failure_reason: `{receipt['failure_reason']}`\n"
        f"- receipt: `{receipt['receipt_id']}`\n"
    )


def _test_results_markdown(baseline_output: str, head_output: str, receipt: dict) -> str:
    return (
        "# test-results\n\n"
        f"- baseline exit code: `{receipt['baseline_exit_code']}`\n"
        f"- head exit code: `{receipt['head_exit_code']}`\n"
        f"- new test files: {receipt['new_test_files']}\n\n"
        "## baseline output\n\n```text\n"
        f"{baseline_output.strip()}\n```\n\n"
        "## head output\n\n```text\n"
        f"{head_output.strip()}\n```\n"
    )
