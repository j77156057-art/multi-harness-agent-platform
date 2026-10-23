"""R10 §39 分配与传阅的两台状态机（代码侧镜像）。

为什么单独一层、而不是往 `app/state/machine.py` 里加状态：§117 要求分配状态与
`tasks.status` **分开**。把 `circulating` / `decided` 塞进 `TaskStatus` 会让
"任务在执行"和"任务还在被抢"变成同一个枚举，之后任何一处 `tasks.status` 的
判定都要先问"这个状态属于哪台机器" —— 那是错误的耦合，不是复用。

§39 在正文里是一张平表（两台的边写在一起，`current` 用 `/` 连接）。这里保持同样
的形态：一张 `AllocationTable` 同时承载两台机器，用 `(current, event)` 定位。
`absent` 是创建边的专用伪状态，和 §10 的 `child_created` 同款 —— 它不是可达状态，
`resolve()` 会拒绝对它做源状态查询以外的事。

两张表的分工：
* 传阅（circulation）—— 方案层的"成员互审"：circulating → revising → completed
* 分配（allocation）—— 任务层的"谁来做"：opened → closed → decided

硬门禁（§39 硬规则 1，群主 2026-10-17 决定）不在这张表里，它在服务层：开分配轮
必须先有至少一轮 `completed` 的传阅。这张表只保证"边本身合法"。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.spec import ALLOCATION_PSEUDO_STATES, AllocationEvent


class CirculationState(StrEnum):
    """§39 传阅状态机的状态。

    没有 `draft`：传阅轮只能被"开启"（`absent --circulation.opened--> circulating`）。
    管理员在开轮之前自己起草方案，那是它的模型工作，不是平台状态 —— 留一个没有入边的
    `draft` 只会让人以为存在一个可观测的起草阶段。
    """

    CIRCULATING = "circulating"
    REVISING = "revising"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AllocationState(StrEnum):
    """§39 分配状态机的状态。"""

    OPENED = "opened"
    CLOSED = "closed"
    DECIDED = "decided"
    CANCELLED = "cancelled"


#: 两台机器的状态并集；`absent` 单列为伪状态，不混进来。
ALLOCATION_STATE_NAMES: frozenset[str] = frozenset(
    {state.value for state in CirculationState} | {state.value for state in AllocationState}
)


class InvalidAllocationTransition(ValueError):
    """§39 没登记的边。fail closed：不猜一个"看起来合理"的下一个状态。"""


@dataclass(frozen=True, slots=True)
class Transition:
    current: frozenset[str]
    event: AllocationEvent
    next: str
    guard: str

    def accepts(self, current: str, event: AllocationEvent) -> bool:
        return current in self.current and event is self.event


class AllocationTable:
    """§39 迁移表的不可变注册表，带唯一性与结构自检。"""

    def __init__(self, transitions: Iterable[Transition]) -> None:
        self._transitions = tuple(transitions)
        self._by_key: dict[tuple[str, AllocationEvent], Transition] = {}
        for transition in self._transitions:
            for current in transition.current:
                key = (current, transition.event)
                if key in self._by_key:
                    raise ValueError(f"duplicate transition: {current}|{transition.event.value}")
                self._by_key[key] = transition

    @property
    def transitions(self) -> tuple[Transition, ...]:
        return self._transitions

    def resolve(self, current: str, event: AllocationEvent) -> Transition:
        try:
            return self._by_key[(current, event)]
        except KeyError as exc:
            raise InvalidAllocationTransition(
                f"event {event.value!r} is not allowed from {current!r}"
            ) from exc

    def outgoing(self, current: str) -> tuple[Transition, ...]:
        return tuple(t for t in self._transitions if current in t.current)

    def events(self) -> frozenset[AllocationEvent]:
        return frozenset(t.event for t in self._transitions)

    def validate(self) -> list[str]:
        """结构诊断，供 §39↔A 双向封闭的测试与巡检复用。"""
        errors: list[str] = []
        for transition in self._transitions:
            if not transition.guard.strip():
                errors.append(f"empty guard: {transition.event.value}")
            unknown = transition.current - ALLOCATION_STATE_NAMES - ALLOCATION_PSEUDO_STATES
            if unknown:
                errors.append(
                    f"unknown source state for {transition.event.value}: {sorted(unknown)}"
                )
            if transition.next not in ALLOCATION_STATE_NAMES:
                errors.append(
                    f"unknown next state for {transition.event.value}: {transition.next!r}"
                )
        # 每个非终态都必须有出边，否则回放会卡死。`completed`（方案定稿）与
        # `cancelled` 都是终态：前者走完轮数冻结快照，后者作废，两者都不再有后续边。
        terminal = {
            CirculationState.COMPLETED.value,
            CirculationState.CANCELLED.value,
            AllocationState.CANCELLED.value,
        }
        for name in sorted(ALLOCATION_STATE_NAMES - terminal):
            if not self.outgoing(name):
                errors.append(f"non-terminal state has no outgoing edge: {name}")
        # 双向封闭：A 集合的每个事件都必须在这张表里出现（§37.7 封闭规则 3）。
        missing = {e.value for e in AllocationEvent} - {e.value for e in self.events()}
        if missing:
            errors.append(f"A events missing from §39 table: {sorted(missing)}")
        return errors


def _t(
    current: str | Iterable[str],
    event: AllocationEvent,
    next_state: str,
    guard: str,
) -> Transition:
    current_set = frozenset({current} if isinstance(current, str) else current)
    return Transition(current=current_set, event=event, next=next_state, guard=guard)


def default_allocation_table() -> AllocationTable:
    """§39 的 21 条边，逐条对应正文。改这里必须同时改 §39 正文。"""
    return AllocationTable(
        [
            # --- 传阅（方案层，成员互审）---
            _t(
                "absent",
                AllocationEvent.CIRCULATION_OPENED,
                "circulating",
                "管理员发布方案草案、本轮传阅名单与轮数上限",
            ),
            _t(
                "circulating",
                AllocationEvent.CIRCULATION_REVIEW_SUBMITTED,
                "circulating",
                "名单内某成员提交本轮意见与材料版本",
            ),
            _t(
                "circulating",
                AllocationEvent.CIRCULATION_ROUND_CLOSED,
                "revising",
                "本轮名单全部提交或本轮截止时间到",
            ),
            _t(
                "revising",
                AllocationEvent.CIRCULATION_REVISED,
                "circulating",
                "管理员按本轮意见修订方案并产生新方案版本",
            ),
            _t(
                "revising",
                AllocationEvent.CIRCULATION_COMPLETED,
                "completed",
                "已完成轮数达到配置轮数且方案定稿冻结快照",
            ),
            _t(
                ("circulating", "revising"),
                AllocationEvent.CIRCULATION_CANCELLED,
                "cancelled",
                "群主取消或方案作废",
            ),
            # --- 分配（任务层，谁来做）---
            _t(
                "absent",
                AllocationEvent.ALLOCATION_OPENED,
                "opened",
                "管理员开分配轮：方案快照已批准、已完成至少 1 轮传阅、绑定截止时间",
            ),
            _t(
                "opened",
                AllocationEvent.APPLICATION_SUBMITTED,
                "opened",
                "主体为本人、任务属于本群、轮次未冻结",
            ),
            _t(
                "opened",
                AllocationEvent.APPLICATION_UPDATED,
                "opened",
                "本人、未冻结、expected_revision 匹配",
            ),
            _t(
                "opened",
                AllocationEvent.APPLICATION_WITHDRAWN,
                "opened",
                "本人撤回",
            ),
            _t(
                "opened",
                AllocationEvent.ALLOCATION_CLOSED,
                "closed",
                "截止时间到或管理员关闭，候选集合冻结",
            ),
            _t(
                "closed",
                AllocationEvent.ALLOCATION_MATERIAL_REQUESTED,
                "closed",
                "管理员要求某申请补材料并重开该任务的材料审核",
            ),
            _t(
                "decided",
                AllocationEvent.ALLOCATION_MATERIAL_REQUESTED,
                "decided",
                "裁决阶段中为同轮另一个任务要求补材料并重开其材料审核",
            ),
            _t(
                "closed",
                AllocationEvent.ALLOCATION_DECISION_MADE,
                "decided",
                "管理员选定候选，回避规则通过、expected_revision 匹配",
            ),
            _t(
                "decided",
                AllocationEvent.ALLOCATION_DECISION_MADE,
                "decided",
                "同一轮内继续为另一个任务裁决（一轮多任务，每任务一条裁决）",
            ),
            _t(
                "closed",
                AllocationEvent.ALLOCATION_DECISION_DEFERRED,
                "closed",
                "管理员暂不分配并记录理由",
            ),
            _t(
                "decided",
                AllocationEvent.ALLOCATION_DECISION_DEFERRED,
                "decided",
                "已进入裁决阶段后再暂不分配另一个任务并记录理由",
            ),
            _t(
                ("closed", "decided"),
                AllocationEvent.ASSIGNMENT_INVITED,
                "decided",
                "管理员给未申请者发改派邀请（含无人申请的任务）",
            ),
            _t(
                "decided",
                AllocationEvent.ASSIGNMENT_ACCEPTED,
                "decided",
                "被选中者或受邀者本人确认接单",
            ),
            _t(
                "decided",
                AllocationEvent.ASSIGNMENT_DECLINED,
                "decided",
                "受邀者本人拒绝改派邀请",
            ),
            _t(
                "decided",
                AllocationEvent.ASSIGNMENT_REVOKED,
                "decided",
                "管理员撤销尚未 Claim 的分配，需理由与版本",
            ),
            _t(
                ("opened", "closed", "decided"),
                AllocationEvent.CIRCULATION_CANCELLED,
                "cancelled",
                "所属方案作废，未执行分配一并失效",
            ),
        ]
    )
