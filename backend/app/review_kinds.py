"""评审意见的四类（R10-fix-15）—— 发还回执必须说清"这是什么性质的事"。

主人对回执的要求是四句话：**哪里有问题、哪里需要改进、哪里会造成冲突、哪里可能
是 bug**。以前评审意见只有 `severity`（note/minor/major/critical）—— 那说明"有多
严重"，但说不出"是什么性质"：一条 critical 可能是逻辑错、可能是路径撞车、也可能
是个潜在空指针，成员拿到它不知道该做哪一种修改。

单独一个模块的原因很实际：`admin.service` 要写它、`adapter.formal_worker` 要把它
翻成工作指令，而前者已经 import 后者。放这里，两边都只依赖它，不会成环。
"""

from __future__ import annotations

#: 闭集。发出方必须从这四个里选一个 —— 开放字符串会让"冲突风险"和"改进建议"
#: 混成同一个词，回执也就失去可读性。
REVIEW_FINDING_KINDS: tuple[str, ...] = ("defect", "improvement", "conflict_risk", "bug_risk")

#: 给成员看的称呼：英文工作指令与中文摘要共用一份，避免两处各写一遍。
REVIEW_FINDING_LABELS: dict[str, str] = {
    "defect": "问题",
    "improvement": "需要改进",
    "conflict_risk": "会造成冲突",
    "bug_risk": "可能引入 bug",
}

#: 英文工作指令里的分组标题（`build_goal` 用）。
REVIEW_FINDING_HEADINGS: dict[str, str] = {
    "defect": "Defects",
    "improvement": "Requested improvements",
    "conflict_risk": "Conflict risks",
    "bug_risk": "Likely bugs",
}


def group_findings(findings: list[object]) -> dict[str, list[str]]:
    """把意见按四类摊开；认不出的 kind 归到 `defect`（宁可直说，不悄悄丢）。"""
    grouped: dict[str, list[str]] = {kind: [] for kind in REVIEW_FINDING_KINDS}
    for item in findings or []:
        if isinstance(item, dict):
            kind = str(item.get("kind") or "defect").strip()
            message = str(item.get("message") or "").strip()
        else:
            kind, message = "defect", str(item or "").strip()
        if not message:
            continue
        if kind not in grouped:
            kind = "defect"
        grouped[kind].append(message)
    return grouped
