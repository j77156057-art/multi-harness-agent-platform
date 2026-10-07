"""存活判定纯政策（liveness-watch）——学形状、原生实现。

机制学习自 Paperclip 的 active-run watchdog（
`server/src/modules/active-run-watchdog/domain/policy.ts`，本会话经 pinned
快照逐行核读；出处见 `docs/reports/paperclip-buzz-source-review.md` §2.5/§6）：
**silence 时钟带回退链**（lastOutput → processStart → runStart → created，取先
可用者）+ **两个注入阈值出三档**。这里按我们的证据映射：

* 他们的 ``lastOutputAt`` ↦ 任务最近事件时间（我们平台可见的"产出"）；
* 他们的 ``processStartedAt``/``startedAt`` ↦ attempt 创建时间；
* 他们的 ``createdAt`` ↦ 任务创建时间。

阈值不写死——由调用方注入（settings 派生），政策本身纯函数、可单测。
"""

from __future__ import annotations

from datetime import datetime

SILENCE_NOT_APPLICABLE = "not_applicable"
SILENCE_OK = "ok"
SILENCE_SUSPICIOUS = "suspicious"
SILENCE_CRITICAL = "critical"


def silence_started_at(
    *,
    last_event_at: datetime | None,
    renewal_at: datetime | None,
    attempt_started_at: datetime | None,
    task_created_at: datetime | None,
) -> datetime | None:
    """The first available clock source, in the learned fallback order.

    Our "last output" is the freshest of last event and last lease renewal
    (the platform-visible activity signals); the rest map to their start and
    creation times.
    """
    for candidate in (last_event_at, renewal_at, attempt_started_at, task_created_at):
        if candidate is not None:
            return candidate
    return None


def classify_silence(
    *,
    is_active: bool,
    silence_seconds: float,
    lease_remaining_seconds: float | None,
    suspicion_seconds: float,
    critical_seconds: float,
    lease_low_seconds: float,
) -> str:
    """Three levels plus ``not_applicable``; thresholds are inclusive (>=).

    ``lease_remaining_seconds`` is ``None`` when the task holds no active
    lease (e.g. states with no lease at all); it is then simply not part of
    the judgement.
    """
    if not is_active:
        return SILENCE_NOT_APPLICABLE
    if lease_remaining_seconds is not None and lease_remaining_seconds <= 0:
        return SILENCE_CRITICAL
    if silence_seconds >= critical_seconds:
        return SILENCE_CRITICAL
    if lease_remaining_seconds is not None and lease_remaining_seconds < lease_low_seconds:
        return SILENCE_SUSPICIOUS
    if silence_seconds >= suspicion_seconds:
        return SILENCE_SUSPICIOUS
    return SILENCE_OK
