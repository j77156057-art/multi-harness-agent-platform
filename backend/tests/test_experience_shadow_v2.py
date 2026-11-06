from __future__ import annotations

import json

from scripts import evaluate_experience_shadow_v2 as shadow


def test_outcome_and_forbidden_word_polarity_are_hard_vetoes():
    assert shadow.guard({"a": "五项验收均满足", "b": "五项验收不满足"})["verdict"] == "different"
    assert shadow.guard({"a": "禁用词零命中", "b": "正文包含多个禁用词"})["reason"] == "forbidden_word_polarity"


def test_preserve_special_case_cannot_merge_delete():
    row = {"a": "必须保留既有入口", "b": "删除既有入口"}

    assert shadow.guard(row)["reason"] == "preserve_delete_polarity"


def test_path_and_field_objects_must_match():
    assert shadow.guard({"a": "修改 docs/checklist.md", "b": "修改 docs/readme.md"})["reason"] == "path_object"
    assert shadow.guard({"a": "exclusive_paths 只允许 docs/a.md", "b": "included_paths 只允许 docs/a.md"})["reason"] == "field_object"


def test_quantity_and_contract_conflicts_are_hard_vetoes():
    assert shadow.guard({"a": "正文至少200字", "b": "正文不超过100字"})["reason"] == "quantity_condition"
    row = {"a": 'contract_id: "C-1" 缺少测试', "b": 'contract_id: "C-2" 缺少测试'}
    assert shadow.guard(row)["reason"] == "explicit_contract_conflict"


def test_missing_present_relation_and_success_failure_are_different():
    assert shadow.guard({"a": "缺少异常处理的验收", "b": "包含异常处理的验收"})["reason"] == "missing_present_relation"
    assert shadow.guard({"a": "缺少失败路径的验收", "b": "缺少成功路径的验收"})["reason"] == "success_failure_object"


def test_unknown_and_cross_identity_default_to_no_merge():
    unknown = shadow.guard({"a": "Alpha", "b": "Beta"})
    assert unknown["verdict"] == "unknown"
    assert not (unknown["verdict"] == "same")
    row = {
        "a": "缺少失败路径的验收条件", "b": "未覆盖异常分支的检查标准",
        "identity_a": shadow.IDENTITY,
        "identity_b": {"task_category": "frontend", "executor": "codex"},
    }
    assert shadow.guard(row)["reason"] == "identity_boundary"


def test_exact_same_is_the_only_deterministic_positive():
    assert shadow.guard({"a": "  缺少   测试 ", "b": "缺少 测试"})["verdict"] == "same"


def test_model_parse_failure_defaults_to_different(monkeypatch):
    monkeypatch.setattr(shadow.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError()))

    result = shadow.adjudicate({"a": "A", "b": "B"})

    assert result["verdict"] == "different"
    assert result["error"] == "TimeoutError_default_different"


def test_model_must_restate_both_roots_before_same(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": shadow.MODEL,
                "message": {"content": json.dumps({
                    "root_a": "缺验收", "root_b": "缺验收", "verdict": "same", "reason": "同根因",
                })},
                "prompt_eval_count": 100, "eval_count": 20,
            }

    monkeypatch.setattr(shadow.httpx, "post", lambda *args, **kwargs: Response())
    result = shadow.adjudicate({"a": "A", "b": "B"})
    assert result["verdict"] == "same" and result["usage"]["prompt_tokens"] == 100

    class Incomplete(Response):
        def json(self):
            return {"model": shadow.MODEL, "message": {"content": '{"verdict":"same","reason":"点头"}'}}

    monkeypatch.setattr(shadow.httpx, "post", lambda *args, **kwargs: Incomplete())
    result = shadow.adjudicate({"a": "A", "b": "B"})
    assert result["verdict"] == "different"
    assert result["error"] == "invalid_model_structure_default_different"


def test_split_is_family_disjoint_and_balanced():
    dataset = {}
    for category, origin in (
        ("positive", "real_seed+local_paraphrase"),
        ("hard_negative", "real_seed+local_mutation"),
        ("ordinary_negative", "real_findings_different_tasks"),
    ):
        rows = []
        for family in range(40):
            copies = 3 if category == "positive" else 1
            for copy in range(copies):
                rows.append({
                    "id": f"{category}-{family}-{copy}",
                    "family_id": f"F-{family:03d}" if category != "ordinary_negative" else f"N-{family:03d}",
                    "label": int(category == "positive"), "origin": origin,
                    "a": f"A{family}{copy}", "b": f"B{family}{copy}",
                })
        dataset[category] = rows

    halves = shadow.split_rows(dataset)

    for half in ("dev", "test"):
        assert len(halves[half]) == 100
        assert sum(row["label"] for row in halves[half]) == 60
    dev_families = {row["family_id"] for row in halves["dev"]}
    test_families = {row["family_id"] for row in halves["test"]}
    assert not dev_families & test_families
