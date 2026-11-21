"""The administrator worker is a runner, not a second scheduler.

These tests use a scripted administrator model and a fake HTTP client. They pin
the two places where the self-loop can silently turn back into "the human did
it": the dispatch body must carry the administrator's identity, and a model
failure must never produce a submitted review verdict.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from app.admin.experience import ExperienceStore
from app.admin.service import AdminHarnessError
from app.admin.worker import (
    ANSWER_RETRY_MAX_ATTEMPTS,
    AdminWorker,
    WorkerState,
    _goal_payload,
    run_once,
)
from app.git.repo import run_git


class FakeAdmin:
    def __init__(self, *, tasks=None, verdict=None, error=None):
        self.tasks = tasks or []
        self.verdict = verdict or {"verdict": "approve", "findings": []}
        self.error = error
        self.refine_calls = []
        self.revise_calls = []
        self.review_calls = []
        self.answer_calls = []
        self.assignable = ["member-window"]
        self.credentials = {"member-window": True, "admin-window": True}
        self.dispatched: set[str] = set()
        # harness_id -> 活着的租约数；用来把某个候选窗口造“忙”。
        self.busy: dict[str, int] = {}

    def assignable_harnesses(self):
        return list(self.assignable)

    def harness_capacity(self):
        rows = []
        for harness_id in self.assignable:
            active = int(self.busy.get(harness_id, 0))
            limit = 1
            rows.append(
                {
                    "harness_id": harness_id,
                    "active_leases": active,
                    "ready_tasks": 0,
                    "max_parallel_tasks": limit,
                    "load": active,
                    "available_slots": max(0, limit - active),
                    "idle": active == 0,
                    "at_capacity": active >= limit,
                    "earliest_available_at": ("2026-01-01T00:00:00+00:00" if active else None),
                }
            )
        return rows

    def unavailability_reasons(self, harness_id):
        if not self.credentials.get(harness_id, False):
            return ["no_credential"]
        row = next(
            (item for item in self.harness_capacity() if item["harness_id"] == harness_id),
            None,
        )
        if row is None:
            return ["not_registered"]
        if row["at_capacity"]:
            return [f"full ({row['active_leases']}/{row['max_parallel_tasks']})"]
        return []

    def earliest_available_at(self):
        stamps = [
            item["earliest_available_at"]
            for item in self.harness_capacity()
            if item["earliest_available_at"]
        ]
        return min(stamps) if stamps else None

    def harness_has_credential(self, harness_id):
        return bool(self.credentials.get(harness_id, False))

    def dispatched_task_ids(self, prefix="admin-"):
        return set(self.dispatched)

    def refine(self, **kwargs):
        self.refine_calls.append(kwargs)
        if self.error:
            raise self.error
        return {
            "status": "ok",
            "harness_id": "admin-window",
            "group_id": kwargs["group_id"],
            "project_name": kwargs.get("project_name"),
            "tasks": self.tasks,
        }

    def pool_protocol(self) -> str:
        """The protocol this administrator's CLI speaks to a model provider."""
        return "openai"

    def answer(self, **kwargs):
        self.answer_calls.append(kwargs)
        if self.error:
            raise self.error
        return {"answer": "答", "asker": kwargs.get("asker")}

    def revise_with_reviews(self, *, plan_id, goal, tasks, reviews, route=None):
        self.revise_calls.append(
            {"plan_id": plan_id, "goal": goal, "tasks": tasks, "reviews": reviews}
        )
        return list(tasks)

    def review(self, **kwargs):
        self.review_calls.append(kwargs)
        if self.error:
            raise self.error
        return self.verdict


class FakeClient:
    def __init__(
        self, *, tasks=None, context=None, diff=None, verification=None, inbox=None, roster=None
    ):
        self.tasks = tasks or []
        self.context = context or {"task": {}, "spec": {}}
        self.diff = diff or {"available": True, "patch": "diff --git a/a b/a\n+ok\n"}
        self.verification = verification or {"status": "passed"}
        self.inbox = inbox or []
        self.roster = (
            roster
            if roster is not None
            else [
                {"harness_id": "admin-window", "provider": "openai", "credential_count": 1},
                {"harness_id": "member-window", "provider": "tencent", "credential_count": 1},
                {"harness_id": "dead-window", "provider": "tencent", "credential_count": 0},
            ]
        )
        self.task_detail = {
            "task_id": "FD-1",
            "status": "abandoned",
            "owner": "member-window",
            "title": "t",
        }
        self.circulation_reviews: list[dict] = []
        self.dispatches = []
        self.plan_drafts = []
        self.plan_revisions = []
        self.completed_rounds = []
        self.closed_rounds = []
        self.claims = []
        self.submissions = []
        self.messages = []
        self.cancels = []
        self.merges = []

    def get(self, path, **params):
        if path.startswith("/allocation/circulation/"):
            return {
                "circulation_id": path.rsplit("/", 1)[-1],
                "state": "circulating",
                "reviews": self.circulation_reviews,
            }
        if path.endswith("/document"):
            return {
                "plan_id": "plan-1",
                "markdown": "# plan",
                "markdown_path": "exports/plans/plan-1.md",
            }
        if path.startswith("/tasks/") and not path.endswith("/diff"):
            return self.task_detail
        if path.endswith("/messages"):
            return {"messages": self.inbox}
        if path.endswith("/harnesses"):
            return {"harnesses": self.roster}
        if path.endswith("/tasks") and "groups/" in path:
            return {"tasks": self.tasks}
        if "/adapter/context/" in path:
            return self.context
        if path.endswith("/diff"):
            return self.diff
        if path.endswith("/verification"):
            return {"verification": self.verification}
        raise AssertionError(path)

    def post(self, path, body):
        if path.endswith("/admin/plan_draft"):
            self.plan_drafts.append(body)
            return {
                "plan_id": "plan-1",
                "snapshot_id": "snap-1",
                "circulation_id": "circ-1",
                "round_no": 1,
                "state": "circulating",
            }
        if path.endswith("/admin/plan_revise"):
            self.plan_revisions.append(body)
            return {
                "plan_id": body.get("plan_id"),
                "snapshot_id": "snap-2",
                "plan_hash": "sha256:x",
            }
        if path.endswith("/circulation/complete"):
            self.completed_rounds.append(body)
            return {"circulation_id": body.get("circulation_id"), "state": "completed"}
        if path.endswith("/circulation/close"):
            self.closed_rounds.append(body)
            return {"circulation_id": body.get("circulation_id"), "state": "revising"}
        if path.endswith("/dispatch"):
            self.dispatches.append(body)
            return {"task_id": f"FD-{len(self.dispatches)}"}
        if path.endswith("/cancel"):
            self.cancels.append(body)
            return {"status": "cancelled"}
        if path.endswith("/review/claim"):
            self.claims.append(body)
            return {"review_id": "rv-1", "reviewed_commit": "deadbeef"}
        if path.endswith("/review/submit"):
            self.submissions.append(body)
            return {"status": "approved"}
        # 审核通过后管理员自己发起合入（R10-fix-15）。
        if path.endswith("/human/merge"):
            self.merges.append(body)
            return {"status": "merged", "merge_commit": "c0ffee"}
        if path.endswith("/messages"):
            self.messages.append(body)
            return {"message_id": "m-1"}
        raise AssertionError(path)


def _worker(tmp_path, monkeypatch, *, admin, client, dry_run=False, database=None):
    monkeypatch.setattr("app.admin.worker.AdminService", lambda *a, **k: admin)
    state_path = tmp_path / "admin-worker.json"
    return AdminWorker(
        client=client,
        database=database,
        group_id="G-1",
        admin_harness_id="admin-window",
        state=WorkerState(),
        state_path=state_path,
        experience=ExperienceStore(tmp_path / "experience.json"),
        dry_run=dry_run,
    )


class AllocationClient(FakeClient):
    """一轮分配：轮已收，候选在册，只回「裁决成功」。"""

    def __init__(self):
        super().__init__()
        self.decisions: list[dict] = []

    def get(self, path, **params):
        if path == "/allocation/rounds":
            return {
                "rounds": [
                    {
                        "round_id": "alloc-1",
                        "state": "decided",
                        "revision": 3,
                        "assignments": [],
                    }
                ]
            }
        if path == "/allocation/candidates":
            return {
                "candidates": [{"application_id": "app-1", "harness_id": "m-1", "state": "frozen"}]
            }
        return super().get(path, **params)

    def post(self, path, body):
        if path.endswith("/allocation/decision"):
            self.decisions.append(body)
            return {"state": "decided"}
        return super().post(path, body)


def test_the_allocation_state_is_cleared_when_the_round_is_fully_decided(tmp_path, monkeypatch):
    """一轮分配裁完，就把 pending 清掉 —— 否则平台自己卡死。

    2026-10-29 的真实演练：管理员把一轮的任务全裁完了，``pending_allocation``
    却还指着那一轮。于是它永远不去给**下一个**已批准的方案开分配轮：新方案的活
    停在 ready，成员没有轮可申请，没人开工，群里也不出声 —— 平台静默地僵住。
    """
    admin = FakeAdmin()
    client = AllocationClient()
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.pending_allocation = {
        "plan_id": "plan-1",
        "plan_snapshot_id": "snap-1",
        "round_id": "alloc-1",
        "opened_at": 0.0,
        "decided": [],
        "tasks": [{"id": "FD-1", "title": "t", "assigned_harness_ids": ["m-1"]}],
    }

    worker._decide_allocation_tasks("alloc-1", worker.state.pending_allocation)

    assert client.decisions, "the task was decided"
    assert worker.state.pending_allocation is None, "the finished round is not carried over"


def test_a_reassign_request_makes_the_administrator_re_point_the_task(tmp_path, monkeypatch):
    """The office *asks*; the administrator decides and does it.

    The request is a message with a ``reassign-request`` ref: the office never
    dispatches, and the administrator re-points the task with its own
    credential.
    """
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 7,
                "sender_type": "human",
                "body": "please re-point this task",
                "task_id": "FD-1",
                "refs": ["reassign-request", "reassign-to:member-window"],
            }
        ],
        tasks=[{"task_id": "FD-1", "title": "t", "assigned_harness_ids": ["member-window"]}],
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert len(client.dispatches) == 1
    body = client.dispatches[0]
    assert body["harness_id"] == "member-window"
    assert body["decided_by"] == "admin-window"
    # The request id is unique per re-point: the count alone would replay an
    # old dispatch after a lost state file.
    assert body["request_id"].endswith("-7")
    # The same request must not re-point the task twice.
    worker.poll_messages()
    assert len(client.dispatches) == 1


