"""代码侧 spec 镜像与 R10 正文的交叉验证。

这是 H-1（状态机唯一真相源）的可执行部分。

背景：R9/R10 复审发现的核心教训是"文档声称的"与"代码实际做的"会漂移。
本文件把 app/spec.py 里的枚举与 docs/spec/0.3-R10.md 正文做机械比对，
任何一侧单独改动都会让测试失败。
"""

from __future__ import annotations

import re

from app.spec import (
    HUMAN_WAIT_STATES,
    OWNER_STATES,
    PSEUDO_STATES,
    QUEUE_REASONS,
    R10_ALLOWLIST_COUNTS,
    R10_TRANSITION_EDGE_COUNT,
    SIDE_EFFECT_INTENT_TYPES,
    VERIFY_FAILURE_REASONS,
    EventKind,
    TaskStatus,
)


def test_task_status_count_matches_r10():
    """R10 §10 有 23 个正式状态。"""
    # 减去 merged / cancelled 两个终态在别处单独断言
    assert len(list(TaskStatus)) == 23, (
        "TaskStatus 必须与 R10 §10 的 23 个正式状态一一对应；"
        "新增状态必须先改 R10 正文并升 schema_version"
    )


def test_terminal_states_have_no_outgoing_edges():
    """R10 §10：merged 与 cancelled 是唯一两个无出边的终态。"""
    terminal = [s for s in TaskStatus if s.is_terminal]
    assert set(terminal) == {TaskStatus.MERGED, TaskStatus.CANCELLED}


def test_owner_states_count():
    """R10 §10 硬规则：6 个 owner 状态必须有独立于 owner 的 Lease 过期出口。"""
    assert len(OWNER_STATES) == 6
    # 每个 owner 状态都必须能迁往 stale（lease_expire 的 next）
    assert TaskStatus.CLAIMED in OWNER_STATES
    assert TaskStatus.IN_PROGRESS in OWNER_STATES
    assert TaskStatus.NEEDS_HELP in OWNER_STATES
    assert TaskStatus.BLOCKED in OWNER_STATES
    assert TaskStatus.PLAN_DRIFT in OWNER_STATES
    assert TaskStatus.IMPL_DRIFT in OWNER_STATES


def test_queue_reasons_exclude_verify_capacity():
    """R10 §13/ADR-054：verify_capacity 只存在于 verify_jobs，不得写入 tasks.queue_reason。"""
    assert "verify_capacity" not in QUEUE_REASONS
    assert {"resource", "worktree_capacity", "merge_capacity"} == QUEUE_REASONS


def test_verify_failure_reasons_distinguishable():
    """R10 §28：failure_reason 必须四分。混用会导致环境失败被当代码失败返工。"""
    assert {"code_failed", "env_failed", "timeout", "aborted"} == VERIFY_FAILURE_REASONS


def test_side_effect_intent_types_include_patch_apply():
    """R9-05 / R10-11：patch_apply 必须在意图类型枚举内，否则该宿主模式不可实现。"""
    assert "patch_apply" in SIDE_EFFECT_INTENT_TYPES
    for t in ("worktree_add", "merge", "revert_merge", "push", "archive", "verify_run"):
        assert t in SIDE_EFFECT_INTENT_TYPES


def test_event_kind_subset_of_r10_allowlist(spec_path):
    """EventKind 的每个值都必须在 R10 §37.7 的 M 集合内。

    这是封闭性检查的代码侧：不允许出现规范里没有的事件名。
    """

    with open(spec_path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"迁移事件（M，74 项）：\n\n~~~text\n(.*?)\n~~~", text, re.S)
    assert m, "无法从 R10 提取 M 集合，请检查 §37.7 格式"
    m_set = set(m.group(1).split())

    code_events = {e.value for e in EventKind}
    escape = sorted(code_events - m_set)
    assert not escape, f"代码中出现 R10 M 集合外的事件名: {escape}"


def test_event_kind_size_is_sane():
    """EventKind 不得多于 M 集合。

    注意：当前 EventKind 恰好等于 74 项（MVP 阶段把 R10 的全部迁移事件都登记了枚举，
    便于状态机实现时按名引用）。这本身没问题——封闭性由
    test_event_kind_subset_of_r10_allowlist 保证而非靠数量。但数量**不得增长**，
    超过 M 集合大小说明有人在代码里自造了事件名。
    """
    n = len(list(EventKind))
    assert n <= R10_ALLOWLIST_COUNTS["M"], (
        f"EventKind 有 {n} 项，超过 R10 M 集合（74）；" "禁止在代码里自造规范中不存在的事件名"
    )
    assert n >= 40, f"EventKind 只有 {n} 项，与 M 集合差距过大，请确认是否误删"


