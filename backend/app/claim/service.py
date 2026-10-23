"""Atomic claim with two-layer idempotency (R10 §11, MVP-04).

Layer responsibilities, straight from R10 §11 as amended by R10-fix-09/10:

* request level -- ``claim_requests`` is one row per request and has no
  ``task_id`` column; it carries the payload hash and the request-level
  first result.
* item level -- ``task_claims`` carries ``unique(claim_request_id, task_id)``
  and one row per task item, including items that never got an attempt
  ("失败项同样留行并带释放原因").

Replay therefore reads items with ``SELECT * FROM task_claims WHERE
claim_request_id = ?`` and joins ``task_attempts`` for attempt details. It
never decodes per-item results out of ``claim_requests.first_result_json`` --
that would be a second source of truth for the same fact.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.allocation.service import authorization_row, consume_authorization
from app.budget.service import (
    raise_incidents,
    record_reservation,
    reservation_for_claim,
    reserve_blocked,
    task_budget,
)
from app.claim.transitions import ConcurrentStateChange, apply_transition
from app.config import settings
from app.db.connection import Database
from app.events.store import EventStore
from app.git.repo import CanonicalRepo
from app.lease.service import (
    LeaseGrant,
    grant_lease,
    parse_iso,
    reissue_lease_token,
    release_attempt_resources,
    utc_now,
)
from app.spec import OWNER_STATES, EventKind, TaskStatus

OUTCOME_CLAIMED = "claimed"
OUTCOME_QUEUED = "queued"
OUTCOME_REJECTED = "rejected"

QUEUE_REASON_RESOURCE = "resource"

#: ``task_claims.attempt_id`` and ``task_claims.owner`` are NOT NULL in
#: ``001_initial.sql``, but R10 §11 requires a row for items that never reached
#: an attempt, and R10 §11 also forbids writing owner/attempt for a deferred
#: item.
#:
#: R10-fix-11 已裁定：``task_claims.attempt_id`` 与 ``owner`` 改为**可空**
#: （migration 002）。R10 §11 要求「延迟资源时不写 owner/attempt/Lease」，
#: 同时要求「每个任务项（含失败项）都必须留一行 task_claims」—— 两者只有在
#: 列可空时才能同时满足，不再需要哨兵值。
#:
#: 留行时用 ``None``，让「未领取」与「已领取」在数据层即可区分。
NO_ATTEMPT = None


class RejectCode(StrEnum):
    """Rejection codes. Names mirror docs/prep/MVP-08/error-codes.md."""

    TASK_NOT_FOUND = "claim.task_not_found"
    TASK_NOT_READY = "claim.task_not_ready"
    ALREADY_OWNED = "claim.already_owned"
    ATTEMPT_BUDGET_EXHAUSTED = "claim.attempt_budget_exhausted"
    TOKEN_BUDGET_EXHAUSTED = "claim.token_budget_exhausted"
    RESOURCE_UNAVAILABLE = "claim.resource_unavailable"
    CONCURRENT_CLAIM = "claim.concurrent_claim"
    BATCH_ABORTED = "claim.batch_aborted"
    IDEMPOTENCY_CONFLICT = "idempotency.conflict"
    HARNESS_INELIGIBLE = "claim.harness_ineligible"
    #: §39 硬规则 2 的强制口径：所在群开着分配门禁时，没有"已 accepted 的分配"就
    #: 不能开第一份 attempt。名字与 docs/prep/MVP-08/error-codes.md 对齐。
    NOT_AUTHORIZED = "claim.not_authorized"


class IdempotencyConflict(RuntimeError):
    """Same key, different payload. Rejects the whole request (R10 §11)."""

    code = RejectCode.IDEMPOTENCY_CONFLICT


@dataclass(frozen=True, slots=True)
class ClaimRequest:
    claim_request_id: str
    harness_id: str
    task_ids: tuple[str, ...]
    partial_allowed: bool = False
    payload_hash: str | None = None


@dataclass(frozen=True, slots=True)
class ClaimItem:
    task_id: str
    outcome: str
    attempt_id: str | None = None
    attempt_no: int | None = None
    owner: str | None = None
    fencing_epoch: int | None = None
    lease_token: str | None = None
    lease_until: str | None = None
    base_commit: str | None = None
    base_locked_seq: int | None = None
    queue_reason: str | None = None
    error_code: str | None = None
    replayed: bool = False
    #: F9 (adapter-hardening): the attempt's worktree path once `start_task`
    #: has provisioned it. A fresh claim predates the worktree (NULL); a
    #: replay after a restart hands the path back so the harness does not need
    #: a side channel to find its workspace.
    worktree_path: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "outcome": self.outcome,
            "attempt_id": self.attempt_id,
            "attempt_no": self.attempt_no,
            "owner": self.owner,
            "fencing_epoch": self.fencing_epoch,
            "lease_token": self.lease_token,
            "lease_until": self.lease_until,
            "base_commit": self.base_commit,
            "base_locked_seq": self.base_locked_seq,
            "queue_reason": self.queue_reason,
            "error_code": self.error_code,
            "replayed": self.replayed,
            "worktree_path": self.worktree_path,
        }


@dataclass(frozen=True, slots=True)
class ClaimResult:
    claim_request_id: str
    replayed: bool
    partial_allowed: bool
    outcome: str
    items: tuple[ClaimItem, ...]
    created_seq: int | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "claim_request_id": self.claim_request_id,
            "replayed": self.replayed,
            "partial_allowed": self.partial_allowed,
            "outcome": self.outcome,
            "created_seq": self.created_seq,
            "items": [item.as_json() for item in self.items],
        }


class _PayloadConflict(Exception):
    """Internal: abort the transaction, then record the conflict separately."""


class _AllOrNothingFailed(Exception):
    """Internal: abort the request so the caller's transaction rolls back."""

    def __init__(self, result: ClaimResult) -> None:
        super().__init__("all-or-nothing claim failed")
        self.result = result


