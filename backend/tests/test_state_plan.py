"""MVP-03 state and Plan validation tests (DOC-STATE-001/DOC-PLAN-001)."""

from __future__ import annotations

import pytest

from app.plan import PlanSpec, PlanValidator
from app.spec import EventKind, TaskStatus
from app.state import InvalidTransition, TaskProjection, default_transition_table


def test_transition_table_has_outgoing_edges_and_unique_keys():
    table = default_transition_table()
    assert table.validate() == []
    assert len(table.events()) >= 40
    assert all(status.is_terminal or table.outgoing(status) for status in TaskStatus)


def test_main_task_flow_and_replay_is_deterministic():
    table = default_transition_table()
    task = TaskProjection("T-001")
    events = [
        EventKind.PLAN_APPROVE,
        EventKind.CLAIM,
        EventKind.START,
        EventKind.SUBMIT_RESULT,
        EventKind.VERIFY_PASSED,
        EventKind.APPROVE,
        EventKind.MERGE_START,
        EventKind.MERGE_SUCCESS,
    ]
    for seq, event in enumerate(events, start=1):
        task = task.apply(event, seq=seq, table=table)
    assert task.status is TaskStatus.MERGED
    assert task.seq == len(events)
    assert task.last_event is EventKind.MERGE_SUCCESS


def test_invalid_transition_is_rejected_without_mutating_projection():
    task = TaskProjection("T-001")
    with pytest.raises(InvalidTransition):
        task.apply(EventKind.MERGE_SUCCESS)
    assert task.status is TaskStatus.DRAFT
    assert task.seq == 0


def test_dependency_wait_has_release_and_orphan_exits():
    task = TaskProjection("T-001")
    task = task.apply(EventKind.PLAN_APPROVE_DEPENDENCY_PENDING)
    assert task.status is TaskStatus.READY_WAITING_DEPS
    assert task.apply(EventKind.ORPHAN_DETECTED).status is TaskStatus.ESCALATED
    task = TaskProjection("T-002").apply(EventKind.PLAN_APPROVE_DEPENDENCY_PENDING)
    assert task.apply(EventKind.DEPENDENCIES_SATISFIED).status is TaskStatus.READY


def test_plan_validator_accepts_contract_and_rejects_cycle_and_unknowns():
    valid = PlanSpec.from_mapping(
        {
            "plan_id": "plan-1",
            "registered_capabilities": ["backend"],
            "registered_resources": ["db:test"],
            "evidence_sources": ["tests"],
            "verify_profiles": ["backend-default"],
            "tasks": [
                {
                    "id": "T-001",
                    "capabilities": ["backend"],
                    "exclusive_resources": ["db:test"],
                    "outputs": [{"name": "model", "type": "code", "schema_version": "v1"}],
                    "acceptance": [{"id": "ok", "check": "cmd", "evidence_source": "tests"}],
                    "verify_profile": "backend-default",
                },
                {
                    "id": "T-002",
                    "depends_on": ["T-001"],
                    "consumes": [{"output": "model", "type": "code", "schema_version": "v1"}],
                    "acceptance": [{"id": "review", "check": "human"}],
                },
            ],
        }
    )
    assert PlanValidator().validate(valid).ok

    invalid = PlanSpec.from_mapping(
        {
            "plan_id": "plan-bad",
            "registered_capabilities": [],
            "tasks": [
                {"id": "A", "depends_on": ["B"], "capabilities": ["missing"]},
                {"id": "B", "depends_on": ["A"]},
            ],
        }
    )
    result = PlanValidator().validate(invalid)
    assert not result.ok
    assert any("cycle" in error for error in result.errors)
    assert any("unregistered capability" in error for error in result.errors)


def _plan_with_acceptance(acceptance, **overrides):
    """构造单任务方案，只让 acceptance 那一项决定evidence 校验结果。"""
    raw = {
        "plan_id": "plan-evidence",
        "tasks": [{"id": "T-001", "acceptance": [acceptance]}],
    }
    raw.update(overrides)
    return PlanSpec.from_mapping(raw)


def test_plan_validator_requires_evidence_source_for_cmd_acceptance():
    """R10 §9：门禁项的 evidence source 必须来自已注册能力。

    旧实现是 `if source is not None:`，所以 `cmd` 判据不声明 evidence source
    时完全不检查 —— 等于绕过了 §9 的整条要求。
    """
    result = PlanValidator().validate(
        _plan_with_acceptance({"id": "ok", "check": "cmd"}, evidence_sources=["tests"])
    )
    assert not result.ok
    assert any("cmd acceptance without an evidence source" in e for e in result.errors)

    # 声明了且在注册表内 -> 放行
    assert (
        PlanValidator()
        .validate(
            _plan_with_acceptance(
                {"id": "ok", "check": "cmd", "evidence_source": "tests"},
                evidence_sources=["tests"],
            )
        )
        .ok
    )


