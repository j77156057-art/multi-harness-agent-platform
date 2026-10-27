"""Administrator-harness self-loop runner.

This is the administrator counterpart of :mod:`app.adapter.formal_worker`: it
does not decide anything itself. It watches the group conversation for a
``project-goal`` message from the owner, asks the administrator's own model
channel to turn that goal into task specs, dispatches those specs with the
administrator token, then reviews submitted attempts with the same model
channel and submits the model's verdict.

The worker deliberately uses the public HTTP routes for every state change.
The model call itself is local (``AdminService``), because it is the
administrator harness's own CLI, not a platform scheduler decision.

Run it next to the member workers::

    python -m app.admin.worker --url http://127.0.0.1:8848 \
        --token-file ../data/codex-window-admin.key \
        --admin-harness-id codex-window-admin \
        --group office-formal-2

The worker never invents a verdict, never bypasses ``review_policy``, and
never dispatches when the model's JSON is invalid.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.adapter.harnesses.cli import ProviderRoute
from app.admin.service import AdminHarnessError, AdminService
from app.config import settings
from app.db.connection import Database
from app.tasks.service import (
    ADD_MEMBER_JSON_REF,
    ADD_MEMBER_REF,
    DIRECT_REF,
    GOAL_CHANGE_REF,
)

GOAL_REF = "project-goal"
GOAL_ANSWER_REF = "project-goal-answer"
#: A human (or the office relaying one) asks the administrator to re-point a
#: task.  The office never dispatches: it *requests*, and the administrator
#: harness decides and executes the re-point with its own credential.
REASSIGN_REQUEST_REF = "reassign-request"
#: The optional target window: "reassign-to:<harness_id>".  Without it the
#: administrator picks the least loaded window that can actually take work.
REASSIGN_TARGET_REF = "reassign-to:"
#: The owner cancelled a task.  Cancelling is a **human** decision and it is final
#: for that task -- but the plan it came from still lists it, so the administrator
#: has to hear about it: otherwise it keeps working on a plan that no longer
#: holds.  The office sends this message right after a successful cancel.
CANCEL_REF = "task-cancelled"
#: The machine-readable part of that message: "task-cancelled-json:{...}".
CANCEL_JSON_REF = "task-cancelled-json:"
#: The task states a re-point may act on: the work is not deliverable from
#: there (failed) or not progressing (lease lost).
REASSIGNABLE_STATES = ("abandoned", "stale", "ready", "changes_requested")
#: A failed review must not re-run the expensive admin model every poll. The
#: platform's review lease is the authority; this is only a local backoff so a
#: competing/refused review does not hammer the CLI.
REVIEW_RETRY_SECONDS = 300.0


@dataclass
class WorkerState:
    """Small, explicit cursor. No task state is cached here."""

    initialized: bool = False
    last_message_seq: int = 0
    processed_goal_seqs: list[int] = field(default_factory=list)
    pending_goal: dict[str, Any] | None = None
    pending_dispatch: dict[str, Any] | None = None
    reviewed_attempts: list[str] = field(default_factory=list)
    #: Tasks whose review is blocked because the only reviewer that can judge
    #: them shares the owner's provider. That refusal is not retryable -- the
    #: worker would cool down and hit the same wall forever -- so the owner is
    #: told **once** per task instead of on every retry.
    same_provider_review_asked: list[str] = field(default_factory=list)
    #: How many times this administrator re-pointed each task. The dispatch is
    #: idempotent on its request id, so a second re-point of the same task needs
    #: a *new* request id -- otherwise it replays the first one and nothing
    #: happens.
    reassign_counts: dict[str, int] = field(default_factory=dict)
    #: The refinement that is ready to be circulated (the model already ran, so
    #: a retry does not pay for it twice).
    pending_refinement: dict[str, Any] | None = None
    #: The plan that is currently in circulation: the administrator refined it,
    #: the readers have not all answered yet.
    pending_circulation: dict[str, Any] | None = None
    #: The message sequence numbers of the re-point requests already handled.
    #: A request is a *message*, and messages are replayed on every poll: the
    #: same request must not re-point the task twice.
    handled_request_seqs: list[int] = field(default_factory=list)
    #: The last goal the administrator worked on.  Kept so that the owner's
    #: answer to a member's "the goal has to change" can be handled (the
    #: answer re-refines that goal instead of being dropped).
    last_goal: dict[str, Any] | None = None
    #: The member messages about the goal already handled (one message, one
    #: answer -- messages are replayed on every poll).
    handled_goal_change_seqs: list[int] = field(default_factory=list)
    #: How many circulation rounds this plan must go through (the office's
    #: opening wizard asks for a round count; 1 means "review once").
    circulation_rounds_target: int = 1
    #: How many rounds are already finished.  The plan is only finalised when
    #: this reaches the target -- §39 says "已完成轮数达到配置轮数" is what
    #: freezes the snapshot.
    circulation_rounds_done: int = 0
    #: The allocation round this administrator opened (round_id + plan id), and
    #: the tasks it has already decided.  §39 hard rule 2: a member may only
    #: claim a task it holds an *accepted* allocation for, so somebody has to
    #: open the round, freeze the candidates and decide -- that is this
    #: administrator's job, not the member's and not the platform's.
    pending_allocation: dict[str, Any] | None = None
    #: A goal whose refinement **failed on the model channel** (not on the
    #: content).  It stays here until the channel works again: the owner's
    #: request must not disappear because one CLI call died.
    goal_retry: dict[str, Any] | None = None

    @classmethod
    def load(cls, path: Path) -> WorkerState:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                initialized=bool(data.get("initialized")),
                last_message_seq=int(data.get("last_message_seq") or 0),
                processed_goal_seqs=[int(x) for x in data.get("processed_goal_seqs") or []],
                pending_goal=data.get("pending_goal"),
                pending_dispatch=data.get("pending_dispatch"),
                reviewed_attempts=[str(x) for x in data.get("reviewed_attempts") or []],
                same_provider_review_asked=[
                    str(x) for x in data.get("same_provider_review_asked") or []
                ],
                reassign_counts={
                    str(k): int(v) for k, v in (data.get("reassign_counts") or {}).items()
                },
                handled_request_seqs=[int(x) for x in data.get("handled_request_seqs") or []],
                pending_circulation=data.get("pending_circulation"),
                pending_refinement=data.get("pending_refinement"),
                last_goal=data.get("last_goal"),
                handled_goal_change_seqs=[
                    int(x) for x in data.get("handled_goal_change_seqs") or []
                ],
                circulation_rounds_target=int(data.get("circulation_rounds_target") or 1),
                circulation_rounds_done=int(data.get("circulation_rounds_done") or 0),
                pending_allocation=data.get("pending_allocation"),
                goal_retry=data.get("goal_retry"),
            )
        except (OSError, ValueError, TypeError):
            # A corrupt cursor must not replay every historical goal. Start
            # from the latest message instead; an operator can post a fresh
            # goal without creating duplicate tasks.
            return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


class PlatformClient:
    """Thin authenticated client for the routes the worker is allowed to use."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )

    def close(self) -> None:
        self.client.close()

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        response = self.client.get(f"/api/v1{path}", params=params or None)
        return self._json(response)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(f"/api/v1{path}", json=body)
        return self._json(response)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code >= 400:
            detail = data.get("error") or data.get("detail") or response.text[:300]
            raise httpx.HTTPStatusError(
                f"{response.status_code} {detail}",
                request=response.request,
                response=response,
            )
        return data if isinstance(data, dict) else {"data": data}


def _cancel_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    """The cancel notice: which task, why, and what it was called.

    The office puts the human-readable sentence in the body and the machine
    part in a ref (``task-cancelled-json:{...}``), so the group conversation
    does not turn into raw JSON.
    """
    refs = message.get("refs") or []
    if CANCEL_REF not in refs:
        return None
    payload: dict[str, Any] = {}
    for ref in refs:
        text = str(ref or "").strip()
        if text.startswith(CANCEL_JSON_REF):
            text = text[len(CANCEL_JSON_REF) :]
        elif not text.startswith("{"):
            continue
        try:
            candidate = json.loads(text)
        except ValueError:
            continue
        if isinstance(candidate, dict) and str(candidate.get("task_id") or "").strip():
            payload = candidate
            break
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        return None
    return {
        "task_id": task_id,
        "title": str(payload.get("title") or "").strip(),
        "reason": str(payload.get("reason") or "").strip(),
    }


