"""Bounded, project-scoped experience evidence for the administrator agent."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_RECORDS = 200
MAX_PROJECT_LENGTH = 120
MAX_CATEGORY_LENGTH = 80
MAX_EXECUTOR_LENGTH = 120
MAX_SPEC_SUMMARY_LENGTH = 2_000
MAX_FINDINGS_SUMMARY_LENGTH = 2_000
MAX_ROOT_CAUSE_LENGTH = 2_000
MAX_ACTION_LENGTH = 2_000
MAX_RESULT_LENGTH = 2_000
MAX_SOURCE_ID_LENGTH = 200
MAX_ACTOR_LENGTH = 120
MAX_PROMPT_LESSONS = 20

_COMMON_FIELDS = {"kind", "project", "task_category", "executor", "spec_summary"}
_STRUCTURED_FIELDS = {
    "experience_id",
    "source",
    "source_task_id",
    "source_event_id",
    "source_snapshot",
    "backfill_event_ids",
    "root_cause",
    "root_key",
    "action",
    "result",
    "contract_id",
    "diff_hash",
    "recorded_at",
    "last_recorded_at",
    "occurrence_count",
    "scope",
    "cross_project_confirmed_by",
}
_ALL_FIELDS = _COMMON_FIELDS | _STRUCTURED_FIELDS | {"findings_summary"}

# 本地概念词表：不调用外部模型或服务。未命中概念时回退到原有文本精确匹配。
_TOPIC_CONCEPTS = {
    "failure_path": ("失败路径", "异常路径", "异常流程", "异常分支", "错误路径", "错误分支", "失败分支", "异常场景", "失败场景"),
    "success_path": ("成功路径", "正常路径", "正常流程", "成功分支"),
    "acceptance": ("验收", "检查标准", "检查条件", "验证标准", "验证条件", "校验标准", "校验条件"),
    "tests": ("测试", "用例", "test"),
    "entry": ("既有入口", "现有入口", "原入口", "入口"),
    "auth": ("认证", "鉴权", "登录", "权限"),
    "overflow": ("横向溢出", "溢出", "裁切", "重叠"),
    "responsive": ("响应式", "窄屏", "小屏", "移动端"),
}
_OPERATION_CONCEPTS = {
    "missing": ("缺少", "缺失", "没有", "未包含", "未覆盖", "漏了", "遗漏", "没写", "未写", "未给出", "未提供"),
    "preserve": ("保留", "不删除", "不删", "不得删除", "删除", "被删", "去掉"),
}


def finding_cluster_key(text: str) -> str:
    """把可确认的同义问题归为一组；证据不足时保持原文，避免误合并。"""
    normalized = " ".join(str(text).casefold().split())
    topics = {
        concept
        for concept, phrases in _TOPIC_CONCEPTS.items()
        if any(phrase in normalized for phrase in phrases)
    }
    if not topics:
        return normalized
    operations = {
        concept
        for concept, phrases in _OPERATION_CONCEPTS.items()
        if any(phrase in normalized for phrase in phrases)
    }
    # “入口被删”与“没有保留入口”都是保留性破坏，不再让 missing 把它们拆开。
    if "preserve" in operations:
        operations.discard("missing")
    return "|".join(sorted(topics | operations))


class ExperienceStoreError(RuntimeError):
    """The experience file cannot be read or replaced safely."""


def _text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise ValueError(f"{field} must not be empty")
    if len(cleaned) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return cleaned


def _optional_text(value: str, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    cleaned = " ".join(value.split())
    if len(cleaned) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    return cleaned


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ExperienceStore:
    """Persist outcomes, deduplicate repeated causes, and recall for display only."""

    def __init__(self, path: str | Path, *, max_records: int = MAX_RECORDS) -> None:
        if (
            isinstance(max_records, bool)
            or not isinstance(max_records, int)
            or not 1 <= max_records <= MAX_RECORDS
        ):
            raise ValueError(f"max_records must be between 1 and {MAX_RECORDS}")
        self.path = Path(path)
        self.max_records = max_records

    def record_request_changes(
        self,
        *,
        project: str,
        task_category: str,
        executor: str,
        spec_summary: str,
        findings_summary: str,
        root_cause: str | None = None,
        action: str = "按验收意见返修",
        result: str = "验收未通过，等待返修",
        contract_id: str = "",
        diff_hash: str = "",
        recorded_at: str | None = None,
    ) -> None:
        """Record a failed review; older callers may omit the structured evidence."""
        self.record_outcome(
            kind="request_changes",
            project=project,
            task_category=task_category,
            executor=executor,
            spec_summary=spec_summary,
            findings_summary=findings_summary,
            root_cause=root_cause or findings_summary,
            action=action,
            result=result,
            contract_id=contract_id,
            diff_hash=diff_hash,
            recorded_at=recorded_at,
        )

    def record_merged(
        self,
        *,
        project: str,
        task_category: str,
        executor: str,
        spec_summary: str,
        action: str = "按工单交付，并通过验证、审核与合并",
        result: str = "已合并",
        contract_id: str = "",
        diff_hash: str = "",
        recorded_at: str | None = None,
    ) -> None:
        """Record a successful merge as evidence separate from rework."""
        self.record_outcome(
            kind="merged",
            project=project,
            task_category=task_category,
            executor=executor,
            spec_summary=spec_summary,
            root_cause="无失败根因：验收与合并通过",
            action=action,
            result=result,
            contract_id=contract_id,
            diff_hash=diff_hash,
            recorded_at=recorded_at,
        )

    def record_abandoned(
        self,
        *,
        project: str,
        task_category: str,
        executor: str,
        spec_summary: str,
        reason: str,
        contract_id: str,
        diff_hash: str = "",
        recorded_at: str | None = None,
    ) -> None:
        """Record an abandoned attempt; stale leases remain retryable and are not recorded here."""
        self.record_outcome(
            kind="abandoned",
            project=project,
            task_category=task_category,
            executor=executor,
            spec_summary=spec_summary,
            root_cause=reason,
            action="保留断点证据，等待改派或人工处理",
            result="执行者放弃本次执行",
            contract_id=contract_id,
            diff_hash=diff_hash,
            recorded_at=recorded_at,
        )

    def record_outcome(
        self,
        *,
        kind: str,
        project: str,
        task_category: str,
        executor: str,
        spec_summary: str,
        root_cause: str,
        action: str,
        result: str,
        findings_summary: str | None = None,
        contract_id: str = "",
        diff_hash: str = "",
        recorded_at: str | None = None,
        scope: str = "project",
        cross_project_confirmed_by: str = "",
        source: str = "live",
        source_task_id: str = "",
        source_event_id: str = "",
        source_snapshot: dict[str, Any] | None = None,
    ) -> str:
        """Write one structured outcome or merge it into the same root-cause entry."""
        return self._append(
            self._record(
                kind=kind,
                project=project,
                task_category=task_category,
                executor=executor,
                spec_summary=spec_summary,
                findings_summary=findings_summary,
                root_cause=root_cause,
                action=action,
                result=result,
                contract_id=contract_id,
                diff_hash=diff_hash,
                recorded_at=recorded_at,
                scope=scope,
                cross_project_confirmed_by=cross_project_confirmed_by,
                source=source,
                source_task_id=source_task_id,
                source_event_id=source_event_id,
                source_snapshot=source_snapshot,
            )
        )

    def records(self) -> list[dict[str, Any]]:
        """Return stored evidence, or an empty list when the file is absent or damaged."""
        try:
            return self._load()
        except ExperienceStoreError:
            return []

    def get(self, experience_id: str) -> dict[str, Any] | None:
        """Return one stable experience entry for confirmation and injection checks."""
        for record in self.records():
            if record["experience_id"] == experience_id:
                return record
        return None

    @staticmethod
    def merge_preview(
        records: list[dict[str, Any]], record: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        """Apply the same dedupe rules without writing; used by backfill dry-runs."""
        return ExperienceStore._merge_records(list(records), record)

    def recall(
        self,
        *,
        project: str,
        task_category: str | None = None,
        executor: str | None = None,
        include_cross_project: bool = False,
        limit: int = MAX_PROMPT_LESSONS,
    ) -> list[dict[str, Any]]:
        """Return display-only matches; cross-project entries require an explicit confirmation."""
        project = _text(project, "project", MAX_PROJECT_LENGTH)
        if task_category is not None:
            task_category = _text(task_category, "task_category", MAX_CATEGORY_LENGTH)
        if executor is not None:
            executor = _text(executor, "executor", MAX_EXECUTOR_LENGTH)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")

        matches: list[dict[str, Any]] = []
        for record in reversed(self.records()):
            same_project = record["project"] == project
            confirmed_cross_project = (
                include_cross_project
                and record["scope"] == "cross_project"
                and bool(record["cross_project_confirmed_by"])
            )
            if not same_project and not confirmed_cross_project:
                continue
            if task_category is not None and record["task_category"] != task_category:
                continue
            if executor is not None and record["executor"] != executor:
                continue
            matches.append(record)
            if len(matches) >= limit:
                break
        return matches

    def prompt_lessons(
        self,
        *,
        task_category: str | None = None,
        executor: str | None = None,
    ) -> str:
        """Legacy formatter retained for compatibility; production prompt injection is disconnected."""
        if task_category is not None:
            task_category = _text(task_category, "task_category", MAX_CATEGORY_LENGTH)
        if executor is not None:
            executor = _text(executor, "executor", MAX_EXECUTOR_LENGTH)

        rework: dict[tuple[str, str, str], dict[str, Any]] = {}
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for record in self.records():
            if task_category is not None and record["task_category"] != task_category:
                continue
            if executor is not None and record["executor"] != executor:
                continue
            identity = (record["task_category"], record["executor"])
            if record["kind"] == "request_changes":
                key = (*identity, finding_cluster_key(record["findings_summary"]))
                group = rework.setdefault(
                    key,
                    {
                        "identity": identity,
                        "findings": record["findings_summary"],
                        "projects": [],
                        "count": 0,
                    },
                )
            elif record["kind"] == "merged":
                group = merged.setdefault(
                    identity,
                    {"identity": identity, "projects": [], "count": 0},
                )
            else:
                continue
            if record["project"] not in group["projects"]:
                group["projects"].append(record["project"])
            group["count"] += int(record["occurrence_count"])

        lessons: list[str] = []
        for group in rework.values():
            projects = group["projects"]
            if len(projects) < 2:
                continue
            category, worker = group["identity"]
            lessons.append(
                f"- 提示词/规格易返工：任务类别「{category}」、执行者「{worker}」；"
                f"相似返工：{group['findings']}；证据项目：{'、'.join(projects)}"
                f"（{len(projects)} 个），共 {group['count']} 次。请在提示词/规格中预先避免该问题。"
            )
        for group in merged.values():
            projects = group["projects"]
            if len(projects) < 2:
                continue
            category, worker = group["identity"]
            lessons.append(
                f"- 正面派单经验：任务类别「{category}」、执行者「{worker}」；"
                f"证据项目：{'、'.join(projects)}（{len(projects)} 个），共 {group['count']} 次。"
                "可将该执行者作为同类任务的候选，并复用其规格组织方式。"
            )
        if not lessons:
            return ""
        return "可复用的跨项目经验：\n" + "\n".join(lessons[:MAX_PROMPT_LESSONS])

    def _record(
        self,
        *,
        kind: str,
        project: str,
        task_category: str,
        executor: str,
        spec_summary: str,
        findings_summary: str | None = None,
        root_cause: str | None = None,
        root_key: str | None = None,
        action: str | None = None,
        result: str | None = None,
        contract_id: str = "",
        diff_hash: str = "",
        recorded_at: str | None = None,
        last_recorded_at: str | None = None,
        occurrence_count: int = 1,
        scope: str = "project",
        cross_project_confirmed_by: str = "",
        experience_id: str | None = None,
        source: str | None = None,
        source_task_id: str = "",
        source_event_id: str = "",
        source_snapshot: dict[str, Any] | None = None,
        backfill_event_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        if kind not in {"request_changes", "merged", "abandoned"}:
            raise ValueError("kind must be request_changes, merged or abandoned")
        if isinstance(occurrence_count, bool) or not isinstance(occurrence_count, int) or occurrence_count < 1:
            raise ValueError("occurrence_count must be a positive integer")
        scope = _text(scope, "scope", 20)
        if scope not in {"project", "cross_project"}:
            raise ValueError("scope must be project or cross_project")
        confirmed_by = _optional_text(
            cross_project_confirmed_by, "cross_project_confirmed_by", MAX_ACTOR_LENGTH
        )
        if scope == "cross_project" and not confirmed_by:
            raise ValueError("cross-project experience requires explicit confirmation")
        if scope == "project" and confirmed_by:
            raise ValueError("project experience cannot carry cross-project confirmation")
        source_name = _optional_text(source or "", "source", 40)
        source_task = _optional_text(source_task_id, "source_task_id", MAX_SOURCE_ID_LENGTH)
        source_event = _optional_text(source_event_id, "source_event_id", MAX_SOURCE_ID_LENGTH)
        if source_name == "backfill" and (not source_task or not source_event):
            raise ValueError("backfill experience requires source_task_id and source_event_id")
        snapshot = source_snapshot or {}
        if not isinstance(snapshot, dict):
            raise TypeError("source_snapshot must be an object")
        try:
            snapshot = json.loads(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        except (TypeError, ValueError) as exc:
            raise ValueError("source_snapshot must be JSON serializable") from exc
        if len(json.dumps(snapshot, ensure_ascii=False)) > 12_000:
            raise ValueError("source_snapshot exceeds 12000 characters")
        event_ids = backfill_event_ids or []
        if not isinstance(event_ids, list) or not all(
            isinstance(item, str) and item.strip() for item in event_ids
        ):
            raise TypeError("backfill_event_ids must be an array of non-empty strings")
        event_ids = sorted(set(event_ids))
        if source_name == "backfill":
            event_ids = sorted(set(event_ids) | {source_event})

        summary = _text(spec_summary, "spec_summary", MAX_SPEC_SUMMARY_LENGTH)
        findings = None
        if findings_summary is not None:
            findings = _text(findings_summary, "findings_summary", MAX_FINDINGS_SUMMARY_LENGTH)
        cause = root_cause or findings or (
            "无失败根因：验收与合并通过" if kind == "merged" else "未记录根因"
        )
        cause = _text(cause, "root_cause", MAX_ROOT_CAUSE_LENGTH)
        if root_key is None or not str(root_key).strip():
            if kind == "merged":
                root_key = "success|" + " ".join(summary.casefold().split())
            else:
                root_key = "failure|" + finding_cluster_key(cause)
        root_key = _text(root_key, "root_key", MAX_ROOT_CAUSE_LENGTH + 16)
        first_time = _now() if recorded_at is None else _optional_text(
            recorded_at, "recorded_at", 80
        )
        last_time = first_time if last_recorded_at is None else _optional_text(
            last_recorded_at, "last_recorded_at", 80
        )
        record: dict[str, Any] = {
            "kind": kind,
            "project": _text(project, "project", MAX_PROJECT_LENGTH),
            "task_category": _text(task_category, "task_category", MAX_CATEGORY_LENGTH),
            "executor": _text(executor, "executor", MAX_EXECUTOR_LENGTH),
            "spec_summary": summary,
            "root_cause": cause,
            "root_key": root_key,
            "action": _text(action or "未记录（旧版经验）", "action", MAX_ACTION_LENGTH),
            "result": _text(result or kind, "result", MAX_RESULT_LENGTH),
            "contract_id": _optional_text(contract_id, "contract_id", MAX_SOURCE_ID_LENGTH),
            "diff_hash": _optional_text(diff_hash, "diff_hash", MAX_SOURCE_ID_LENGTH),
            "recorded_at": first_time,
            "last_recorded_at": last_time,
            "occurrence_count": occurrence_count,
            "scope": scope,
            "cross_project_confirmed_by": confirmed_by,
            "source": source_name,
            "source_task_id": source_task,
            "source_event_id": source_event,
            "source_snapshot": snapshot,
            "backfill_event_ids": event_ids,
        }
        supplied_id = _optional_text(experience_id or "", "experience_id", 80)
        identity = json.dumps(self._dedupe_key(record), ensure_ascii=False).encode("utf-8")
        record["experience_id"] = supplied_id or (
            "exp-" + hashlib.sha256(identity).hexdigest()[:24]
        )
        if findings is not None:
            record["findings_summary"] = findings
        return record

    @staticmethod
    def _dedupe_key(record: dict[str, Any]) -> tuple[str, str, str, str, str]:
        return (
            record["project"],
            record["task_category"],
            record["executor"],
            record["kind"],
            record["root_key"],
        )

    @staticmethod
    def _merge_records(
        records: list[dict[str, Any]], record: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], str]:
        key = ExperienceStore._dedupe_key(record)
        for index, existing in enumerate(records):
            if ExperienceStore._dedupe_key(existing) != key:
                continue
            if (
                record["source"] == "backfill"
                and record["source_event_id"] in existing["backfill_event_ids"]
            ):
                return records, "skip"
            combined = {**existing, **record}
            combined["recorded_at"] = existing["recorded_at"] or record["recorded_at"]
            combined["occurrence_count"] = int(existing["occurrence_count"]) + 1
            combined["backfill_event_ids"] = sorted(
                set(existing["backfill_event_ids"]) | set(record["backfill_event_ids"])
            )
            if record["source"] == "backfill" and record["source_event_id"]:
                combined["backfill_event_ids"] = sorted(
                    set(combined["backfill_event_ids"]) | {record["source_event_id"]}
                )
            if existing["scope"] == "cross_project" and record["scope"] == "project":
                combined["scope"] = "cross_project"
                combined["cross_project_confirmed_by"] = existing[
                    "cross_project_confirmed_by"
                ]
            records[index] = combined
            return records, "merge"
        records.append(record)
        return records, "add"

    def _append(self, record: dict[str, Any]) -> str:
        records = self._load()[-self.max_records :]
        records, action = self._merge_records(records, record)
        if action != "skip":
            self._write(records[-self.max_records :])
        return record["experience_id"]

    def _load(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExperienceStoreError(f"cannot read experience file: {self.path}") from exc

        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "records"}
            or payload["version"] != 1
            or not isinstance(payload["records"], list)
            or len(payload["records"]) > MAX_RECORDS
        ):
            raise ExperienceStoreError(f"invalid experience file: {self.path}")

        records: list[dict[str, Any]] = []
        for item in payload["records"]:
            if not isinstance(item, dict) or item.get("kind") not in {
                "request_changes",
                "merged",
                "abandoned",
            }:
                raise ExperienceStoreError(f"invalid experience record: {self.path}")
            if not _COMMON_FIELDS.issubset(item) or not set(item).issubset(_ALL_FIELDS):
                raise ExperienceStoreError(f"invalid experience fields: {self.path}")
            if item["kind"] == "request_changes" and "findings_summary" not in item:
                raise ExperienceStoreError(f"invalid experience fields: {self.path}")
            try:
                # Additive v1 fields are optional on read. Missing values are defaults,
                # never fabricated timestamps or cross-project approval.
                record = self._record(
                    **{"recorded_at": "", "last_recorded_at": "", **item}
                )
            except (TypeError, ValueError) as exc:
                raise ExperienceStoreError(f"invalid experience values: {self.path}") from exc
            records.append(record)
        return records

    def _write(self, records: list[dict[str, Any]]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ExperienceStoreError(f"cannot create experience directory: {self.path.parent}") from exc

        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(
                    {"version": 1, "records": records},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            temporary = None
        except OSError as exc:
            raise ExperienceStoreError(f"cannot replace experience file: {self.path}") from exc
        finally:
            if temporary is not None:
                with suppress(OSError):
                    temporary.unlink()


__all__ = ["ExperienceStore", "ExperienceStoreError", "finding_cluster_key"]
