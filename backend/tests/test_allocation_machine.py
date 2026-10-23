"""§39 分配与传阅状态机的代码侧回归（R10-fix-13，§38.11）。

这里钉四件事：

1. **逐边镜像**：`default_allocation_table()` 的每一条边都和规范 §39 正文一字不差。
   这是 `DOC-MIRROR-001` 在分配域上的对应物 —— 只回读正文、不信代码里的注释。
2. **结构自检**：`validate()` 无诊断（无空 guard、无未知状态、非终态都有出边、
   A 集合无遗漏）。
3. **fail closed**：规范没登记的边一律拒绝，而不是猜一个"看起来合理"的下一个状态。
4. **硬门禁的落点**：`allocation.opened` 只能从 `absent`（创建边）出发 —— 轮次不能
   被"再开一次"从一个已经在跑的分配状态里重开，传阅是否达标由服务层查（§39 硬规则 1）。
"""

from __future__ import annotations

import pytest

from app.allocation.machine import (
    ALLOCATION_STATE_NAMES,
    AllocationState,
    InvalidAllocationTransition,
    default_allocation_table,
)
from app.spec import ALLOCATION_PSEUDO_STATES, AllocationEvent, TaskStatus


def _spec_edges(spec_path) -> list[tuple[str, str, str, str]]:
    """按正文解析 §39 的边，顺序与文件一致。"""
    from docstate.docstate import extract_allocation_transitions, load_spec

    return extract_allocation_transitions(load_spec(str(spec_path)))


def test_the_code_table_mirrors_section_39_edge_for_edge(spec_path):
    """代码表 == 正文表（`current`/`event`/`next` 三项，逐边）。

    正文把共享同一条边的多个源状态写成 `a/b/c`，所以比对必须按集合做，不能按
    字符串 —— 否则 `opened/closed/decided` 与排序后的写法会被判成"不相等"。
    """

    def canon(current: str) -> frozenset[str]:
        return frozenset(part.strip() for part in current.split("/"))

    table = default_allocation_table()
    from_spec = {(canon(cur), ev, nxt) for cur, ev, _, nxt in _spec_edges(spec_path)}
    from_code = {(t.current, t.event.value, t.next) for t in table.transitions}
    assert (
        from_code == from_spec
    ), f"代码独有 {sorted(from_code - from_spec)}；正文独有 {sorted(from_spec - from_code)}"


def test_the_table_is_structurally_clean():
    assert default_allocation_table().validate() == []


def test_absent_is_a_creation_pseudo_state_not_a_real_state():
    table = default_allocation_table()
    assert frozenset({"absent"}) == ALLOCATION_PSEUDO_STATES
    assert "absent" not in ALLOCATION_STATE_NAMES
    # `absent` 只作为创建边的源状态出现，不能是任何边的 next。
    assert all(t.next != "absent" for t in table.transitions)


def test_allocation_opened_is_a_creation_edge_only():
    """硬门禁的可执行前提：分配轮只能被"创建"，不能被重开。"""
    table = default_allocation_table()
    opened = [t for t in table.transitions if t.event is AllocationEvent.ALLOCATION_OPENED]
    assert len(opened) == 1
    assert opened[0].current == frozenset({"absent"})
    assert opened[0].next == AllocationState.OPENED.value
    for state in ("opened", "closed", "decided"):
        with pytest.raises(InvalidAllocationTransition):
            table.resolve(state, AllocationEvent.ALLOCATION_OPENED)