def test_a_reassign_request_for_a_moving_task_is_ignored(tmp_path, monkeypatch):
    """Nothing to re-point: the work is already moving."""
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 9,
                "sender_type": "human",
                "body": "please re-point this task",
                "task_id": "FD-1",
                "refs": ["reassign-request"],
            }
        ],
    )
    client.task_detail = {"task_id": "FD-1", "status": "in_progress", "owner": "member-window"}
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert client.dispatches == []


def test_goal_envelope_can_ride_in_refs_so_chat_stays_readable():
    payload = _goal_payload(
        {
            "author_kind": "human",
            "created_seq": 7,
            "body": "项目目标：补一条说明",
            "refs": [
                "project-goal",
                'project-goal-json:{"goal_id":"g-1","goal":"补一条说明",'
                '"project_name":"DEMO","members":["m-1"],"max_tasks":2,'
                '"admin":"admin-window"}',
            ],
        }
    )
    assert payload["goal_id"] == "g-1"
    assert payload["project_name"] == "DEMO"
    assert payload["members"] == ["m-1"]
    assert payload["max_tasks"] == 2
    assert payload["admin"] == "admin-window"


def test_goal_for_another_admin_is_not_dispatched(tmp_path, monkeypatch):
    admin = FakeAdmin(
        tasks=[
            {
                "title": "DEMO · x",
                "goal": "x",
                "exclusive_paths": ["a"],
                "acceptance": "x",
                "harness_id": "member-1",
            }
        ]
    )
    client = FakeClient()
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.handle_goal(
        {
            "goal_id": "g-other",
            "goal": "补一条说明",
            "project_name": "DEMO",
            "members": ["member-1"],
            "max_tasks": 1,
            "answers": [],
            "admin": "someone-else-window-admin",
            "seq": 8,
        },
        force=True,
    )
    assert admin.refine_calls == []
    assert client.dispatches == []


class PoolClient(FakeClient):
    """管理员窗口走额度池：/pool/plan 返回一个池模型，让 refine 的 route 落地。"""

    base_url = "http://127.0.0.1:8848/api/v1"
    token = "fake-token"

    def get(self, path, **params):
        if path == "/pool/plan":
            return {
                "mode": "pool",
                "model": "qoder/Qwen3.8-Flash",
                "base_path": "/pool/v1",
                "anthropic_path": "/pool",
                "upstream": "qoder",
            }
        return super().get(path, **params)


class FakeMessageDatabase:
    """只实现 worker 用到的那一件事：按 created_seq 倒序返回本群消息正文。"""

    def __init__(self, rows):
        self._rows = sorted(rows, key=lambda row: int(row["created_seq"]), reverse=True)

    def read(self, sql, parameters=()):
        return [dict(row) for row in self._rows]


def test_a_first_refine_failure_names_the_pool_model(tmp_path, monkeypatch):
    """细化第一次失败：报模型是谁，但还说不上「连着失败」。"""
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = PoolClient()
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.handle_goal(
        {
            "goal_id": "g-1",
            "goal": "补一条说明",
            "project_name": "DEMO",
            "members": ["member-1"],
            "max_tasks": 1,
            "answers": [],
            "admin": "admin-window",
            "seq": 8,
        },
        force=True,
    )

    assert len(client.messages) == 1
    body = client.messages[0]["body"]
    assert "管理员 Harness 细化失败" in body
    assert "（池模型 qoder/Qwen3.8-Flash）" in body
    assert "已连续失败" not in body


def test_repeated_refine_failures_tell_the_owner_to_switch_models(tmp_path, monkeypatch):
    """连续失败不再怪「通道」：管理员窗口的池模型没有自动换道，得换模型。

    平台只会给成员任务做失败换道（pool/routing.py 按 task_id 找证据）；管理员
    窗口的 route 是人配的固定池模型。所以连着失败时，真话是「换个池模型」，
    不是「等通道恢复」。
    """
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = PoolClient()
    database = FakeMessageDatabase(
        [
            {"created_seq": 1, "body": "有人说话，不是失败报告"},
            {"created_seq": 2, "body": "管理员 Harness 细化失败：旧的一次；未分派任何任务。"},
            {"created_seq": 3, "body": "管理员 Harness 细化失败：更新的一次；未分派任何任务。"},
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client, database=database)

    worker.handle_goal(
        {
            "goal_id": "g-1",
            "goal": "补一条说明",
            "project_name": "DEMO",
            "members": ["member-1"],
            "max_tasks": 1,
            "answers": [],
            "admin": "admin-window",
            "seq": 8,
        },
        force=True,
    )

    assert len(client.messages) == 1
    body = client.messages[0]["body"]
    assert "已连续失败 3 次" in body
    assert "（池模型 qoder/Qwen3.8-Flash）" in body
    assert "请给管理员窗口换一个池模型" in body


def test_every_member_reads_every_round(tmp_path, monkeypatch):
    """每一轮传阅，**每个**成员都要审一遍；两轮之间变的是方案，不是人。

    群主定的流程是：方案传下去，所有成员各审一遍并把意见写在下面，意见汇总给
    管理员整理成完整方案；管理员再把**新的**方案传下去，所有成员再各审一遍。
    所以第二轮的读者名单必须和第一轮一样 —— 少一个人，就等于有人没机会回应自己
    上一轮提的意见。
    """
    admin = FakeAdmin(
        tasks=[
            {
                "title": "DEMO · 写作品页",
                "goal": "写一张作品页",
                "exclusive_paths": ["portfolio/index.html"],
                "acceptance": "x",
                "harness_id": "member-a",
            }
        ]
    )
    admin.assignable = ["member-a", "member-b", "member-c"]
    client = FakeClient()
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.handle_goal(
        {
            "goal_id": "g-1",
            "goal": "写一张作品页",
            "project_name": "DEMO",
            "members": ["member-a", "member-b", "member-c"],
            "max_tasks": 1,
            "answers": [],
            "admin": "admin-window",
            "seq": 21,
        },
        force=True,
    )

    # 草案开出来时，读者是**全部**成员 —— 不只是方案点名的那个执行窗口。
    drafted = list(client.plan_drafts)
    assert drafted, "the administrator did not draft a plan"
    assert drafted[0]["reviewer_harness_ids"] == ["member-a", "member-b", "member-c"]

    # 第二轮：同一批人再审一遍（变的是方案）。
    assert worker._next_circulation_readers(["member-a", "member-b", "member-c"]) == [
        "member-a",
        "member-b",
        "member-c",
    ]


def test_a_cancel_notice_makes_the_administrator_refine_that_part(tmp_path, monkeypatch):
    """人取消了一条活 → 管理员就**这一部分**重新细化。

    范围要窄：被取消那条的工单原文就是要求，重新细化整个目标会把还在做的活
    再派一遍。细化结果照旧走草案 → 传阅 → 人审，不直接派单。
    """
    admin = FakeAdmin(
        tasks=[
            {
                "title": "写对照说明",
                "goal": "写一份对照说明",
                "exclusive_paths": ["docs/x.md"],
                "acceptance": "docs/x.md exists",
                "harness_id": "member-window",
            }
        ]
    )
    admin.assignable = ["member-window"]
    client = FakeClient(
        inbox=[
            {
                "created_seq": 11,
                "sender_type": "human",
                "body": "不做了：方向不对",
                "task_id": "FD-9",
                "refs": [
                    "to-admin",
                    "task-cancelled",
                    "task-cancelled:FD-9",
                    'task-cancelled-json:{"task_id":"FD-9","title":"写对照说明","reason":"方向不对"}',
                ],
            }
        ]
    )
    client.task_detail = {
        "task_id": "FD-9",
        "status": "cancelled",
        "title": "写对照说明",
        "spec": {"goal": "写一份对照说明"},
    }
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert len(admin.refine_calls) == 1
    requirement = admin.refine_calls[0]["requirement"]
    assert "写一份对照说明" in requirement, "要求就是被取消那条自己的工单原文"
    assert "方向不对" in requirement, "取消的原因也给它"
    assert admin.refine_calls[0]["max_tasks"] == 1, "只细化这一部分"
    # 照旧走草案 → 传阅，不直接派单。
    assert client.dispatches == []
    assert len(client.plan_drafts) == 1
    assert client.plan_drafts[0]["opened_by"] == "admin-window"


def test_a_cancel_notice_without_a_work_order_is_not_guessed(tmp_path, monkeypatch):
    """读不到工单原文就**不猜**范围：明说，让人再说一次要做什么。"""
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 12,
                "sender_type": "human",
                "body": "不做了：方向不对",
                "task_id": "FD-9",
                "refs": [
                    "task-cancelled",
                    'task-cancelled-json:{"task_id":"FD-9","title":"写对照说明","reason":"方向不对"}',
                ],
            }
        ]
    )
    client.task_detail = {"task_id": "FD-9", "status": "cancelled", "title": "写对照说明"}
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert admin.refine_calls == [], "没有工单原文就不细化"
    assert client.plan_drafts == []
    assert any("工单原文" in str(message.get("body", "")) for message in client.messages)


def test_goal_handler_circulates_the_plan_instead_of_dispatching_it(tmp_path, monkeypatch):
    """After the refinement the plan is **circulated**, not dispatched.

    Nothing may be assigned before the readers have had their say and a human
    has approved the final plan.
    """
    admin = FakeAdmin(
        tasks=[
            {
                "title": "DEMO - write a note",
                "goal": "write docs/demo.md",
                "exclusive_paths": ["docs/demo.md"],
                "acceptance": "docs/demo.md says it",
                "harness_id": "member-1",
            }
        ]
    )
    admin.assignable = ["member-window"]
    client = FakeClient()
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.handle_goal(
        {
            "goal_id": "g-1",
            "goal": "write a note",
            "project_name": "DEMO",
            "members": ["member-1"],
            "max_tasks": 2,
            "answers": [],
            "seq": 7,
        },
        force=True,
    )
    # No task is dispatched: the plan is a draft and the readers have not
    # answered yet.
    assert client.dispatches == []
    assert len(client.plan_drafts) == 1
    assert client.plan_drafts[0]["opened_by"] == "admin-window"
    assert client.plan_drafts[0]["reviewer_harness_ids"] == ["member-window"]
    assert admin.refine_calls[0]["members"] == ["member-1"]
    assert admin.refine_calls[0]["project_name"] == "DEMO"


