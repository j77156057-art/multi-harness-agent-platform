"""The administrator worker is a runner, not a second scheduler.

These tests use a scripted administrator model and a fake HTTP client. They pin
the two places where the self-loop can silently turn back into "the human did
it": the dispatch body must carry the administrator's identity, and a model
failure must never produce a submitted review verdict.
"""

from __future__ import annotations

import httpx

from app.admin.service import AdminHarnessError
from app.admin.worker import (
    AdminWorker,
    WorkerState,
    _goal_payload,
    run_once,
)


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

    def assignable_harnesses(self):
        return list(self.assignable)

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
        if path.endswith("/messages"):
            self.messages.append(body)
            return {"message_id": "m-1"}
        raise AssertionError(path)


def _worker(tmp_path, monkeypatch, *, admin, client, dry_run=False):
    monkeypatch.setattr("app.admin.worker.AdminService", lambda *a, **k: admin)
    state_path = tmp_path / "admin-worker.json"
    return AdminWorker(
        client=client,
        database=None,
        group_id="G-1",
        admin_harness_id="admin-window",
        state=WorkerState(),
        state_path=state_path,
        dry_run=dry_run,
    )


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

    assert admin.answer_calls == [
        {"question": "FD-1 为什么被放弃？", "group_id": "G-1", "asker": "office-operator"}
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
