from __future__ import annotations

import json

import pytest

from app.admin.experience import (
    MAX_FINDINGS_SUMMARY_LENGTH,
    MAX_RECORDS,
    ExperienceStore,
    ExperienceStoreError,
)


def _rework(
    store: ExperienceStore,
    project: str,
    *,
    category: str = "backend",
    executor: str = "codex",
    findings: str = "缺少失败路径的验收条件",
) -> None:
    store.record_request_changes(
        project=project,
        task_category=category,
        executor=executor,
        spec_summary="实现接口并覆盖异常路径",
        findings_summary=findings,
    )


def _merged(
    store: ExperienceStore,
    project: str,
    *,
    category: str = "backend",
    executor: str = "codex",
) -> None:
    store.record_merged(
        project=project,
        task_category=category,
        executor=executor,
        spec_summary="实现接口并覆盖异常路径",
    )


def test_similar_rework_requires_two_projects_and_reports_evidence(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    _rework(store, "P-one")
    _rework(store, "P-one")
    _rework(store, "P-two", findings="另一个问题")

    assert store.prompt_lessons() == ""

    _rework(store, "P-two")
    prompt = store.prompt_lessons(task_category="backend", executor="codex")

    assert "提示词/规格易返工" in prompt
    assert "相似返工：缺少失败路径的验收条件" in prompt
    assert "证据项目：P-one、P-two（2 个），共 3 次" in prompt
    assert "另一个问题" not in prompt
    assert "实现接口并覆盖异常路径" not in prompt


def test_single_project_success_is_not_a_dispatch_lesson(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    _merged(store, "P-one")
    _merged(store, "P-one")

    assert store.prompt_lessons() == ""

    _merged(store, "P-two")
    prompt = store.prompt_lessons()

    assert "正面派单经验" in prompt
    assert "证据项目：P-one、P-two（2 个），共 3 次" in prompt


def test_success_and_rework_evidence_are_isolated(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    _rework(store, "P-one")
    _merged(store, "P-two")

    assert store.prompt_lessons() == ""

    _merged(store, "P-three")
    prompt = store.prompt_lessons()

    assert "正面派单经验" in prompt
    assert "P-two、P-three（2 个），共 2 次" in prompt
    assert "提示词/规格易返工" not in prompt


def test_prompt_scope_does_not_borrow_other_category_or_executor(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    for project in ("P-one", "P-two"):
        _rework(store, project, category="frontend", executor="claude")
        _merged(store, project, category="frontend", executor="claude")

    assert store.prompt_lessons(task_category="backend", executor="codex") == ""
    assert store.prompt_lessons(task_category="frontend", executor="codex") == ""
    assert "提示词/规格易返工" in store.prompt_lessons(executor="claude")


def test_semantically_equivalent_rework_is_shared_across_projects(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    _rework(store, "P-one", findings="缺少失败路径的验收条件")
    _rework(store, "P-two", findings="未覆盖异常分支的检查标准")

    prompt = store.prompt_lessons(task_category="backend", executor="codex")

    assert "提示词/规格易返工" in prompt
    assert "P-one、P-two（2 个），共 2 次" in prompt


def test_different_problems_are_not_merged_by_the_local_concept_matcher(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    _rework(store, "P-one", findings="缺少失败路径的验收条件")
    _rework(store, "P-two", findings="缺少成功路径的验收条件")
    _rework(store, "P-three", findings="缺少失败路径的测试用例")

    assert store.prompt_lessons() == ""


def test_damaged_json_reads_empty_and_is_never_silently_replaced(tmp_path):
    path = tmp_path / "experience.json"
    damaged = b'{"version": 1, "records": ['
    path.write_bytes(damaged)
    store = ExperienceStore(path)

    assert store.records() == []
    assert store.prompt_lessons() == ""
    assert path.read_bytes() == damaged

    with pytest.raises(ExperienceStoreError):
        _merged(store, "P-after-damage")
    assert path.read_bytes() == damaged


def test_records_persist_with_complete_structured_fields(tmp_path):
    path = tmp_path / "nested" / "experience.json"
    first = ExperienceStore(path)
    _rework(first, "P-one")
    _merged(first, "P-two", category="docs", executor="claude")

    second = ExperienceStore(path)
    records = second.records()

    assert [record["kind"] for record in records] == ["request_changes", "merged"]
    required = {
        "kind", "project", "task_category", "executor", "spec_summary",
        "root_cause", "root_key", "action", "result", "contract_id", "diff_hash",
        "recorded_at", "last_recorded_at", "occurrence_count", "scope",
        "cross_project_confirmed_by",
    }
    for record in records:
        assert required <= set(record)
        assert record["root_cause"]
        assert record["action"]
        assert record["result"]
        assert record["recorded_at"] and record["last_recorded_at"]
        assert record["occurrence_count"] == 1
        assert record["scope"] == "project"
        assert record["cross_project_confirmed_by"] == ""
    assert records[0]["findings_summary"] == "缺少失败路径的验收条件"
    assert json.loads(path.read_text(encoding="utf-8"))["records"] == records


def test_legacy_records_default_missing_fields_without_fabricating_history(tmp_path):
    path = tmp_path / "experience.json"
    path.write_text(json.dumps({"version": 1, "records": [{
        "kind": "request_changes",
        "project": "P-old",
        "task_category": "backend",
        "executor": "codex",
        "spec_summary": "旧规格",
        "findings_summary": "缺少失败路径的验收条件",
    }]}), encoding="utf-8")

    record = ExperienceStore(path).records()[0]

    assert record["root_cause"] == "缺少失败路径的验收条件"
    assert record["action"] == "未记录（旧版经验）"
    assert record["recorded_at"] == record["last_recorded_at"] == ""
    assert record["occurrence_count"] == 1
    assert record["scope"] == "project"
    assert record["cross_project_confirmed_by"] == ""
    assert record["experience_id"].startswith("exp-")
    assert record["source"] == record["source_task_id"] == record["source_event_id"] == ""
    assert record["source_snapshot"] == {}
    assert record["backfill_event_ids"] == []


def test_same_root_cause_merges_count_and_keeps_latest_result(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    for task_id, result, moment in (
        ("T-1", "第一次验收失败", "2026-11-04T01:00:00+00:00"),
        ("T-2", "第二次验收失败", "2026-11-04T02:00:00+00:00"),
    ):
        store.record_request_changes(
            project="P-one",
            task_category="backend",
            executor="codex",
            spec_summary="实现接口",
            findings_summary="缺少失败路径的验收条件",
            action="补充异常路径验收",
            result=result,
            contract_id=task_id,
            diff_hash=f"commit-{task_id}",
            recorded_at=moment,
        )

    (record,) = store.records()
    assert record["occurrence_count"] == 2
    assert record["recorded_at"] == "2026-11-04T01:00:00+00:00"
    assert record["last_recorded_at"] == "2026-11-04T02:00:00+00:00"
    assert record["contract_id"] == "T-2"
    assert record["diff_hash"] == "commit-T-2"
    assert record["result"] == "第二次验收失败"


def test_cross_project_recall_requires_explicit_confirmation(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    with pytest.raises(ValueError, match="explicit confirmation"):
        store.record_outcome(
            kind="merged", project="P-one", task_category="docs", executor="codex",
            spec_summary="未确认经验", root_cause="无失败根因", action="完成", result="合并",
            scope="cross_project",
        )
    _merged(store, "P-one", category="docs")
    store.record_outcome(
        kind="merged", project="P-one", task_category="docs", executor="codex",
        spec_summary="已确认经验", root_cause="无失败根因", action="完成", result="合并",
        scope="cross_project", cross_project_confirmed_by="human-admin",
    )

    assert store.recall(project="P-two", task_category="docs") == []
    confirmed = store.recall(
        project="P-two", task_category="docs", executor="codex", include_cross_project=True
    )
    assert [item["spec_summary"] for item in confirmed] == ["已确认经验"]


def test_abandoned_attempt_is_structured_but_not_a_dispatch_lesson(tmp_path):
    store = ExperienceStore(tmp_path / "experience.json")
    store.record_abandoned(
        project="P-one", task_category="backend", executor="codex",
        spec_summary="实现接口", reason="执行环境缺少依赖", contract_id="T-1",
    )

    (record,) = store.records()
    assert record["kind"] == "abandoned"
    assert record["root_cause"] == "执行环境缺少依赖"
    assert record["contract_id"] == "T-1"
    assert store.prompt_lessons() == ""


def test_text_and_record_count_bounds(tmp_path):
    path = tmp_path / "experience.json"
    store = ExperienceStore(path, max_records=2)
    for project in ("P-one", "P-two", "P-three"):
        _merged(store, project)

    assert [record["project"] for record in store.records()] == ["P-two", "P-three"]

    with pytest.raises(ValueError):
        _rework(store, "P-four", findings="x" * (MAX_FINDINGS_SUMMARY_LENGTH + 1))
    with pytest.raises(ValueError):
        store.record_merged(
            project=" ",
            task_category="backend",
            executor="codex",
            spec_summary="valid",
        )
    with pytest.raises(TypeError):
        store.record_merged(
            project="P-four",
            task_category="backend",
            executor="codex",
            spec_summary="valid",
            token="must-not-be-accepted",
        )
    with pytest.raises(ValueError):
        ExperienceStore(path, max_records=MAX_RECORDS + 1)

    assert [record["project"] for record in store.records()] == ["P-two", "P-three"]


def test_failed_atomic_replace_preserves_previous_file_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / "experience.json"
    store = ExperienceStore(path)
    _merged(store, "P-original")
    original = path.read_bytes()

    def fail_replace(_source, _target):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("app.admin.experience.os.replace", fail_replace)
    with pytest.raises(ExperienceStoreError):
        _merged(store, "P-new")

    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
