"""Liveness 扫描与「零 commit 告警」（liveness-watch）。

两块，边界刻意分开：

* **观测**（advisory）：对 owner 态与 verifying 的任务给出可解释读数
  （租约剩余 / 静默秒 / 零 commit 窗口数 → ok|suspicious|critical + reason）。
  读数**从不移动任务状态**——R10 状态机的出口保持原样，本模块只看不动。
* **告警**（R10 §13 L706 的字面实现）："零 commit：连续 2 个 heartbeat 窗口
  无新 commit 时告警"。触发=租约仍活 + attempt 尚未发布任何 commit 且
  attempt 年龄 ≥ 2 个心跳窗口；经 alert 队列投递 `run.no_commit`，
  去重键=attempt_id（一次/attempt；新 attempt 重新计）。未配置 webhook 时
  显式不入队（告警通道 opt-in）。入队与投递分离——投递由 `python -m app.alerts`
  运行。

声明近似（不假装）：R10 的"新 commit"以**平台可见的发布**为准
（``task_attempts.head_commit``，由 publish_task_branch 写入）；harness 工作树
内部的增量 commit 平台不可见，因此"零 commit 窗口"的实际含义是
"attempt 开始后一直未发布"。测试按此语义钉死。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.alerts.service import AlertNotifier
from app.config import settings
from app.db.connection import Database
from app.lease.service import parse_iso
from app.liveness.policy import (
    SILENCE_CRITICAL,
    SILENCE_OK,
    SILENCE_SUSPICIOUS,
    classify_silence,
    silence_started_at,
)
from app.spec import OWNER_STATES, TaskStatus

KIND_RUN_NO_COMMIT = "run.no_commit"
NO_COMMIT_WINDOWS = 2

_WATCH_STATES = tuple(
    sorted({state.value for state in OWNER_STATES} | {TaskStatus.VERIFYING.value})
)


@dataclass(frozen=True, slots=True)
class LivenessReading:
    task_id: str
    status: str
    owner: str | None
    attempt_id: str | None
    lease_remaining_seconds: int | None
    silence_seconds: int
    no_commit_windows: int
    state: str
    reason: str
    alarm_eligible: bool

    def as_json(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "owner": self.owner,
            "attempt_id": self.attempt_id,
            "lease_remaining_seconds": self.lease_remaining_seconds,
            "silence_seconds": self.silence_seconds,
            "no_commit_windows": self.no_commit_windows,
            "state": self.state,
            "reason": self.reason,
            "alarm_eligible": self.alarm_eligible,
        }


@dataclass(frozen=True, slots=True)
class LivenessRunReport:
    tasks: int
    ok: int
    suspicious: int
    critical: int
    alarms_queued: int
    readings: tuple[LivenessReading, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "tasks": self.tasks,
            "ok": self.ok,
            "suspicious": self.suspicious,
            "critical": self.critical,
            "alarms_queued": self.alarms_queued,
            "readings": [reading.as_json() for reading in self.readings],
        }


class LivenessWatch:
    def __init__(
        self,
        database: Database,
        *,
        notifier: AlertNotifier | None = None,
        heartbeat_seconds: int | None = None,
    ) -> None:
        self.database = database
        self.notifier = notifier
        self.heartbeat_seconds = heartbeat_seconds or settings.heartbeat_seconds

    # --------------------------------------------------------------- scan

    def scan(self, *, now: datetime | None = None) -> list[LivenessReading]:
        moment = now or datetime.now(UTC)
        placeholders = ", ".join("?" for _ in _WATCH_STATES)
        rows = self.database.read(
            "SELECT t.task_id, t.status, t.owner, t.current_attempt_id, t.created_at,"
            " a.created_at AS attempt_started_at, a.head_commit AS attempt_head,"
            " (SELECT MAX(l.lease_until) FROM leases l"
            "   WHERE l.task_id = t.task_id AND l.released_at IS NULL) AS lease_until,"
            " (SELECT e.created_at FROM task_events e"
            "   WHERE e.aggregate_id = t.task_id ORDER BY e.seq DESC LIMIT 1) AS last_event_at"
            f"  FROM tasks t LEFT JOIN task_attempts a ON a.attempt_id = t.current_attempt_id"
            f" WHERE t.status IN ({placeholders}) ORDER BY t.task_id",
            _WATCH_STATES,
        )
        readings: list[LivenessReading] = []
        for row in rows:
            readings.append(self._reading(row, moment))
        return readings

    def _reading(self, row: object, moment: datetime) -> LivenessReading:
        last_event_at = row["last_event_at"]  # type: ignore[index]
        attempt_started_at = row["attempt_started_at"]  # type: ignore[index]
        task_created_at = row["created_at"]  # type: ignore[index]
        lease_until = row["lease_until"]  # type: ignore[index]

        # A renewal is platform-visible activity (our CLI worker heartbeats
        # without progress_hash), so the silence clock follows the freshest of:
        # last event, last lease renewal (derived: lease_until - lease window),
        # attempt start, task creation -- Paperclip's fallback-chain shape.
        renewal_at: datetime | None = None
        lease_remaining: float | None = None
        if lease_until is not None:
            parsed_until = parse_iso(str(lease_until))
            lease_remaining = (parsed_until - moment).total_seconds()
            renewal_at = parsed_until - timedelta(seconds=settings.lease_seconds)
        anchor = silence_started_at(
            last_event_at=None if last_event_at is None else parse_iso(str(last_event_at)),
            renewal_at=renewal_at,
            attempt_started_at=(
                None if attempt_started_at is None else parse_iso(str(attempt_started_at))
            ),
            task_created_at=None if task_created_at is None else parse_iso(str(task_created_at)),
        )
        silence_seconds = 0 if anchor is None else max(0, int((moment - anchor).total_seconds()))

        attempt_age: float | None = None
        if attempt_started_at is not None:
            attempt_age = (moment - parse_iso(str(attempt_started_at))).total_seconds()
        has_commit = row["attempt_head"] is not None  # type: ignore[index]
        no_commit_windows = 0
        if attempt_age is not None and not has_commit:
            no_commit_windows = max(0, math.floor(attempt_age / self.heartbeat_seconds))

        state = classify_silence(
            is_active=True,
            silence_seconds=silence_seconds,
            lease_remaining_seconds=lease_remaining,
            suspicion_seconds=2 * self.heartbeat_seconds,
            critical_seconds=4 * self.heartbeat_seconds,
            lease_low_seconds=self.heartbeat_seconds,
        )
        reason = self._reason(state, silence_seconds, lease_remaining)
        alarm_eligible = (
            row["current_attempt_id"] is not None  # type: ignore[index]
            and not has_commit
            and lease_remaining is not None
            and lease_remaining > 0
            and no_commit_windows >= NO_COMMIT_WINDOWS
        )
        return LivenessReading(
            task_id=str(row["task_id"]),  # type: ignore[index]
            status=str(row["status"]),  # type: ignore[index]
            owner=None if row["owner"] is None else str(row["owner"]),  # type: ignore[index]
            attempt_id=(
                None
                if row["current_attempt_id"] is None  # type: ignore[index]
                else str(row["current_attempt_id"])  # type: ignore[index]
            ),
            lease_remaining_seconds=(None if lease_remaining is None else int(lease_remaining)),
            silence_seconds=silence_seconds,
            no_commit_windows=no_commit_windows,
            state=state,
            reason=reason,
            alarm_eligible=alarm_eligible,
        )

    @staticmethod
    def _reason(state: str, silence_seconds: int, lease_remaining: float | None) -> str:
        minutes = silence_seconds // 60
        if state == SILENCE_CRITICAL:
            if lease_remaining is not None and lease_remaining <= 0:
                return f"lease expired {abs(int(lease_remaining))}s ago"
            return f"silent for {minutes}min"
        if state == SILENCE_SUSPICIOUS:
            if lease_remaining is not None and lease_remaining < 0:
                return "lease expired"
            if lease_remaining is not None and lease_remaining < 0.5 * 60:
                return "lease about to expire"
            return f"silent for {minutes}min"
        return f"active {minutes}min ago" if minutes > 0 else "active recently"

    # ------------------------------------------------------------ run once

    def run_once(self, *, now: datetime | None = None, enqueue: bool = True) -> LivenessRunReport:
        readings = self.scan(now=now)
        queued = 0
        if enqueue and self.notifier is not None and self.notifier.webhook_url is not None:
            for reading in readings:
                if not reading.alarm_eligible:
                    continue
                if self.notifier.enqueue(
                    kind=KIND_RUN_NO_COMMIT,
                    task_id=reading.task_id,
                    payload={
                        "attempt_id": reading.attempt_id,
                        "status": reading.status,
                        "no_commit_windows": reading.no_commit_windows,
                        "window_seconds": self.heartbeat_seconds,
                        "silence_seconds": reading.silence_seconds,
                        "reason": reading.reason,
                    },
                    source_key=str(reading.attempt_id),
                ):
                    queued += 1
        counts = {SILENCE_OK: 0, SILENCE_SUSPICIOUS: 0, SILENCE_CRITICAL: 0}
        for reading in readings:
            if reading.state in counts:
                counts[reading.state] += 1
        return LivenessRunReport(
            tasks=len(readings),
            ok=counts[SILENCE_OK],
            suspicious=counts[SILENCE_SUSPICIOUS],
            critical=counts[SILENCE_CRITICAL],
            alarms_queued=queued,
            readings=tuple(readings),
        )
