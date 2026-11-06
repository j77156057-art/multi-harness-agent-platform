from __future__ import annotations

from scripts.semantic_transfer_review import judge, literal_judge


def _result(goal: str, acceptance: str) -> dict:
    return {
        "tasks": [
            {
                "title": "t",
                "goal": goal,
                "exclusive_paths": ["ops/review-checklist.md"],
                "acceptance": acceptance,
                "harness_id": "m",
            }
        ]
    }


def test_negated_old_values_are_not_pollution_but_literal_says_they_are():
    result = _result(
        "生成 ops/review-checklist.md",
        "只保留 F1、F2；不含 F3/F4；不沿用 round-1 清单",
    )
    judged = judge(old_values=["F3", "F4", "round-1"], required_groups=[["F1"], ["F2"]], result=result)
    assert judged["semantic"] == "transferred"
    assert judged["evidence"] == []
    assert {row["value"] for row in judged["excluded_values"]} == {"F3", "F4", "round-1"}
    # 旧的字面判会把这三条都算成污染。
    assert literal_judge(old_values=["F3", "F4", "round-1"], required_groups=[["F1"], ["F2"]], result=result) == "copied_wrong"


def test_old_item_required_as_delivered_content_is_pollution():
    result = _result("生成 ops/review-checklist.md", "除 F1、F2 外，F3 仍需保留并写入清单")
    judged = judge(old_values=["F3"], required_groups=[["F1"], ["F2"]], result=result)
    assert judged["semantic"] == "copied_wrong"
    hit = judged["evidence"][0]
    assert hit["json_path"] == "tasks[0].acceptance"
    assert hit["value"] == "F3"
    assert "F3" in hit["quote"]


def test_forbidding_deletion_keeps_the_old_item_and_counts_as_pollution():
    result = _result("生成 ops/review-checklist.md", "不得删除 F3 检查项")
    judged = judge(old_values=["F3"], required_groups=[["F1"], ["F2"]], result=result)
    assert judged["semantic"] == "copied_wrong"
    assert judged["evidence"][0]["context"] == "keep_required"


def test_missing_new_value_without_old_value_is_missed():
    result = _result("生成 ops/review-checklist.md", "只保留 F1")
    judged = judge(old_values=["F3"], required_groups=[["F1"], ["F2"]], result=result)
    assert judged["semantic"] == "missed"
    assert judged["missing_required_groups"] == [["F2"]]


def test_conflicting_questions_are_escalated():
    judged = judge(
        old_values=["F3"],
        required_groups=[["F1"]],
        result={"tasks": [], "questions": ["F3 与 F1 冲突，是否保留 F3？"]},
    )
    assert judged["semantic"] == "escalated"


def test_truncated_output_is_unjudgeable_and_never_missed():
    judged = judge(
        old_values=["F3"],
        required_groups=[["F1"]],
        result=None,
        error="AdminHarnessError: no balanced JSON",
        finish_reason="length",
    )
    assert judged["semantic"] == "unjudgeable"
    assert "length" in judged["reason"]


def test_list_form_exclusion_and_forbidden_keep_are_not_pollution():
    list_form = _result("生成 ops/review-checklist.md", "只保留 F1、F2；F3/F4 不再检查")
    judged = judge(old_values=["F3", "F4"], required_groups=[["F1"], ["F2"]], result=list_form)
    assert judged["semantic"] == "transferred"
    assert {row["value"] for row in judged["excluded_values"]} == {"F3", "F4"}

    forbidden_keep = _result("生成 ops/review-checklist.md", "只保留 F1、F2；不得保留 F3")
    judged = judge(old_values=["F3"], required_groups=[["F1"], ["F2"]], result=forbidden_keep)
    assert judged["semantic"] == "transferred"
    assert judged["evidence"] == []


def test_generic_name_in_a_ban_clause_is_an_exclusion_not_a_delivery():
    result = _result(
        "整理证据清单",
        "不得存在“相关 CLI 实现文档”，只列 ops/adapter-notes.md 与 ops/receipt-notes.md 的实际章节",
    )
    judged = judge(
        old_values=["相关 CLI 实现文档", "平台回执实现文档"],
        required_groups=[["ops/adapter-notes.md"], ["ops/receipt-notes.md"]],
        result=result,
    )
    assert judged["semantic"] == "transferred"
    assert judged["evidence"] == []
    assert [row["value"] for row in judged["excluded_values"]] == ["相关 CLI 实现文档"]