def _goal_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    refs = message.get("refs") or []
    if GOAL_REF not in refs:
        return None
    body = str(message.get("body") or "").strip()
    payload: Any = None
    # The office keeps the human-readable goal in the message body and puts
    # the machine-readable envelope in refs, so the group conversation does
    # not turn into raw JSON. Accept both forms for manual/API callers.
    for ref in refs:
        text = str(ref or "").strip()
        if text.startswith("project-goal-json:"):
            text = text[len("project-goal-json:") :]
        elif not text.startswith("{"):
            continue
        try:
            candidate = json.loads(text)
        except ValueError:
            continue
        if isinstance(candidate, dict) and str(candidate.get("goal") or "").strip():
            payload = candidate
            break
    if payload is None:
        if not body:
            return None
        try:
            payload = json.loads(body)
        except ValueError:
            payload = {"goal": body}
    if not isinstance(payload, dict):
        return None
    goal = str(payload.get("goal") or "").strip()
    if not goal:
        return None
    try:
        max_tasks = int(payload.get("max_tasks") or 3)
    except (TypeError, ValueError):
        max_tasks = 3
    return {
        "goal_id": str(payload.get("goal_id") or f"seq-{message.get('created_seq')}"),
        "goal": goal,
        "project_name": str(payload.get("project_name") or "").strip() or None,
        "members": [str(x) for x in (payload.get("members") or []) if str(x).strip()],
        "max_tasks": max(1, min(8, max_tasks)),
        "answers": [str(x) for x in (payload.get("answers") or []) if str(x).strip()],
        "admin": str(payload.get("admin") or "").strip() or None,
        "seq": int(message.get("created_seq") or 0),
    }


def _title_project(task: dict[str, Any]) -> str | None:
    title = str(task.get("title") or "")
    if " · " not in title:
        return None
    return title.split(" · ", 1)[0].strip() or None


def _sender_type(message: dict[str, Any]) -> str:
    """The API returns ``sender_type``; fake clients in tests use ``author_kind``.

    The write schema calls the field ``author_kind`` but ``list_messages``
    projects the raw ``task_messages`` row, whose column is ``sender_type``.
    Reading only ``author_kind`` made every real owner goal look non-human, so
    the worker advanced its cursor and silently ignored it.
    """
    return str(message.get("sender_type") or message.get("author_kind") or "")


