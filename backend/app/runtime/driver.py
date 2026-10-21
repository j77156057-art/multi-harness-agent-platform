"""The runtime driver: Verify and Merge actually run (F6, runtime-driver card).

The drill found the gap plainly (e2e-harness-drill F6): ``VerifyWorker`` and
``MergeWorker`` were complete classes that only tests and drill scripts ever
called. Nothing in the running platform drove a ``verifying`` task through the
receipt gate or an ``approved`` task through the merge gate -- the sweeper
family maintains the state machine, but ``sweeps.py`` records itself that the
``verify_jobs`` queue has no producer.

v1 is deliberately small: scan the two states, execute inside the spec's
concurrency bounds (R10 §16 caps concurrent verify runs; §21 gives integration
a single writer, so merges are serial), and make idempotence structural -- a
passing receipt skips a verify, and the merge gate refuses a repeat on its own.

Deliberately not here (recorded, not silently skipped):

* the full §16 verify job lifecycle (queue producer, worker lease/heartbeat,
  job-level crash requeue) -- the existing ``verify_lease`` / ``verify_queue``
  / ``merge_recovery`` sweeps are the v1 safety net;
* acceptance -> verify command compilation (R10 §8 acceptance is the proper
  source; v1 uses the policy profile named by the task snapshot) -- that is
  the F11 tightening card;
* review is not driven at all. Reviewing is an external harness's job through
  ``review.claim`` / ``review.submit``; the platform side is the queue, the
  SLA and the serial gate that already exist.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.db.connection import Database
from app.git.repo import CanonicalRepo
from app.merge.gate import MergeGateRejected
from app.merge.worker import HealthRecheck, MergeError, MergeWorker
from app.spec import TaskStatus
from app.verify.profile import DEFAULT_POLICY_PATH, VerifyPolicy, VerifyPolicyError
from app.verify.worker import CommandRunner, VerifyWorker

#: R10 §16 fixes the concurrent verify cap at 3; ``settings.concurrent_verify``
#: defaults to the same number (ADR-013) and operators may lower it.
CONCURRENT_VERIFY_CAP = 3

#: §21 gives integration exactly one writer. v1 also bounds it per round: one
#: successful merge per round is the backpressure knob until the real merge
#: queue exists; a blocked candidate does not consume the budget, so a stuck
#: head of the line cannot starve the rest.
MAX_MERGES_PER_ROUND = 1


class RuntimePolicyError(ValueError):
    """The runtime policy file is missing or unusable; execution is refused."""


@dataclass(frozen=True, slots=True)
class RuntimePolicy:
    verify: VerifyPolicy
    integration_profile: str | None
    source: Path | None
    #: Where the profiles came from when the repository carried its own
    #: ``verify-profiles.json`` (``integration:verify-profiles.json``). The
    #: command-root allowlist still comes from ``source``.
    repo_source: str | None = None

    def effective_integration_profile(self) -> str | None:
        """The profile the merge worker will actually run.

        MergeWorker's existing convention (merge/worker.py) is the first
        profile in the policy; an explicit ``integration_profile`` key
        overrides it. The resolved name is reported by ``--check`` rather
        than left implicit.
        """
        if self.integration_profile is not None:
            return self.integration_profile
        first = next(iter(self.verify.profiles), None)
        return first

    def merge_policy(self) -> VerifyPolicy | None:
        name = self.effective_integration_profile()
        if name is None:
            return None
        return VerifyPolicy(
            command_roots=self.verify.command_roots,
            profiles={name: self.verify.profiles[name]},
            version=self.verify.version,
        )


def _repo_policy_mapping(
    repo: CanonicalRepo, base: VerifyPolicy
) -> tuple[dict[str, object], object] | None:
    """Read ``verify-profiles.json`` from the repository's ``integration``.

    A repository that carries the file governs its own verify profiles; the
    platform file keeps the command-root allowlist, so a repo can name a
    command but not widen what may run (R10 §16). A present-but-unusable file
    is a refusal, never a silent fallback to the platform's profiles.
    """
    relative = "verify-profiles.json"
    text = repo.read_file(repo.integration_ref, relative)
    if text is None:
        return None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimePolicyError(
            f"repository policy {relative} (integration) is not valid JSON: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise RuntimePolicyError(f"repository policy {relative} must be a JSON object")
    profiles = raw.get("profiles")
    if not isinstance(profiles, Mapping) or not profiles:
        raise RuntimePolicyError(f"repository policy {relative} must declare at least one profile")
    merged = base.as_mapping()
    base_profiles = dict(merged["profiles"])  # type: ignore[arg-type]
    merged["profiles"] = {**base_profiles, **dict(profiles)}
    merged["version"] = str(raw.get("version", base.version))
    return merged, raw.get("integration_profile")


def load_runtime_policy(
    path: Path | str | None, repo: CanonicalRepo | None = None
) -> RuntimePolicy:
    """Load the policy the runtime obeys, refusing loudly when it cannot.

    ``AP_RUNTIME_POLICY_FILE`` wins; unset means the repo-side
    ``verify-profiles.json`` (the human-maintained allowlist). A configured
    path that does not exist is a refusal, not a fallback -- guessing a
    command is exactly what R10 §16 says must not happen.

    The platform file is the allowlist of command roots. When ``repo`` is
    given and its ``integration`` tree carries ``verify-profiles.json``, that
    file supplies the profiles and ``integration_profile``: the policy follows
    the repository under test instead of assuming every project is the Python
    backend this platform was first built for.
    """
    resolved = Path(path) if path is not None else DEFAULT_POLICY_PATH
    if not resolved.exists():
        raise RuntimePolicyError(
            f"runtime policy file not found: {resolved}; "
            "set AP_RUNTIME_POLICY_FILE to a human-maintained policy JSON"
        )
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimePolicyError(f"runtime policy is not valid JSON: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RuntimePolicyError("runtime policy must be a JSON object")
    try:
        policy = VerifyPolicy.from_mapping(raw)
    except VerifyPolicyError as exc:
        raise RuntimePolicyError(f"runtime policy is unusable: {exc}") from exc
    name = raw.get("integration_profile")
    integration = str(name).strip() if name else None
    repo_source: str | None = None
    if repo is not None:
        found = _repo_policy_mapping(repo, policy)
        if found is not None:
            mapping, repo_integration = found
            try:
                policy = VerifyPolicy.from_mapping(mapping)
            except VerifyPolicyError as exc:
                raise RuntimePolicyError(f"repository policy is unusable: {exc}") from exc
            repo_source = "integration:verify-profiles.json"
            integration = str(repo_integration).strip() if repo_integration else integration
    if integration is not None and integration not in policy.profiles:
        raise RuntimePolicyError(
            f"integration_profile {integration!r} is not a declared profile; "
            f"registered: {sorted(policy.profiles)}"
        )
    return RuntimePolicy(
        verify=policy,
        integration_profile=integration,
        source=resolved,
        repo_source=repo_source,
    )


@dataclass(frozen=True, slots=True)
class VerifyCandidate:
    task_id: str
    attempt_id: str
    commit: str
    base_commit: str | None
    verify_profile: str
    fencing_epoch: int

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "commit": self.commit,
            "base_commit": self.base_commit,
            "verify_profile": self.verify_profile,
            "fencing_epoch": self.fencing_epoch,
        }


@dataclass(frozen=True, slots=True)
class MergeCandidate:
    task_id: str
    attempt_id: str
    commit: str
    branch: str

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "commit": self.commit,
            "branch": self.branch,
        }


@dataclass(frozen=True, slots=True)
class Skipped:
    task_id: str
    reason: str

    def as_json(self) -> dict[str, object]:
        return {"task_id": self.task_id, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class ScanResult:
    verify_candidates: tuple[VerifyCandidate, ...] = ()
    verify_skipped: tuple[Skipped, ...] = ()
    merge_candidates: tuple[MergeCandidate, ...] = ()
    merge_skipped: tuple[Skipped, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "verify": [item.as_json() for item in self.verify_candidates],
            "verify_skipped": [item.as_json() for item in self.verify_skipped],
            "merge": [item.as_json() for item in self.merge_candidates],
            "merge_skipped": [item.as_json() for item in self.merge_skipped],
        }


@dataclass(frozen=True, slots=True)
class VerifyRun:
    task_id: str
    status: str
    detail: str | None = None
    next_status: str | None = None
    replayed: bool = False

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "detail": self.detail,
            "next_status": self.next_status,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class MergeRun:
    task_id: str
    status: str
    detail: str | None = None
    merge_commit: str | None = None
    next_status: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "detail": self.detail,
            "merge_commit": self.merge_commit,
            "next_status": self.next_status,
        }


@dataclass(frozen=True, slots=True)
class RoundReport:
    scan: ScanResult
    verify: tuple[VerifyRun, ...] = ()
    merge: tuple[MergeRun, ...] = ()

    @property
    def actions(self) -> int:
        """Attempted executions this round (skips are not actions)."""
        return len(self.verify) + len(self.merge)

    def as_json(self) -> dict[str, object]:
        return {
            "actions": self.actions,
            "verify": [item.as_json() for item in self.verify],
            "merge": [item.as_json() for item in self.merge],
            "skipped": {
                "verify": [item.as_json() for item in self.scan.verify_skipped],
                "merge": [item.as_json() for item in self.scan.merge_skipped],
            },
        }


class RuntimeDriver:
    """Scan the two runnable states and execute within the spec's bounds."""

    def __init__(
        self,
        database: Database,
        repo: CanonicalRepo,
        policy: RuntimePolicy,
        *,
        concurrent_verify: int | None = None,
        runner: CommandRunner | None = None,
        overlay_root: Path | None = None,
        evidence_root: Path | None = None,
    ) -> None:
        workers = settings.concurrent_verify if concurrent_verify is None else concurrent_verify
        self.concurrent_verify = max(1, min(int(workers), CONCURRENT_VERIFY_CAP))
        self.database = database
        self.repo = repo
        self.policy = policy
        self.runner = runner
        self.overlay_root = overlay_root
        self.evidence_root = evidence_root

    # ------------------------------------------------------------------ scan

    def scan(self) -> ScanResult:
        """Read-only: what this round would run, and why anything is skipped."""
        verify_candidates: list[VerifyCandidate] = []
        verify_skipped: list[Skipped] = []
        rows = self.database.read(
            "SELECT t.task_id, t.current_attempt_id, t.current_epoch, t.base_commit,"
            "       a.head_commit"
            "  FROM tasks t"
            "  LEFT JOIN task_attempts a ON a.attempt_id = t.current_attempt_id"
            " WHERE t.status = ? ORDER BY t.task_id",
            (TaskStatus.VERIFYING.value,),
        )
        for row in rows:
            task_id = str(row["task_id"])
            attempt_id = row["current_attempt_id"]
            if attempt_id is None:
                verify_skipped.append(Skipped(task_id, "no current attempt to verify"))
                continue
            commit = row["head_commit"]
            if commit is None:
                verify_skipped.append(
                    Skipped(task_id, "attempt has not published a commit (publish before verify)")
                )
                continue
            profile = self._snapshot_profile(task_id)
            if profile is None:
                verify_skipped.append(
                    Skipped(
                        task_id,
                        "plan snapshot declares no verify_profile for this task;"
                        " refusing to guess a command",
                    )
                )
                continue
            if profile not in self.policy.verify.profiles:
                verify_skipped.append(
                    Skipped(
                        task_id,
                        f"verify profile {profile!r} is not in the runtime policy;"
                        f" registered: {sorted(self.policy.verify.profiles)}",
                    )
                )
                continue
            if self._passed_receipt(
                task_id=task_id,
                attempt_id=str(attempt_id),
                commit=str(commit),
                verify_profile=profile,
            ):
                verify_skipped.append(
                    Skipped(task_id, "a passing receipt already exists for this attempt/commit")
                )
                continue
            base_commit = row["base_commit"]
            verify_candidates.append(
                VerifyCandidate(
                    task_id=task_id,
                    attempt_id=str(attempt_id),
                    commit=str(commit),
                    base_commit=None if base_commit is None else str(base_commit),
                    verify_profile=profile,
                    fencing_epoch=int(row["current_epoch"]),
                )
            )

        merge_candidates: list[MergeCandidate] = []
        merge_skipped: list[Skipped] = []
        rows = self.database.read(
            "SELECT t.task_id, t.current_attempt_id, a.head_commit"
            "  FROM tasks t"
            "  LEFT JOIN task_attempts a ON a.attempt_id = t.current_attempt_id"
            " WHERE t.status = ? ORDER BY t.task_id",
            (TaskStatus.APPROVED.value,),
        )
        for row in rows:
            task_id = str(row["task_id"])
            attempt_id = row["current_attempt_id"]
            if attempt_id is None:
                merge_skipped.append(Skipped(task_id, "no current attempt to merge"))
                continue
            commit = row["head_commit"]
            if commit is None:
                merge_skipped.append(Skipped(task_id, "attempt has no published commit"))
                continue
            branch = self._branch_of(str(attempt_id))
            if not branch:
                merge_skipped.append(
                    Skipped(task_id, "no workspace row records the attempt branch")
                )
                continue
            merge_candidates.append(
                MergeCandidate(
                    task_id=task_id,
                    attempt_id=str(attempt_id),
                    commit=str(commit),
                    branch=branch,
                )
            )

        return ScanResult(
            verify_candidates=tuple(verify_candidates),
            verify_skipped=tuple(verify_skipped),
            merge_candidates=tuple(merge_candidates),
            merge_skipped=tuple(merge_skipped),
        )

    # ------------------------------------------------------------- execution

    def recheck_integration_health(self, project_id: str, *, apply: bool = False) -> HealthRecheck:
        """Re-run the integration profile at the canonical tip for one project.

        The queue-stopping health row has no way back to green inside a merge
        round (see ``MergeWorker.recheck_integration_health``), so the runtime
        exposes it as an explicit operator action. ``apply=False`` checks only.
        """
        worker = MergeWorker(
            self.database,
            self.repo,
            policy=self.policy.merge_policy(),
            overlay_root=self.overlay_root,
            runner=self.runner,
        )
        return worker.recheck_integration_health(project_id, apply=apply)

    def run_once(self) -> RoundReport:
        scan = self.scan()
        verify_runs = self._run_verifies(scan.verify_candidates)
        merge_runs = self._run_merges(scan.merge_candidates)
        return RoundReport(scan=scan, verify=verify_runs, merge=merge_runs)

    def _run_verifies(self, candidates: tuple[VerifyCandidate, ...]) -> tuple[VerifyRun, ...]:
        # At most ``concurrent_verify`` runs per round (§16's cap, and the
        # card's ruling: a 4th candidate waits for the next round). ``map``
        # keeps input order in the report; each worker executes with its own
        # Database so no connection is shared across threads.
        selected = candidates[: self.concurrent_verify]
        if not selected:
            return ()
        with ThreadPoolExecutor(max_workers=len(selected)) as pool:
            return tuple(pool.map(self._verify_one, selected))

    def _verify_one(self, candidate: VerifyCandidate) -> VerifyRun:
        worker = VerifyWorker(
            Database(self.database.path),
            self.repo,
            self.policy.verify,
            overlay_root=self.overlay_root,
            evidence_root=self.evidence_root,
            runner=self.runner,
        )
        try:
            outcome = worker.run(
                task_id=candidate.task_id,
                attempt_id=candidate.attempt_id,
                commit=candidate.commit,
                base_commit=candidate.base_commit,
                verify_profile=candidate.verify_profile,
                fencing_epoch=candidate.fencing_epoch,
            )
        except Exception as exc:  # noqa: BLE001 - one bad run must not kill the round
            return VerifyRun(
                task_id=candidate.task_id,
                status="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
        return VerifyRun(
            task_id=candidate.task_id,
            status=outcome.status,
            detail=outcome.failure_reason,
            next_status=outcome.next_status.value,
            replayed=outcome.replayed,
        )

    def _run_merges(self, candidates: tuple[MergeCandidate, ...]) -> tuple[MergeRun, ...]:
        runs: list[MergeRun] = []
        merged = 0
        for candidate in candidates:
            if merged >= MAX_MERGES_PER_ROUND:
                break
            run = self._merge_one(candidate)
            runs.append(run)
            if run.status == "merged":
                merged += 1
        return tuple(runs)

    def _merge_one(self, candidate: MergeCandidate) -> MergeRun:
        worker = MergeWorker(
            self.database,
            self.repo,
            policy=self.policy.merge_policy(),
            overlay_root=self.overlay_root,
            runner=self.runner,
        )
        try:
            outcome = worker.merge(
                task_id=candidate.task_id,
                attempt_id=candidate.attempt_id,
                commit=candidate.commit,
                branch=candidate.branch,
            )
        except MergeGateRejected as exc:
            return MergeRun(
                task_id=candidate.task_id,
                status="blocked",
                detail="; ".join(exc.failures),
            )
        except MergeError as exc:
            return MergeRun(task_id=candidate.task_id, status="error", detail=str(exc))
        except Exception as exc:  # noqa: BLE001 - one bad merge must not kill the round
            return MergeRun(
                task_id=candidate.task_id,
                status="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
        return MergeRun(
            task_id=candidate.task_id,
            status=outcome.status,
            detail=outcome.detail,
            merge_commit=outcome.merge_commit,
            next_status=outcome.task_status.value,
        )

    # ------------------------------------------------------------- internals

    def _snapshot_profile(self, task_id: str) -> str | None:
        rows = self.database.read(
            "SELECT ps.content_json"
            "  FROM tasks t"
            "  JOIN plan_snapshots ps ON ps.snapshot_id = t.plan_snapshot_id"
            " WHERE t.task_id = ?",
            (task_id,),
        )
        if not rows:
            return None
        try:
            content = json.loads(str(rows[0]["content_json"]))
        except json.JSONDecodeError:
            return None
        if not isinstance(content, Mapping):
            return None
        for item in content.get("tasks", ()):
            if not isinstance(item, Mapping):
                continue
            if str(item.get("id", "")).strip() != task_id:
                continue
            profile = item.get("verify_profile")
            if profile is None:
                return None
            text = str(profile).strip()
            return text or None
        return None

    def _passed_receipt(
        self, *, task_id: str, attempt_id: str, commit: str, verify_profile: str
    ) -> bool:
        rows = self.database.read(
            "SELECT receipt_json FROM verification_receipts"
            ' WHERE task_id = ? AND attempt_id = ? AND "commit" = ? AND verify_profile = ?',
            (task_id, attempt_id, commit, verify_profile),
        )
        if not rows:
            return False
        try:
            receipt = json.loads(str(rows[0]["receipt_json"]))
        except json.JSONDecodeError:
            return False
        return isinstance(receipt, Mapping) and receipt.get("status") == "passed"

    def _branch_of(self, attempt_id: str) -> str:
        rows = self.database.read(
            "SELECT branch FROM workspaces WHERE attempt_id = ? ORDER BY rowid DESC LIMIT 1",
            (attempt_id,),
        )
        return str(rows[0]["branch"]) if rows else ""