def canonical_payload_hash(
    *, harness_id: str, task_ids: tuple[str, ...], partial_allowed: bool
) -> str:
    """Hash the claim payload the way the rest of the repo hashes payloads."""
    payload = {
        "harness_id": harness_id,
        "partial_allowed": partial_allowed,
        "task_ids": list(task_ids),
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def allocation_gate_applies(task: sqlite3.Row | dict) -> bool:
    """这个任务所在的群是否开着 §39 硬规则 2 的门禁。

    按**群**开关而不是全局（群主 2026-10-17 选定）。全局强制的代价是把 57 条存量任务
    一次性变成不可领取，连带把已经跑通的合并链和 `test_real_adapters` 全打红 —— 那不是
    把规则做实，那是换掉领取机制。按群开关让规则在"已经进入分配治理的群"里是真的，
    同时不动其余群：默认空集合 = 一个群都不强制，纳入哪个群是显式配置，不是默认放宽。
    """
    required = settings.allocation_required_groups
    return bool(required) and str(task["group_id"]) in required


class ClaimService:
    """Claim a batch of tasks atomically, with per-item results."""

    def __init__(self, database: Database, repo: CanonicalRepo | None = None) -> None:
        self.database = database
        self.repo = repo
        self.store = EventStore(database)
        self._conflict_detail: tuple[str, str] | None = None

    @staticmethod
    def eligibility(
        conn: sqlite3.Connection, task: sqlite3.Row | dict, harness_id: str
    ) -> tuple[str | None, str | None]:
        snapshot = conn.execute(
            "SELECT content_json FROM plan_snapshots WHERE snapshot_id = ?",
            (task["plan_snapshot_id"],),
        ).fetchone()
        raw = json.loads(snapshot[0]) if snapshot else {}
        item = next((x for x in raw.get("tasks", []) if x.get("id") == task["task_id"]), {})
        targets = item.get("assigned_harness_ids", []) or json.loads(
            task["assigned_harness_ids_json"]
        )
        if targets and harness_id not in targets:
            return "wrong_target", None
        if (item.get("require_human_assign") or task["require_human_assign"]) and not targets:
            return "assignment_required", None
        row = conn.execute("SELECT * FROM harnesses WHERE harness_id = ?", (harness_id,)).fetchone()
        # Legacy auth-off drills remain explicitly legacy. Targeted work is
        # always checked, and enforce mode never trusts an unregistered probe.
        strict = settings.auth_mode == "enforce" or bool(targets) or bool(item.get("capabilities"))
        if row is None or not row["registration_version"]:
            return ("unregistered" if strict else None), None
        registration = conn.execute(
            "SELECT * FROM harness_registrations WHERE harness_id = ? AND registration_version = ?",
            (harness_id, row["registration_version"]),
        ).fetchone()
        if registration is None:
            return "registration_missing", None
        if strict:
            if row["status"] != "online":
                return "offline_or_degraded", None
            if not set(item.get("capabilities", [])).issubset(
                json.loads(registration["capabilities_json"])
            ):
                return "capability_missing", None
            probe = conn.execute(
                "SELECT * FROM capability_probes WHERE harness_id = ? ORDER BY rowid DESC LIMIT 1",
                (harness_id,),
            ).fetchone()
            if (
                probe
                and probe["registration_version"] == row["registration_version"]
                and probe["degraded"]
            ):
                return "probe_degraded", None
            running = conn.execute(
                "SELECT COUNT(*) FROM leases WHERE owner = ? AND kind = 'development' AND released_at IS NULL AND lease_until > ?",
                (harness_id, utc_now().isoformat()),
            ).fetchone()[0]
            if running >= registration["max_parallel_tasks"]:
                return "parallel_limit", None
        return None, str(row["registration_version"])

    def claim(self, request: ClaimRequest) -> ClaimResult:
        if not request.task_ids:
            raise ValueError("task_ids must not be empty")
        effective_hash = request.payload_hash or canonical_payload_hash(
            harness_id=request.harness_id,
            task_ids=request.task_ids,
            partial_allowed=request.partial_allowed,
        )
        if request.payload_hash is not None:
            recomputed = canonical_payload_hash(
                harness_id=request.harness_id,
                task_ids=request.task_ids,
                partial_allowed=request.partial_allowed,
            )
            if request.payload_hash != recomputed:
                raise IdempotencyConflict(
                    f"payload_hash mismatch for {request.claim_request_id}: "
                    f"declared {request.payload_hash}, computed {recomputed}"
                )

        try:
            with self.database.transaction() as conn:
                existing = conn.execute(
                    "SELECT * FROM claim_requests WHERE claim_request_id = ?",
                    (request.claim_request_id,),
                ).fetchone()
                if existing is not None:
                    stored_hash = str(existing["payload_hash"])
                    if stored_hash != effective_hash:
                        # Recorded *after* the rollback, in its own transaction:
                        # an observation event about a rejected request must
                        # outlive the rejected request's transaction.
                        self._conflict_detail = (stored_hash, effective_hash)
                        raise _PayloadConflict
                    return self._replay(conn, existing, request)
                return self._first_pass(conn, request, effective_hash)
        except _PayloadConflict:
            self._record_idempotency_conflict(request)
            raise IdempotencyConflict(
                f"claim_request_id {request.claim_request_id} already exists with a "
                "different payload; the whole request is rejected"
            ) from None
        except _AllOrNothingFailed as failure:
            return failure.result

    # ---------------------------------------------------------------- replay

    def _replay(
        self, conn: sqlite3.Connection, existing: sqlite3.Row, request: ClaimRequest
    ) -> ClaimResult:
        # Replay is the documented recovery path for a harness that lost its
        # lease token (verify-mvp-04 deferral): a live, unreleased lease gets a
        # freshly minted token, the old one stops working. See _read_items.
        items = self._read_items(conn, request.claim_request_id, reissue_tokens=True)
        return ClaimResult(
            claim_request_id=request.claim_request_id,
            replayed=True,
            partial_allowed=bool(existing["partial_allowed"]),
            outcome=_merge_outcome(items),
            items=items,
            created_seq=int(existing["created_seq"]) if existing["created_seq"] else None,
        )

    def _record_idempotency_conflict(self, request: ClaimRequest) -> None:
        stored_hash, presented_hash = self._conflict_detail or ("", "")
        self.store.append(
            event_name="idempotency_conflict",
            aggregate_type="claim_request",
            aggregate_id=request.claim_request_id,
            actor_type="harness",
            actor_id=request.harness_id,
            payload={
                "stored_payload_hash": stored_hash,
                "presented_payload_hash": presented_hash,
            },
        )

    def _read_items(
        self,
        conn: sqlite3.Connection,
        claim_request_id: str,
        *,
        reissue_tokens: bool = False,
    ) -> tuple[ClaimItem, ...]:
        rows = conn.execute(
            "SELECT tc.task_id, tc.attempt_id, tc.owner, tc.release_reason,"
            "       ta.attempt_no, ta.fencing_epoch, ta.base_commit, ta.base_locked_seq,"
            "       l.lease_id, l.lease_until,"
            "       (SELECT w.path FROM workspaces w"
            "         WHERE w.attempt_id = tc.attempt_id AND w.status = 'active'"
            "         ORDER BY w.rowid DESC LIMIT 1) AS worktree_path"
            "  FROM task_claims tc"
            "  LEFT JOIN task_attempts ta ON ta.attempt_id = tc.attempt_id"
            "  LEFT JOIN leases l ON l.task_id = tc.task_id"
            "       AND l.attempt_id = tc.attempt_id AND l.kind = 'development'"
            "       AND l.released_at IS NULL"
            " WHERE tc.claim_request_id = ?"
            " ORDER BY tc.rowid",
            (claim_request_id,),
        ).fetchall()
        items: list[ClaimItem] = []
        for row in rows:
            has_attempt = row["attempt_no"] is not None
            release_reason = row["release_reason"]
            if has_attempt and release_reason is None:
                outcome = OUTCOME_CLAIMED
                error_code = None
                queue_reason = None
            elif release_reason is not None and str(release_reason).startswith("queued:"):
                outcome = OUTCOME_QUEUED
                error_code = None
                queue_reason = str(release_reason).split(":", 1)[1]
            else:
                outcome = OUTCOME_REJECTED
                error_code = (
                    str(release_reason).split(":", 1)[1]
                    if release_reason and ":" in str(release_reason)
                    else RejectCode.TASK_NOT_READY.value
                )
                queue_reason = None
            lease_token = None
            if (
                reissue_tokens
                and outcome == OUTCOME_CLAIMED
                and row["lease_id"] is not None
                and parse_iso(str(row["lease_until"])) > utc_now()
            ):
                # Only the hash is stored, so a replay cannot hand the original
                # token back -- it mints a new one instead. An expired lease is
                # not reissued: the attempt is about to be swept to stale and
                # must go through reclaim, not keep its dead credential alive.
                lease_token = reissue_lease_token(conn, lease_id=str(row["lease_id"]))
            items.append(
                ClaimItem(
                    task_id=str(row["task_id"]),
                    outcome=outcome,
                    attempt_id=row["attempt_id"] if has_attempt else None,
                    attempt_no=int(row["attempt_no"]) if has_attempt else None,
                    owner=row["owner"] if has_attempt else None,
                    fencing_epoch=int(row["fencing_epoch"]) if has_attempt else None,
                    lease_token=lease_token,
                    lease_until=row["lease_until"] if has_attempt else None,
                    base_commit=row["base_commit"] if has_attempt else None,
                    base_locked_seq=int(row["base_locked_seq"])
                    if has_attempt and row["base_locked_seq"] is not None
                    else None,
                    queue_reason=queue_reason,
                    error_code=error_code,
                    replayed=True,
                    # F9 (adapter-hardening): a replayed claim hands the live
                    # worktree back; a recycled one reads as NULL, not a stale
                    # path the restarted harness would walk into.
                    worktree_path=(
                        str(row["worktree_path"])
                        if has_attempt and row["worktree_path"] is not None
                        else None
                    ),
                )
            )
        return tuple(items)

    # ------------------------------------------------------------ first pass

    def _first_pass(
        self, conn: sqlite3.Connection, request: ClaimRequest, effective_hash: str
    ) -> ClaimResult:
        conn.execute(
            "INSERT INTO claim_requests("
            "  claim_request_id, task_ids_json, payload_hash, partial_allowed,"
            "  first_result_json, created_seq, created_at"
            ") VALUES (?, ?, ?, ?, NULL, NULL, ?)",
            (
                request.claim_request_id,
                json.dumps(list(request.task_ids), ensure_ascii=False),
                effective_hash,
                1 if request.partial_allowed else 0,
                utc_now().isoformat(),
            ),
        )

        items: list[ClaimItem] = []
        rejected = False
        for task_id in request.task_ids:
            conn.execute("SAVEPOINT claim_item")
            try:
                item = self._claim_one(conn, request, task_id)
                if item.outcome == OUTCOME_REJECTED:
                    # R10 §11: every item leaves a task_claims row, including the
                    # ones that never got an attempt. Skipped when the task does
                    # not exist -- task_id is a foreign key there.
                    self._record_rejected(conn, request, task_id, item.error_code)
                conn.execute("RELEASE claim_item")
            except (ConcurrentStateChange, sqlite3.IntegrityError):
                conn.execute("ROLLBACK TO claim_item")
                conn.execute("RELEASE claim_item")
                item = ClaimItem(
                    task_id=task_id,
                    outcome=OUTCOME_REJECTED,
                    error_code=RejectCode.CONCURRENT_CLAIM.value,
                )
                self._record_rejected(conn, request, task_id, item.error_code)
            items.append(item)
            if item.outcome == OUTCOME_REJECTED:
                rejected = True

        result = ClaimResult(
            claim_request_id=request.claim_request_id,
            replayed=False,
            partial_allowed=request.partial_allowed,
            outcome=_merge_outcome(tuple(items)),
            items=tuple(items),
        )
        if rejected and not request.partial_allowed:
            aborted = ClaimResult(
                claim_request_id=result.claim_request_id,
                replayed=False,
                partial_allowed=False,
                outcome=OUTCOME_REJECTED,
                items=tuple(
                    item
                    if item.outcome == OUTCOME_REJECTED
                    else ClaimItem(
                        task_id=item.task_id,
                        outcome=OUTCOME_REJECTED,
                        error_code=RejectCode.BATCH_ABORTED.value,
                    )
                    for item in items
                ),
            )
            raise _AllOrNothingFailed(aborted)

        seq = self._last_seq(conn)
        conn.execute(
            "UPDATE claim_requests SET first_result_json = ?, created_seq = ?"
            " WHERE claim_request_id = ?",
            (
                json.dumps(result.as_json(), ensure_ascii=False, sort_keys=True),
                seq,
                request.claim_request_id,
            ),
        )
        return ClaimResult(
            claim_request_id=result.claim_request_id,
            replayed=False,
            partial_allowed=result.partial_allowed,
            outcome=result.outcome,
            items=result.items,
            created_seq=seq,
        )

    def _record_rejected(
        self,
        conn: sqlite3.Connection,
        request: ClaimRequest,
        task_id: str,
        error_code: str | None,
    ) -> None:
        """Leave the per-item row for an item that never reached an attempt."""
        exists = conn.execute("SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if exists is None:
            return
        now = utc_now().isoformat()
        conn.execute(
            "INSERT INTO task_claims("
            "  claim_id, claim_request_id, task_id, attempt_id, owner, claimed_at,"
            "  released_at, release_reason"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"tc-{uuid.uuid4().hex}",
                request.claim_request_id,
                task_id,
                NO_ATTEMPT,
                NO_ATTEMPT,
                now,
                now,
                f"rejected:{error_code or RejectCode.TASK_NOT_READY.value}",
            ),
        )

    def _claim_one(
        self, conn: sqlite3.Connection, request: ClaimRequest, task_id: str
    ) -> ClaimItem:
        task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if task is None:
            # No task row means no task_claims row either: task_id is a foreign
            # key there, so "every item leaves a row" cannot be honoured for a
            # task that does not exist.
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_REJECTED,
                error_code=RejectCode.TASK_NOT_FOUND.value,
            )

        status = TaskStatus(str(task["status"]))
        if status is TaskStatus.CHANGES_REQUESTED:
            # R10 §10 L449/L523: the rework loop. ``claim_for_revision`` creates
            # a fresh attempt (attempt_no+1), epoch and lease under the same
            # checks as a first claim -- without this branch a verify failure
            # was a permanent dead end (the drill's F1: re-claiming hit
            # ALREADY_OWNED because ``current_attempt_id`` stays set).
            previous = task["current_attempt_id"]
            return self._grant_claim(
                conn,
                request,
                task,
                task_id=task_id,
                event=EventKind.CLAIM_FOR_REVISION,
                guard_status=TaskStatus.CHANGES_REQUESTED,
                required_attempt_id=None if previous is None else str(previous),
                revised_from=None if previous is None else str(previous),
            )
        if task["current_attempt_id"] is not None or status in OWNER_STATES:
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_REJECTED,
                error_code=RejectCode.ALREADY_OWNED.value,
            )
        if status is not TaskStatus.READY:
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_REJECTED,
                error_code=RejectCode.TASK_NOT_READY.value,
            )
        return self._grant_claim(
            conn,
            request,
            task,
            task_id=task_id,
            event=EventKind.CLAIM,
            guard_status=TaskStatus.READY,
            required_attempt_id=None,
            revised_from=None,
        )

    def _grant_claim(
        self,
        conn: sqlite3.Connection,
        request: ClaimRequest,
        task: sqlite3.Row,
        *,
        task_id: str,
        event: EventKind,
        guard_status: TaskStatus,
        required_attempt_id: str | None,
        revised_from: str | None,
    ) -> ClaimItem:
        """The shared grant tail: checks, resources, event, takeover UPDATE.

        ``guard_status``/``required_attempt_id`` are what distinguishes a first
        claim (``ready``, no current attempt) from a revision
        (``changes_requested``, the previous attempt still on the row): the
        conditional UPDATE matches on them so the race guard is exact for both.
        """
        refusal, registration_version = self.eligibility(conn, task, request.harness_id)
        if refusal:
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_REJECTED,
                error_code=f"{RejectCode.HARNESS_INELIGIBLE.value}:{refusal}",
            )
        # §39 硬规则 2（群主 2026-10-17 选定的"分群开关"口径）：所在群开着分配门禁时，
        # **第一份** attempt 必须由一份"已 accepted 的分配"授权。
        #
        # 修订（CLAIM_FOR_REVISION）刻意不走这道门：它继续的是同一份已经被授权过的作业。
        # 若连返工都要重新分配，一次 verify 失败就会把任务钉在 changes_requested 上等
        # 分配 —— 这正是本仓已经踩过的那类死端（见 reviews/rework 那轮的修复）。
        # 授权在下面**真正开出 attempt** 时才标记用掉，因此"排队等资源"不消耗授权。
        authorization = None
        if event is EventKind.CLAIM and allocation_gate_applies(task):
            authorization = authorization_row(
                conn,
                task_id=task_id,
                harness_id=request.harness_id,
                # 硬规则 2 列的"计划版本"：分配轮绑定的快照必须就是任务当前的快照，
                # 否则方案换版后旧授权仍能开工（硬规则 5 要求旧授权失效）。
                plan_snapshot_id=task["plan_snapshot_id"],
            )
            # 已用过的授权不再算授权：`claimed_at` 一旦写下，这份分配就"已经开过工"。
            # 任务若后来被放弃/重开回到 ready，再领就必须由管理员重新分配 —— 这正是
            # 门禁要守住的东西（不许自选）。不在这里拦住的话，下面 `consume_authorization`
            # 会因为 claim_id 不同而抛错，那就从"拒绝"变成"500"了。
            if authorization is None or authorization["claimed_at"] is not None:
                return ClaimItem(
                    task_id=task_id,
                    outcome=OUTCOME_REJECTED,
                    error_code=RejectCode.NOT_AUTHORIZED.value,
                )
        budget_left = int(task["attempt_no"]) < int(task["max_attempts"])
        overridden = task["budget_override_by"] is not None
        if not budget_left and not overridden:
            # R10 §10's attempt_budget_exhausted edge covers ready/queued as
            # well as stale/reclaiming. A failed claim still writes no state
            # itself; the lease sweep escalates the task on its next round.
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_REJECTED,
                error_code=RejectCode.ATTEMPT_BUDGET_EXHAUSTED.value,
            )

        resources = self._exclusive_resources(conn, task_id, task["plan_snapshot_id"])
        moment = utc_now()
        attempt_no = int(task["attempt_no"]) + 1
        attempt_id = f"{task_id}.a{attempt_no}"
        fencing_epoch = int(task["current_epoch"]) + 1
        lease_until = (moment + timedelta(seconds=settings.lease_seconds)).isoformat()

        # budget-guard: the dispatch-time reservation. R10 §11's "预占资源"
        # read as the task's declared token budget too; the check runs before
        # any claim state is written, so a denial leaves no claim event, no
        # resource lock and no hold behind (the incident is the only trace).
        budget_limit = task_budget(conn, task_id)
        budget_amount = 0
        if budget_limit is not None:
            budget_amount = reservation_for_claim(budget_limit)
            if reserve_blocked(conn, task_id=task_id, limit=budget_limit, amount=budget_amount):
                raise_incidents(conn, task_id=task_id, now=moment)
                return ClaimItem(
                    task_id=task_id,
                    outcome=OUTCOME_REJECTED,
                    error_code=RejectCode.TOKEN_BUDGET_EXHAUSTED.value,
                )

        blocked = self._resource_blocker(
            conn, resources, moment, skip_attempt_id=required_attempt_id
        )
        if blocked is not None:
            if required_attempt_id is None:
                return self._defer(
                    conn,
                    request,
                    task,
                    task_id=task_id,
                    reason=QUEUE_REASON_RESOURCE,
                    moment=moment,
                )
            # A revision has no defer edge (queued is entered from ready only);
            # a blocked resource simply means this claim cannot fire. The task
            # stays changes_requested and the review-wait SLA sweep is the
            # safety net.
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_REJECTED,
                error_code=RejectCode.RESOURCE_UNAVAILABLE.value,
            )

        if required_attempt_id is not None:
            # The previous attempt's resource rows go back to the pool in the
            # same transaction that grants the revision.
            release_attempt_resources(conn, attempt_id=required_attempt_id, reason="revision")

        self._acquire_resources(
            conn,
            resources,
            attempt_id=attempt_id,
            fencing_epoch=fencing_epoch,
            lease_until=lease_until,
        )

        # R10 §11/§13: revisions retain the first claim's differential anchor.
        base_commit = task["base_commit"] or self._integration_head(conn, task_id)
        payload = {
            "claim_request_id": request.claim_request_id,
            "attempt_id": attempt_id,
            "attempt_no": attempt_no,
            "fencing_epoch": fencing_epoch,
            "registration_version": registration_version,
        }
        if revised_from is not None:
            payload["revised_from"] = revised_from
        event = self.store.append(
            event_name=event.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="harness",
            actor_id=request.harness_id,
            payload=payload,
            connection=conn,
        )
        # Step 5/6 of the R10 §11 order: freeze base_commit and increment the
        # epoch in the same conditional UPDATE that takes ownership.
        self._conditional_claim_update(
            conn,
            task_id=task_id,
            harness_id=request.harness_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            fencing_epoch=fencing_epoch,
            base_commit=base_commit,
            guard_status=guard_status,
            required_attempt_id=required_attempt_id,
        )
        # base_locked_seq is the seq of the event that locked the base, so it can
        # only be written once the claim event has a seq.
        cursor = conn.execute(
            "UPDATE tasks SET base_locked_seq = ?, state_changed_seq = ?"
            " WHERE task_id = ? AND current_epoch = ?",
            (event.seq, event.seq, task_id, fencing_epoch),
        )
        if cursor.rowcount != 1:
            raise ConcurrentStateChange(f"could not stamp base_locked_seq for {task_id}")
        conn.execute(
            "UPDATE tasks SET registration_version = ? WHERE task_id = ?",
            (registration_version, task_id),
        )

        conn.execute(
            "INSERT INTO task_attempts("
            "  attempt_id, task_id, attempt_no, owner, fencing_epoch,"
            "  base_commit, base_locked_seq, head_commit, status, created_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
            (
                attempt_id,
                task_id,
                attempt_no,
                request.harness_id,
                fencing_epoch,
                base_commit,
                event.seq,
                TaskStatus.CLAIMED.value,
                moment.isoformat(),
            ),
        )
        conn.execute(
            "UPDATE task_attempts SET registration_version = ? WHERE attempt_id = ?",
            (registration_version, attempt_id),
        )
        lease: LeaseGrant = grant_lease(
            conn,
            task_id=task_id,
            attempt_id=attempt_id,
            owner=request.harness_id,
            fencing_epoch=fencing_epoch,
            now=moment,
        )
        claim_id = f"tc-{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO task_claims("
            "  claim_id, claim_request_id, task_id, attempt_id, owner, claimed_at,"
            "  released_at, release_reason"
            ") VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                claim_id,
                request.claim_request_id,
                task_id,
                attempt_id,
                request.harness_id,
                moment.isoformat(),
            ),
        )
        if authorization is not None:
            # 同一 `claim_id` 落在两张表上，"撤销一份没人用的授权"与"撤掉一个正在跑的
            # Lease"因此可以靠 join 分开判断，不用猜（§39 撤销前置条件）。
            consume_authorization(
                conn,
                allocation_id=str(authorization["allocation_id"]),
                claim_id=claim_id,
            )
        if budget_limit is not None:
            record_reservation(
                conn,
                task_id=task_id,
                attempt_id=attempt_id,
                amount=budget_amount,
                now=moment,
            )
        return ClaimItem(
            task_id=task_id,
            outcome=OUTCOME_CLAIMED,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            owner=request.harness_id,
            fencing_epoch=fencing_epoch,
            lease_token=lease.token,
            lease_until=lease.lease_until,
            base_commit=base_commit,
            base_locked_seq=event.seq,
        )

    def _defer(
        self,
        conn: sqlite3.Connection,
        request: ClaimRequest,
        task: sqlite3.Row,
        *,
        task_id: str,
        reason: str,
        moment: datetime,
    ) -> ClaimItem:
        """Resource capacity unavailable: record the deferral, take nothing.

        R10 §11: no owner, no attempt, no lease, no development side effect.
        """
        event = self.store.append(
            event_name=EventKind.CLAIM_DEFERRED_RESOURCE.value,
            aggregate_type="task",
            aggregate_id=task_id,
            actor_type="harness",
            actor_id=request.harness_id,
            payload={"claim_request_id": request.claim_request_id, "queue_reason": reason},
            connection=conn,
        )
        current = TaskStatus(str(task["status"]))
        cursor = conn.execute(
            "UPDATE tasks SET status = ?, queue_reason = ?, waiting_since = ?"
            " WHERE task_id = ? AND status = ? AND current_attempt_id IS NULL",
            (TaskStatus.QUEUED.value, reason, moment.isoformat(), task_id, current.value),
        )
        if cursor.rowcount != 1:
            raise ConcurrentStateChange(f"could not queue {task_id}")
        conn.execute(
            "UPDATE tasks SET state_changed_seq = ? WHERE task_id = ?",
            (event.seq, task_id),
        )
        conn.execute(
            "INSERT INTO task_claims("
            "  claim_id, claim_request_id, task_id, attempt_id, owner, claimed_at,"
            "  released_at, release_reason"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"tc-{uuid.uuid4().hex}",
                request.claim_request_id,
                task_id,
                NO_ATTEMPT,
                NO_ATTEMPT,
                moment.isoformat(),
                moment.isoformat(),
                f"queued:{reason}",
            ),
        )
        return ClaimItem(
            task_id=task_id,
            outcome=OUTCOME_QUEUED,
            queue_reason=reason,
        )

    # ---------------------------------------------------------- deferred resume

    def resume_deferred(self, task_id: str, *, now: datetime | None = None) -> ClaimItem | None:
        """Complete a resource-deferred claim once capacity allows (R10 §10 ``resource_available``).

        Returns ``None`` while the task is not queued for resources, the deferral
        record is gone, the resource is still held, or the attempt budget is
        exhausted (from ``queued`` there is no legal budget event; the sweeper's
        aging edge owns that case).

        The requester is recovered from the ``claim_deferred_resource`` event:
        R10 §11 forbids owner/attempt on the deferred row, so the event is the
        durable record of who asked. Per the coordinator's O1 ruling
        (2026-10-07) the grant does not check whether that requester is still
        alive; an absent requester experiences it through the ordinary
        lease-expiry chain.
        """
        moment = now or utc_now()
        with self.database.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                return None
            if TaskStatus(str(task["status"])) is not TaskStatus.QUEUED:
                return None
            if task["queue_reason"] != QUEUE_REASON_RESOURCE:
                return None
            if task["current_attempt_id"] is not None:
                return None
            budget_left = int(task["attempt_no"]) < int(task["max_attempts"])
            if not budget_left and task["budget_override_by"] is None:
                return None

            deferred = conn.execute(
                "SELECT * FROM task_claims WHERE task_id = ? AND released_at IS NOT NULL"
                " AND release_reason = ? ORDER BY rowid DESC LIMIT 1",
                (task_id, f"queued:{QUEUE_REASON_RESOURCE}"),
            ).fetchone()
            if deferred is None:
                return None
            claim_request_id = str(deferred["claim_request_id"])

            event_row = conn.execute(
                "SELECT actor_id FROM task_events WHERE event_name = ? AND aggregate_id = ?"
                " ORDER BY seq DESC LIMIT 1",
                (EventKind.CLAIM_DEFERRED_RESOURCE.value, task_id),
            ).fetchone()
            requester = str(event_row["actor_id"]) if event_row is not None else "unknown"

            resources = self._exclusive_resources(conn, task_id, task["plan_snapshot_id"])
            if self._resource_blocker(conn, resources, moment) is not None:
                return None

            attempt_no = int(task["attempt_no"]) + 1
            attempt_id = f"{task_id}.a{attempt_no}"
            fencing_epoch = int(task["current_epoch"]) + 1
            lease_until = (moment + timedelta(seconds=settings.lease_seconds)).isoformat()
            # budget-guard: a deferred resume is a real dispatch too -- the
            # hold is re-checked now (budget may have moved since the defer)
            # and stays queued (returns None) when it no longer fits.
            budget_limit = task_budget(conn, task_id)
            budget_amount = 0
            if budget_limit is not None:
                budget_amount = reservation_for_claim(budget_limit)
                if reserve_blocked(conn, task_id=task_id, limit=budget_limit, amount=budget_amount):
                    raise_incidents(conn, task_id=task_id, now=moment)
                    return None
            self._acquire_resources(
                conn,
                resources,
                attempt_id=attempt_id,
                fencing_epoch=fencing_epoch,
                lease_until=lease_until,
            )
            base_commit = self._integration_head(conn, task_id)
            event = self.store.append(
                event_name=EventKind.RESOURCE_AVAILABLE.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="scheduler",
                actor_id="sweeper",
                payload={
                    "claim_request_id": claim_request_id,
                    "attempt_id": attempt_id,
                    "attempt_no": attempt_no,
                    "fencing_epoch": fencing_epoch,
                    "requester": requester,
                },
                connection=conn,
            )
            # The R8 guard of §11, restated for the queued edge: one conditional
            # UPDATE takes ownership, freezes the base and advances the epoch.
            cursor = conn.execute(
                "UPDATE tasks"
                "   SET status = ?, owner = ?, current_attempt_id = ?, current_epoch = ?,"
                "       attempt_no = ?, base_commit = ?, queue_reason = NULL,"
                "       waiting_since = NULL, base_locked_seq = ?, state_changed_seq = ?"
                " WHERE task_id = ? AND status = ? AND current_attempt_id IS NULL"
                "   AND (attempt_no < max_attempts OR budget_override_by IS NOT NULL)",
                (
                    TaskStatus.CLAIMED.value,
                    requester,
                    attempt_id,
                    fencing_epoch,
                    attempt_no,
                    base_commit,
                    event.seq,
                    event.seq,
                    task_id,
                    TaskStatus.QUEUED.value,
                ),
            )
            if cursor.rowcount != 1:
                raise ConcurrentStateChange(f"deferred resume lost the race for {task_id}")

            conn.execute(
                "INSERT INTO task_attempts("
                "  attempt_id, task_id, attempt_no, owner, fencing_epoch,"
                "  base_commit, base_locked_seq, head_commit, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    attempt_id,
                    task_id,
                    attempt_no,
                    requester,
                    fencing_epoch,
                    base_commit,
                    event.seq,
                    TaskStatus.CLAIMED.value,
                    moment.isoformat(),
                ),
            )
            lease: LeaseGrant = grant_lease(
                conn,
                task_id=task_id,
                attempt_id=attempt_id,
                owner=requester,
                fencing_epoch=fencing_epoch,
                now=moment,
            )
            # The deferred row becomes the live claim row again: replaying the
            # original claim_request_id now answers with the completed attempt
            # (and rotates its lease token, the 10b recovery path).
            cursor = conn.execute(
                "UPDATE task_claims SET attempt_id = ?, owner = ?, released_at = NULL,"
                " release_reason = NULL WHERE claim_id = ?",
                (attempt_id, requester, str(deferred["claim_id"])),
            )
            if cursor.rowcount != 1:
                raise ConcurrentStateChange(f"could not reopen the deferred claim for {task_id}")
            if budget_limit is not None:
                record_reservation(
                    conn,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    amount=budget_amount,
                    now=moment,
                )
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_CLAIMED,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                owner=requester,
                fencing_epoch=fencing_epoch,
                lease_token=lease.token,
                lease_until=lease.lease_until,
                base_commit=base_commit,
                base_locked_seq=event.seq,
            )

    def _conditional_claim_update(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        harness_id: str,
        attempt_id: str,
        attempt_no: int,
        fencing_epoch: int,
        base_commit: str | None,
        guard_status: TaskStatus,
        required_attempt_id: str | None,
    ) -> None:
        """R10 §11 R8 guard, verbatim, with a rowcount == 1 assertion.

        A first claim matches ``current_attempt_id IS NULL``; a revision
        matches the exact previous attempt instead (``required_attempt_id``),
        so two harnesses racing to rework the same task cannot both win.
        """
        attempt_guard = (
            "current_attempt_id IS NULL"
            if required_attempt_id is None
            else "current_attempt_id = ?"
        )
        parameters: list[object] = [
            TaskStatus.CLAIMED.value,
            harness_id,
            attempt_id,
            fencing_epoch,
            attempt_no,
            base_commit,
            task_id,
            guard_status.value,
        ]
        if required_attempt_id is not None:
            parameters.append(required_attempt_id)
        cursor = conn.execute(
            "UPDATE tasks"
            "   SET status = ?, owner = ?, current_attempt_id = ?, current_epoch = ?,"
            "       attempt_no = ?, base_commit = ?, queue_reason = NULL, waiting_since = NULL"
            f" WHERE task_id = ? AND status = ? AND {attempt_guard}"
            "   AND (attempt_no < max_attempts OR budget_override_by IS NOT NULL)",
            tuple(parameters),
        )
        if cursor.rowcount != 1:
            raise ConcurrentStateChange(
                f"claim lost the race for {task_id} (rowcount {cursor.rowcount})"
            )

    # -------------------------------------------------------------- recovery

    def reclaim(self, task_id: str, *, grace_elapsed: bool) -> TaskStatus:
        """Open the takeover window for a stale task (R10 §10 ``reclaim``).

        The grace window is the caller's decision: R10 §13 defines it as three
        missed heartbeats, which is a scheduling policy rather than a storage
        concern.
        """
        if not grace_elapsed:
            raise ValueError("reclaim requires the stale grace window to have elapsed")
        with self.database.transaction() as conn:
            task = conn.execute(
                "SELECT status, current_epoch FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            status = TaskStatus(str(task["status"]))
            event = self.store.append(
                event_name=EventKind.RECLAIM.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="scheduler",
                actor_id="stale-sweeper",
                payload={"previous_status": status.value},
                connection=conn,
            )
            return apply_transition(
                conn,
                task_id=task_id,
                current=status,
                event=EventKind.RECLAIM,
                expected_epoch=int(task["current_epoch"]),
                state_seq=event.seq,
            )

    def take_over(self, task_id: str, *, harness_id: str, fencing_epoch: int) -> ClaimItem:
        """A new harness adopts a reclaiming task (R10 §10 ``take_over``).

        Creates a new attempt, increments the epoch and grants a fresh lease.
        The frozen ``base_commit`` is reused -- R10 §13 forbids recomputing the
        merge base on retry. No ``task_claims`` row is written: that table exists
        for per-item claim-request idempotency, and a takeover is not part of a
        claim request.
        """
        with self.database.transaction() as conn:
            task = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"unknown task: {task_id}")
            current = TaskStatus(str(task["status"]))
            if fencing_epoch != int(task["current_epoch"]):
                raise ConcurrentStateChange(
                    f"take_over for {task_id} carries epoch {fencing_epoch}, "
                    f"current is {task['current_epoch']}"
                )
            attempt_no = int(task["attempt_no"]) + 1
            if attempt_no > int(task["max_attempts"]) and task["budget_override_by"] is None:
                raise ValueError(f"attempt budget exhausted for {task_id}")
            attempt_id = f"{task_id}.a{attempt_no}"
            new_epoch = int(task["current_epoch"]) + 1
            moment = utc_now()
            event = self.store.append(
                event_name=EventKind.TAKE_OVER.value,
                aggregate_type="task",
                aggregate_id=task_id,
                actor_type="harness",
                actor_id=harness_id,
                payload={"attempt_id": attempt_id, "fencing_epoch": new_epoch},
                connection=conn,
            )
            apply_transition(
                conn,
                task_id=task_id,
                current=current,
                event=EventKind.TAKE_OVER,
                expected_epoch=int(task["current_epoch"]),
                extra_sets={
                    "owner": harness_id,
                    "current_attempt_id": attempt_id,
                    "current_epoch": new_epoch,
                    "attempt_no": attempt_no,
                    "waiting_since": None,
                },
                state_seq=event.seq,
            )
            conn.execute(
                "INSERT INTO task_attempts("
                "  attempt_id, task_id, attempt_no, owner, fencing_epoch,"
                "  base_commit, base_locked_seq, head_commit, status, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    attempt_id,
                    task_id,
                    attempt_no,
                    harness_id,
                    new_epoch,
                    task["base_commit"],
                    task["base_locked_seq"],
                    TaskStatus.CLAIMED.value,
                    moment.isoformat(),
                ),
            )
            lease = grant_lease(
                conn,
                task_id=task_id,
                attempt_id=attempt_id,
                owner=harness_id,
                fencing_epoch=new_epoch,
                now=moment,
            )
            return ClaimItem(
                task_id=task_id,
                outcome=OUTCOME_CLAIMED,
                attempt_id=attempt_id,
                attempt_no=attempt_no,
                owner=harness_id,
                fencing_epoch=new_epoch,
                lease_token=lease.token,
                lease_until=lease.lease_until,
                base_commit=task["base_commit"],
                base_locked_seq=task["base_locked_seq"],
            )

    # -------------------------------------------------------------- resources

    def _exclusive_resources(
        self, conn: sqlite3.Connection, task_id: str, snapshot_id: str | None
    ) -> list[str]:
        if not snapshot_id:
            return []
        row = conn.execute(
            "SELECT content_json FROM plan_snapshots WHERE snapshot_id = ?", (snapshot_id,)
        ).fetchone()
        if row is None:
            return []
        try:
            content = json.loads(str(row["content_json"]))
        except json.JSONDecodeError:
            return []
        for spec in content.get("tasks", []) if isinstance(content, dict) else []:
            if not isinstance(spec, dict):
                continue
            if spec.get("id") == task_id or spec.get("task_id") == task_id:
                resources = spec.get("exclusive_resources") or []
                return [str(item) for item in resources]
        return []

    def _resource_blocker(
        self,
        conn: sqlite3.Connection,
        resources: list[str],
        moment: datetime,
        *,
        skip_attempt_id: str | None = None,
    ) -> str | None:
        cutoff = moment.isoformat()
        for resource_id in resources:
            if skip_attempt_id is None:
                row = conn.execute(
                    "SELECT attempt_id FROM resource_leases"
                    " WHERE resource_id = ? AND released_at IS NULL AND lease_until > ?",
                    (resource_id, cutoff),
                ).fetchone()
            else:
                # A revision must not be blocked by its own previous attempt's
                # still-unreleased hold (release_lease only frees the dev lease;
                # resource rows linger until their deadline). The grant below
                # releases those rows as part of taking over.
                row = conn.execute(
                    "SELECT attempt_id FROM resource_leases"
                    " WHERE resource_id = ? AND released_at IS NULL AND lease_until > ?"
                    "   AND attempt_id != ?",
                    (resource_id, cutoff, skip_attempt_id),
                ).fetchone()
            if row is not None:
                return resource_id
        return None

    def _acquire_resources(
        self,
        conn: sqlite3.Connection,
        resources: list[str],
        *,
        attempt_id: str,
        fencing_epoch: int,
        lease_until: str,
    ) -> None:
        for resource_id in resources:
            conn.execute(
                "INSERT INTO resource_leases("
                "  resource_lease_id, resource_id, attempt_id, lease_until, epoch,"
                "  released_at, release_reason"
                ") VALUES (?, ?, ?, ?, ?, NULL, NULL)",
                (
                    f"rl-{uuid.uuid4().hex}",
                    resource_id,
                    attempt_id,
                    lease_until,
                    fencing_epoch,
                ),
            )

    # --------------------------------------------------------------- helpers

    def _integration_head(self, conn: sqlite3.Connection, task_id: str) -> str | None:
        if self.repo is not None:
            # integration_health is a health projection, not Git HEAD. It can
            # be NULL before the first merge or lag a scheduler ref update.
            head = self.repo.ref_commit(self.repo.integration_ref)
            if head is None:
                raise RuntimeError("canonical integration HEAD unavailable; claim refused")
            return head
        row = conn.execute(
            "SELECT ih.integration_commit"
            "  FROM tasks t"
            "  JOIN groups g ON g.group_id = t.group_id"
            "  LEFT JOIN integration_health ih ON ih.project_id = g.project_id"
            " WHERE t.task_id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return row["integration_commit"]

    def _last_seq(self, conn: sqlite3.Connection) -> int:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) AS seq FROM task_events").fetchone()
        return int(row["seq"]) if row is not None else 0


def _merge_outcome(items: tuple[ClaimItem, ...]) -> str:
    outcomes = {item.outcome for item in items}
    if outcomes == {OUTCOME_CLAIMED}:
        return OUTCOME_CLAIMED
    if OUTCOME_REJECTED in outcomes:
        return OUTCOME_REJECTED
    if OUTCOME_QUEUED in outcomes:
        return OUTCOME_QUEUED if outcomes == {OUTCOME_QUEUED} else "partial"
    return OUTCOME_REJECTED