def test_the_plan_is_finalised_for_a_human_once_the_readers_answered(tmp_path, monkeypatch):
    """The comments are folded in, the round is closed, the document is written."""
    admin = FakeAdmin()
    admin.assignable = ["member-window"]
    client = FakeClient()
    client.circulation_reviews = [
        {"harness_id": "member-window", "body": "write the acceptance clearer"}
    ]
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.pending_circulation = {
        "goal_seq": 7,
        "plan_id": "plan-1",
        "circulation_id": "circ-1",
        "goal": "g",
        "project_name": "DEMO",
        "tasks": [{"title": "t", "harness_id": "member-window"}],
        "reviewers": ["member-window"],
    }

    worker.collect_circulation()

    assert len(client.plan_revisions) == 1
    # The round's state machine: close (once every reader answered) -> complete.
    assert client.closed_rounds and client.closed_rounds[0]["circulation_id"] == "circ-1"
    assert client.completed_rounds and client.completed_rounds[0]["circulation_id"] == "circ-1"
    # Still no dispatch: the human approves the plan.
    assert client.dispatches == []
    assert worker.state.pending_circulation is None


def test_only_the_owner_reply_is_fed_back_as_an_answer(tmp_path, monkeypatch):
    admin = FakeAdmin(
        tasks=[
            {
                "title": "DEMO · x",
                "goal": "x",
                "exclusive_paths": ["a"],
                "acceptance": "x",
                "harness_id": "member-1",
            }
        ]
    )
    client = FakeClient(
        inbox=[
            {"created_seq": 11, "author_kind": "harness", "body": "管理员回执，不是回答"},
            {"created_seq": 12, "author_kind": "human", "body": "用中文写"},
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 10
    worker.state.pending_goal = {
        "goal_id": "g-1",
        "goal": "补一条说明",
        "project_name": "DEMO",
        "members": ["member-1"],
        "max_tasks": 1,
        "answers": [],
        "seq": 10,
        "admin": "admin-window",
    }
    worker.poll_messages()
    assert admin.refine_calls[0]["answers"] == ["用中文写"]


def test_an_assistant_relay_is_answered_to_the_asker_not_to_the_assistant(tmp_path, monkeypatch):
    """助手代转达的请求，回话要送给**提问人**。

    助手不能冒用人的身份发消息（平台的作者必须等于令牌主体），所以转达
    的那条消息作者是助手。管理员若按"作者是谁就回给谁"处理，这句话就
    掉进助手的私聊里 —— 在办公室等回话的人永远等不到。
    """
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 9,
                "sender_type": "harness",
                "sender_id": "office-assistant",
                "body": "替用户问：这条目标还要不要调整？",
                "refs": [
                    "assistant-relay",
                    'assistant-relay-json:{"asker": "office-operator"}',
                    "direct",
                ],
            }
        ],
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert len(admin.answer_calls) == 1
    call = admin.answer_calls[0]
    assert call["asker"] == "office-operator", "回话要送给提问人，不是送给助手"
    assert call["question"] == "替用户问：这条目标还要不要调整？"
    # 同一条转达不许被回两遍。
    worker.poll_messages()
    assert len(admin.answer_calls) == 1


def test_a_relay_without_an_asker_on_record_is_not_answered(tmp_path, monkeypatch):
    """没有记录提问人的转达，回给谁都是回给空气 —— 如实跳过。"""
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 9,
                "sender_type": "harness",
                "sender_id": "office-assistant",
                "body": "有人问了一件事",
                "refs": ["assistant-relay", "direct"],
            }
        ],
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert admin.answer_calls == []