def test_plan_validator_rejects_evidence_source_when_registry_is_empty():
    """fail-closed：`evidence_sources` 为空时不再静默放行未注册的 source。

    旧实现是 `elif evidence_sources and ...`，注册表为空就整条跳过。叠加
    「可以不声明 source」之后，一个既不声明 source、注册表又为空的方案能通过
    全部 evidence 校验，等于把 ADR-032 要求的登记整体绕过。
    """
    result = PlanValidator().validate(
        _plan_with_acceptance({"id": "ok", "check": "cmd", "evidence_source": "tests/anything.py"})
    )
    assert not result.ok
    assert any("unregistered evidence source" in e for e in result.errors)


def test_plan_validator_allows_human_acceptance_without_evidence_source():
    """`human` 判据由人读，没有可执行文件可指，不应强制 evidence source。"""
    assert (
        PlanValidator()
        .validate(
            _plan_with_acceptance({"id": "review", "check": "human"}, evidence_sources=["tests"])
        )
        .ok
    )


def test_plan_validator_rejects_blank_evidence_source():
    """声明了但为空白串：与未注册同属不可用证据，必须拒绝。"""
    result = PlanValidator().validate(
        _plan_with_acceptance(
            {"id": "ok", "check": "cmd", "evidence_source": "   "},
            evidence_sources=["tests"],
        )
    )
    assert not result.ok
    assert any("empty evidence source" in e for e in result.errors)


def test_plan_validator_rejects_duplicate_dependency_edges_without_fake_cycle():
    """R10 §9：重复声明的依赖边必须被拒绝，且不能被 Kahn 误判成环。

    旧实现的 graph 是 set 去重的，但 indegree 对每条原始边无条件 +1，
    ["T-001", "T-001"] 会让 indegree 虚高为 2，拓扑排序永远清不完，
    于是重复边被误报为「contains a cycle」。
    """
    plan = PlanSpec.from_mapping(
        {
            "plan_id": "plan-dup-edge",
            "tasks": [
                {"id": "T-001"},
                {"id": "T-002", "depends_on": ["T-001", "T-001"]},
            ],
        }
    )
    result = PlanValidator().validate(plan)
    assert any("repeats a dependency edge" in error for error in result.errors)
    assert not any("cycle" in error for error in result.errors)


@pytest.mark.parametrize(
    ("status", "event", "expected"),
    [
        (TaskStatus.AWAITING_HUMAN, EventKind.HUMAN_CANCEL, TaskStatus.CANCELLED),
        (TaskStatus.STALE, EventKind.HUMAN_CANCEL, TaskStatus.CANCELLED),
        (TaskStatus.RECLAIMING, EventKind.HUMAN_CANCEL, TaskStatus.CANCELLED),
        (TaskStatus.VERIFYING, EventKind.CANCEL, TaskStatus.CANCELLED),
        (TaskStatus.QUEUED, EventKind.AGING_TIMEOUT, TaskStatus.ESCALATED),
        (TaskStatus.BLOCKED, EventKind.BLOCK_TIMEOUT, TaskStatus.ESCALATED),
        (
            TaskStatus.AWAITING_REVIEW,
            EventKind.VERIFY_CODE_FAILED,
            TaskStatus.CHANGES_REQUESTED,
        ),
        (TaskStatus.APPROVED, EventKind.REVIEW_WAIT_TIMEOUT, TaskStatus.ESCALATED),
        (TaskStatus.MERGING, EventKind.REVIEW_WAIT_TIMEOUT, TaskStatus.ESCALATED),
        (
            TaskStatus.CHANGES_REQUESTED,
            EventKind.REVIEW_ROUND_EXHAUSTED,
            TaskStatus.ESCALATED,
        ),
        (TaskStatus.CLAIMED, EventKind.PLAN_HASH_CHANGED, TaskStatus.PLAN_DRIFT),
        (
            TaskStatus.AWAITING_REVIEW,
            EventKind.IMPL_HASH_CHANGED,
            TaskStatus.IMPL_DRIFT,
        ),
        (
            TaskStatus.PLAN_DRIFT,
            EventKind.HUMAN_REOPEN_NO_OWNER,
            TaskStatus.READY,
        ),
    ],
)
def test_r10_section_10_missing_edges_are_now_accepted(status, event, expected):
    """MVP-03-amend：验收时发现的 25 条缺失边的代表性行为对拍。"""
    table = default_transition_table()
    projection = TaskProjection("T-001", status=status)
    assert projection.apply(event, table=table).status is expected


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (TaskStatus.ESCALATED, EventKind.CANCEL),
        (TaskStatus.STALE, EventKind.CANCEL),
        (TaskStatus.RECLAIMING, EventKind.CANCEL),
        (TaskStatus.AWAITING_HUMAN, EventKind.CANCEL),
        (TaskStatus.DRAFT_REJECTED, EventKind.CANCEL),
        (TaskStatus.READY_WAITING_DEPS, EventKind.CANCEL),
        (TaskStatus.NEEDS_HELP, EventKind.DEPENDENCY_MISSING),
        (TaskStatus.AWAITING_HUMAN, EventKind.ESCALATION_REMINDER),
        (TaskStatus.BLOCKED, EventKind.PLAN_HASH_CHANGED),
        (TaskStatus.BLOCKED, EventKind.IMPL_HASH_CHANGED),
    ],
)
def test_r10_section_10_forbidden_edges_are_rejected(status, event):
    """MVP-03-amend：R10 §10/§37.7 不允许的边必须继续被拒绝。"""
    table = default_transition_table()
    projection = TaskProjection("T-001", status=status)
    with pytest.raises(InvalidTransition):
        projection.apply(event, table=table)


