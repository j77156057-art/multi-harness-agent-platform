"""Dry-run first, idempotently backfill historical task outcomes into experience.json."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from app.admin.experience import ExperienceStore
from app.admin.service import task_category_for_spec
from app.config import settings


def _rows(database: Path) -> tuple[list[sqlite3.Row], dict[str, dict[str, Any]]]:
    connection = sqlite3.connect(
        "file:" + database.as_posix() + "?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    try:
        merged = connection.execute(
            "SELECT t.task_id, t.group_id, t.plan_snapshot_id, t.status, t.created_at,"
            " t.updated_at, t.assigned_harness_ids_json, ta.owner AS executor, ta.head_commit,"
            " (SELECT e.created_at FROM task_events e WHERE e.aggregate_type = 'task'"
            "  AND e.aggregate_id = t.task_id AND e.event_name = 'merge_success'"
            "  ORDER BY e.seq DESC LIMIT 1) AS outcome_at"
            " FROM tasks t LEFT JOIN task_attempts ta ON ta.attempt_id = t.current_attempt_id"
            " WHERE t.status = 'merged' ORDER BY t.task_id"
        ).fetchall()
        reviews = connection.execute(
            "SELECT r.review_id, r.task_id, r.attempt_id, r.reviewed_commit, r.findings_json,"
            " r.completed_seq, t.group_id, t.plan_snapshot_id, t.status, t.created_at,"
            " t.updated_at, t.assigned_harness_ids_json, ta.owner AS executor, ta.head_commit,"
            " e.created_at AS outcome_at"
            " FROM reviews r JOIN tasks t ON t.task_id = r.task_id"
            " LEFT JOIN task_attempts ta ON ta.attempt_id = r.attempt_id"
            " LEFT JOIN task_events e ON e.aggregate_type = 'task'"
            " AND e.aggregate_id = r.task_id AND e.seq = r.completed_seq"
            " WHERE r.verdict = 'request_changes' AND r.status = 'completed'"
            " ORDER BY r.review_id"
        ).fetchall()
        snapshots: dict[str, dict[str, Any]] = {}
        for row in connection.execute("SELECT snapshot_id, content_json FROM plan_snapshots"):
            try:
                snapshots[str(row["snapshot_id"])] = json.loads(str(row["content_json"]))
            except ValueError:
                continue
        return [*merged, *reviews], snapshots
    finally:
        connection.close()


def _spec(row: sqlite3.Row, snapshots: dict[str, dict[str, Any]]) -> dict[str, Any]:
    snapshot = snapshots.get(str(row["plan_snapshot_id"] or ""), {})
    for item in snapshot.get("tasks", ()) or ():
        if isinstance(item, dict) and str(item.get("id") or "") == str(row["task_id"]):
            return item
    return {}


def _project(row: sqlite3.Row, spec: dict[str, Any]) -> str:
    title = str(spec.get("title") or row["task_id"])
    title_project = title.split(" · ", 1)[0] if " · " in title else ""
    return str(spec.get("project_label") or title_project or row["group_id"])


def _executor(row: sqlite3.Row) -> str:
    if row["executor"]:
        return str(row["executor"])
    try:
        assigned = json.loads(str(row["assigned_harness_ids_json"] or "[]"))
    except ValueError:
        assigned = []
    return str(assigned[0]) if assigned else "unassigned"


def _summary(row: sqlite3.Row, spec: dict[str, Any]) -> str:
    return json.dumps(
        {
            "title": spec.get("title") or row["task_id"],
            "goal": spec.get("goal"),
            "exclusive_paths": spec.get("exclusive_paths"),
            "acceptance": spec.get("acceptance"),
        },
        ensure_ascii=False,
    )[:1900]


def _snapshot(row: sqlite3.Row, spec: dict[str, Any]) -> dict[str, Any]:
    original = {
        "task": {
            "task_id": row["task_id"],
            "group_id": row["group_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "executor": row["executor"],
            "head_commit": row["head_commit"],
            "outcome_at": row["outcome_at"],
        },
        "spec": spec,
    }
    if "review_id" in row.keys():
        original["review"] = {
            "review_id": row["review_id"],
            "attempt_id": row["attempt_id"],
            "reviewed_commit": row["reviewed_commit"],
            "completed_seq": row["completed_seq"],
            "findings": json.loads(str(row["findings_json"] or "[]")),
        }
    return original


def build_records(database: Path, store: ExperienceStore) -> list[dict[str, Any]]:
    rows, snapshots = _rows(database)
    records: list[dict[str, Any]] = []
    for row in rows:
        spec = _spec(row, snapshots)
        is_review = "review_id" in row.keys()
        findings: list[dict[str, Any]] = []
        if is_review:
            try:
                findings = json.loads(str(row["findings_json"] or "[]"))
            except ValueError:
                findings = []
        finding_text = "；".join(
            str(item.get("message") or "").strip()
            for item in findings
            if isinstance(item, dict) and str(item.get("message") or "").strip()
        )[:1800]
        if is_review and not finding_text:
            finding_text = "历史验收返修，原始记录未提供具体意见"
        kind = "request_changes" if is_review else "merged"
        source_event = (
            f"review:{row['review_id']}" if is_review else f"merged:{row['task_id']}"
        )
        records.append(
            store._record(
                kind=kind,
                project=_project(row, spec),
                task_category=task_category_for_spec(spec),
                executor=_executor(row),
                spec_summary=_summary(row, spec),
                findings_summary=finding_text if is_review else None,
                root_cause=(
                    f"验收未通过（unknown）：{finding_text}"
                    if is_review
                    else "无失败根因：验收与合并通过"
                ),
                action=(
                    "按原始验收 findings 返修；历史记录未提供进一步归因"
                    if is_review
                    else "按工单交付，并通过验证、审核与合并"
                ),
                result=(
                    "验收结论 request_changes（历史回填）"
                    if is_review
                    else "已合并（历史回填）"
                ),
                contract_id=str(row["task_id"]),
                diff_hash=str((row["reviewed_commit"] if is_review else row["head_commit"]) or ""),
                recorded_at=str(row["outcome_at"] or ""),
                source="backfill",
                source_task_id=str(row["task_id"]),
                source_event_id=source_event,
                source_snapshot=_snapshot(row, spec),
            )
        )
    return records


def run(database: Path, experience: Path, *, apply: bool) -> dict[str, Any]:
    store = ExperienceStore(experience)
    source_records = build_records(database, store)
    records = store.records()
    plans: list[dict[str, str]] = []
    counts = {"add": 0, "merge": 0, "skip": 0}
    for record in source_records:
        records, action = store.merge_preview(records, record)
        counts[action] += 1
        plans.append(
            {
                "source_event_id": record["source_event_id"],
                "experience_id": record["experience_id"],
                "action": action,
            }
        )
    planned_unique = len(records)
    actual_unique: int | None = None
    if apply:
        # 全批次只替换一次，避免 Windows 下几十次原子替换放大文件占用冲突。
        if counts["add"] or counts["merge"]:
            store._write(records[-store.max_records :])
        actual_unique = len(store.records())
        records = store.records()
    return {
        "mode": "apply" if apply else "dry-run",
        "database": str(database),
        "experience": str(experience),
        "source_records": len(source_records),
        "planned_unique_records": planned_unique,
        "actual_unique_records": actual_unique,
        "added": counts["add"],
        "merged": counts["merge"],
        "skipped": counts["skip"],
        "total_occurrences": sum(int(item["occurrence_count"]) for item in records),
        "default_unconfirmed": True,
        "plans": plans,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=settings.db_path)
    parser.add_argument(
        "--experience", type=Path, default=Path(settings.data_dir) / "admin" / "experience.json"
    )
    parser.add_argument("--apply", action="store_true", help="write; default is dry-run")
    parser.add_argument("--report", type=Path, help="optionally write the JSON report")
    args = parser.parse_args()
    report = run(args.database, args.experience, apply=args.apply)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