def test_poll_messages_reads_the_apis_sender_type_column(tmp_path, monkeypatch):
    """The read API exposes ``sender_type``, not the write field ``author_kind``.

    Reading only ``author_kind`` made a real owner goal look like non-human
    chatter: the worker advanced last_message_seq and never dispatched.
    """
    admin = FakeAdmin(
        tasks=[
            {
                "title": "DEMO · x",
                "goal": "x",
                "exclusive_paths": ["a"],
                "acceptance": "x",
                "harness_id": "member-1",
            }
        ]
    )
    client = FakeClient(
        inbox=[
            {
                "created_seq": 7,
                "sender_type": "human",
                "sender_id": "owner",
                "body": "项目目标：补一条说明",
                "refs": [
                    "project-goal",
                    'project-goal-json:{"goal_id":"g-1","goal":"补一条说明",'
                    '"project_name":"DEMO","members":["member-1"],"max_tasks":1,'
                    '"admin":"admin-window"}',
                ],
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0
    worker.poll_messages()
    assert len(admin.refine_calls) == 1
    # The plan is circulated, not dispatched: the human approves it later.
    assert client.dispatches == []
    assert client.plan_drafts and client.plan_drafts[0]["opened_by"] == "admin-window"


def test_review_claim_and_submit_use_the_model_verdict(tmp_path, monkeypatch):
    admin = FakeAdmin(verdict={"verdict": "approve", "findings": []})
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-1",
                # Raw task rows expose ``current_attempt_id``; the gateway
                # projects it as ``attempt_id``. The worker must accept both.
                "current_attempt_id": "FD-1.a1",
                "title": "DEMO · 补一条说明",
                "status": "awaiting_review",
                "assigned_harness_ids": ["member-1"],
            }
        ],
        context={"task": {"task_id": "FD-1"}, "spec": {"id": "FD-1"}},
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    run_once(worker)
    assert client.claims[0]["reviewer_harness_id"] == "admin-window"
    assert client.submissions[0]["verdict"] == "approve"
    assert client.submissions[0]["reviewed_commit"] == "deadbeef"
    assert admin.review_calls[0]["project_name"] == "DEMO"
    records = worker.experience.records()
    assert len(records) == 1 and records[0]["kind"] == "merged"
    assert records[0]["project"] == "DEMO"
    assert records[0]["contract_id"] == "FD-1"
    assert records[0]["diff_hash"] == "deadbeef"
    assert records[0]["action"] and records[0]["result"]
    assert records[0]["scope"] == "project"


def test_prompt_failure_revises_the_goal_and_redispatches_through_admin_routes(
    tmp_path, monkeypatch
):
    verdict = {
        "verdict": "request_changes",
        "findings": [{"message": "目标漏了保留入口的要求"}],
        "failure_class": "prompt",
        "correction": "补全目标后重派",
        "revised_goal": "补一条说明，并保留既有入口",
    }
    admin = FakeAdmin(verdict=verdict)
    task = {
        "task_id": "FD-1",
        "attempt_id": "FD-1.a1",
        "owner": "member-window",
        "title": "DEMO · 补一条说明",
        "status": "awaiting_review",
        "assigned_harness_ids": ["member-window"],
    }
    client = FakeClient(
        tasks=[task],
        context={
            "spec": {
                "title": task["title"],
                "goal": "补一条说明",
                "exclusive_paths": ["docs/demo.md"],
                "acceptance": "说明存在且既有入口未删除",
            }
        },
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker._review_one(task, "FD-1.a1")

    assert client.submissions[0]["verdict"] == "request_changes"
    assert client.dispatches[0]["goal"] == verdict["revised_goal"]
    assert client.dispatches[0]["acceptance"] == "说明存在且既有入口未删除"
    assert client.dispatches[0]["exclusive_paths"] == ["docs/demo.md"]
    assert client.dispatches[0]["decided_by"] == "admin-window"
    assert any("提示词问题" in message["body"] for message in client.messages)
    records = worker.experience.records()
    assert len(records) == 1 and records[0]["kind"] == "request_changes"
    assert records[0]["task_category"] == "docs"
    assert records[0]["project"] == "DEMO"
    assert records[0]["root_cause"].startswith("验收未通过（prompt）")
    assert records[0]["action"] == "补全目标后重派"
    assert records[0]["contract_id"] == "FD-1"
    assert records[0]["diff_hash"] == "deadbeef"


def test_prompt_repair_runs_through_real_api_sqlite_and_git(tmp_path, monkeypatch):
    """实跑边界：真 FastAPI/SQLite/Git，只有管理员模型判定用脚本替身。"""
    from fastapi.testclient import TestClient

    from app.api.app import create_app
    from app.auth.tokens import TokenService
    from app.config import settings
    from tests.api_support import (
        advance_to_awaiting_review,
        scheduler,
        worktree_manager,
    )
    from tests.merge_support import build_chain, register_harness

    chain = build_chain(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({"admin-window"}))
    register_harness(chain.database, "admin-window", provider="qoder")
    register_harness(chain.database, "member-window", provider="tencent")
    tokens = TokenService(chain.database)
    admin_token = tokens.issue(subject_type="harness", subject_id="admin-window")
    member_token = tokens.issue(subject_type="harness", subject_id="member-window")
    human_token = tokens.issue(subject_type="human", subject_id="office-operator")
    api = TestClient(create_app(chain.database, chain.repo), base_url="http://127.0.0.1")

    def post(path, body, token):
        return api.post(path, json=body, headers={"Authorization": f"Bearer {token}"})

    assert (
        post("/api/v1/projects", {"project_id": "P-live", "name": "DEMO"}, human_token).status_code
        == 200
    )
    assert (
        post(
            "/api/v1/projects/P-live/groups", {"group_id": "G-live", "name": "g"}, human_token
        ).status_code
        == 200
    )
    dispatched = post(
        "/api/v1/groups/G-live/dispatch",
        {
            "request_id": "admin-live-1",
            "title": "DEMO · 补一条说明",
            "goal": "补一条说明",
            "harness_id": "member-window",
            "decided_by": "admin-window",
            "exclusive_paths": ["work.txt"],
            "acceptance": "work.txt 存在且包含任务 id",
        },
        admin_token,
    )
    assert dispatched.status_code == 200, dispatched.text
    task_id = dispatched.json()["task_id"]
    claimed = post(
        "/api/v1/adapter/claim",
        {
            "claim_request_id": "claim-live-1",
            "harness_id": "member-window",
            "task_ids": [task_id],
        },
        member_token,
    )
    assert claimed.status_code == 200, claimed.text
    attempt = claimed.json()["items"][0]
    manager = worktree_manager(tmp_path, chain.database)
    scheduler(chain.database).start_task(
        task_id=task_id,
        attempt_id=attempt["attempt_id"],
        fencing_epoch=attempt["fencing_epoch"],
        worktrees=manager,
    )
    advance_to_awaiting_review(
        api,
        chain.database,
        tmp_path,
        task_id=task_id,
        attempt_id=attempt["attempt_id"],
        fencing_epoch=attempt["fencing_epoch"],
        manager=manager,
        headers={"Authorization": f"Bearer {member_token}"},
    )

    class LiveClient:
        def get(self, path, **params):
            response = api.get(
                "/api/v1" + path,
                params=params or None,
                headers={"Authorization": f"Bearer {admin_token}"},
            )
            assert response.status_code < 400, response.text
            return response.json()

        def post(self, path, body):
            response = post("/api/v1" + path, body, admin_token)
            assert response.status_code < 400, response.text
            return response.json()

    verdict = {
        "verdict": "request_changes",
        "findings": [{"message": "目标漏了任务 id 要求"}],
        "failure_class": "prompt",
        "correction": "补全目标后重派",
        "revised_goal": "补一条说明，内容必须包含任务 id",
    }
    worker = AdminWorker(
        client=LiveClient(),
        database=chain.database,
        group_id="G-live",
        admin_harness_id="admin-window",
        state=WorkerState(),
        state_path=tmp_path / "worker.json",
        experience=ExperienceStore(tmp_path / "admin" / "experience.json"),
    )
    worker.admin = FakeAdmin(verdict=verdict)
    task = LiveClient().get(f"/tasks/{task_id}")

    worker._review_one(task, attempt["attempt_id"])

    remaining = LiveClient().get("/groups/G-live/tasks")["tasks"]
    new_task_id = next(row["task_id"] for row in remaining if row["task_id"] != task_id)
    new_context = LiveClient().get(f"/adapter/context/{new_task_id}")
    assert new_context["spec"]["goal"] == verdict["revised_goal"]
    assert new_context["spec"]["acceptance"][0]["description"] == "work.txt 存在且包含任务 id"
    assert LiveClient().get(f"/tasks/{task_id}")["status"] == "cancelled"
    # 同一真实验收结果若因重投再次落账，必须合并计数而不是增加第二条经验。
    head_commit = chain.database.read(
        "SELECT head_commit FROM task_attempts WHERE attempt_id = ?", (attempt["attempt_id"],)
    )[0]["head_commit"]
    worker._record_experience(
        task,
        LiveClient().get(f"/adapter/context/{task_id}")["spec"],
        merged=False,
        verdict=verdict,
        diff_hash=str(head_commit),
    )
    records = worker.experience.records()
    assert len(records) == 1 and records[0]["kind"] == "request_changes"
    assert records[0]["occurrence_count"] == 2

    # 同项目、同类别的第二条任务只召回陈列；返回本身不含任何提示词注入动作。
    recalled = LiveClient().get(f"/tasks/{new_task_id}/experiences")
    assert recalled["presentation_only"] is True
    assert recalled["prompt_injection"] == "disabled"
    assert recalled["project"] == "DEMO" and recalled["task_category"] == "docs"
    assert len(recalled["experiences"]) == 1
    assert recalled["experiences"][0]["contract_id"] == task_id
    assert recalled["experiences"][0]["occurrence_count"] == 2


def test_abandon_writes_experience_through_real_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.app import create_app
    from app.auth.tokens import TokenService
    from app.config import settings
    from tests.api_support import scheduler, worktree_manager
    from tests.merge_support import build_chain, register_harness

    chain = build_chain(tmp_path)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "auth_mode", "enforce")
    monkeypatch.setattr(settings, "admin_subjects", frozenset({"admin-window"}))
    register_harness(chain.database, "admin-window", provider="qoder")
    register_harness(chain.database, "member-window", provider="tencent")
    tokens = TokenService(chain.database)
    admin_token = tokens.issue(subject_type="harness", subject_id="admin-window")
    member_token = tokens.issue(subject_type="harness", subject_id="member-window")
    human_token = tokens.issue(subject_type="human", subject_id="office-operator")
    api = TestClient(create_app(chain.database, chain.repo), base_url="http://127.0.0.1")

    def post(path, body, token):
        response = api.post(path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200, response.text
        return response.json()

    post("/api/v1/projects", {"project_id": "P-live", "name": "DEMO"}, human_token)
    post("/api/v1/projects/P-live/groups", {"group_id": "G-live", "name": "g"}, human_token)
    dispatched = post(
        "/api/v1/groups/G-live/dispatch",
        {
            "request_id": "admin-abandon-1",
            "title": "DEMO · 放弃路径经验",
            "goal": "补一条说明",
            "harness_id": "member-window",
            "decided_by": "admin-window",
            "exclusive_paths": ["abandon.txt"],
            "acceptance": "abandon.txt 存在",
        },
        admin_token,
    )
    task_id = dispatched["task_id"]
    claimed = post(
        "/api/v1/adapter/claim",
        {
            "claim_request_id": "claim-abandon-1",
            "harness_id": "member-window",
            "task_ids": [task_id],
        },
        member_token,
    )
    attempt = claimed["items"][0]
    scheduler(chain.database).start_task(
        task_id=task_id,
        attempt_id=attempt["attempt_id"],
        fencing_epoch=attempt["fencing_epoch"],
        worktrees=worktree_manager(tmp_path, chain.database),
    )

    abandoned = post(
        "/api/v1/adapter/abandon",
        {
            "task_id": task_id,
            "attempt_id": attempt["attempt_id"],
            "fencing_epoch": attempt["fencing_epoch"],
            "reason": "执行环境缺少必要依赖",
        },
        member_token,
    )

    assert abandoned["experience_recorded"] is True
    (record,) = ExperienceStore(tmp_path / "admin" / "experience.json").records()
    assert record["kind"] == "abandoned"
    assert record["root_cause"] == "执行环境缺少必要依赖"
    assert record["contract_id"] == task_id
    assert record["executor"] == "member-window"
    response = api.get(
        f"/api/v1/tasks/{task_id}/experiences",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["experiences"][0]["kind"] == "abandoned"


def test_spec_failure_waits_for_owner_reapproval_instead_of_rewriting_acceptance(
    tmp_path, monkeypatch
):
    admin = FakeAdmin(
        verdict={
            "verdict": "request_changes",
            "findings": [{"message": "验收与目标冲突"}],
            "failure_class": "spec",
            "correction": "请群主重审验收条款",
        }
    )
    task = {
        "task_id": "FD-1",
        "attempt_id": "FD-1.a1",
        "owner": "member-window",
        "title": "DEMO · 补一条说明",
        "status": "awaiting_review",
        "assigned_harness_ids": ["member-window"],
    }
    client = FakeClient(tasks=[task])
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker._review_one(task, "FD-1.a1")

    assert client.dispatches == []
    assert any("未自动改写" in message["body"] for message in client.messages)


def test_model_failure_never_submits_a_review(tmp_path, monkeypatch):
    admin = FakeAdmin(error=AdminHarnessError("model unavailable"))
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-1",
                "attempt_id": "FD-1.a1",
                "title": "DEMO · 补一条说明",
                "status": "awaiting_review",
                "assigned_harness_ids": ["member-1"],
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    run_once(worker)
    run_once(worker)
    # The model is asked before the review lease is claimed; a failed model call
    # must never become a submitted verdict (and must not hold a lease). The
    # local cooldown keeps the next poll from re-running the same model call.
    assert len(admin.review_calls) == 1
    assert client.claims == []
    assert client.submissions == []
    assert any("未提交审核结论" in message["body"] for message in client.messages)


def test_a_same_provider_refusal_is_reported_to_the_owner_once(tmp_path, monkeypatch):
    """同厂商拒收不是可重试的错误：要么人开通道，要么一直停在待审。

    重试循环会一直撞同一堵墙 —— 群里反复刷同一句没有信息量，而群主始终不知道
    要去开通道。所以按任务只报一次，并写明要调哪个接口。
    """

    class SameProviderRefusal(FakeClient):
        def post(self, path, body):
            if path.endswith("/review/claim"):
                request = httpx.Request("POST", "http://127.0.0.1" + path)
                response = httpx.Response(
                    409,
                    request=request,
                    json={
                        "error": {
                            "code": "review.cross_provider_required",
                            "message": "reviewer provider 'openai' equals the owner's",
                        }
                    },
                )
                raise httpx.HTTPStatusError("409", request=request, response=response)
            return super().post(path, body)

    admin = FakeAdmin(verdict={"verdict": "approve", "findings": []})
    client = SameProviderRefusal(
        tasks=[
            {
                "task_id": "FD-1",
                "current_attempt_id": "FD-1.a1",
                "title": "DEMO · 补一条说明",
                "status": "awaiting_review",
                "assigned_harness_ids": ["member-1"],
            }
        ],
        context={"task": {"task_id": "FD-1"}, "spec": {"id": "FD-1"}},
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    run_once(worker)
    worker._review_cooldowns.clear()  # 冷却期不是这条断言要测的东西
    run_once(worker)

    told = [m["body"] for m in client.messages if "review_same_provider" in m["body"]]
    assert len(told) == 1
    assert "FD-1" in told[0]
    assert client.submissions == []


def test_admin_never_reviews_its_own_task_even_with_the_raw_json_column(tmp_path, monkeypatch):
    admin = FakeAdmin()
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-1",
                "current_attempt_id": "FD-1.a1",
                "title": "DEMO · 管理员自己的任务",
                "status": "awaiting_review",
                "assigned_harness_ids_json": '["admin-window"]',
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    run_once(worker)
    assert admin.review_calls == []
    assert client.claims == []
    assert client.submissions == []


def test_a_task_whose_executor_cannot_claim_is_re_pointed(tmp_path, monkeypatch):
    """执行者没有凭据 → 管理员改派给能领活的窗口。

    实测：管理员把两个子任务派给了 ``codebuddy`` / ``qoder``（登记在册但没有
    worker 令牌），那两条活永远停在 ``ready``。改派 = 取消旧的 + 派一条同样规格
    的新任务给能领活的窗口，两步都是既有动作，各自留痕。
    """
    admin = FakeAdmin()
    admin.credentials = {"member-window": True, "admin-window": True, "dead-window": False}
    admin.dispatched = {"FD-dead"}
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-dead",
                "status": "ready",
                "assigned_harness_ids": ["dead-window"],
                "title": "写一份说明",
            }
        ],
        context={
            "task": {"task_id": "FD-dead"},
            "spec": {
                "id": "FD-dead",
                "title": "写一份说明",
                "goal": "写一份说明",
                "exclusive_paths": ["docs/说明.md"],
                "acceptance": [{"id": "A-1", "check": "human", "description": "说明写清楚"}],
            },
        },
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.reassign_dead_tasks()

    # 旧任务被取消（管理员身份，不是人类）
    assert len(client.cancels) == 1
    assert client.cancels[0]["decided_by"] == "admin-window"
    # 新任务派给了能领活的窗口，规格原样保留
    assert len(client.dispatches) == 1
    dispatched = client.dispatches[0]
    assert dispatched["harness_id"] == "member-window"
    assert dispatched["title"] == "写一份说明"
    assert dispatched["exclusive_paths"] == ["docs/说明.md"]
    assert dispatched["acceptance"] == "说明写清楚"
    assert dispatched["decided_by"] == "admin-window"
    # 群里说清楚为什么改派
    assert client.messages
    assert "改派" in client.messages[0]["body"]


def test_a_busy_member_is_skipped_in_favour_of_an_idle_one(tmp_path, monkeypatch, capsys):
    """忙碌成员（有活租约）不接新活；同样能领活时优先挑真正空闲的那个。

    实测踩过：只按 ``ready`` 数排队，结果把一个已经挂着租约的窗口当成空闲，
    新活派过去就排在那条没跑完的任务后面。
    """
    admin = FakeAdmin()
    admin.assignable = ["busy-member", "idle-member"]
    admin.credentials = {
        "busy-member": True,
        "idle-member": True,
        "dead-window": False,
    }
    admin.busy = {"busy-member": 1}
    admin.dispatched = {"FD-dead"}
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-dead",
                "status": "ready",
                "assigned_harness_ids": ["dead-window"],
                "title": "写一份说明",
            }
        ],
        context={
            "task": {"task_id": "FD-dead"},
            "spec": {
                "id": "FD-dead",
                "title": "写一份说明",
                "goal": "写一份说明",
                "exclusive_paths": ["docs/说明.md"],
                "acceptance": [{"id": "A-1", "check": "human", "description": "说明写清楚"}],
            },
        },
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.reassign_dead_tasks()

    assert [item["harness_id"] for item in client.dispatches] == ["idle-member"]
    assert client.cancels, "改派必须真的发生了"
    assert "busy-member" not in json.dumps(client.dispatches, ensure_ascii=False)