def _assigned_ids(task: dict[str, Any]) -> list[str]:
    """Raw task rows expose ``assigned_harness_ids_json``; gateway rows expose a list.

    The self-review guard must read both, otherwise an administrator whose id is
    stored only in the JSON column looks unassigned and wastes a model call on a
    review the platform will reject.
    """
    raw = task.get("assigned_harness_ids")
    if raw is None:
        raw = task.get("assigned_harness_ids_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    return [str(item) for item in (raw or []) if str(item).strip()]


class AdminWorker:
    def __init__(
        self,
        *,
        client: PlatformClient,
        database: Database,
        group_id: str,
        admin_harness_id: str,
        state: WorkerState,
        state_path: Path,
        dry_run: bool = False,
        replay_latest: bool = False,
        review_rounds: int = 1,
    ) -> None:
        self.client = client
        self.group_id = group_id
        self.admin_harness_id = admin_harness_id
        self.database = database
        self.state = state
        self.state_path = state_path
        self.dry_run = dry_run
        self.replay_latest = replay_latest
        #: How many circulation rounds a plan must go through before it is
        #: finalised (§39: "已完成轮数达到配置轮数" is what freezes the snapshot).
        self.review_rounds = max(1, int(review_rounds))
        self.admin = AdminService(database, harness_id=admin_harness_id)
        # attempt key -> monotonic deadline; not persisted (a restart may retry
        # once, which is fine, but a hot loop must not invoke the model).
        self._review_cooldowns: dict[str, float] = {}
        #: The members the owner named for this project.  They are the readers
        #: of every circulation round (see ``_circulation_reviewers``).
        self._requested_members: list[str] = []

    def retry_failed_goal(self) -> None:
        """Try again a goal whose refinement died on the model channel.

        The owner asked for something; the administrator's CLI fell over.  That
        is not an answer, so the goal stays in the queue and is retried once the
        channel may be back.  A goal that keeps failing is not retried in a
        tight loop: the backoff grows with every attempt.
        """
        retry = self.state.goal_retry
        if not retry:
            return
        now = time.time()
        if now < float(retry.get("retry_after") or 0.0):
            return
        attempts = int(retry.get("attempts") or 0) + 1
        payload = {
            key: value
            for key, value in retry.items()
            if key not in ("retry_after", "last_error")
        }
        payload["attempts"] = attempts
        print(
            json.dumps({"goal_retry": payload.get("goal_id"), "attempt": attempts}, ensure_ascii=False),
            flush=True,
        )
        self.state.goal_retry = {
            **payload,
            "attempts": attempts,
            "retry_after": now + min(self.GOAL_RETRY_SECONDS * attempts, 300.0),
        }
        self.state.save(self.state_path)
        self.handle_goal(payload, force=True)

    # --------------------------------------------------------------- 额度池

    def pool_route(self, purpose: str) -> ProviderRoute | None:
        """Where the administrator's own model calls should go.

        The administrator is a harness like any other: when the human picked a
        model from the platform's **quota pool** for this window, the admin's
        CLI must talk to the pool.  Without this the administrator silently used
        whatever provider the machine happened to have configured -- and when
        that local provider was down, the whole collaboration stopped with a
        stream error instead of using the quota the platform already had.

        The platform decides (it knows what the human chose and what is alive);
        this worker only carries the answer out.  A platform that cannot answer
        -- or a channel that cannot be resolved -- is not a reason to stop: the
        administrator falls back to its own CLI.
        """
        try:
            plan = self.client.get(
                "/pool/plan",
                task_id=f"admin:{purpose}",
                harness_id=self.admin_harness_id,
                attempt_no=0,
            )
        except Exception as exc:  # pragma: no cover - network path
            print(
                json.dumps({"admin_pool_plan_failed": type(exc).__name__}, ensure_ascii=False),
                flush=True,
            )
            return None
        if not isinstance(plan, dict) or str(plan.get("mode") or "") != "pool":
            return None
        try:
            # The protocol is the one the administrator's **own CLI** speaks, not
            # the upstream's: the pool serves both shapes and translates.
            protocol = self.admin.pool_protocol()
            base_path = str(
                plan.get("anthropic_path")
                if protocol == "anthropic"
                else plan.get("base_path") or ""
            )
            model = str(plan.get("model") or "")
        except Exception as exc:  # pragma: no cover - unresolvable channel
            print(
                json.dumps({"admin_pool_route_failed": type(exc).__name__}, ensure_ascii=False),
                flush=True,
            )
            return None
        if not base_path or not model:
            return None
        route = ProviderRoute(
            base_url=f"{self.client.base_url}{base_path}",
            token=self.client.token,
            model=model,
            protocol=protocol,
        )
        print(
            json.dumps(
                {
                    "admin_model_route": "pool",
                    "model": model,
                    "upstream": str(plan.get("upstream") or ""),
                    "purpose": purpose,
                    "reason": str(plan.get("reason") or ""),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return route

    # --------------------------------------------------------------- messages

    def poll_messages(self) -> bool:
        if not self.state.initialized:
            # First run: anchor at the latest message. Historical goals are
            # deliberately NOT replayed by default: starting a worker must not
            # silently dispatch an old requirement. An operator who really
            # wants the newest historical goal can pass --replay-latest.
            latest = self.client.get(f"/groups/{self.group_id}/messages", since_seq=0, limit=500)
            all_messages = latest.get("messages") or []
            if all_messages:
                self.state.last_message_seq = int(all_messages[-1].get("created_seq") or 0)
            self.state.initialized = True
            self.state.save(self.state_path)
            if self.replay_latest:
                goals = [m for m in all_messages if _goal_payload(m)]
                if goals:
                    self.handle_goal(_goal_payload(goals[-1]), force=True)
            return bool(all_messages)
        page = self.client.get(
            f"/groups/{self.group_id}/messages",
            since_seq=self.state.last_message_seq,
            limit=100,
        )
        messages = page.get("messages") or []
        for message in messages:
            seq = int(message.get("created_seq") or 0)
            if seq <= self.state.last_message_seq:
                continue
            self.state.last_message_seq = seq
            self.state.initialized = True
            refs = message.get("refs") or []
            if _sender_type(message) != "human":
                # 不是人的话，**不是全都丢掉**（R10-fix-22）：成员窗口说「这个
                # 目标要改」，管理员必须听见。别的闲聊只记在运行日志里，不惊动
                # 任何人（逐条回应是要花钱的）。
                if GOAL_CHANGE_REF in refs:
                    self.handle_member_goal_change(message, seq=seq)
                else:
                    print(
                        json.dumps(
                            {"member_message": message.get("sender_id"), "seq": seq},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                continue
            if GOAL_REF in refs:
                payload = _goal_payload(message)
                if payload:
                    self.handle_goal(payload)
                continue
            if REASSIGN_REQUEST_REF in refs:
                self.handle_reassign_request(message, seq=seq)
                continue
            if CANCEL_REF in refs:
                self.handle_cancel_notice(message, seq=seq)
                continue
            if DIRECT_REF in refs:
                # A private question. It is answered privately and is NOT fed to
                # the pending goal: the group receipt is public, so replaying the
                # owner's private words there would break the channel.
                self.handle_direct_message(message, seq=seq)
                continue
            if ADD_MEMBER_REF in refs:
                # A window joined the project half way through: the administrator
                # has to know (and the work that nobody picked up yet can go to
                # it).
                self.handle_add_member(message, seq=seq)
                continue
            if self.state.pending_goal and (
                GOAL_ANSWER_REF in refs
                or (_sender_type(message) == "human" and str(message.get("body") or "").strip())
            ):
                # The owner answers the administrator's question in the group.
                # Only the owner's human message (or an explicit answer ref) is
                # an answer; the admin's own receipt or member chatter must not
                # be replayed to the model as if the owner had spoken.
                answer = str(message.get("body") or "").strip()
                if answer:
                    pending = dict(self.state.pending_goal)
                    pending["answers"] = [*pending.get("answers", []), answer]
                    pending["seq"] = seq
                    self.handle_goal(pending)
        self.state.save(self.state_path)
        return bool(messages)

    def handle_add_member(self, message: dict[str, Any], *, seq: int) -> None:
        """A window joined the project half way through.

        The administrator does three things:

        * it **knows** (the message is handled once; messages replay on every
          poll);
        * it **says so in the group** -- the people in the group should see that
          the team grew;
        * it gives the newcomer the work **nobody has picked up yet** (the
          tasks still waiting).  Work that is already running is not taken away
          from whoever is doing it -- moving that around is the human's call
          (the "re-point this task" request).
        """
        if seq in self.state.handled_request_seqs:
            return
        member = ""
        for ref in message.get("refs") or []:
            if str(ref).startswith(ADD_MEMBER_JSON_REF):
                member = str(ref)[len(ADD_MEMBER_JSON_REF) :].strip()
        if not member:
            print(
                json.dumps({"add_member_ignored": "no member id", "seq": seq}, ensure_ascii=False),
                flush=True,
            )
            return
        self.state.handled_request_seqs.append(seq)
        self.state.handled_request_seqs = self.state.handled_request_seqs[-200:]
        self.state.save(self.state_path)
        self._post_admin_message(f"成员窗口 {member} 中途加入了这个项目。还没人领的活可以派给它。")
        moved: list[str] = []
        if not self.dry_run and self.admin.harness_has_credential(member):
            try:
                page = self.client.get(f"/groups/{self.group_id}/tasks", status="ready")
            except httpx.HTTPStatusError:
                page = {}
            for task in page.get("tasks") or []:
                task_id = str(task.get("task_id") or "")
                if not task_id:
                    continue
                try:
                    detail = self.client.get(f"/tasks/{task_id}")
                except httpx.HTTPStatusError:
                    continue
                try:
                    self._reassign_one(
                        task_id,
                        detail,
                        member,
                        str(detail.get("owner") or ""),
                        token=str(seq),
                    )
                    moved.append(task_id)
                except httpx.HTTPStatusError as exc:
                    print(
                        json.dumps(
                            {"add_member_reassign_error": str(exc), "task_id": task_id},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
        print(
            json.dumps(
                {"member_added": member, "tasks_given": moved, "seq": seq},
                ensure_ascii=False,
            ),
            flush=True,
        )

    def handle_member_goal_change(self, message: dict[str, Any], *, seq: int) -> None:
        """A member says the goal has to change: the administrator hears it.

        The goal belongs to the owner (R10 §19): a member cannot change it.  So
        the administrator does two things and one non-thing:

        * it **knows** -- the request is recorded and not replayed;
        * it **tells the owner**, quoting the member, in the group -- that is
          where the owner reads, and the office shows it;
        * it does **not** change the goal by itself.

        The owner's answer is then picked up by the pending-goal path, so the
        administrator re-refines the goal with that answer and the plan follows.
        """
        sender = str(message.get("sender_id") or "").strip()
        body = str(message.get("body") or "").strip()
        if not sender or not body or sender == self.admin_harness_id:
            return
        if seq in self.state.handled_goal_change_seqs:
            return
        self.state.handled_goal_change_seqs.append(seq)
        self.state.handled_goal_change_seqs = self.state.handled_goal_change_seqs[-200:]
        task_id = str(message.get("task_id") or "")
        where = f"（{task_id}）" if task_id else ""
        self._post_admin_message(
            f"成员窗口 {sender}{where} 说目标要改：{body}\n"
            "要不要改、改成什么，由群主说；管理员会照新的说法修订方案。"
        )
        # 群主的回答要接得住：把「当前目标」摆成待答状态。管理员本来就在等群主
        # 回答时**合并**进去，别把那个问题顶掉（顶掉 = 那个问题永远没人答）。
        if self.state.last_goal:
            pending = dict(self.state.pending_goal or {}) or dict(self.state.last_goal)
            pending["questions"] = [
                *(pending.get("questions") or []),
                f"成员窗口 {sender} 说目标要改：{body}",
            ]
            pending["seq"] = seq
            self.state.pending_goal = pending
        self.state.save(self.state_path)
        print(
            json.dumps(
                {"member_goal_change": sender, "task_id": task_id, "seq": seq},
                ensure_ascii=False,
            ),
            flush=True,
        )

    def handle_direct_message(self, message: dict[str, Any], *, seq: int) -> None:
        """Answer a private question from the owner, in the same private channel.

        A direct message is a *conversation*, not a dispatch request: the answer
        goes through the administrator's own model channel and leaves as one more
        direct message (R10 §19). It is deliberately NOT fed to the pending goal
        -- the goal's receipt is posted to the whole group, which would drag the
        owner's private words into public view.
        """
        if seq in self.state.handled_request_seqs:
            return
        sender = str(message.get("sender_id") or "").strip()
        body = str(message.get("body") or "").strip()
        if not sender or not body or sender == self.admin_harness_id:
            return
        # Marked before the model call: a question is answered once, and the
        # answer is expensive. Messages replay on every poll, so the mark is the
        # only thing that keeps one question from becoming two answers.
        self.state.handled_request_seqs.append(seq)
        self.state.handled_request_seqs = self.state.handled_request_seqs[-200:]
        self.state.save(self.state_path)
        if self.dry_run:
            return
        try:
            self.admin.answer(question=body, group_id=self.group_id, asker=sender)
        except Exception as exc:  # noqa: BLE001 - the owner must hear about the failure
            # Never silence the owner. A failure is reported as a failure,
            # privately and in plain words -- a canned "收到" would claim work
            # that did not happen. The type is kept so a 500 or a secret-scan
            # block is not misreported as "the model was busy".
            self._post_direct_message(
                sender,
                "这一条没有答上来。\n"
                f"原因：{type(exc).__name__}: {exc}\n"
                "（不是你的问题；可以稍后重发，或者让管理员人工回答。）",
            )

    def handle_goal(self, payload: dict[str, Any] | None, *, force: bool = False) -> None:
        if not payload:
            return
        # The opening wizard records which administrator the owner chose. A
        # worker for a different admin window must not dispatch this goal: one
        # goal, one administrator actor. Manual callers that omit the field are
        # still accepted (the worker configuration is then the only selector).
        if payload.get("admin") and payload["admin"] != self.admin_harness_id:
            print(
                json.dumps(
                    {
                        "goal_skipped_for_other_admin": payload.get("goal_id"),
                        "admin": payload.get("admin"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        seq = int(payload.get("seq") or 0)
        if not force and seq in self.state.processed_goal_seqs:
            return
        # 记住这次的目标：成员后来提「目标要改」时，群主的回答要能据此重新细化。
        self.state.last_goal = dict(payload)
        # 这个项目的成员名单 = 这一轮传阅的读者名单。
        self._requested_members = [
            str(item).strip()
            for item in (payload.get("members") or [])
            if str(item).strip()
        ]
        if self.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": "goal",
                        **{
                            k: payload[k]
                            for k in (
                                "goal_id",
                                "goal",
                                "project_name",
                                "members",
                                "max_tasks",
                                "admin",
                            )
                        },
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            self.state.processed_goal_seqs.append(seq)
            self.state.pending_goal = None
            self.state.save(self.state_path)
            return
        try:
            result = self.admin.refine(
                requirement=payload["goal"],
                group_id=self.group_id,
                max_tasks=payload["max_tasks"],
                answers=payload.get("answers") or [],
                members=payload.get("members") or None,
                project_name=payload.get("project_name"),
                route=self.pool_route("refine"),
            )
        except AdminHarnessError as exc:
            # The administrator's own model failed.  Do not create a task from
            # a fallback spec; report the failure.
            #
            # And do **not** mark the goal as processed: a CLI that died, a
            # provider that went away or a stream that broke is an
            # infrastructure failure, not "this goal has been dealt with".
            # Marking it processed made the owner's request disappear after one
            # flaky call -- the platform said "failed" and then never tried
            # again.  The goal waits here and is retried once the channel is
            # back (with a backoff, so a broken channel is not hammered).
            self._post_admin_message(
                f"管理员 Harness 细化失败：{exc}；未分派任何任务。"
                "这条目标仍然在队列里，通道恢复后会自动重试。"
            )
            print(json.dumps({"goal_error": str(exc), "seq": seq}, ensure_ascii=False), flush=True)
            self.state.goal_retry = {
                **payload,
                "attempts": int(payload.get("attempts") or 0),
                "retry_after": time.time() + self.GOAL_RETRY_SECONDS,
                "last_error": str(exc)[:400],
            }
            self.state.save(self.state_path)
            return
        if result.get("status") == "needs_owner":
            self.state.pending_goal = {
                **payload,
                "questions": result.get("questions") or [],
            }
            self.state.goal_retry = None
            self.state.processed_goal_seqs.append(seq)
            self.state.save(self.state_path)
            print(
                json.dumps(
                    {
                        "goal_needs_owner": payload.get("goal_id"),
                        "questions": result.get("questions") or [],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        self.state.pending_goal = None
        # The goal is being worked on now: it is no longer waiting for a retry.
        self.state.goal_retry = None
        specs = result.get("tasks") or []
        reviewers = self._circulation_reviewers(specs)
        if not reviewers:
            # Nobody can read the draft: the plan is the only sensible next
            # step (this is the old behaviour, and it is still correct).
            self.state.pending_dispatch = {
                "goal_seq": seq,
                "goal_id": payload.get("goal_id"),
                "tasks": specs,
                "dispatched": {},
            }
            self.state.processed_goal_seqs.append(seq)
            self.resume_dispatch()
            return
        # 传阅：方案先变成**草案**并开一轮传阅，任务还不能被领取。
        # 细化结果先落进状态：建草案要是失败了，下一轮重试**不用再跑一次模型**。
        self.state.pending_refinement = {
            "goal_seq": seq,
            "goal_id": payload.get("goal_id"),
            "goal": str(payload.get("goal") or ""),
            "project_name": str(payload.get("project_name") or ""),
            "tasks": specs,
            "reviewers": reviewers,
            # 成员名单要跟着状态走：worker 重启之后还得知道这一轮该给谁看。
            "members": list(self._requested_members),
        }
        # 新方案从第 1 轮开始数（上一轮方案的轮数不能继承过来）。
        self.state.circulation_rounds_done = 0
        self.state.circulation_rounds_target = max(1, int(self.review_rounds))
        self.state.processed_goal_seqs.append(seq)
        self.state.save(self.state_path)
        self.start_circulation()

    def start_circulation(self) -> None:
        """Turn the refinement into a draft plan and open a circulation round.

        Kept separate from the refinement on purpose: if the platform refuses
        the draft, the next cycle retries **without** paying for the model
        again.
        """
        pending = self.state.pending_refinement
        if not pending:
            return
        # 成员名单随状态恢复：worker 重启之后，这一轮仍然要给**全部**成员看。
        self._requested_members = [
            str(item).strip()
            for item in (pending.get("members") or [])
            if str(item).strip()
        ] or self._requested_members
        if self.dry_run:
            print(
                json.dumps(
                    {"dry_run": "circulation", "goal_id": pending.get("goal_id")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        try:
            draft = self.client.post(
                "/adapter/admin/plan_draft",
                {
                    "group_id": self.group_id,
                    "title": pending.get("project_name") or pending.get("goal_id") or "方案",
                    "goal": str(pending.get("goal") or ""),
                    "tasks": pending.get("tasks") or [],
                    "reviewer_harness_ids": pending.get("reviewers") or [],
                    "opened_by": self.admin_harness_id,
                },
            )
        except httpx.HTTPError as exc:
            print(
                json.dumps(
                    {"circulation_error": str(exc), "goal_id": pending.get("goal_id")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        self.state.pending_circulation = {
            "goal_seq": pending["goal_seq"],
            "goal_id": pending.get("goal_id"),
            "goal": pending.get("goal") or "",
            "project_name": pending.get("project_name") or "",
            "plan_id": str(draft.get("plan_id") or ""),
            "circulation_id": str(draft.get("circulation_id") or ""),
            "tasks": pending.get("tasks") or [],
            "reviewers": pending.get("reviewers") or [],
        }
        self.state.pending_refinement = None
        self.state.save(self.state_path)
        self._post_admin_message(
            f"管理员已细化方案（{draft.get('plan_id')}）并开一轮传阅："
            f"{'、'.join(pending.get('reviewers') or [])}。审阅意见回来后并入定稿，再交人最终审核；"
            f"在此之前**没有任务可以被领取**。"
        )
        print(
            json.dumps(
                {
                    "plan_drafted": draft.get("plan_id"),
                    "circulation": draft.get("circulation_id"),
                    "reviewers": pending.get("reviewers") or [],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def _next_circulation_readers(self, previous: list[str]) -> list[str]:
        """Who reads the next round.

        **The same readers as the round before** -- and that is the point.
        The owner defined the process: the plan goes down to the members, every
        member reads it and writes their comments below, the comments are
        collected and the administrator folds them into a complete plan; then
        the administrator sends the **new** plan down again and every member
        reads **that** one.  What changes between the rounds is the plan, not
        the audience -- so the second round must not quietly drop anyone.

        (An earlier version rotated the readers and capped the round size.
        That produced rounds where one member read the plan and a different
        member read the revision, which is not the process the owner asked
        for: a member who did not see the first round cannot tell whether the
        revision answered their own remarks.)
        """
        return list(previous)

    def _circulation_reviewers(self, specs: list[dict[str, Any]]) -> list[str]:
        """Who reads the draft before anything is assigned.

        **Every member of the project**, not only the windows the plan happens
        to name: the owner asked for the plan to be passed down and for every
        member to review it and put their comments below.  A member that only
        gets to look at the plan it will not execute has no standing to ask
        for changes to it.

        A window that cannot take work (no credential, offline, the
        administrator itself) is not a reader: it would leave the round
        waiting for an answer that never comes.
        """
        assignable = list(self.admin.assignable_harnesses())
        requested = self._requested_members
        if requested:
            members = [harness for harness in requested if harness in assignable]
            if members:
                return members
        if assignable:
            return assignable
        # Nobody can read it: the plan is the only sensible next step.
        return []

    # --------------------------------------------------------- circulation

    def collect_circulation(self) -> None:
        """Gather the readers' comments and finalise the plan.

        The plan is finalised **for a human**: the comments are folded in, the
        round is closed, and the document is written.  Nobody is dispatched
        here -- the human approves the plan, and only then do tasks exist.
        """
        pending = self.state.pending_circulation
        if not pending:
            return
        try:
            round_info = self.client.get(f"/allocation/circulation/{pending['circulation_id']}")
        except httpx.HTTPStatusError as exc:
            print(json.dumps({"circulation_read_error": str(exc)}, ensure_ascii=False), flush=True)
            return
        reviews = round_info.get("reviews") or []
        reviewers = pending.get("reviewers") or []
        missing = [
            harness
            for harness in reviewers
            if harness not in {str(r.get("harness_id")) for r in reviews}
        ]
        if missing:
            return
        if self.dry_run:
            print(
                json.dumps(
                    {
                        "dry_run": "circulation",
                        "plan_id": pending["plan_id"],
                        "reviews": len(reviews),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        try:
            revised = self.admin.revise_with_reviews(
                plan_id=str(pending.get("plan_id") or ""),
                goal=str(pending.get("goal") or ""),
                tasks=list(pending.get("tasks") or []),
                reviews=list(reviews),
                route=self.pool_route("revise"),
            )
            self.client.post(
                "/adapter/admin/plan_revise",
                {
                    "plan_id": pending["plan_id"],
                    "goal": str(pending.get("goal") or ""),
                    "tasks": revised,
                    "revised_by": self.admin_harness_id,
                },
            )
            # The round's state machine (R10 §39): circulating -> revising
            # (the round is closed once every reader has answered) -> completed.
            self.client.post(
                "/allocation/circulation/close",
                {
                    "circulation_id": pending["circulation_id"],
                    "harness_id": self.admin_harness_id,
                },
            )
            rounds_done = int(self.state.circulation_rounds_done) + 1
            target = max(1, int(self.state.circulation_rounds_target))
            if rounds_done < target:
                # 还没到轮数：**再开一轮**，让方案被另一批人看一遍。
                #
                # §39 的 `revising -> circulating` 就是"修订产生新方案版本，并进入
                # 下一轮"：轮号前进、名单可以换。下一轮读的是**刚改过的**方案
                # （方案改版会让在途的传阅轮跟着换绑），所以第二轮的意见是对着
                # 修订稿说的，不是重复第一轮。
                next_readers = self._next_circulation_readers(pending.get("reviewers") or [])
                self.client.post(
                    "/allocation/circulation/revise",
                    {
                        "circulation_id": pending["circulation_id"],
                        "harness_id": self.admin_harness_id,
                        "reviewer_harness_ids": next_readers,
                    },
                )
                self.state.circulation_rounds_done = rounds_done
                self.state.pending_circulation = {
                    **pending,
                    "reviewers": next_readers,
                    "tasks": revised,
                }
                self.state.save(self.state_path)
                self._post_admin_message(
                    f"第 {rounds_done} 轮传阅意见已并入方案（{pending['plan_id']}），"
                    f"第 {rounds_done + 1} 轮传阅已开：{'、'.join(next_readers)}。"
                    f"本轮共 {target} 轮，定稿后才会交人最终审核。"
                )
                print(
                    json.dumps(
                        {
                            "circulation_next_round": rounds_done + 1,
                            "circulation_rounds_target": target,
                            "reviewers": next_readers,
                            "plan_id": pending["plan_id"],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return
            self.client.post(
                "/allocation/circulation/complete",
                {
                    "circulation_id": pending["circulation_id"],
                    "harness_id": self.admin_harness_id,
                },
            )
            self.state.circulation_rounds_done = rounds_done
            document = self.client.get(f"/plans/{pending['plan_id']}/document")
        except httpx.HTTPStatusError as exc:
            print(
                json.dumps(
                    {"circulation_error": str(exc), "plan_id": pending.get("plan_id")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        self.state.pending_circulation = None
        self.state.save(self.state_path)
        self._post_admin_message(
            f"方案定稿（{pending['plan_id']}，{len(reviews)} 条意见已并入）："
            f"{document.get('markdown_path')}。请在办公室做**最终审核**，"
            f"批准后才会派单。"
        )
        print(
            json.dumps(
                {
                    "plan_finalised": pending["plan_id"],
                    "reviews": len(reviews),
                    "document": document.get("markdown_path"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    # --------------------------------------------------------------- dispatch

    # ------------------------------------------------------ 分配治理（§39 硬规则 2）

    #: 一个分配轮开着最久多久（秒）。到了就冻结候选集合往下走 —— 否则一个
    #: 没人申请的任务会把整轮卡住，成员永远领不到活。
    ALLOCATION_ROUND_GRACE_SECONDS = 180.0

    #: 细化在**模型通道**上失败后，等多久重试这条目标。够长，不至于把一条坏掉的
    #: 通道锤一遍；够短，群主不用等太久才看到自己的目标被处理。
    GOAL_RETRY_SECONDS = 60.0

    #: 申请在本轮里的两种"还算数"的状态。轮次一关闭（``allocation.closed``），
    #: 所有还在候选集合里的申请会从 ``submitted`` 转成 ``frozen`` —— 冻结之后
    #: 仍然可以被裁决，只是本人不能再改、也不能撤回。所以裁决时**两种都要认**：
    #: 只认 ``submitted`` 会让"已经申请、但轮次已冻结"的候选凭空消失。
    APPLICABLE_APPLICATION_STATES = frozenset({"submitted", "frozen"})

    def _plan_snapshot(self, plan_id: str) -> tuple[str, list[dict[str, Any]]]:
        """The plan's newest snapshot and the tasks it holds."""
        rows = self.database.read(
            "SELECT snapshot_id, content_json FROM plan_snapshots WHERE plan_id = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (plan_id,),
        )
        if not rows:
            return "", []
        try:
            raw = json.loads(str(rows[0]["content_json"] or "{}"))
        except ValueError:
            return str(rows[0]["snapshot_id"]), []
        tasks = raw.get("tasks")
        items = [item for item in tasks if isinstance(item, dict)] if isinstance(tasks, list) else []
        return str(rows[0]["snapshot_id"]), items

    def _approved_plan_without_round(self) -> tuple[str, str, list[dict[str, Any]]] | None:
        """The approved plan of this group that has no allocation round yet.

        Only an **approved** plan may get one: §39 says the round is opened on
        "方案快照已批准、已完成至少 1 轮传阅" -- a draft has no tasks to assign.

        Two filters keep the old history from hijacking the live one:

        * the **newest** approved plan wins (the human's last approval is the
          live work; this group has dozens of approved plans from earlier runs);
        * the snapshot must still hold a task in ``ready`` -- a plan whose tasks
          are all cancelled, merged or already awaiting a review is not waiting
          for an assignment.
        """
        rows = self.database.read(
            "SELECT plan_id FROM plans WHERE group_id = ? AND status = ? ORDER BY created_at DESC",
            (self.group_id, "approved"),
        )
        existing = {
            str(row["plan_snapshot_id"])
            for row in self.database.read(
                "SELECT plan_snapshot_id FROM allocation_rounds WHERE group_id = ?",
                (self.group_id,),
            )
        }
        for row in rows:
            plan_id = str(row["plan_id"])
            snapshot_id, tasks = self._plan_snapshot(plan_id)
            if not snapshot_id or snapshot_id in existing or not tasks:
                continue
            pending = []
            for item in tasks:
                task_id = str(item.get("id") or "")
                if not task_id:
                    continue
                status_rows = self.database.read(
                    "SELECT status FROM tasks WHERE task_id = ? AND plan_snapshot_id = ?",
                    (task_id, snapshot_id),
                )
                if status_rows and str(status_rows[0]["status"]) == "ready":
                    pending.append(task_id)
            if pending:
                return plan_id, snapshot_id, tasks
        return None

    def _current_round(self, round_id: str) -> dict[str, Any] | None:
        """One allocation round as the platform currently holds it.

        Read fresh every time: the round's **revision** moves on every
        application and on every decision, and the decision carries an
        optimistic lock (``expected_revision``).  A revision read once at the
        top of a poll is stale by the time it is used -- the platform would
        refuse the decision, and the task would sit unassigned for ever.
        """
        rounds = self.client.get("/allocation/rounds", group_id=self.group_id).get("rounds") or []
        for item in rounds:
            if str(item.get("round_id")) == round_id:
                return item
        return None

    def open_allocation_and_decide(self) -> None:
        """Open the allocation round, freeze the candidates, decide per task.

        §39 hard rule 2: only an **accepted** allocation authorises a Claim.
        So somebody has to open the round, let the members apply, freeze the
        candidate set and pick -- and that somebody is the administrator
        harness, not the member (a member may not decide who competes with it)
        and not the platform (the platform would be inventing the assignment).
        """
        if self.dry_run:
            return
        pending = self.state.pending_allocation
        if not pending:
            if self.database is None:
                # 没有平台库就认不出"哪个已批准的方案还没开分配轮"。这不是错误：
                # 只是这个 worker 不参与分配治理（只用到假客户端的单元测试会走到这里）。
                return
            found = self._approved_plan_without_round()
            if not found:
                return
            plan_id, snapshot_id, tasks = found
            try:
                opened = self.client.post(
                    "/allocation/round/open",
                    {
                        "group_id": self.group_id,
                        "plan_snapshot_id": snapshot_id,
                        "opened_by": self.admin_harness_id,
                    },
                )
            except httpx.HTTPStatusError as exc:
                print(
                    json.dumps({"allocation_open_error": str(exc), "plan_id": plan_id}),
                    flush=True,
                )
                return
            self.state.pending_allocation = {
                "plan_id": plan_id,
                "plan_snapshot_id": snapshot_id,
                "round_id": str(opened.get("round_id") or ""),
                "opened_at": time.time(),
                "decided": [],
                # 这一轮要分配的是哪些活。**必须存下来**：没有它就不知道"谁该
                # 申请"，宽限期一过就会把一条都没人申请的活也判给某人。
                "tasks": [
                    {
                        "id": str(item.get("id") or ""),
                        "title": str(item.get("title") or ""),
                        "assigned_harness_ids": [
                            str(x) for x in (item.get("assigned_harness_ids") or []) if str(x)
                        ],
                    }
                    for item in tasks
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                ],
            }
            self.state.save(self.state_path)
            self._post_admin_message(
                f"已为方案（{plan_id}）开分配轮：成员可以申请自己适合的任务，"
                f"我会逐条裁决；裁决后由被选中的人自己确认接单，"
                f"**确认之前谁都领不动这条活**（§39 硬规则 2）。"
            )
            print(
                json.dumps(
                    {"allocation_opened": opened.get("round_id"), "plan_id": plan_id},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return

        round_id = str(pending.get("round_id") or "")
        if not round_id:
            self.state.pending_allocation = None
            self.state.save(self.state_path)
            return
        rounds = self.client.get("/allocation/rounds", group_id=self.group_id).get("rounds") or []
        current = next((item for item in rounds if str(item.get("round_id")) == round_id), None)
        if current is None:
            return
        state = str(current.get("state") or "")
        if state == "opened":
            self._close_allocation_if_ready(current, pending)
            return
        if state not in ("closed", "decided"):
            # cancelled: the plan was voided. Nothing left to decide.
            self.state.pending_allocation = None
            self.state.save(self.state_path)
            return
        self._decide_allocation_tasks(round_id, pending)

    def _close_allocation_if_ready(self, current: dict[str, Any], pending: dict[str, Any]) -> None:
        """Freeze the candidate set once the members had their chance."""
        tasks = [item for item in (pending.get("tasks") or []) if isinstance(item, dict)]
        wanted = {str(item.get("id")) for item in tasks if item.get("id")}
        applied: set[str] = set()
        for application in current.get("applications") or []:
            if str(application.get("state")) == "withdrawn":
                continue
            for task_id in application.get("task_ids") or []:
                applied.add(str(task_id))
        missing = sorted(wanted - applied)
        opened_at = float(pending.get("opened_at") or 0.0)
        waited = time.time() - opened_at if opened_at else 0.0
        if missing and waited < self.ALLOCATION_ROUND_GRACE_SECONDS:
            return
        try:
            self.client.post(
                "/allocation/round/close",
                {
                    "round_id": str(current.get("round_id") or ""),
                    "harness_id": self.admin_harness_id,
                    "reason": (
                        "所有任务都有人申请" if not missing else
                        f"等待超过 {int(self.ALLOCATION_ROUND_GRACE_SECONDS)} 秒，"
                        f"仍无人申请的任务：{'、'.join(missing)}"
                    ),
                },
            )
        except httpx.HTTPStatusError as exc:
            print(json.dumps({"allocation_close_error": str(exc)}), flush=True)
            return
        print(
            json.dumps(
                {"allocation_closed": current.get("round_id"), "unapplied": missing},
                ensure_ascii=False,
            ),
            flush=True,
        )

    def _decide_allocation_tasks(self, round_id: str, pending: dict[str, Any]) -> None:
        """Pick one candidate per task (or invite the window the plan named).

        Three rules decide who gets the work, and they are the platform's, not
        the administrator's invention:

        * the plan says which window a task is **for** (``assigned_harness_ids``);
          that window's application wins if there is one;
        * a task nobody applied for is **invited** to the window the plan named
          (§39 ``assignment.invited``) -- the window still has to accept;
        * a task with no named window goes to whoever applied.

        In every case the chosen window must still **accept** before the
        allocation authorises a Claim (§39 hard rule 2).
        """
        decided = {str(item) for item in (pending.get("decided") or [])}
        tasks = [item for item in (pending.get("tasks") or []) if isinstance(item, dict)]
        for task in tasks:
            task_id = str(task.get("id") or "")
            if not task_id or task_id in decided:
                continue
            assigned = [str(x) for x in (task.get("assigned_harness_ids") or []) if str(x)]
            # Read the round fresh: the revision moved (an application arrived
            # since the last poll) and the decision carries an optimistic lock.
            current = self._current_round(round_id)
            if current is None:
                return
            if str(current.get("state") or "") not in ("closed", "decided"):
                return
            already = [
                str(item.get("task_id"))
                for item in (current.get("assignments") or [])
                if str(item.get("task_id")) == task_id
            ]
            if already:
                # This task already has an assignment in this round (a previous
                # decision, or an invitation). The platform refuses a second
                # decision for the same task, and it should: one task, one
                # assignment.
                decided.add(task_id)
                self.state.pending_allocation = {**pending, "decided": sorted(decided)}
                self.state.save(self.state_path)
                continue
            candidates = self.client.get(
                "/allocation/candidates", round_id=round_id, task_id=task_id
            ).get("candidates") or []
            usable = [
                item
                for item in candidates
                if str(item.get("state")) in self.APPLICABLE_APPLICATION_STATES
            ]
            named = [item for item in usable if str(item.get("harness_id")) in assigned]
            chosen = named[0] if named else (usable[0] if not assigned and usable else None)
            revision = int(current.get("revision") or 1)
            try:
                if chosen is not None:
                    self.client.post(
                        "/allocation/decision",
                        {
                            "round_id": round_id,
                            "task_id": task_id,
                            "application_id": str(chosen.get("application_id") or ""),
                            "decided_by": self.admin_harness_id,
                            "expected_revision": revision,
                            "reason": (
                                "按方案指定的执行窗口裁决；该窗口已申请。"
                                if named
                                else "本轮申请者中裁决。"
                            ),
                            "compared_harness_ids": sorted(
                                {str(item.get("harness_id")) for item in candidates}
                            ),
                        },
                    )
                elif assigned:
                    # 方案点了名、但它没申请：改派邀请（§39 的 assignment.invited）。
                    # 邀请之后**仍然要它自己接受**，管理员不能替它答应。
                    self.client.post(
                        "/allocation/invite",
                        {
                            "round_id": round_id,
                            "task_id": task_id,
                            "harness_id": assigned[0],
                            "invited_by": self.admin_harness_id,
                            "reason": "方案指定该窗口执行，但本轮没有申请：发出改派邀请。",
                        },
                    )
            except httpx.HTTPStatusError as exc:
                print(
                    json.dumps(
                        {"allocation_decision_error": str(exc), "task_id": task_id},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return
            decided.add(task_id)
            self.state.pending_allocation = {**pending, "decided": sorted(decided)}
            self.state.save(self.state_path)
            print(
                json.dumps(
                    {"allocation_decided": task_id, "round_id": round_id},
                    ensure_ascii=False,
                ),
                flush=True,
            )

    def resume_dispatch(self) -> None:
        pending = self.state.pending_dispatch
        if not pending:
            return
        tasks = pending.get("tasks") or []
        done = pending.setdefault("dispatched", {})
        for index, task in enumerate(tasks):
            key = str(index)
            if key in done:
                continue
            if self.dry_run:
                print(
                    json.dumps({"dry_run": "dispatch", "task": task}, ensure_ascii=False),
                    flush=True,
                )
                done[key] = "dry-run"
                continue
            try:
                result = self.client.post(
                    f"/groups/{self.group_id}/dispatch",
                    {
                        "request_id": f"admin-goal-{pending['goal_seq']}-{index}",
                        "title": task["title"],
                        "goal": task["goal"],
                        "harness_id": task["harness_id"],
                        "exclusive_paths": task["exclusive_paths"],
                        "acceptance": task["acceptance"],
                        "decided_by": self.admin_harness_id,
                    },
                )
                done[key] = result.get("task_id")
                print(
                    json.dumps(
                        {"dispatched": result.get("task_id"), "title": task["title"]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            except httpx.HTTPStatusError as exc:
                self._post_admin_message(f"管理员 Harness 分派失败：{task.get('title')} · {exc}")
                print(
                    json.dumps(
                        {"dispatch_error": str(exc), "title": task.get("title")},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                return
        self.state.pending_dispatch = None
        self.state.save(self.state_path)

    # ------------------------------------------------------------- 改派

    def reassign_dead_tasks(self) -> None:
        """把执行者**领不了活**的任务改派给能领活的窗口。

        登记在册但没有平台凭据的窗口领不了任务，那条活会永远停在 ``ready``。
        管理员把**同样的规格**改派给一个能领活的窗口：取消旧任务（`cancel`
        边本来就有），再按同样的标题/目标/路径/验收派一条新的（`dispatch` 也是
        既有动作），最后在群里说清楚为什么改派。

        这里没有新造事件名或状态边：改派 = 取消 + 派单，两步各自留痕。

        候选窗口走的是**服务端同一套规则**（`AdminService.assignable_harnesses`），
        所以改派不会派给一个同样领不了活的窗口。
        """
        candidates = sorted(self.admin.assignable_harnesses())
        if not candidates:
            return
        # 只改**自己派出去的**任务：别人派的历史活不是管理员该动的东西。
        own = self.admin.dispatched_task_ids()
        if not own:
            return
        page = self.client.get(f"/groups/{self.group_id}/tasks", status="ready")
        load: dict[str, int] = {}
        for task in page.get("tasks") or []:
            for target in _assigned_ids(task):
                load[target] = load.get(target, 0) + 1
        for task in page.get("tasks") or []:
            task_id = str(task.get("task_id") or "")
            targets = _assigned_ids(task)
            if not task_id or not targets or task_id not in own:
                continue
            dead = [target for target in targets if not self.admin.harness_has_credential(target)]
            if not dead:
                continue
            # 优先给**最闲**的窗口，别把活全堆到一个窗口上。
            target = min(
                (candidate for candidate in candidates if candidate not in targets),
                key=lambda candidate: (load.get(candidate, 0), candidate),
                default=None,
            )
            if target is None:
                continue
            load[target] = load.get(target, 0) + 1
            try:
                self._reassign_one(task_id, task, target, dead[0])
            except httpx.HTTPStatusError as exc:
                print(
                    json.dumps(
                        {"reassign_error": str(exc), "task_id": task_id, "target": target},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )

    # ------------------------------------------------------ cancel notices

    def handle_cancel_notice(self, message: dict[str, Any], *, seq: int) -> None:
        """The owner cancelled a task: re-refine **that part** of the plan.

        The scope stays narrow on purpose.  The cancelled task's own work order
        is the requirement; re-refining the whole goal would dispatch work that
        is already running a second time.  Everything else in the plan keeps
        going, and the new refinement goes through the usual route (draft ->
        circulation -> the owner approves -> dispatch).

        If the work order cannot be read, nothing is invented: the administrator
        says so in the group and waits for the human to say what the part was.
        """
        payload = _cancel_payload(message)
        if payload is None:
            print(
                json.dumps({"cancel_notice_ignored": "no task id", "seq": seq}, ensure_ascii=False),
                flush=True,
            )
            return
        task_id = payload["task_id"]
        try:
            task = self.client.get(f"/tasks/{task_id}")
        except httpx.HTTPStatusError:
            print(
                json.dumps(
                    {"cancel_notice_ignored": "task not found", "task_id": task_id, "seq": seq},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        spec = task.get("spec") or {}
        requirement = str(spec.get("goal") or "").strip()
        if not requirement:
            # 没有工单原文就**不猜**范围：把一句话丢给模型让它自己补，那是编。
            self._post_admin_message(
                f"群主取消了一条活（{task_id}），但平台读不到它的工单原文，"
                "所以我没有重新细化。请人再说一次那部分要做什么。"
            )
            print(
                json.dumps({"cancel_notice_without_spec": task_id, "seq": seq}, ensure_ascii=False),
                flush=True,
            )
            return
        reason = payload.get("reason") or "（没写原因）"
        title = payload.get("title") or str(task.get("title") or task_id)
        self.handle_goal(
            {
                "goal_id": f"cancel-{task_id}",
                "goal": (
                    f"{requirement}\n\n"
                    f"（这一条已被群主取消：{reason}。请就**它原本要做的这部分**重新细化一版；"
                    "如果这部分已经不需要了，就不要产出任务。）"
                ),
                "project_name": None,
                "members": [],
                "max_tasks": 1,
                "answers": [],
                "admin": self.admin_harness_id,
                "seq": seq,
            }
        )
        print(
            json.dumps({"cancel_refined": task_id, "title": title, "seq": seq}, ensure_ascii=False),
            flush=True,
        )

    # ------------------------------------------------------ re-point requests

    def handle_reassign_request(self, message: dict[str, Any], *, seq: int) -> None:
        """Act on a human's "please re-point this task" request.

        The request comes from the office (the human pressed the button) or
        from the group.  It is a **request**, not an order: the administrator
        decides whether to re-point, and it does the re-pointing itself, with
        its own credential -- the office never dispatches.

        The request names a task; the target window is optional (the human may
        have picked one, otherwise the least loaded window that can take work
        is chosen).
        """
        if seq in self.state.handled_request_seqs:
            return
        task_id = str(message.get("task_id") or "").strip()
        if not task_id:
            body = str(message.get("body") or "")
            for token in body.replace("：", " ").replace(":", " ").split():
                if token.startswith("FD-"):
                    task_id = token
                    break
        if not task_id:
            print(
                json.dumps(
                    {"reassign_request_ignored": "no task id", "seq": seq}, ensure_ascii=False
                ),
                flush=True,
            )
            return
        self.state.handled_request_seqs.append(seq)
        self.state.handled_request_seqs = self.state.handled_request_seqs[-200:]
        self.state.save(self.state_path)
        refs = [str(ref) for ref in (message.get("refs") or [])]
        target = ""
        for ref in refs:
            if ref.startswith(REASSIGN_TARGET_REF):
                target = ref[len(REASSIGN_TARGET_REF) :].strip()
        try:
            task = self.client.get(f"/tasks/{task_id}")
        except httpx.HTTPStatusError:
            print(
                json.dumps(
                    {"reassign_request_ignored": f"task {task_id} not found", "seq": seq},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        status = str(task.get("status") or "")
        if status not in REASSIGNABLE_STATES:
            # Nothing to re-point: the work is already moving.
            print(
                json.dumps(
                    {"reassign_request_ignored": f"task {task_id} is {status}", "seq": seq},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        candidates = sorted(self.admin.assignable_harnesses())
        if not candidates:
            print(
                json.dumps(
                    {"reassign_request_ignored": "no window can take work", "seq": seq},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        previous = str(task.get("owner") or "")
        if target and target not in candidates:
            print(
                json.dumps(
                    {"reassign_request_ignored": f"{target} cannot take work", "seq": seq},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        if not target:
            load: dict[str, int] = {}
            page = self.client.get(f"/groups/{self.group_id}/tasks", status="ready")
            for ready_task in page.get("tasks") or []:
                for assigned in _assigned_ids(ready_task):
                    load[assigned] = load.get(assigned, 0) + 1
            target = min(
                (candidate for candidate in candidates if candidate != previous),
                key=lambda candidate: (load.get(candidate, 0), candidate),
                default="",
            )
        if not target:
            print(
                json.dumps(
                    {"reassign_request_ignored": "no other window available", "seq": seq},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        try:
            self._reassign_one(task_id, task, target, previous, token=str(seq))
        except httpx.HTTPStatusError as exc:
            print(
                json.dumps(
                    {"reassign_error": str(exc), "task_id": task_id, "target": target},
                    ensure_ascii=False,
                ),
                flush=True,
            )

    def _reassign_one(
        self,
        task_id: str,
        task: dict[str, Any],
        target: str,
        dead: str,
        token: str = "",
    ) -> None:
        context = self.client.get(f"/adapter/context/{task_id}")
        spec = context.get("spec") or {}
        acceptance = spec.get("acceptance")
        if isinstance(acceptance, list):
            acceptance_text = "；".join(
                str(item.get("description") or "") for item in acceptance if isinstance(item, dict)
            )
        else:
            acceptance_text = str(acceptance or "")
        # 理由要**如实**：原执行者没有凭据、原执行者上一轮没交出活、和"按负载重排"，
        # 是三件不同的事。
        if dead and not self.admin.harness_has_credential(dead):
            reason = f"执行方 {dead} 没有平台凭据，无法领取任务；改派给 {target}"
        elif dead and dead == target:
            reason = f"原执行者 {dead} 上一轮没能交出交付物，重新派一次"
        else:
            reason = f"管理员将该任务改派给负载较低的窗口：{dead or '原执行者'} → {target}"
        if self.dry_run:
            print(
                json.dumps(
                    {"dry_run": "reassign", "task_id": task_id, "from": dead, "to": target},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return
        try:
            self.client.post(
                f"/tasks/{task_id}/cancel",
                {
                    "decided_by": self.admin_harness_id,
                    "reason": reason,
                },
            )
        except httpx.HTTPStatusError as exc:
            # 已经不在 ready（例如被人工取消过）时，取消会拒绝；改派照做，
            # 否则那条活就永远没有能领它的执行者了。
            if exc.response.status_code not in (409, 422):
                raise
        # 派单按 request_id 幂等：同一条任务第二次改派必须用**新的** request_id，
        # 否则只会重放上一次，什么都不会发生。次数记在 worker 的状态里。
        count = int(self.state.reassign_counts.get(task_id, 0))
        self.state.reassign_counts[task_id] = count + 1
        self.state.save(self.state_path)
        dispatched = self.client.post(
            f"/groups/{self.group_id}/dispatch",
            {
                # The request id has to be unique **per re-point**, not just per
                # task: the count lives in the worker's state, and a lost or
                # reset state file would make the platform replay an *old*
                # dispatch (the same task id comes back and nothing happens).
                # A message-driven request brings its own sequence number.
                "request_id": f"admin-reassign-{task_id}-{count}" + (f"-{token}" if token else ""),
                "title": spec.get("title") or task.get("title") or task_id,
                "goal": self._goal_with_handoff(
                    spec.get("goal") or task.get("title") or task_id, task_id
                ),
                "harness_id": target,
                "exclusive_paths": spec.get("exclusive_paths") or [],
                "acceptance": acceptance_text,
                "decided_by": self.admin_harness_id,
                # The lineage is a **platform fact**, not a sentence in a
                # group message: the new task's snapshot says which task it
                # takes over.
                "supersedes": task_id,
            },
        )
        new_task_id = str(dispatched.get("task_id") or "")
        self._post_admin_message(f"管理员 Harness 改派：{task_id} → {new_task_id}（{reason}）")
        print(
            json.dumps(
                {
                    "reassigned": task_id,
                    "new_task": new_task_id,
                    "from": dead,
                    "to": target,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    def _goal_with_handoff(self, goal: str, task_id: str) -> str:
        """The new owner's goal, with the failed attempt's breakpoint report.

        The report lives next to the run evidence (``formal-runs/<task>.handoff.json``).
        Whoever claims the re-pointed task reads it in the work order, so the
        second harness continues instead of starting over.
        """
        report = self._handoff_report(task_id)
        if not report:
            return goal
        return (
            goal + "\nThe previous attempt did NOT finish; here is its breakpoint report. "
            "Continue from it instead of starting over: keep what is already written, "
            "fix what it says is broken. Breakpoint report (verbatim): "
            + json.dumps(report, ensure_ascii=False)
        )

    def _handoff_report(self, task_id: str) -> dict[str, Any] | None:
        path = Path(settings.data_dir) / "formal-runs" / f"{task_id}.handoff.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _report_same_provider_wall(
        self, task_id: str, exc: httpx.HTTPStatusError
    ) -> None:
        """Say it out loud when only a human can unblock this review.

        A same-provider refusal is not a transient error: cooling down and
        retrying hits the same wall until someone opens the channel, and the
        owner never learns why the work sits in ``awaiting_review``. Reported
        once per task (the retry loop would otherwise bury the group chat), and
        it names the exact call to make -- "需要人工" alone leaves them guessing.
        """
        response = getattr(exc, "response", None)
        body = "" if response is None else str(getattr(response, "text", "") or "")
        if "cross_provider_required" not in body:
            return
        if task_id in self.state.same_provider_review_asked:
            return
        self.state.same_provider_review_asked.append(task_id)
        self._post_admin_message(
            f"{task_id} 待审，但能审它的窗口与它的持有者同厂商，平台默认不放行"
            f"（review_policy=cross_provider）。要让我审这一条，请群主开一次同厂商通道："
            f"POST /api/v1/human/review_same_provider "
            f'{{"task_id": "{task_id}", "decided_by": "<群主>", "reason": "<理由>"}}。'
            " 开了以后我会再审一遍；在开之前它会一直停在待审。"
        )

    def reassign_task(self, task_id: str, target: str) -> dict[str, Any]:
        """Explicitly re-point one task to a window that can claim it.

        The automatic sweep only looks at ``ready`` tasks; this is the manual
        tool for the rest (a task that was cancelled on the way, or one the
        operator wants moved). The target must be assignable by the same rule
        the refinement uses.
        """
        if target not in self.admin.assignable_harnesses():
            raise AdminHarnessError(
                f"{target} cannot take this work: it holds no platform credential,"
                " is an administrator window, or shares the administrator's provider"
            )
        task = self.client.get(f"/tasks/{task_id}")
        targets = _assigned_ids(task)
        dead = targets[0] if targets else target
        self._reassign_one(task_id, task, target, dead)
        return {"task_id": task_id, "to": target}

    # ----------------------------------------------------------------- review

    def review_awaiting(self) -> None:
        page = self.client.get(f"/groups/{self.group_id}/tasks", status="awaiting_review")
        for task in page.get("tasks") or []:
            task_id = str(task.get("task_id") or "")
            attempt_id = str(task.get("attempt_id") or task.get("current_attempt_id") or "")
            if not task_id or not attempt_id:
                continue
            key = f"{task_id}:{attempt_id}"
            if key in self.state.reviewed_attempts:
                continue
            if self.admin_harness_id in _assigned_ids(task):
                continue
            if self._review_cooldowns.get(key, 0.0) > time.monotonic():
                continue
            if self.dry_run:
                print(
                    json.dumps({"dry_run": "review", "task_id": task_id}, ensure_ascii=False),
                    flush=True,
                )
                continue
            try:
                self._review_one(task, attempt_id)
            except httpx.HTTPStatusError as exc:
                # A cross-provider refusal or a competing lease is a platform
                # rule, not something the worker may override.
                self._review_cooldowns[key] = time.monotonic() + REVIEW_RETRY_SECONDS
                self._report_same_provider_wall(task_id, exc)
                print(
                    json.dumps(
                        {"review_skipped": task_id, "error": str(exc)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                continue
            except AdminHarnessError as exc:
                # Model failure happens before the claim, so no lease is held
                # and another administrator (or a human) can still review.
                self._review_cooldowns[key] = time.monotonic() + REVIEW_RETRY_SECONDS
                self._post_admin_message(
                    f"管理员 Harness 无法裁决 {task_id}：{exc}；未提交审核结论。"
                )
                print(
                    json.dumps({"review_error": task_id, "error": str(exc)}, ensure_ascii=False),
                    flush=True,
                )
                continue
            self._review_cooldowns.pop(key, None)
            self.state.reviewed_attempts.append(key)
            self.state.save(self.state_path)

    def _review_one(self, task: dict[str, Any], attempt_id: str) -> None:
        task_id = str(task["task_id"])
        # Read the frozen evidence and ask the model BEFORE taking the review
        # lease. A slow/failed model call must not hold a 20-minute lease that
        # blocks every other reviewer; the claim happens only when there is a
        # verdict ready to submit.
        context = self.client.get(f"/adapter/context/{task_id}")
        diff = self.client.get(
            f"/tasks/{task_id}/diff",
            include_patch="true",
            attempt_id=attempt_id,
        )
        verification = self.client.get(f"/tasks/{task_id}/verification").get("verification")
        verdict = self.admin.review(
            task=task,
            spec=context.get("spec") or {},
            diff=diff,
            verification=verification,
            project_name=_title_project(task),
            route=self.pool_route("review"),
        )
        claim = self.client.post(
            "/review/claim",
            {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "reviewer_harness_id": self.admin_harness_id,
            },
        )
        submitted = self.client.post(
            "/review/submit",
            {
                "task_id": task_id,
                "attempt_id": attempt_id,
                "reviewer_harness_id": self.admin_harness_id,
                "review_id": claim["review_id"],
                "verdict": verdict["verdict"],
                "reviewed_commit": claim["reviewed_commit"],
                "review_revision": 1,
                "findings": verdict["findings"],
            },
        )
        print(
            json.dumps(
                {
                    "reviewed": task_id,
                    "verdict": verdict["verdict"],
                    "status": submitted.get("status") or submitted.get("event"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    # ------------------------------------------------------------------ misc

    def _post_direct_message(self, recipient: str, body: str) -> None:
        """Post one private message; a reporting failure must not crash the loop."""
        if self.dry_run:
            return
        with contextlib.suppress(httpx.HTTPStatusError):
            self.client.post(
                f"/groups/{self.group_id}/messages",
                {
                    "author_kind": "harness",
                    "author_id": self.admin_harness_id,
                    "body": body,
                    "mentions": [recipient],
                    "direct": True,
                },
            )

    def _post_admin_message(self, body: str) -> None:
        if self.dry_run:
            return
        # A reporting failure must not hide the state transition failure.
        with contextlib.suppress(httpx.HTTPStatusError):
            self.client.post(
                f"/groups/{self.group_id}/messages",
                {
                    "author_kind": "harness",
                    "author_id": self.admin_harness_id,
                    "body": body,
                },
            )


def run_once(worker: AdminWorker) -> None:
    worker.poll_messages()
    worker.retry_failed_goal()
    worker.start_circulation()
    worker.resume_dispatch()
    worker.collect_circulation()
    # 分配治理（§39 硬规则 2）：人批准之后才开轮、开轮之后才裁决。
    # 放在 collect_circulation 之后：传阅没走完就没有可分配的快照。
    worker.open_allocation_and_decide()
    worker.reassign_dead_tasks()
    worker.review_awaiting()


def main() -> None:
    parser = argparse.ArgumentParser(description="administrator harness self-loop worker")
    parser.add_argument("--url", default="http://127.0.0.1:8848")
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--admin-harness-id", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replay-latest",
        action="store_true",
        help="on a fresh state file, process the newest historical project-goal once",
    )
    parser.add_argument(
        "--cancel-task",
        metavar="TASK_ID",
        default=None,
        help="one-shot: cancel this task as the administrator harness, then exit",
    )
    parser.add_argument("--reason", default="", help="reason recorded with --cancel-task")
    parser.add_argument(
        "--review-rounds",
        type=int,
        default=1,
        help=(
            "how many circulation rounds a plan must go through before it is"
            " finalised (§39: '已完成轮数达到配置轮数' is what freezes the snapshot);"
            " 2 means a second round read by a different list of members"
        ),
    )
    parser.add_argument(
        "--reassign-task",
        metavar="TASK_ID",
        default=None,
        help="one-shot: re-point this task to a window that can claim it, then exit",
    )
    parser.add_argument(
        "--to",
        metavar="HARNESS_ID",
        default=None,
        help="the window that takes over (with --reassign-task)",
    )
    args = parser.parse_args()

    if settings.auth_mode != "enforce":
        raise SystemExit("admin worker requires enforce authentication")
    token = args.token_file.read_text(encoding="utf-8").strip()
    state_path = args.state_file or (
        settings.data_dir / f"admin-worker-{args.admin_harness_id}-{args.group}.json"
    )
    state = WorkerState.load(state_path)
    database = Database()
    client = PlatformClient(args.url, token)
    if args.cancel_task:
        # 管理员自己取消：走 `/tasks/{id}/cancel`，事件里 actor_type 由服务端
        # 从令牌主体判定，记成 harness/<admin-harness-id>，不冒充人类。
        result = client.post(
            f"/tasks/{args.cancel_task}/cancel",
            {
                "decided_by": args.admin_harness_id,
                "reason": args.reason or "administrator harness cancelled the task",
            },
        )
        print(
            json.dumps({"cancelled": args.cancel_task, **result}, ensure_ascii=False),
            flush=True,
        )
        client.close()
        return
    if args.reassign_task:
        if not args.to:
            raise SystemExit("--reassign-task requires --to (the window that takes over)")
        worker = AdminWorker(
            client=client,
            database=database,
            group_id=args.group,
            admin_harness_id=args.admin_harness_id,
            state=state,
            state_path=state_path,
            dry_run=args.dry_run,
        )
        try:
            result = worker.reassign_task(args.reassign_task, args.to)
        finally:
            client.close()
        print(json.dumps({"reassigned": result}, ensure_ascii=False), flush=True)
        return
    worker = AdminWorker(
        client=client,
        database=database,
        group_id=args.group,
        admin_harness_id=args.admin_harness_id,
        state=state,
        state_path=state_path,
        dry_run=args.dry_run,
        replay_latest=args.replay_latest,
        review_rounds=args.review_rounds,
    )
    try:
        while True:
            try:
                run_once(worker)
            except httpx.HTTPError as exc:
                print(
                    json.dumps({"worker_error": type(exc).__name__}, ensure_ascii=False), flush=True
                )
                if args.once:
                    raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                print(
                    json.dumps(
                        {"worker_error": type(exc).__name__, "detail": str(exc)[:300]},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if args.once:
                    raise
            if args.once:
                return
            time.sleep(max(1.0, float(args.interval)))
    finally:
        client.close()


if __name__ == "__main__":
    main()