def document_counts(spec_path) -> dict[str, int]:
    """§37.7 的实际集合大小（用 docstate 自己的解析器，避免第二份解析实现）。"""
    from docstate.docstate import extract_allowlist, load_spec

    sets = extract_allowlist(load_spec(str(spec_path)))
    counts = {key: len(sets[key]) for key in ("M", "O", "R")}
    counts["UNION"] = len(sets["M"] | sets["O"] | sets["R"])
    return counts


def constants_match_document(spec_path) -> bool:
    from app import spec as spec_module

    counts = {k: v for k, v in spec_module.R10_ALLOWLIST_COUNTS.items() if k != "LOOPS"}
    return counts == document_counts(spec_path)


def test_recorded_counts_match_r10(spec_path):
    """代码里记录的 R10 常量必须与 §37.7 的**实际解析值**一致。

    R10-fix-12 升级：原实现把 74/13/1/88 手写进断言 —— 正是 §37.7
    "allowlist 的任何数字都不得手写"的方法论批判的那种写法（手写必错，
    R10 一轮内被修正三次）。现在断言的是三方互等：文档实际集合、
    `R10_ALLOWLIST_COUNTS`、docstate 的 `EXPECTED_COUNTS`；任一处单方面
    改动都会让本测试或 docstate 门禁转红。
    """
    from docstate.docstate import EXPECTED_COUNTS

    actual = document_counts(spec_path)

    assert constants_match_document(
        spec_path
    ), f"spec.py 常量与 §37.7 实际集合不一致：常量={dict(R10_ALLOWLIST_COUNTS)} 文档={actual}"
    assert {key: EXPECTED_COUNTS[key] for key in ("M", "O", "R", "UNION")} == actual
    assert EXPECTED_COUNTS["LOOPS"] == R10_ALLOWLIST_COUNTS["LOOPS"] == 10
    assert R10_TRANSITION_EDGE_COUNT == 86


def test_mirror_detects_a_drifted_constant(spec_path, monkeypatch):
    """注入验证：常量被单方面改动（文档没动）必须被抓到。"""
    from app import spec as spec_module

    assert constants_match_document(spec_path)
    drifted = dict(spec_module.R10_ALLOWLIST_COUNTS)
    drifted["O"] = drifted["O"] - 1  # 假装有人把 O 计数改回去而没改文档
    monkeypatch.setattr(spec_module, "R10_ALLOWLIST_COUNTS", drifted)
    assert not constants_match_document(spec_path)


def test_permission_denied_is_registered_exactly_once_as_an_observation_event(spec_path):
    """R10-fix-12 的登记：§17 的 403/ACL 审计事件名在 O 集合恰好一次。"""
    from docstate.docstate import extract_allowlist, load_spec

    sets = extract_allowlist(load_spec(str(spec_path)))

    assert sets["O_raw"].count("permission_denied") == 1
    assert "permission_denied" not in sets["M"]
    assert "permission_denied" not in sets["R"]


def test_permission_denied_is_accepted_by_the_event_store(tmp_path):
    """登记后运行期合法：EventStore 的白名单直接来自 §37.7 三块文本。

    修复前 `append("permission_denied")` 会因「event is not registered in R10」
    被拒——这正是 §17 与 §37.7 矛盾的运行期形态；修复后同一调用可往返，
    且未登记的名字依然被拒（拒绝路径未被放宽）。
    """
    import pytest

    from app.events.store import EventStore
    from tests.claim_support import make_database

    store = EventStore(make_database(tmp_path))
    record = store.append(
        event_name="permission_denied",
        aggregate_type="task",
        aggregate_id="T-1",
        actor_type="human",
        actor_id="operator",
        payload={"scope": "regression-check"},
    )

    assert record.event_name == "permission_denied"
    assert record.schema_version >= 1
    with pytest.raises(ValueError, match="not registered"):
        store.append(
            event_name="permission_denied_typo",
            aggregate_type="task",
            aggregate_id="T-1",
            actor_type="human",
            actor_id="operator",
        )


def test_pseudo_states_not_in_task_status():
    """伪状态不得混入正式状态枚举。"""
    assert {"child_created"} == PSEUDO_STATES
    assert "child_created" not in {s.value for s in TaskStatus}


def test_human_wait_states():
    """R10 §18：awaiting_human 与 escalated 由等待巡检器管理，超时不自动放行。"""
    assert {TaskStatus.AWAITING_HUMAN, TaskStatus.ESCALATED} == HUMAN_WAIT_STATES
