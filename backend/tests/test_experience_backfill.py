from __future__ import annotations

import json

from app.admin.experience import ExperienceStore
from app.admin.injection import confirmed_prompt, set_injection_enabled
from scripts.backfill_experience import run
from tests.claim_support import make_database, seed_project, seed_task


def _source_database(tmp_path):
    database = make_database(tmp_path)
    seed_project(database)
    for task_id, status in (
        ("T-1", "merged"),
        ("T-2", "merged"),
        ("T-3", "changes_requested"),
        ("T-4", "changes_requested"),
    ):
        seed_task(database, task_id=task_id, status=status, acceptance=[{"check": "human"}])
        spec = {
            "plan_version": "v1",
            "tasks": [{
                "id": task_id,
                "title": "DEMO · 同一规格",
                "goal": "补说明",
                "exclusive_paths": ["docs/start.md"],
                "acceptance": "包含失败路径验收",
            }],
        }
        with database.transaction() as conn:
            conn.execute(
                "UPDATE plan_snapshots SET content_json = ? WHERE snapshot_id = ?",
                (json.dumps(spec, ensure_ascii=False), f"snap-{task_id}"),
            )
            conn.execute(
                "INSERT INTO task_attempts(attempt_id, task_id, attempt_no, owner,"
                " fencing_epoch, base_commit, head_commit, status, created_at,"
                " registration_version) VALUES(?, ?, 1, 'codex', 1, 'base', 'head', ?, ?, 'reg-1')",
                (f"{task_id}.a1", task_id, status, "2026-01-01T00:00:00+00:00"),
            )
            conn.execute(
                "UPDATE tasks SET current_attempt_id = ? WHERE task_id = ?",
                (f"{task_id}.a1", task_id),
            )
            conn.execute(
                "INSERT INTO reviews(review_id, task_id, attempt_id, reviewer_harness_id,"
                " reviewer_provider, status, reviewed_commit, verdict, completed_seq, findings_json)"
                " SELECT ?, task_id, attempt_id, 'reviewer', 'other', 'completed', 'head',"
                " 'request_changes', NULL, ? FROM task_attempts WHERE task_id = ?",
                (
                    f"rv-{task_id}",
                    json.dumps([{"message": "缺少失败路径的验收条件"}], ensure_ascii=False),
                    task_id,
                ),
            ) if task_id in {"T-3", "T-4"} else None
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO task_events(seq, event_name, aggregate_type, aggregate_id,"
            " actor_type, actor_id, payload_json, payload_hash, created_at)"
            " VALUES(100, 'merge_success', 'task', 'T-1', 'platform', 'test', '{}', 'hash', ?)",
            ("2026-01-02T00:00:00+00:00",),
        )
    return database


def test_backfill_dry_run_apply_and_second_run_are_identical(tmp_path):
    database = _source_database(tmp_path)
    experience = tmp_path / "admin" / "experience.json"

    dry = run(database.path, experience, apply=False)
    assert dry["source_records"] == 4
    assert dry["planned_unique_records"] == 2
    assert dry["added"] == 2 and dry["merged"] == 2 and dry["skipped"] == 0
    assert not experience.exists()

    applied = run(database.path, experience, apply=True)
    assert applied["source_records"] == 4
    assert applied["actual_unique_records"] == 2
    assert applied["total_occurrences"] == 4
    first_bytes = experience.read_bytes()

    second_dry = run(database.path, experience, apply=False)
    assert second_dry["source_records"] == 4
    assert second_dry["planned_unique_records"] == 2
    assert second_dry["skipped"] == 4
    second = run(database.path, experience, apply=True)
    assert second["skipped"] == 4 and second["actual_unique_records"] == 2
    assert experience.read_bytes() == first_bytes

    records = ExperienceStore(experience).records()
    assert {record["source"] for record in records} == {"backfill"}
    assert sum(len(record["backfill_event_ids"]) for record in records) == 4
    assert all(record["source_task_id"] and record["source_snapshot"] for record in records)
    merged = next(record for record in records if record["kind"] == "merged")
    assert merged["recorded_at"] == "2026-01-02T00:00:00+00:00"
    missing = next(
        snapshot for record in records for snapshot in [record["source_snapshot"]]
        if snapshot["task"]["task_id"] == "T-2"
    )
    assert missing["task"]["outcome_at"] is None

    # 总开关开启也不会把未确认的回填经验自动注入。
    set_injection_enabled(database, enabled=True, decided_by="human-1")
    prompt, selections = confirmed_prompt(
        database, ExperienceStore(experience), group_id="G-1", project_name="DEMO"
    )
    assert prompt == "" and selections == []
