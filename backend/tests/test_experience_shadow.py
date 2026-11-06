from __future__ import annotations

import json

from app.admin.experience import ExperienceStore
from scripts import evaluate_experience_shadow as shadow


def _row(a="缺少失败路径的验收条件", b="未覆盖异常分支的检查标准", **fields):
    return {"id": "X", "a": a, "b": b, **fields}


def _vectors(row, a, b):
    return {shadow.sha_text(row["a"]): a, shadow.sha_text(row["b"]): b}


def test_identity_boundary_rejects_even_identical_vectors_and_concepts():
    identity = {"task_category": "backend", "executor": "codex"}
    row = _row(
        identity_a=identity,
        identity_b={"task_category": "frontend", "executor": "codex"},
    )
    vectors = _vectors(row, [1.0, 0.0], [1.0, 0.0])

    assert not shadow.old_decision(row, identity)
    merged, score = shadow.candidate_decision(row, identity, vectors)
    assert not merged and score == 1.0


def test_concept_key_is_high_precision_shortcut_and_vector_is_candidate_only():
    identity = {"task_category": "backend", "executor": "codex"}
    same = _row()
    different = _row("缺少失败路径的验收条件", "缺少成功路径的验收条件")

    assert shadow.old_decision(same, identity)
    assert not shadow.old_decision(different, identity)
    assert shadow.candidate_decision(different, identity, _vectors(different, [1.0, 0.0], [0.9, 0.1]))[0]
    assert not shadow.candidate_decision(different, identity, _vectors(different, [1.0, 0.0], [0.0, 1.0]))[0]


def test_cluster_metric_catches_transitive_third_issue():
    rows = [
        {"id": "A", "label": 1, "family_id": "F", "a": "a", "b": "b"},
        {"id": "B", "label": 0, "issue_id": "H", "a": "b", "b": "c"},
        {"id": "C", "label": 0, "issue_id": "H", "a": "a", "b": "c"},
    ]

    result = shadow.cluster_metrics(rows, {"A": True, "B": True, "C": False})

    assert result["cluster_errors"] == 1
    assert result["wrong_cross_project_prompts"] == 1
    assert result["bad_components"] == [["F", "H"]]


def test_pair_metrics_include_sample_counts_and_wilson_intervals():
    rows = [
        {"id": "P", "label": 1}, {"id": "N", "label": 0},
        {"id": "H", "label": 0},
    ]

    result = shadow.pair_metrics(rows, {"P": True, "N": False, "H": True})

    assert result["n"] == 3 and result["tp"] == 1 and result["fp"] == 1
    assert result["accuracy"] == 2 / 3
    assert result["positive_recall"] == 1
    assert result["false_merge_rate"] == 0.5
    assert 0 <= result["accuracy_ci95"][0] <= 2 / 3 <= result["accuracy_ci95"][1] <= 1


def test_derived_index_has_model_source_text_and_record_lineage(monkeypatch):
    monkeypatch.setattr(shadow, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(shadow, "model_fingerprint", lambda: "model-digest")

    index = shadow.build_derived_index(
        [{"locator": "record:000", "record": {"project": "P-one"}, "text": "缺少测试"}],
        "sha256:source",
    )

    assert index["schema_version"] == 1
    assert index["model_id"] == shadow.MODEL
    assert index["dimensions"] == 2
    assert index["model_fingerprint"] == "model-digest"
    assert index["source_sha256"] == "sha256:source"
    assert index["records"][0]["locator"] == "record:000"
    assert index["records"][0]["record"] == {"project": "P-one"}
    assert index["records"][0]["text_sha256"] == shadow.sha_text("缺少测试")
    assert index["records"][0]["vector"] == [1.0, 0.0]


def test_rebuild_is_atomic_and_original_experience_bytes_are_unchanged(tmp_path, monkeypatch):
    experience = tmp_path / "experience.json"
    output = tmp_path / "shadow.json"
    store = ExperienceStore(experience)
    store.record_request_changes(
        project="P-one", task_category="backend", executor="codex",
        spec_summary="覆盖失败路径", findings_summary="缺少失败路径的验收条件",
    )
    original = experience.read_bytes()
    monkeypatch.setattr(shadow, "EXPERIENCE", experience)
    monkeypatch.setattr(shadow, "REAL_INDEX", output)
    monkeypatch.setattr(shadow, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(shadow, "model_fingerprint", lambda: "model-digest")

    shadow.rebuild()

    assert experience.read_bytes() == original
    index = json.loads(output.read_text(encoding="utf-8"))
    assert index["source_sha256"] == shadow.sha_bytes(original)
    assert len(index["records"]) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_rebuild_empty_real_file_does_not_call_embedding(tmp_path, monkeypatch):
    experience = tmp_path / "missing.json"
    monkeypatch.setattr(shadow, "EXPERIENCE", experience)
    monkeypatch.setattr(shadow, "REAL_INDEX", tmp_path / "shadow.json")
    monkeypatch.setattr(shadow, "model_fingerprint", lambda: "model-digest")
    def no_network(texts):
        assert texts == []
        return []

    monkeypatch.setattr(shadow, "embed", no_network)

    shadow.rebuild()

    assert not experience.exists()
    assert json.loads((tmp_path / "shadow.json").read_text(encoding="utf-8"))["records"] == []
