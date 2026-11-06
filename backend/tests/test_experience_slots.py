from __future__ import annotations

import json

import pytest

from app.admin.slots import NO_LESSON, classify_experience, slot_for_experience, slot_prompt


@pytest.mark.parametrize(
    ("action", "root", "path", "source_task", "expected_slot", "expected"),
    [
        ("交付路径必须明确固定为 docs/startup.md", "产出物路径不明确，成员不知道该写到哪里", "docs/startup.md", "T-1", "delivery_path", "docs/startup.md"),
        ("派单目标必须显式包含任务 id", "派单目标漏了任务 id，返工时无法对应原任务", "docs/a.md", "FD-1", "task_id_format", "FD-1"),
        ("验收必须包含启动命令失败时的失败路径检查", "验收缺少启动命令失败时的失败路径检查", "docs/a.md", "T-1", "acceptance_check", "验收必须包含启动命令失败时的失败路径检查"),
        ("验收必须明确要求恰好三条启动步骤", "验收没有限定启动步骤条数", "docs/a.md", "T-1", "step_count", "恰好三条启动步骤"),
        ("验收必须要求记录错误日志路径 logs/startup.log", "故障排查没有指定错误日志路径", "docs/a.md", "T-1", "log_path", "logs/startup.log"),
        ("目标和验收都必须明确要求保留既有 README.md 入口", "改写启动段落时既有 README.md 入口可能被删除", "README.md", "T-1", "entry_preserved", "README.md"),
    ],
)
def test_concrete_values_map_to_finite_slots(action, root, path, source_task, expected_slot, expected):
    record = {
        "action": action,
        "root_cause": root,
        "spec_summary": json.dumps({"exclusive_paths": [path]}),
        "source_task_id": source_task,
    }
    slot = slot_for_experience(record)
    assert slot is not None
    assert slot["slot"] == expected_slot
    assert slot["expected"] == expected
    assert f"slot: {expected_slot}" in slot_prompt(slot)


def test_abstract_experience_is_unactionable():
    assert slot_for_experience({
        "action": "以后要更明确一些",
        "root_cause": "表述不够清楚",
        "spec_summary": "没有可机读路径",
        "source_task_id": "T-1",
    }) is None


def test_merged_success_is_no_lesson_even_with_a_delivery_path():
    record = {
        "kind": "merged",
        "root_cause": "无失败根因：验收与合并通过",
        "action": "按工单交付，并通过验证、审核与合并",
        "spec_summary": json.dumps({"exclusive_paths": ["docs/a.md"]}),
    }
    classified = classify_experience(record)
    assert classified["category"] == NO_LESSON
    assert classified["evidence_quote"] == "无失败根因：验收与合并通过"
    assert slot_for_experience(record) is None


def test_multiple_paths_do_not_invent_a_single_delivery_path():
    assert slot_for_experience({
        "action": "按工单交付，并通过验证、审核与合并",
        "root_cause": "原始记录没有明确失败原因",
        "spec_summary": json.dumps({"exclusive_paths": ["docs/a.md", "docs/b.md"]}),
        "source_task_id": "T-1",
    }) is None