def test_no_idle_member_means_no_new_task_and_an_explicit_refusal(tmp_path, monkeypatch, capsys):
    """全忙时不创建新任务，并把「最早什么时候有空」说清楚。"""
    admin = FakeAdmin()
    admin.assignable = ["busy-member"]
    admin.credentials = {"busy-member": True, "dead-window": False}
    admin.busy = {"busy-member": 1}
    admin.dispatched = {"FD-dead"}
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-dead",
                "status": "ready",
                "assigned_harness_ids": ["dead-window"],
                "title": "写一份说明",
            }
        ],
        context={"task": {"task_id": "FD-dead"}, "spec": {"id": "FD-dead", "title": "x"}},
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.reassign_dead_tasks()

    assert client.dispatches == []
    assert client.cancels == []
    printed = capsys.readouterr().out
    refusal = json.loads(printed.strip().splitlines()[-1])
    assert refusal["reassign_deferred"] == "no idle window"
    assert refusal["earliest_available_at"] == "2026-01-01T00:00:00+00:00"


def test_a_task_whose_executor_can_claim_is_left_alone(tmp_path, monkeypatch):
    admin = FakeAdmin()
    admin.dispatched = {"FD-ok"}
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-ok",
                "status": "ready",
                "assigned_harness_ids": ["member-window"],
                "title": "写一份说明",
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.reassign_dead_tasks()

    assert client.cancels == []
    assert client.dispatches == []


