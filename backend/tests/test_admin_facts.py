from __future__ import annotations

import pytest

from app.admin.facts import FactValidationError, validate_answer_facts


def _task(**overrides):
    task = {
        "task_id": "T-1",
        "status": "awaiting_review",
        "owner": None,
        "executor": "harness-1",
        "exclusive_paths": ["docs/startup.md"],
    }
    task.update(overrides)
    return task


def test_deterministic_fields_are_corrected_with_readable_evidence():
    answer = (
        "T-1 当前正在执行，尚无执行者，本群共 2 条任务。"
        "下一步：成员继续执行。允许修改的路径：office/src/main.js。"
    )

    corrected, issues = validate_answer_facts(answer, question="T-1 现在怎样？", tasks=[_task()])

    assert "当前：待验收" in corrected
    assert "执行者为 harness-1" in corrected
    assert "本群共 1 条任务" in corrected
    assert "下一步：审核人领取审阅并裁决" in corrected
    assert "允许修改的路径：docs/startup.md" in corrected
    by_field = {issue.field: issue for issue in issues}
    assert by_field["executor"].expected == "harness-1"
    assert by_field["executor"].model_text == "尚无执行者"
    assert by_field["status"].expected == "待验收"
    assert by_field["next_action"].expected == "审核人领取审阅并裁决"
    assert by_field["exclusive_paths"].expected == "docs/startup.md"
    assert by_field["task_count"].expected == "1"
    assert all(issue.action == "corrected" for issue in issues)


def test_no_executor_and_suggestions_are_not_false_positives():
    answer = (
        "T-1 当前待领取，尚无执行者。"
        "下一步：方案指定的执行窗口领取任务。建议如果预算允许，可以并行。"
    )
    task = _task(status="ready", executor=None, exclusive_paths=[])

    corrected, issues = validate_answer_facts(answer, question="T-1 下一步？", tasks=[task])

    assert corrected == answer
    assert issues == []


def test_conditional_future_plan_is_not_rewritten():
    answer = "T-1 当前待验收。下一步：如果审核人同意，可以合并。"

    corrected, issues = validate_answer_facts(answer, question="T-1 下一步？", tasks=[_task()])

    assert corrected == answer
    assert issues == []


def test_ambiguous_task_reference_is_rejected_instead_of_guessed():
    tasks = [_task(), _task(task_id="T-2")]
    answer = "T-1 与 T-2 当前正在执行，下一步：成员继续执行。"

    with pytest.raises(FactValidationError) as error:
        validate_answer_facts(answer, question="两条任务怎样？", tasks=tasks)

    issue = error.value.issues[0]
    assert issue.field == "task_reference"
    assert issue.action == "rejected"
    assert issue.expected == "唯一任务对象"


def test_counts_and_paths_only_change_when_the_fact_source_disagrees():
    answer = "本群共 1 条任务。允许修改的路径：docs/startup.md。"

    corrected, issues = validate_answer_facts(answer, question="T-1 的路径？", tasks=[_task()])

    assert corrected == answer
    assert issues == []
