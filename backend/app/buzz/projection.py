"""事件 → 展示消息投影（ADR-DEV-005 决策 5；buzz-bridge 卡 ③）。

三条纪律：

* **发布集从规范推导，不手抄**：M（迁移）集合按 `app.events.store` 同款读法
  从 R10 §37.7 文本解析；ADR 语义 = "状态变迁 + human.* + 安全类"，
  实现为 `M ∖ {escalation_reminder} ∪ {permission_denied, secret_scan_blocked}`
  ——退避提醒自环排除（ADR「提醒默认静默」，且它在 escalated 期间按退避反复
  触发）、两个安全类 O 事件放行（ADR 点名）。测试对 M/O/R 白名单做集合关系
  断言，规范一改立刻可见。
* **消息 = 投影不是转发**：只渲染字段白名单（时间/事件名/聚合/群/actor/迁移后
  状态）；payload **一个字节都不进消息**——completion/artifact 可能携带代码与
  自由文本，对"发到第三方平台"这道新出口按最小披露重投影（§17 同口径）。
* **「迁移后状态」从状态机冻结表推导**：每事件 next 唯一（`test_buzz_bridge`
  对推导面断言无歧义），不手写映射表。
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.config import settings
from app.events.store import EventRecord
from app.state.machine import default_transition_table

#: ADR「提醒默认静默」：escalated 自环的退避提醒，发布集显式排除
SILENT_REMINDER = frozenset({"escalation_reminder"})
#: ADR「安全类」点名：两个 O 集合事件默认放行
SILENT_EXCEPTIONS = frozenset({"permission_denied", "secret_scan_blocked"})


def _spec_event_block(index: int) -> frozenset[str]:
    blocks = re.findall(
        r"(?:迁移|观测|恢复)事件（[MOR]，\d+ 项）：\s*~~~text\n(.*?)\n~~~",
        settings.spec_path.read_text(encoding="utf-8"),
        re.S,
    )
    if len(blocks) != 3:
        raise ValueError("cannot load the R10 M/O/R event allowlist")
    return frozenset(word for word in blocks[index].split())


def migration_events() -> frozenset[str]:
    return _spec_event_block(0)


def observable_events() -> frozenset[str]:
    return _spec_event_block(1)


def recovery_events() -> frozenset[str]:
    return _spec_event_block(2)


def publish_events() -> frozenset[str]:
    """默认发布集（ADR 消息映射原则 v1）。"""
    return (migration_events() - SILENT_REMINDER) | SILENT_EXCEPTIONS


@lru_cache(maxsize=1)
def _event_next_map() -> dict[str, str]:
    next_by_event: dict[str, str] = {}
    for transition in default_transition_table().transitions:
        value = str(transition.next)
        previous = next_by_event.setdefault(transition.event.value, value)
        if previous != value:
            raise ValueError(f"event {transition.event.value!r} maps to multiple next states")
    return next_by_event


def next_state(event_name: str) -> str | None:
    """事件迁移后的单一状态；不在冻结表内则 None（不猜）。"""
    return _event_next_map().get(event_name)


def display_line(record: EventRecord, *, group_id: str | None = None) -> str:
    """一行展示文本：白名单字段拼接，机械生成、无自由文本。"""
    stamp = record.created_at[:19].replace("T", " ")
    parts = [
        f"[{stamp} UTC]",
        record.event_name,
        f"{record.aggregate_type}:{record.aggregate_id}",
    ]
    if group_id:
        parts.append(f"group {group_id}")
    parts.append(f"{record.actor_type} {record.actor_id}")
    line = " · ".join(parts)
    following = next_state(record.event_name)
    if following:
        line += f" → {following}"
    return line