def test_a_task_this_admin_did_not_dispatch_is_left_alone(tmp_path, monkeypatch):
    """别人派的历史活不是管理员该动的东西。

    曾经踩过：改派一开始把整群的任务都改了（包括别的项目留下的历史活），
    还全堆到同一个窗口。现在只改**管理员自己派出去的**那些。
    """
    admin = FakeAdmin()
    admin.credentials = {"member-window": True, "admin-window": True, "dead-window": False}
    admin.dispatched = set()
    client = FakeClient(
        tasks=[
            {
                "task_id": "FD-someone-else",
                "status": "ready",
                "assigned_harness_ids": ["dead-window"],
                "title": "别人派的历史活",
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)

    worker.reassign_dead_tasks()

    assert client.cancels == []
    assert client.dispatches == []


def test_state_cursor_round_trips(tmp_path):
    path = tmp_path / "state.json"
    state = WorkerState(
        initialized=True,
        last_message_seq=42,
        processed_goal_seqs=[7],
        reviewed_attempts=["FD-1:FD-1.a1"],
    )
    state.save(path)
    loaded = WorkerState.load(path)
    assert loaded.initialized is True
    assert loaded.last_message_seq == 42
    assert loaded.processed_goal_seqs == [7]
    assert loaded.reviewed_attempts == ["FD-1:FD-1.a1"]


def test_a_direct_question_is_answered_privately_and_only_once(tmp_path, monkeypatch):
    """A private question is a conversation, not a dispatch.

    It must not reach ``refine``: the refinement receipt is posted to the whole
    group, so replaying the owner's private words there would break the channel
    the office opened.
    """
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 4,
                "sender_type": "human",
                "sender_id": "office-operator",
                "body": "FD-1 为什么被放弃？",
                "refs": ["direct"],
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    # 回答与细化/修订/审阅同一个规矩：`route` 必须一起传（人给这个窗口选了
    # 池子里的模型，回答也走池子）。这里没有选择，所以是 None。
    assert admin.answer_calls == [
        {
            "question": "FD-1 为什么被放弃？",
            "group_id": "G-1",
            "asker": "office-operator",
            "route": None,
        }
    ]
    assert admin.refine_calls == []

    # Messages replay on every poll; one question must not become two answers.
    worker.poll_messages()
    assert len(admin.answer_calls) == 1


def test_a_private_question_is_never_replayed_into_the_public_goal(tmp_path, monkeypatch):
    """The pending goal answers in the group; a private question answers privately."""
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 11,
                "sender_type": "human",
                "sender_id": "office-operator",
                "body": "这句是私聊，不是群里的回答",
                "refs": ["direct"],
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 10
    worker.state.pending_goal = {
        "goal_id": "g-1",
        "goal": "补一条说明",
        "project_name": "DEMO",
        "members": ["member-1"],
        "max_tasks": 1,
        "answers": [],
        "seq": 10,
        "admin": "admin-window",
    }

    worker.poll_messages()

    assert admin.refine_calls == []
    assert admin.answer_calls[0]["question"] == "这句是私聊，不是群里的回答"


def test_a_private_question_the_model_cannot_answer_says_so_privately(tmp_path, monkeypatch):
    """A model failure is reported as a failure, not as a silent non-answer."""
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = FakeClient(
        inbox=[
            {
                "created_seq": 5,
                "sender_type": "human",
                "sender_id": "office-operator",
                "body": "在吗",
                "refs": ["direct"],
            }
        ]
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert len(client.messages) == 1
    sent = client.messages[0]
    assert sent["direct"] is True
    assert sent["mentions"] == ["office-operator"]
    assert "没有答上来" in sent["body"]
    assert "boom" in sent["body"]


def test_a_member_asking_for_a_goal_change_is_heard(tmp_path, monkeypatch):
    """成员窗口说「这个目标要改」：管理员要听见，并把原话带给群主。

    目标是群主的（§19）：管理员听见之后**转达**，自己不改目标。
    """
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 11,
                "sender_type": "harness",
                "sender_id": "member-window",
                "body": "这个目标做不到：要改成只做那半边",
                "task_id": "FD-1",
                "refs": ["goal-change"],
            }
        ],
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.last_goal = {
        "goal": "把两边都做完",
        "goal_id": "g-1",
        "max_tasks": 2,
        "members": ["member-window"],
    }
    # 首轮只锚定不处理（历史不重放）—— 这次要处理的是「之后」的消息。
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    said = " ".join(str(item.get("body") or "") for item in client.messages)
    assert said, "管理员要在群里说话（把成员的话带给群主）"
    assert "member-window" in said, "要说清是谁说的"
    assert "要改成只做那半边" in said, "要原话转达"
    # 群主的回答要接得住：目标摆成待答。
    assert worker.state.pending_goal, "群主的回答要有地方接"
    assert worker.state.pending_goal["goal"] == "把两边都做完"


def test_the_owners_answer_to_a_member_goal_change_is_used(tmp_path, monkeypatch):
    """群主回答之后，管理员照新的说法重新细化（方案随之修订）。"""
    admin = FakeAdmin()
    client = FakeClient()
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.last_goal = {
        "goal": "把两边都做完",
        "goal_id": "g-1",
        "max_tasks": 2,
        "members": ["member-window"],
    }
    worker.state.pending_goal = {
        **worker.state.last_goal,
        "questions": ["成员窗口 member-window 说目标要改"],
        "seq": 11,
    }
    worker.state.last_message_seq = 11
    worker.state.initialized = True
    client.inbox = [
        {"created_seq": 12, "sender_type": "human", "body": "那就只做那半边"},
    ]

    worker.poll_messages()

    assert admin.refine_calls, "群主回答后要重新细化"
    assert admin.refine_calls[0]["answers"] == ["那就只做那半边"]


def test_a_member_message_is_not_treated_as_an_answer(tmp_path, monkeypatch):
    """成员的话不能被当成群主对目标的回答（那会把别人的话写进群主的回执）。"""
    admin = FakeAdmin()
    client = FakeClient(
        inbox=[
            {
                "created_seq": 13,
                "sender_type": "harness",
                "sender_id": "member-window",
                "body": "随便说句话",
                "refs": [],
            }
        ],
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.pending_goal = {
        "goal": "把两边都做完",
        "goal_id": "g-1",
        "max_tasks": 2,
        "members": ["member-window"],
        "seq": 11,
    }
    worker.state.last_message_seq = 11

    worker.poll_messages()

    assert not admin.refine_calls, "成员的闲聊不能被当成群主的回答"


def test_a_window_joining_midway_is_heard(tmp_path, monkeypatch):
    """中途加入的窗口：管理员知道、在群里说一声、并把等人领的活改派给它。"""
    admin = FakeAdmin()
    admin.credentials["member-3"] = True
    client = FakeClient(
        inbox=[
            {
                "created_seq": 21,
                "sender_type": "human",
                "sender_id": "office-operator",
                "body": "Qoder · 成员3 中途加入了这个项目。",
                "refs": ["add-member", "add-member-json:member-3", "to-admin"],
            }
        ],
        tasks=[{"task_id": "FD-9", "title": "还在等人领的活", "owner": "member-window"}],
    )
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    said = " ".join(str(item.get("body") or "") for item in client.messages)
    assert "member-3" in said, "管理员要在群里说出来（团队多了一个人）"
    assert client.cancels, "还没人领的活要改派给新来的窗口"
    # 已经在跑的活不换人：这里只有一条等人领的活。
    assert len(client.cancels) == 1


# ------------------------------------------------------------- 回答失败重试队列
# 旧行为是 mark-before-call + 失败就完事：模型通道一坏，问题试一次就被永久
# 标记、静默丢失（2026-11-21 collab-big-g1：35 秒内 3 个积压问题全丢，外加
# 1 条 goal 附带）。修复后与 goal_retry 同一个哲学：失败要让人听见，但问题
# 本身要留在 answer_retry 里带退避自动重试，而不是把重发担子甩给人。


def _direct_question_inbox(seq: int = 5, body: str = "在吗", sender: str = "office-operator"):
    return [
        {
            "created_seq": seq,
            "sender_type": "human",
            "sender_id": sender,
            "body": body,
            "refs": ["direct"],
        }
    ]


def test_a_first_answer_failure_queues_a_backoff_retry(tmp_path, monkeypatch):
    """第 1 次失败：恰好 1 条失败消息（说明会自动重试）+ 队列条目 attempts=1。"""
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = FakeClient(inbox=_direct_question_inbox(seq=5))
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    # 第 1 次失败必须让人听见，且要说明会自动重试（人不用盯着重发）。
    assert len(client.messages) == 1
    sent = client.messages[0]
    assert sent["direct"] is True
    assert sent["mentions"] == ["office-operator"]
    assert "没有答上来" in sent["body"]
    assert "自动重试" in sent["body"]
    assert "boom" in sent["body"]
    # 问题进了重试队列，等退避到期，而不是就此消失。
    entry = worker.state.answer_retry.get("5")
    assert entry is not None, "失败的问题要留在 answer_retry 里等重试"
    assert entry["attempts"] == 1
    assert entry["question"] == "在吗"
    assert entry["asker"] == "office-operator"
    assert entry["harness"] == "admin-window"
    assert entry["retry_after"] > 0
    assert "boom" in entry["last_error"]


def test_an_answer_retry_that_is_not_yet_due_waits(tmp_path, monkeypatch):
    """退避未到期：不调模型、不发消息、条目原样。"""
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = FakeClient(inbox=_direct_question_inbox(seq=5))
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()
    before = dict(worker.state.answer_retry["5"])
    assert len(admin.answer_calls) == 1, "只有首次失败那一次调用"

    worker.retry_failed_answers(now=float(before["retry_after"]) - 1.0)

    assert len(admin.answer_calls) == 1, "未到期不许提前重试"
    assert worker.state.answer_retry["5"] == before, "未到期不许改动条目"
    assert len(client.messages) == 1, "未到期不发消息"


def test_a_due_answer_retry_answers_and_clears_the_queue(tmp_path, monkeypatch):
    """到期重试成功：回答发出、条目移除、无新失败消息。"""
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = FakeClient(inbox=_direct_question_inbox(seq=5))
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()
    assert len(admin.answer_calls) == 1

    # 通道恢复了：到期的那次重试要把问题真正答上。
    admin.error = None
    retry_after = float(worker.state.answer_retry["5"]["retry_after"])
    worker.retry_failed_answers(now=retry_after + 0.1)

    assert len(admin.answer_calls) == 2
    assert admin.answer_calls[1]["question"] == "在吗"
    assert admin.answer_calls[1]["asker"] == "office-operator"
    assert "5" not in worker.state.answer_retry, "成功后条目要移除"
    assert len(client.messages) == 1, "成功重试不发失败消息"


def test_an_answer_that_keeps_failing_gives_up_after_five_attempts(tmp_path, monkeypatch):
    """连续失败到上限：中途静默，第 5 次发一条放弃收尾，之后不再重试。"""
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: still down"))
    client = FakeClient(inbox=_direct_question_inbox(seq=5))
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()
    assert len(client.messages) == 1, "首条失败消息"

    # 第 2..N-1 次失败：静默重试（防刷屏），条目留在队列里。
    for attempt in range(2, ANSWER_RETRY_MAX_ATTEMPTS):
        retry_after = float(worker.state.answer_retry["5"]["retry_after"])
        worker.retry_failed_answers(now=retry_after + 0.1)
        assert worker.state.answer_retry["5"]["attempts"] == attempt
        assert len(client.messages) == 1, "后续失败不刷屏"

    # 第 5 次失败：发一条最终放弃消息，条目移除。
    retry_after = float(worker.state.answer_retry["5"]["retry_after"])
    worker.retry_failed_answers(now=retry_after + 0.1)
    assert "5" not in worker.state.answer_retry, "放弃后条目要移除"
    assert len(client.messages) == 2, "总计两条：首条 + 放弃条"
    final = client.messages[-1]
    assert "停止自动重试" in final["body"]
    assert "still down" in final["body"], "放弃条要带上最后原因"

    # 条目没了：再跑也不会有新的模型调用或消息。
    worker.retry_failed_answers(now=time.time() + 1000.0)
    assert len(client.messages) == 2
    assert len(admin.answer_calls) == ANSWER_RETRY_MAX_ATTEMPTS


def test_a_retried_answer_keeps_its_seq_marked(tmp_path, monkeypatch):
    """重试成功后 seq 仍在 handled_request_seqs：poll 循环不许重复处理。

    mark-before-call 防的是 poll 对重放消息的重复处理；重试由队列驱动、
    不走 poll 解析 —— 所以 seq 留在原地，两条防线各管各的。
    """
    admin = FakeAdmin(error=AdminHarnessError("admin harness call exited 1: boom"))
    client = FakeClient(inbox=_direct_question_inbox(seq=5))
    worker = _worker(tmp_path, monkeypatch, admin=admin, client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()
    assert 5 in worker.state.handled_request_seqs

    admin.error = None
    retry_after = float(worker.state.answer_retry["5"]["retry_after"])
    worker.retry_failed_answers(now=retry_after + 0.1)
    assert len(admin.answer_calls) == 2

    # 消息每轮重放，但 seq 已标记：同一条问题不许答两遍。
    assert 5 in worker.state.handled_request_seqs, "重试成功不清 seq 标记"
    worker.poll_messages()
    assert len(admin.answer_calls) == 2


def test_state_round_trip_keeps_answer_retry(tmp_path):
    """WorkerState save/load 往返后 answer_retry 不丢。"""
    path = tmp_path / "state.json"
    state = WorkerState(
        answer_retry={
            "5": {
                "question": "在吗",
                "asker": "office-operator",
                "harness": "admin-window",
                "attempts": 2,
                "retry_after": 1234.5,
                "last_error": "AdminHarnessError: boom",
            }
        }
    )
    state.save(path)
    loaded = WorkerState.load(path)
    assert loaded.answer_retry["5"]["question"] == "在吗"
    assert loaded.answer_retry["5"]["asker"] == "office-operator"
    assert loaded.answer_retry["5"]["harness"] == "admin-window"
    assert loaded.answer_retry["5"]["attempts"] == 2
    assert loaded.answer_retry["5"]["retry_after"] == 1234.5
    assert loaded.answer_retry["5"]["last_error"] == "AdminHarnessError: boom"


# ------------------------------------------------- 人类下发、管理员确定性执行
# 「让管理员提交这几个文件」是真实痛点：admin worker 只认白名单 ref，人类发的
# 普通 to-admin 消息被静默忽略；而成员派单是 worktree 隔离的，看不到主工作区
# 的未提交改动 —— 提交只能由能在主工作区跑的东西做。
#
# 设计决定：**不让模型碰 git**。人发一条结构化 JSON（repo + branch + 显式
# paths），worker 用确定性代码校验并执行（分支校验 → git add 指定路径 →
# git commit），全程没有模型调用。下面是这条路径的八个边界。


def _temp_git_repo(tmp_path: Path, *, branch: str = "main") -> Path:
    """A real repository in tmp: these tests must never touch the platform repo."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git("init", "--initial-branch", branch, str(repo))
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    run_git("add", "seed.txt", cwd=repo)
    run_git("commit", "-m", "seed", cwd=repo)
    return repo


def _head(repo: Path) -> str:
    return run_git("rev-parse", "HEAD", cwd=repo).stdout.strip()


def _commit_count(repo: Path) -> int:
    return len(
        [line for line in run_git("rev-list", "--all", cwd=repo).stdout.splitlines() if line]
    )


def _current_branch(repo: Path) -> str:
    return run_git("branch", "--show-current", cwd=repo).stdout.strip()


def _commit_command(repo: Path, commits, *, branch="office-ui-and-admin-fixes", command_id=None):
    payload = {"action": "commit", "repo": str(repo), "branch": branch, "commits": commits}
    if command_id:
        payload["command_id"] = command_id
    return payload


def _command_message(body, *, seq: int) -> dict:
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return {
        "created_seq": seq,
        "sender_type": "human",
        "sender_id": "office-operator",
        "body": text,
        "refs": ["admin-command"],
    }


def _run_admin_command(tmp_path, monkeypatch, payload, *, seq: int = 31):
    client = FakeClient(inbox=[_command_message(payload, seq=seq)])
    worker = _worker(tmp_path, monkeypatch, admin=FakeAdmin(), client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0
    worker.poll_messages()
    return worker, client


def _said(client) -> str:
    return "\n".join(str(item.get("body") or "") for item in client.messages)


def test_a_human_commit_command_lands_every_commit(tmp_path, monkeypatch):
    """正常两条：分支创建、两个 hash 回报、文件真的进了库。"""
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "two.txt").write_text("two\n", encoding="utf-8")
    before = _commit_count(repo)

    worker, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(
            repo,
            [
                {"message": "第一个提交：普通改动", "paths": ["one.txt"]},
                {"message": "第二个提交：另一个文件", "paths": ["two.txt"]},
            ],
        ),
    )

    assert worker.state.handled_request_seqs == [31], "指令消息要标成已处理（消息每轮重放）"
    assert _current_branch(repo) == "office-ui-and-admin-fixes", "分支要建出来并切过去"
    assert _commit_count(repo) == before + 2, "两条提交都要落地"
    hashes = run_git("rev-list", "-2", "HEAD", cwd=repo).stdout.split()
    said = _said(client)
    for commit_hash in hashes:
        assert commit_hash[:8] in said, "报告里要能看到每条提交的 hash"
    assert "office-ui-and-admin-fixes" in said, "报告里要写清分支"
    for name in ("one.txt", "two.txt"):
        assert (
            run_git("cat-file", "-e", f"HEAD:{name}", cwd=repo).returncode == 0
        ), f"{name} 必须真的进库（不是只 add 没提交）"


def test_a_branch_that_is_empty_or_main_is_refused(tmp_path, monkeypatch):
    """空分支名、main、master：硬拒绝，仓库里不许出现新提交。"""
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    before = _commit_count(repo)
    head_before = _head(repo)

    for branch in ("", "main", "master"):
        _worker_used, client = _run_admin_command(
            tmp_path,
            monkeypatch,
            _commit_command(
                repo,
                [{"message": "不该落地的提交", "paths": ["one.txt"]}],
                branch=branch,
            ),
        )
        said = _said(client)
        assert _commit_count(repo) == before, f"branch={branch!r} 不许产生新提交"
        assert _head(repo) == head_before, "HEAD 一动没动"
        assert _current_branch(repo) == "main", "被拒的指令连分支都不许切"
        assert "失败" in said, "要明确回报失败"
        if branch.lower() in {"main", "master"}:
            assert "rule #6" in said, "拒绝理由要说清 rule #6"


def test_a_path_that_escapes_the_repository_is_refused(tmp_path, monkeypatch):
    """`../outside.txt` 是逃逸：拒绝、没有提交，**也不能把工作区切走**。"""
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (tmp_path / "outside.txt").write_text("outside\n", encoding="utf-8")
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "偷渡", "paths": ["../outside.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "仓库外的东西不许被提交"
    assert _current_branch(repo) == "main", "被拒的指令不许先 git switch 再校验（QA P2-1）"
    assert "跳出仓库根" in said or "跑出仓库根" in said, "要说清是路径逃逸"
    assert "分支也没有切换" in said, "报告要如实说工作区没被移动"


def test_a_repo_outside_the_allowed_roots_is_refused(tmp_path, monkeypatch):
    """repo 不在允许根内：拒绝（允许根可被 AP_ADMIN_COMMAND_ROOTS 追加）。"""
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(elsewhere))
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "越界", "paths": ["one.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before
    assert "不在允许根内" in said
    assert "AP_ADMIN_COMMAND_ROOTS" in said, "拒绝时要告诉人怎么加允许根"


def test_a_commit_with_no_staged_change_is_skipped_not_emptied(tmp_path, monkeypatch):
    """paths 没有变化：跳过这一条（不造空提交），其余照常，报告写明跳过。"""
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(
            repo,
            [
                {"message": "真改动", "paths": ["one.txt"]},
                # seed.txt 已提交且没再改：add 之后暂存区还是空的。
                {"message": "没变化的提交", "paths": ["seed.txt"]},
            ],
        ),
    )

    said = _said(client)
    assert _commit_count(repo) == before + 1, "无变化的那条不许变成空提交"
    assert "无变化跳过" in said
    assert "没变化的提交" in said, "跳过的那条要点名"
    assert run_git("log", "-1", "--pretty=%s", cwd=repo).stdout.strip() == "真改动"


def test_a_body_that_is_not_json_is_reported_not_raised(tmp_path, monkeypatch):
    """人发了一句中文而不是 JSON：友好报错、不抛异常、不每轮刷屏。"""
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    client = FakeClient(inbox=[_command_message("请帮我把这几个文件提交了", seq=32)])
    worker = _worker(tmp_path, monkeypatch, admin=FakeAdmin(), client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()  # 不许抛

    said = _said(client)
    assert "指令格式不对" in said
    assert "期望的字段" in said, "报错要贴出期望的字段"
    assert 32 in worker.state.handled_request_seqs, "不合法指令也要标记，否则每轮都刷一遍"


def test_a_repeated_command_id_is_ignored(tmp_path, monkeypatch):
    """同一个 command_id 第二次到达：直接忽略（幂等），seq 拦不住这种情况。"""
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    before = _commit_count(repo)
    payload = _commit_command(
        repo, [{"message": "只该落一次", "paths": ["one.txt"]}], command_id="cmd-1"
    )
    # 两条消息、不同 seq、同一 command_id：办公室重发就是这个形状。
    client = FakeClient(
        inbox=[_command_message(payload, seq=31), _command_message(payload, seq=32)]
    )
    worker = _worker(tmp_path, monkeypatch, admin=FakeAdmin(), client=client)
    worker.state.initialized = True
    worker.state.last_message_seq = 0

    worker.poll_messages()

    assert _commit_count(repo) == before + 1, "同一个 command_id 只许提交一次"
    assert worker.state.handled_command_ids == ["cmd-1"]
    # 重发不许闷掉：中途失败后人的第一反应就是原样重发，沉默等于「平台坏了」。
    assert len(client.messages) == 2, "第一次的报告 + 重复那条的一句说明"
    assert "已经执行过" in _said(client)


def test_a_failure_halfway_stops_and_names_the_commit_that_landed(tmp_path, monkeypatch):
    """中途失败：停止后续提交，失败消息里带上已完成那条的 hash。

    失败点在**执行期**（第二条的路径被 .gitignore 挡住，git add 退出非 0），
    因为静态错误（逃逸/目录/通配符）现在在任何副作用之前就被拦下了 —— 那条
    路径要求零副作用，见 test_a_path_that_escapes_the_repository_is_refused。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "debug.log").write_text("noise\n", encoding="utf-8")
    run_git("add", ".gitignore", cwd=repo)
    run_git("commit", "-m", "chore: ignore logs", cwd=repo)
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(
            repo,
            [
                {"message": "先落地的这条", "paths": ["one.txt"]},
                {"message": "第二条被忽略规则挡住", "paths": ["debug.log"]},
            ],
        ),
    )

    said = _said(client)
    assert _commit_count(repo) == before + 1, "第一条已经落地，不许回滚"
    landed = _head(repo)
    assert run_git("log", "-1", "--pretty=%s", cwd=repo).stdout.strip() == "先落地的这条"
    assert "失败" in said
    assert landed[:8] in said, "失败报告里必须能看到已完成那条的 hash"
    assert "已完成的提交" in said


def test_a_leftover_staged_file_is_never_committed(tmp_path, monkeypatch):
    """P1（QA）：`git add` 只追加进 index，`git commit` 提交整个 index。

    主工作区里残留一条别人 `git add` 过的文件是常态。指令只声明 one.txt，
    那条残留就会搭车进提交 —— 不写 `-A` 也能把我的防线绕过去。所以暂存区
    里只要有没声明的条目就**拒绝**，并且一个字节都不许动它。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "PRESTAGED.txt").write_text("someone else's work\n", encoding="utf-8")
    run_git("add", "PRESTAGED.txt", cwd=repo)  # 上一次操作遗留的暂存
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "只声明了 one.txt", "paths": ["one.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "残留暂存不许被一起提交"
    assert _current_branch(repo) == "main", "拒绝要发生在切分支之前"
    assert "PRESTAGED.txt" in said, "要指名是哪个文件挡住了"
    assert "git restore --staged" in said, "要告诉人怎么清"
    # 别人的暂存还在原地（不许替人 unstage）。
    staged = run_git("diff", "--cached", "--name-only", cwd=repo).stdout.split()
    assert staged == ["PRESTAGED.txt"], "我们没有动别人的暂存区"


def test_a_leftover_staged_file_cannot_masquerade_as_a_change(tmp_path, monkeypatch):
    """P1 变体二：「无变化跳过」只看整个 index 时，会把别人的暂存造成一条提交。

    指令声明 seed.txt（没改过），暂存区里有 P2.txt。修复前 skipped=False 且
    凭空多出一条内容为 P2.txt 的提交；现在必须在任何副作用之前拒绝。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "P2.txt").write_text("staged by someone\n", encoding="utf-8")
    run_git("add", "P2.txt", cwd=repo)
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "seed.txt 没变化，应该跳过", "paths": ["seed.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "不许把他人的暂存内容造成一条提交"
    assert run_git("log", "-1", "--pretty=%s", cwd=repo).stdout.strip() == "seed"
    assert "P2.txt" in said
    assert "无变化跳过" not in said, "这不是「无变化」，是暂存区脏了，必须说清"


def test_a_branch_name_with_a_refs_prefix_is_refused(tmp_path, monkeypatch):
    """P2-2：`check-ref-format --branch refs/heads/main` 会说 OK。

    照抄下去 `git switch -c` 会建出 `refs/heads/refs/heads/main` —— main 本体
    没动，但人的本意就是「在 main 上提交」，必须拒。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    before = _commit_count(repo)

    for branch in ("refs/heads/main", "refs/heads/feature"):
        _worker_used, client = _run_admin_command(
            tmp_path,
            monkeypatch,
            _commit_command(repo, [{"message": "不该落地", "paths": ["one.txt"]}], branch=branch),
        )
        said = _said(client)
        assert _commit_count(repo) == before, f"branch={branch!r} 不许产生提交"
        assert _current_branch(repo) == "main"
        assert "refs/" in said, "要说明不要带 refs/ 前缀"


def test_a_dry_run_does_not_burn_the_command_id(tmp_path, monkeypatch):
    """P2-3：先 --dry-run 看计划、再去掉 flag 真跑，是最自然的操作序列。

    修复前 dry_run 把 command_id 烧了，真跑那条被当成重复、静默 +0 提交。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    before = _commit_count(repo)
    command = _commit_command(
        repo, [{"message": "预览过的这条", "paths": ["one.txt"]}], command_id="dry-cmd-1"
    )
    command["dry_run"] = True

    worker, client = _run_admin_command(tmp_path, monkeypatch, command)
    assert _commit_count(repo) == before, "dry_run 不许落地"
    assert worker.state.handled_command_ids == [], "dry_run 不许记下 command_id"

    # 同一条指令去掉 dry_run、同一个 id：必须真的执行。
    command["dry_run"] = False
    worker, client = _run_admin_command(tmp_path, monkeypatch, command, seq=32)

    said = _said(client)
    assert _commit_count(repo) == before + 1, "预览之后真跑不许被当成重复"
    assert "提交完成" in said
    assert worker.state.handled_command_ids == ["dry-cmd-1"]


def test_a_directory_path_is_refused(tmp_path, monkeypatch):
    """P3-2：`paths:["office"]` 等价 `git add office`，连**未跟踪的新文件**一起带走。

    指令的语义是「这几个文件」，目录是笔误而不是简写：实测 `paths:["office"]`
    会把 `office/untracked-new.js`（人从没看过的）也提交进去。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "office").mkdir()
    (repo / "office" / "a.css").write_text("a\n", encoding="utf-8")
    (repo / "office" / "untracked-new.js").write_text("b\n", encoding="utf-8")
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "不该落地", "paths": ["office"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "目录不许被展开成一堆文件提交"
    assert "是一个目录" in said, "要说明目录会被整体带走"
    assert run_git("log", "-1", "--pretty=%s", cwd=repo).stdout.strip() == "seed"
    # 未跟踪的新文件还在工作区，没被提交。
    assert (
        run_git("cat-file", "-e", "HEAD:office/untracked-new.js", cwd=repo, check=False).returncode
        != 0
    )


def test_a_non_string_path_is_refused(tmp_path, monkeypatch):
    """P3-3：`paths:[123]` 不许被 `str()` coerce 成真实路径 "123"。

    把 `null` 变成 "None"、把 `123` 变成 "123"，等于让笔误静默生效；解析阶段
    就拒绝，比让 git 报 "pathspec did not match" 更容易让人修对。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    before = _commit_count(repo)

    for raw in (123, None, {"path": "one.txt"}):
        _worker_used, client = _run_admin_command(
            tmp_path,
            monkeypatch,
            _commit_command(repo, [{"message": "不该落地", "paths": [raw]}]),
        )
        said = _said(client)
        assert _commit_count(repo) == before, f"paths={[raw]!r} 不许产生提交"
        assert "不是字符串" in said, f"paths={[raw]!r} 的拒绝理由要说清"


def test_a_repository_in_the_middle_of_a_merge_is_refused(tmp_path, monkeypatch):
    """进行中的 merge：拒绝，且不新建分支、不产生提交（QA 回归用例）。

    在别人半截的 merge 上提交，等于把别人的中间状态写成一条「这次要做的
    改动」。这里造一次**真冲突**而不是手写 `.git/MERGE_HEAD`：手写 marker
    只能证明「文件存在性判断」，证明不了真实 merge 现场（QA 第 1 轮就是用
    真冲突验的，没进自动化用例，现在补上）。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "conflict.txt").write_text("base\n", encoding="utf-8")
    run_git("add", "conflict.txt", cwd=repo)
    run_git("commit", "-m", "base", cwd=repo)
    run_git("switch", "-c", "side", cwd=repo)
    (repo / "conflict.txt").write_text("side\n", encoding="utf-8")
    run_git("commit", "-am", "side", cwd=repo)
    run_git("switch", "main", cwd=repo)
    (repo / "conflict.txt").write_text("main\n", encoding="utf-8")
    run_git("commit", "-am", "main", cwd=repo)
    merged = run_git("merge", "side", cwd=repo, check=False)

    assert merged.returncode != 0, "环境前提：这次 merge 必须真的冲突"
    assert (repo / ".git" / "MERGE_HEAD").exists(), "环境前提：MERGE_HEAD 真的在"

    before = _commit_count(repo)
    head_before = _head(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "在半截 merge 上提交", "paths": ["conflict.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "进行中的 merge 上不许产生提交"
    assert _head(repo) == head_before, "HEAD 一动没动"
    assert _current_branch(repo) == "main", "被拒的指令连分支都不许切"
    assert "进行中的 git 操作" in said, "要说清是「有进行中的 git 操作」挡住的"
    assert "MERGE_HEAD" in said, "要点名是哪个 marker"
    assert "merge --abort" in said, "要告诉人怎么收场"


def test_a_failing_pre_commit_hook_reports_the_hook_and_commits_nothing(tmp_path, monkeypatch):
    """pre-commit hook 拒绝：报告 hook 的原话、零提交、且分支**已经切了**。

    这是**执行期**错误（静态校验一条都没错：路径合法、分支合法），所以
    `git switch` 已经发生。报告必须如实说「分支已经切到 X（需要你处理）」，
    不能笼统写「仓库里没有半成品提交」—— 那会让人以为工作区没被动过。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'qa hook refuses' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "被 hook 挡住", "paths": ["one.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "hook 拒绝后不许有提交"
    assert "git commit 失败" in said, "要说明是提交这一步失败"
    assert "qa hook refuses" in said, "报告要带上 hook 的原话，否则人不知道为什么失败"
    assert "分支已经切到" in said, "执行期失败要如实说分支已经切了"


def test_a_path_with_the_wrong_case_is_reported_not_silently_skipped(tmp_path, monkeypatch):
    """N1（QA + team-lead 拍板）：大小写不符是「静默空操作」，不许报成「完成」。

    Windows 上 `git add -- new.txt`（盘上是 `New.txt`）返回 0 却什么都没
    staged。旧行为把这种「什么都没发生」报成「提交完成 · 无变化跳过」，而
    人据此会对成员宣布「已提交」——工作区里却什么都没有。这是这条通道唯一
    还能骗人的地方，所以它必须是错误，不是跳过。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "New.txt").write_text("new\n", encoding="utf-8")  # 盘上是 New.txt，未跟踪
    before = _commit_count(repo)
    files_before = set(
        run_git("show", "--pretty=format:", "--name-only", "HEAD", cwd=repo).stdout.split()
    )

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "大小写写错了", "paths": ["new.txt"]}]),
    )

    said = _said(client)
    files_after = set(
        run_git("show", "--pretty=format:", "--name-only", "HEAD", cwd=repo).stdout.split()
    )
    assert _commit_count(repo) == before, "大小写写错时不许产生提交"
    assert files_after == files_before, "HEAD 的文件集合不许变"
    assert "注意大小写" in said, "报错要点名是大小写问题"
    assert "new.txt" in said, "要点名是哪条路径"
    assert "静默什么都不做" in said, "要解释 git 为什么会这样"
    assert "无变化跳过" not in said, "这不是「无变化」，是路径根本没命中"
    assert "提交完成" not in said, "一个字节都没提交，不许写「完成」"
    # 执行期才发现（先 add 才知道空操作），所以按已拍板的二分如实说分支已切。
    assert "分支已经切到" in said


def test_a_legitimate_no_change_is_still_skipped(tmp_path, monkeypatch):
    """N1 的新检查不许误伤真正的「无变化」：文件已提交、内容未改、大小写正确。

    末尾顺带覆盖反向用例：自己已改动并 staged、再声明自己 → 正常提交，
    不许因为新检查被当成「找不到」。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "seed.txt 本来就没变", "paths": ["seed.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before, "真无变化就是跳过，不造空提交"
    assert "无变化跳过" in said
    assert "注意大小写" not in said, "大小写正确时不许误报"

    # 反向用例：真的改了、也 staged 过，再声明自己 → 必须提交成功。
    (repo / "seed.txt").write_text("seed changed\n", encoding="utf-8")
    run_git("add", "seed.txt", cwd=repo)
    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "这次真的有变化", "paths": ["seed.txt"]}]),
        seq=33,
    )
    said = _said(client)
    assert _commit_count(repo) == before + 1, "有变化的合法提交不许被新检查挡住"
    assert "注意大小写" not in said
    assert "无变化跳过" not in said


