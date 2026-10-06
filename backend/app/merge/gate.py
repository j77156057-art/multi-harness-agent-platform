"""Merge Gate cross-assertions (R10 §21).

The gate is the reason a commit cannot reach the integration branch by accident.
R10 §21 lists what the Merge Queue must verify; the three the checklist names as
MVP-07's acceptance criteria are here, together with the checks they depend on
(a passing receipt, an approving review for the same commit, a green integration
health). Every failure is returned rather than raised so a caller can report all
of them at once instead of fixing them one restart at a time.

The commit is a required argument throughout: a receipt or a review for a
different commit is not evidence about this one, and "the latest" is not a
substitute (R10 §16).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.db.connection import Database
from app.git.repo import CanonicalRepo
from app.spec import TaskStatus
from app.verify.receipts import passing_receipt_for

INTEGRATION_HEALTH_GREEN = "green"

#: R10 §17: anything a secret scan ever blocked on this task stops the merge.
SCAN_BLOCKING_CODE = "merge.secret_scan_blocking"


@dataclass(frozen=True, slots=True)
class GateResult:
    ok: bool
    failures: tuple[str, ...]
    code: str | None = None

    def raise_for_failures(self) -> None:
        if not self.ok:
            raise MergeGateRejected(self.failures, code=self.code)


class MergeGateRejected(RuntimeError):
    """The merge was refused before anything was written."""

    code = "merge.gate_rejected"

    def __init__(self, failures: tuple[str, ...], *, code: str | None = None) -> None:
        super().__init__("merge gate refused: " + "; ".join(failures))
        self.failures = failures
        self.code = code or type(self).code


def evaluate_merge_gate(
    database: Database,
    repo: CanonicalRepo,
    *,
    task_id: str,
    attempt_id: str,
    commit: str,
    branch: str,
) -> GateResult:
    """Answer whether this exact commit may be merged, and if not, why not."""
    failures: list[str] = []
    tasks = database.read("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
    if not tasks:
        return GateResult(False, (f"unknown task {task_id}",))
    task = dict(tasks[0])

    if str(task["status"]) != TaskStatus.APPROVED.value:
        failures.append(f"task is {task['status']}, not approved")
    if task["current_attempt_id"] != attempt_id:
        failures.append("attempt is not the task's current attempt")

    # --- the three cross-assertions the checklist names ---------------------
    receipt = passing_receipt_for(database, task_id=task_id, commit=commit)
    if receipt is None:
        failures.append(f"no passing verification receipt for commit {commit}")
    else:
        if str(receipt["commit"]) != commit:
            failures.append("receipt.commit does not match the commit to merge")
        if receipt["fencing_epoch"] != task["current_epoch"]:
            failures.append(
                f"receipt.fencing_epoch {receipt['fencing_epoch']} != task.current_epoch "
                f"{task['current_epoch']}"
            )
        if receipt["base_commit"] != task["base_commit"]:
            failures.append("receipt.base_commit != task.base_commit")

    review = _approving_review(database, task_id=task_id, attempt_id=attempt_id, commit=commit)
    if review is None:
        failures.append(f"no approving review for commit {commit}")
    elif receipt is not None and str(review["reviewed_commit"]) != str(receipt["commit"]):
        failures.append("review.reviewed_commit != receipt.commit")

    head = repo.ref_commit(f"refs/heads/{branch}")
    if head is None:
        failures.append(f"branch {branch} does not exist in canonical")
    elif head != commit:
        failures.append(f"branch head {head} != commit to merge {commit}")

    health = _integration_health(database, task_id)
    if health is None:
        failures.append("no integration_health row for this project")
    elif health != INTEGRATION_HEALTH_GREEN:
        failures.append(f"integration_health is {health}; the queue is stopped")

    # R10 §17: a secret found at any stage must stop the merge. The event log
    # is the evidence; fail closed on it rather than trusting the current
    # payload to be clean.
    code = None
    scan_blocks = _blocking_scans(database, task_id=task_id)
    if scan_blocks:
        code = SCAN_BLOCKING_CODE
        for scan in scan_blocks:
            failures.append(f"secret scan {scan['scan_id']} blocked content ({scan['rule_name']})")

    return GateResult(not failures, tuple(failures), code)


def _blocking_scans(database: Database, *, task_id: str) -> list[dict[str, str]]:
    """``secret_scan_blocked`` events recorded against this task.

    Only the scan id and rule name are surfaced -- never the location or the
    value hash, and never anything of the match itself.
    """
    rows = database.read(
        "SELECT payload_json FROM task_events"
        " WHERE event_name = 'secret_scan_blocked' AND aggregate_id = ?"
        " ORDER BY seq",
        (task_id,),
    )
    scans: list[dict[str, str]] = []
    for row in rows:
        payload = json.loads(str(row["payload_json"]))
        findings = payload.get("findings") or [{}]
        scans.append(
            {
                "scan_id": str(payload.get("scan_id", "scan-unknown")),
                "rule_name": str(findings[0].get("rule_name", "unknown")),
            }
        )
    return scans


def _approving_review(
    database: Database, *, task_id: str, attempt_id: str, commit: str
) -> dict | None:
    rows = database.read(
        "SELECT * FROM reviews WHERE task_id = ? AND attempt_id = ? AND reviewed_commit = ?"
        " AND verdict = 'approve' ORDER BY rowid",
        (task_id, attempt_id, commit),
    )
    return dict(rows[0]) if rows else None


def _integration_health(database: Database, task_id: str) -> str | None:
    rows = database.read(
        "SELECT ih.status FROM tasks t"
        " JOIN groups g ON g.group_id = t.group_id"
        " LEFT JOIN integration_health ih ON ih.project_id = g.project_id"
        " WHERE t.task_id = ?",
        (task_id,),
    )
    if not rows:
        return None
    return None if rows[0]["status"] is None else str(rows[0]["status"])