def test_plan_validator_rejects_limits_resources_globs_and_acceptance():
    plan = PlanSpec.from_mapping(
        {
            "plan_id": "plan-limits",
            "limits": {"max_tasks": 1, "max_budget_tokens": 1, "max_path_globs": 1},
            "registered_resources": [],
            "tasks": [
                {
                    "id": "A",
                    "budget_tokens": 2,
                    "exclusive_resources": ["not-registered"],
                    "acceptance": [{"check": "judge"}],
                },
                {"id": "B"},
            ],
            "path_globs": ["src\\a", "src/a"],
        }
    )
    result = PlanValidator().validate(plan)
    assert not result.ok
    assert any("task count" in error for error in result.errors)
    assert any("budget" in error for error in result.errors)
    assert any("unregistered resource" in error for error in result.errors)
    assert any("invalid acceptance" in error for error in result.errors)
    assert any("POSIX" in error for error in result.errors)
    assert any("duplicates" in error for error in result.errors)


def test_the_acceptance_may_not_require_a_file_outside_the_landing_scope():
    """验收条款要求交出的文件，必须在工单的落点范围里。

    2026-10-29 的真实演练：管理员在第 2 轮把「提交 SHA 往哪儿交」折成了
    「另写一个 portfolio/lanes/<lane>/receipt.txt」，而 exclusive_paths 只允许
    retro-round2.md。成员照做写了回执，平台按范围规则正确地拒收了 —— 但错的是
    方案：它要求人交一样东西，又不让那样东西进仓库。
    """
    contradictory = PlanSpec.from_mapping(
        {
            "plan_id": "plan-receipt",
            "tasks": [
                {
                    "id": "T-001",
                    "exclusive_paths": ["portfolio/lanes/claude/retro-round2.md"],
                    "acceptance": [
                        {
                            "id": "ok",
                            "check": "human",
                            "description": (
                                "(5) portfolio/lanes/claude/receipt.txt 中的 SHA 与 "
                                "git log --oneline -20 中实际存在的提交一致"
                            ),
                        }
                    ],
                }
            ],
        }
    )
    result = PlanValidator().validate(contradictory)
    assert not result.ok
    assert any("receipt.txt" in error for error in result.errors)


def test_the_acceptance_may_still_say_which_file_it_must_not_touch():
    """「未改动 X」是禁止，不是要求 —— 这样的条款不该被当成矛盾。"""
    prohibiting = PlanSpec.from_mapping(
        {
            "plan_id": "plan-prohibition",
            "tasks": [
                {
                    "id": "T-001",
                    "exclusive_paths": ["portfolio/lanes/claude/retro-round2.md"],
                    "acceptance": [
                        {
                            "id": "ok",
                            "check": "human",
                            "description": (
                                "(7) 该提交未改动 portfolio/lanes/claude/retro.md"
                                " 及别的车道的文件"
                            ),
                        }
                    ],
                }
            ],
        }
    )
    assert PlanValidator().validate(prohibiting).ok
