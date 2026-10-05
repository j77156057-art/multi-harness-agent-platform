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