@pytest.mark.parametrize(
    ("current", "event"),
    [
        # 申请只能在轮次未冻结时提交/更新/撤回
        ("closed", AllocationEvent.APPLICATION_SUBMITTED),
        ("decided", AllocationEvent.APPLICATION_SUBMITTED),
        ("closed", AllocationEvent.APPLICATION_UPDATED),
        # 裁决只能在冻结之后
        ("opened", AllocationEvent.ALLOCATION_DECISION_MADE),
        ("opened", AllocationEvent.ALLOCATION_DECISION_DEFERRED),
        ("circulating", AllocationEvent.ALLOCATION_DECISION_MADE),
        # 分配确认只能发生在已经裁决之后
        ("opened", AllocationEvent.ASSIGNMENT_ACCEPTED),
        ("closed", AllocationEvent.ASSIGNMENT_ACCEPTED),
        # 传阅的边不能拿来推分配
        ("circulating", AllocationEvent.ALLOCATION_CLOSED),
        ("opened", AllocationEvent.CIRCULATION_ROUND_CLOSED),
        # 终态没有出边
        ("completed", AllocationEvent.CIRCULATION_REVISED),
        ("cancelled", AllocationEvent.ALLOCATION_DECISION_MADE),
    ],
)
def test_unregistered_edges_are_refused(current, event):
    with pytest.raises(InvalidAllocationTransition):
        default_allocation_table().resolve(current, event)


def test_registered_edges_resolve_to_the_documented_next():
    table = default_allocation_table()
    cases = [
        ("absent", AllocationEvent.CIRCULATION_OPENED, "circulating"),
        ("circulating", AllocationEvent.CIRCULATION_REVIEW_SUBMITTED, "circulating"),
        ("circulating", AllocationEvent.CIRCULATION_ROUND_CLOSED, "revising"),
        ("revising", AllocationEvent.CIRCULATION_REVISED, "circulating"),
        ("revising", AllocationEvent.CIRCULATION_COMPLETED, "completed"),
        ("absent", AllocationEvent.ALLOCATION_OPENED, "opened"),
        ("opened", AllocationEvent.ALLOCATION_CLOSED, "closed"),
        ("closed", AllocationEvent.ALLOCATION_DECISION_MADE, "decided"),
        ("closed", AllocationEvent.ALLOCATION_DECISION_DEFERRED, "closed"),
        # 一轮多任务：裁决过第一个任务后，还要能继续为同轮的其他任务裁决/暂缓 ——
        # 否则一张分配轮只能分配一个任务，多任务轮会在第二个任务上被拒。
        ("decided", AllocationEvent.ALLOCATION_DECISION_MADE, "decided"),
        ("decided", AllocationEvent.ALLOCATION_DECISION_DEFERRED, "decided"),
        ("decided", AllocationEvent.ASSIGNMENT_ACCEPTED, "decided"),
        ("decided", AllocationEvent.ASSIGNMENT_REVOKED, "decided"),
    ]
    for current, event, expected in cases:
        assert table.resolve(current, event).next == expected


def test_cancellation_covers_both_machines():
    """方案作废要让在途的传阅与分配一起失效 —— 但它是两条边，不是一条。"""
    table = default_allocation_table()
    cancel = AllocationEvent.CIRCULATION_CANCELLED
    assert table.resolve("circulating", cancel).next == "cancelled"
    assert table.resolve("revising", cancel).next == "cancelled"
    for state in ("opened", "closed", "decided"):
        assert table.resolve(state, cancel).next == "cancelled"
    # 传阅没开之前没有可作废的轮次：`absent` 是创建边，不是可取消的在途状态。
    with pytest.raises(InvalidAllocationTransition):
        table.resolve("absent", cancel)


def test_the_allocation_machine_does_not_share_task_states():
    """§117：分配状态不得混进 `tasks.status`。唯一共享的字面是 `cancelled`。

    三张表各自都有 `cancelled` 终态，那是同一个词的三种用法，不是同一台状态机；
    除此之外两边一个状态都不许重叠 —— 尤其不许出现"传阅中"这种任务状态。
    """
    task_states = {status.value for status in TaskStatus}
    assert (ALLOCATION_STATE_NAMES & task_states) == {"cancelled"}
    for task_only_word in ("circulating", "revising", "decided", "opened", "closed"):
        assert task_only_word in ALLOCATION_STATE_NAMES
        assert task_only_word not in task_states


def test_every_a_event_is_reachable_from_some_registered_edge():
    table = default_allocation_table()
    used = {event.value for event in table.events()}
    assert used == {event.value for event in AllocationEvent}
