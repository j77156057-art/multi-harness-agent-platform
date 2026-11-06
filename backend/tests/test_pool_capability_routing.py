"""F2：池层在**选窗口时**按能力过滤，并尊重 ``retryable:false``。

2026-11-05（FD-cdf70117146f4319）现场：窗口被选到 CLI 档 ``codebuddy/hy3``，
工作请求必带 ``read/edit/write`` 工具定义，池层在开流前回 502
``relay_cli_cannot_use_tools``；响应自己写着 ``retryable:false``，可分类逻辑
只认 stderr 里的 ``Refused:``，于是同一句拒绝又烧了三次预算。

这里钉住四件事：

1. 只有 CLI 档可选时，工具型请求直接 ``unavailable``（``protocol_unsupported``），
   **不发起任何上游调用**；
2. 有能接工具定义的窗口时，选中它 —— 断言选中的不是 CLI 档；
3. ``retryable:false`` 的失败恰好留下 **1 行** attempt、``close_reason`` 是
   传入的原因、并且不会被再次领走；
4. ``retryable:true`` 仍然按原策略重试（别把"不可重试"修成"全都不重试"）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.adapter.formal_worker import ProviderUnavailable, _classify_failure, ask_pool_plan
from app.adapter.harnesses.cli import CliFailed, CliRunResult
from app.auth.tokens import TokenService
from app.claim.service import ClaimRequest, ClaimService
from app.git.repo import CanonicalRepo
from app.git.worktree import WorktreeManager
from app.pool.routing import plan_attempt
from app.pool.service import PoolService
from app.tasks.service import SchedulerService
from tests.api_support import bootstrap, make_client, publish_plan
from tests.claim_support import count, make_database, seed_project, seed_task
from tests.merge_support import register_harness


class _Pool:
    """A pool that can list models and nothing else: any relay call would blow up."""

    def __init__(self, entries: list[dict[str, object]]) -> None:
        self.entries = entries
        self.model_reads = 0

    def models(self, *, refresh: bool = False) -> list[dict[str, object]]:
        self.model_reads += 1
        return list(self.entries)


class _Selection:
    model_mode = "pool"
    model_tier = "free"

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id


CLI_SLOT = {
    "id": "codebuddy/hy3",
    "upstream": "codebuddy",
    "protocol": "cli",
    "tier": "free",
}
PROVIDER = {
    "id": "relay/gpt-5",
    "upstream": "relay",
    "protocol": "openai-chat",
    "tier": "free",
}
TOOLS = ("read", "edit", "write")


def _failure(tmp_path: Path, **overrides: object) -> Path:
    root = tmp_path / "data" / "formal-runs"
    root.mkdir(parents=True, exist_ok=True)
    document: dict[str, object] = {
        "task_id": "T-1",
        "harness_id": "w-1",
        # 失败的是窗口**自己的** provider，不是池里的 CLI 档：否则「选中档刚好
        # 是刚死的那个」会被当成需要自动降级的场景，测不到按能力过滤这一段。
        "model": "own/model",
        "retryable": True,
        "error_type": "provider_error",
        "retry_reason": "502 relay_cli_cannot_use_tools",
    }
    document.update(overrides)
    (root / "T-1.failure.json").write_text(json.dumps(document), encoding="utf-8")
    return tmp_path / "data"


# -------------------------------------------------- 选窗口时按能力过滤


def test_a_tool_request_with_only_cli_slots_makes_no_upstream_call(tmp_path):
    """只有 CLI 档 → ``unavailable`` + ``protocol_unsupported``，一次上游都没打。"""
    data_dir = _failure(tmp_path)
    pool = _Pool([CLI_SLOT])

    plan = plan_attempt(
        "T-1",
        harness_id="w-1",
        attempt_no=1,
        data_dir=data_dir,
        pool=pool,
        required_tools=TOOLS,
    )

    assert plan["mode"] == "unavailable"
    assert plan["reason_code"] == "protocol_unsupported"
    assert plan["required_tools"] == list(TOOLS)
    assert pool.model_reads == 1, "only the model list is read; no relay call was made"


def test_a_chosen_cli_slot_is_excluded_for_tool_requests(tmp_path):
    """人选了 CLI 档、池里也没有别的档 → 开流前拒绝，不烧 attempt。"""
    data_dir = _failure(tmp_path)
    pool = _Pool([CLI_SLOT])

    plan = plan_attempt(
        "T-1",
        harness_id="w-1",
        attempt_no=0,
        data_dir=data_dir,
        pool=pool,
        selection=_Selection("codebuddy/hy3"),
        required_tools=TOOLS,
    )

    assert plan["mode"] == "unavailable"
    assert plan["reason_code"] == "protocol_unsupported"
    assert plan["selected_model"] == "codebuddy/hy3"
    assert plan["selected_protocol"] == "cli"


def test_a_window_that_can_take_tools_is_selected_instead(tmp_path):
    """存在能接工具定义的窗口 → 选中它，且断言选中的**不是** CLI 档。"""
    data_dir = _failure(tmp_path)
    pool = _Pool([CLI_SLOT, PROVIDER])

    plan = plan_attempt(
        "T-1",
        harness_id="w-1",
        attempt_no=0,
        data_dir=data_dir,
        pool=pool,
        selection=_Selection("codebuddy/hy3"),
        required_tools=TOOLS,
    )

    assert plan["mode"] == "pool"
    assert plan["model"] == "relay/gpt-5"
    assert plan["model"] != "codebuddy/hy3"
    assert plan["upstream_protocol"] != "cli"
    assert plan["fallback_from"]["model"] == "codebuddy/hy3"
    assert plan["reason_code"] == "protocol_unsupported"


def test_the_automatic_path_never_picks_a_cli_slot_for_tools(tmp_path):
    """自动降级路径同样只挑能接工具定义的窗口。"""
    data_dir = _failure(tmp_path)
    pool = _Pool([CLI_SLOT, PROVIDER])

    plan = plan_attempt(
        "T-1",
        harness_id="w-1",
        attempt_no=1,
        data_dir=data_dir,
        pool=pool,
        required_tools=TOOLS,
    )

    assert plan["mode"] == "pool"
    assert plan["model"] == "relay/gpt-5"
    assert plan["upstream_protocol"] == "openai-chat"


def test_approve_blocks_a_cli_only_window_before_any_attempt(tmp_path, monkeypatch):
    """approve 阶段就拒绝：0 次 attempt、0 次上游调用，原因含 ``protocol_unsupported``。"""
    client, database = make_client(tmp_path)
    bootstrap(client)
    for harness_id, provider in (("w-cli", "codebuddy"), ("w-pro", "relay")):
        register_harness(database, harness_id, provider=provider)
        TokenService(database).issue(
            subject_type="harness", subject_id=harness_id, label="test"
        )
    with database.transaction() as conn:
        conn.execute(
            "UPDATE harnesses SET status = 'online', model_mode = 'pool',"
            " model_id = 'codebuddy/hy3' WHERE harness_id = 'w-cli'"
        )
        conn.execute(
            "UPDATE harnesses SET status = 'online', model_mode = 'pool',"
            " model_id = 'relay/gpt-5' WHERE harness_id = 'w-pro'"
        )

    monkeypatch.setattr(
        PoolService, "models", lambda self, refresh=False: [dict(CLI_SLOT), dict(PROVIDER)]
    )

    publish_plan(
        client,
        tasks=[
            {
                "id": "T-1",
                "goal": "write the doc",
                "exclusive_paths": ["docs/a.md"],
                "assigned_harness_ids": ["w-cli"],
            }
        ],
    )

    preflight = client.get("/api/v1/plans/PL-1/assignability")
    assert preflight.status_code == 200, preflight.text
    blocked = preflight.json()["blocked"]
    assert blocked, "a CLI-only window must not be reported as assignable"
    reasons = " ".join(blocked[0]["reasons"])
    assert "protocol_unsupported" in reasons
    assert preflight.json()["approvable"] is False

    response = client.post("/api/v1/plans/PL-1/approve", json={"decided_by": "human-1"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "plan.targets_unavailable"
    assert count(
        database, "SELECT COUNT(*) FROM task_attempts WHERE task_id = 'T-1'"
    ) == 0


# -------------------------------------------------- retryable:false


def _cli_failed(stderr: str) -> CliFailed:
    return CliFailed(
        "T-1",
        CliRunResult(
            argv=["claude", "-p", "do it"],
            returncode=1,
            stdout="",
            stderr=stderr,
            duration_seconds=3.0,
        ),
    )


REFUSAL = (
    "API error 502: {\"code\":\"relay_cli_cannot_use_tools\","
    "\"message\":\"this model is served by a CLI slot\",\"retryable\":false}"
)


def test_retryable_false_is_classified_as_not_retryable():
    retryable, reason = _classify_failure(
        _cli_failed(REFUSAL),
        task={"task_id": "T-1", "max_attempts": 3, "attempt_no": 1},
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is False
    assert "retryable:false" in reason
    assert "relay_cli_cannot_use_tools" in reason


def test_retryable_true_is_still_retried():
    retryable, reason = _classify_failure(
        _cli_failed("API error 502: {\"retryable\":true}"),
        task={"task_id": "T-1", "max_attempts": 3, "attempt_no": 1},
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is True
    assert "harness CLI" in reason


def test_a_retryable_false_failure_leaves_exactly_one_attempt(tmp_path):
    """``retryable:false`` → 1 行 attempt + ``close_reason`` 是传入的原因，不再领活。"""
    database = make_database(tmp_path)
    repo = CanonicalRepo(tmp_path / "canonical.git")
    repo.ensure()
    seed_project(database, integration_commit=repo.ref_commit(repo.integration_ref))
    seed_task(database)
    claimed = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="w-1", task_ids=("T-1",))
    )
    item = claimed.items[0]
    # abandon 只允许从 in_progress 起步（R10 表）：先真的开一棵工作树，而不是
    # 直接把状态改进去 —— 这条测的是"失败如何收尾"，不该用伪造的状态来测。
    service = SchedulerService(database)
    service.start_task(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        group_short="g1",
        worktrees=WorktreeManager(database, repo, root=tmp_path / "wt"),
    )
    # 收尾动作**由分类结果决定**：``retryable:false`` 必须走 abandon（而不是
    # release 再烧一次）。把分类里的 retryable:false 分支去掉，这条就会红。
    retryable, reason = _classify_failure(
        _cli_failed(REFUSAL),
        task={"task_id": "T-1", "max_attempts": 3, "attempt_no": 1},
        manager=None,
        attempt_id=item.attempt_id,
    )
    assert retryable is False, "the relay refusal says retryable:false"

    service.abandon(
        task_id="T-1",
        attempt_id=item.attempt_id,
        fencing_epoch=item.fencing_epoch,
        reason=reason,
    )

    rows = database.read(
        "SELECT attempt_id, status, close_reason, closed_at FROM task_attempts"
        " WHERE task_id = 'T-1'"
    )
    assert len(rows) == 1, "a non-retryable refusal must not produce a second attempt"
    assert rows[0]["status"] == "abandoned"
    assert rows[0]["close_reason"] == reason
    assert rows[0]["closed_at"]
    # 任务已终止：再领一次也不会产生第 2 行。
    again = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-2", harness_id="w-1", task_ids=("T-1",))
    )
    assert [item.outcome for item in again.items] == ["rejected"]
    assert again.items[0].error_code == "claim.task_not_ready"
    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id = 'T-1'") == 1


def test_a_retryable_failure_still_gets_a_second_attempt(tmp_path):
    """``retryable:true`` 走 release：任务回到队列，第二次领活产生第 2 行 attempt。"""
    database = make_database(tmp_path)
    seed_project(database)
    seed_task(database)
    first = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-1", harness_id="w-1", task_ids=("T-1",))
    ).items[0]

    SchedulerService(database).release(
        task_id="T-1",
        attempt_id=first.attempt_id,
        fencing_epoch=first.fencing_epoch,
        reason="the harness CLI failed before it could deliver",
    )

    second = ClaimService(database).claim(
        ClaimRequest(claim_request_id="cr-2", harness_id="w-1", task_ids=("T-1",))
    )
    assert list(second.items), "a retryable failure must leave the task claimable"
    assert count(database, "SELECT COUNT(*) FROM task_attempts WHERE task_id = 'T-1'") == 2


def test_provider_unavailable_is_not_retryable():
    retryable, reason = _classify_failure(
        ProviderUnavailable("no model", reason_code="protocol_unsupported", required_tools=TOOLS),
        task={"task_id": "T-1", "max_attempts": 3, "attempt_no": 1},
        manager=None,
        attempt_id="T-1.a1",
    )

    assert retryable is False
    assert "protocol_unsupported" in reason


def test_the_worker_asks_with_needs_tools_and_refuses_before_running():
    """复验「派单之后人才把窗口改成 CLI 档」这条边。

    批准时窗口还能接工具，之后人改了模型绑定；worker 开跑前必须带着
    ``needs_tools=true`` 再问一次平台，拿到 unavailable 后**不发起执行**、
    也不烧 attempt（ProviderUnavailable 分类为不可重试），任务留在队列里，
    由 ready_unclaimed sweep 带着原因提醒。
    """
    seen: dict[str, object] = {}

    class _Response:
        status_code = 200

        def json(self) -> dict[str, object]:
            return {
                "mode": "unavailable",
                "reason": "no model can serve this attempt's tools",
                "reason_code": "protocol_unsupported",
                "required_tools": list(TOOLS),
            }

    class _Client:
        def get(self, path: str, params: dict[str, object] | None = None):
            seen["path"] = path
            seen.update(params or {})
            return _Response()

    plan = ask_pool_plan(
        _Client(),
        task_id="T-1",
        harness_id="w-1",
        attempt_no=2,
        platform_url="http://platform",
        token="window-token",
        needs_tools=True,
    )

    assert seen["path"] == "/api/v1/pool/plan"
    assert seen["needs_tools"] is True, "worker 必须把执行类活的工具需求带上"
    assert plan["mode"] == "unavailable"
    error = ProviderUnavailable(
        str(plan["reason"]),
        reason_code=str(plan["reason_code"]),
        required_tools=tuple(plan["required_tools"]),
    )
    retryable, reason = _classify_failure(
        error,
        task={"task_id": "T-1", "max_attempts": 3, "attempt_no": 1},
        manager=None,
        attempt_id="T-1.a1",
    )
    assert retryable is False
    assert "protocol_unsupported" in reason
