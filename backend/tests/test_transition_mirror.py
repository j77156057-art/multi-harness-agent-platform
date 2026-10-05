"""R10 §10 迁移表与代码迁移表的机械对拍（DOC-STATE-001）。

test_spec_mirror.py 保证枚举名不越界，但不保证「§10 的每一条边代码都投影了、
代码也没有偷偷接受 §10 不允许的边」。MVP-03 验收时正是这一层漂移：代码缺
25 条展开边、多 11 条非法边。本文件直接复用 tools/docstate/docstate.py 的
§10 解析器，把 `a/b/c | event | ... | next` 展开成 (state, event, next)
三元组，与 default_transition_table() 全量比对：任一侧单独改动都会红。
"""

from __future__ import annotations

from docstate.docstate import extract_transitions

from app.spec import PSEUDO_STATES, EventKind, TaskStatus
from app.state.machine import default_transition_table

# R10 §10 迁移表展开后的三元组总数（86 行，含 1 个 child_created 伪状态出口）。
# 这是规范事实而非实现选择：R10 正文改动时必须显式更新本断言并经评审。
EXPECTED_EXPANDED_TRIPLES = 138


def _spec_triples(spec_path) -> set[tuple[str, str, str]]:
    text = spec_path.read_text(encoding="utf-8")
    triples: set[tuple[str, str, str]] = set()
    for current, event, _guard, next_state in extract_transitions(text):
        for state in current.split("/"):
            triples.add((state.strip(), event.strip(), next_state.strip()))
    return triples


def _code_triples() -> set[tuple[str, str, str]]:
    triples: set[tuple[str, str, str]] = set()
    for transition in default_transition_table().transitions:
        next_state = transition.next if transition.next in PSEUDO_STATES else transition.next.value
        for current in transition.current:
            triples.add((current.value, transition.event.value, next_state))
    return triples


def test_transition_table_mirrors_r10_section_10(spec_path):
    """代码边集必须与 R10 §10 展开三元组集合完全相等，不多也不少。"""
    spec = _spec_triples(spec_path)
    assert spec, "未能从 R10 §10 解析出任何迁移行"
    code = _code_triples()

    missing = sorted(spec - code)
    extra = sorted(code - spec)
    assert not missing and not extra, (
        "迁移表与 R10 §10 漂移；"
        f"代码缺失 {len(missing)} 条: {missing}；"
        f"代码多出 {len(extra)} 条: {extra}"
    )


def test_expanded_triple_count_is_recorded(spec_path):
    """展开后的 §10 三元组数量必须与 EXPECTED_EXPANDED_TRIPLES 一致。"""
    assert len(_spec_triples(spec_path)) == EXPECTED_EXPANDED_TRIPLES
    assert len(_code_triples()) == EXPECTED_EXPANDED_TRIPLES


def test_r10_section_10_tokens_are_registered(spec_path):
    """§10 出现的每个状态名/事件名都必须在代码枚举内登记（禁止自造名字）。"""
    allowed_states = {state.value for state in TaskStatus} | PSEUDO_STATES
    allowed_events = {event.value for event in EventKind}
    bad_states: set[str] = set()
    bad_events: set[str] = set()
    for state, event, _next in _spec_triples(spec_path):
        if state not in allowed_states:
            bad_states.add(state)
        if event not in allowed_events:
            bad_events.add(event)
    assert not bad_states, f"R10 §10 出现未登记状态: {sorted(bad_states)}"
    assert not bad_events, f"R10 §10 出现未登记事件: {sorted(bad_events)}"


def test_table_has_no_structural_diagnostics():
    """非终态必须有出边，且每条边都带 guard 与至少一个副作用声明。"""
    assert default_transition_table().validate() == []