def test_an_execution_failure_says_how_to_get_back(tmp_path, monkeypatch):
    """执行期失败会把工作区留在新分支上 —— 报告必须给出「怎么回去」，且顺序可执行。

    这是 P2-1 想防的事故换了个入口：人不知道自己在哪个分支，接着顺手
    `git add` / `git commit`，改动就落到错分支。文案只说「怎么回去」，不写
    「回滚 / 撤销」—— 实际什么都没被撤销，只是切了一次分支。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "debug.log").write_text("noise\n", encoding="utf-8")
    run_git("add", ".gitignore", cwd=repo)
    run_git("commit", "-m", "chore: ignore logs", cwd=repo)
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "被忽略规则挡住", "paths": ["debug.log"]}]),
    )

    said = _said(client)
    assert _current_branch(repo) == "office-ui-and-admin-fixes", "执行期失败时分支已经切了"
    assert _commit_count(repo) == before, "这条 commit 没落地"
    assert "要回去" in said
    assert "git switch -" in said
    assert "git branch -D office-ui-and-admin-fixes" in said
    # 顺序即正确性：分支正被 checkout 时 `branch -D` 会失败，写反了人就卡住。
    assert said.index("git switch -") < said.index("git branch -D"), "必须先切走再删分支"
    assert "刚建的、没有提交" in said, "要说明为什么这个分支可以删"
    assert "回滚" not in said and "撤销" not in said, "什么都没被撤销，不许这么说"

    # 有提交落地的那一类**不许**加这句：会让人以为已完成的工作要丢弃。
    (repo / "one.txt").write_text("one\n", encoding="utf-8")
    (repo / "debug2.log").write_text("noise\n", encoding="utf-8")
    before2 = _commit_count(repo)
    _worker_used, client2 = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(
            repo,
            [
                {"message": "这条会落地", "paths": ["one.txt"]},
                {"message": "这条被挡住", "paths": ["debug2.log"]},
            ],
        ),
        seq=41,
    )

    said2 = _said(client2)
    assert _commit_count(repo) == before2 + 1, "第一条已经落地"
    assert "已完成的提交" in said2
    assert "要回去" not in said2, "有落地提交时不许说「回去」，会被理解成丢弃"


def test_a_path_whose_case_matches_the_file_on_disk_is_committed(tmp_path, monkeypatch):
    """正例对照：大小写写对就必须提交成功（钉住 Windows 的大小写折叠）。

    只测「写错被拒」会漏掉反向的可怕情形：如果哪天把路径比较写成大小写不一致，
    结果会拒绝掉**每一个**名字带大写的文件（`README.md`、`Button.js`），而所有
    小写名的用例都会照样通过 —— 没人会发现。所以这一条故意用大小写混合的名字，
    断言它真能被提交，并且报告里不出现「注意大小写」。
    """
    monkeypatch.setenv("AP_ADMIN_COMMAND_ROOTS", str(tmp_path))
    repo = _temp_git_repo(tmp_path)
    (repo / "New.txt").write_text("hello\n", encoding="utf-8")
    before = _commit_count(repo)

    _worker_used, client = _run_admin_command(
        tmp_path,
        monkeypatch,
        _commit_command(repo, [{"message": "大小写写对", "paths": ["New.txt"]}]),
    )

    said = _said(client)
    assert _commit_count(repo) == before + 1, "大小写写对必须真的提交"
    assert run_git("cat-file", "-e", "HEAD:New.txt", cwd=repo, check=False).returncode == 0
    assert "注意大小写" not in said, "写对了不许报大小写"
    assert "提交完成" in said
