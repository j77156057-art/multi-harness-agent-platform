"""把任务状态翻译成可追溯的当前步骤与下一责任人。"""

from __future__ import annotations

from typing import Any


def next_action(task: dict[str, Any]) -> str:
    """只根据平台已有状态说明下一步，不替任何角色执行或裁决。"""
    status = str(task.get("status") or "")
    owner = str(task.get("owner") or "").strip()
    if status == "draft":
        return "当前：方案草案；下一步：管理员完善并传阅，群主最终批准"
    if status in {"ready", "queued"}:
        return "当前：待领取；下一步：方案指定的执行窗口领取任务"
    if status in {"claimed", "in_progress", "reclaiming"}:
        return f"当前：执行中；下一步：{owner or '持有窗口'}完成并提交交付"
    if status == "verifying":
        return "当前：交付验证；下一步：平台验证工作器给出回执"
    if status == "awaiting_review":
        return "当前：待验收；下一步：审核人领取审阅并裁决"
    if status == "changes_requested":
        return f"当前：验收返修；下一步：{owner or '原执行窗口'}按意见修正并重新提交"
    if status in {"awaiting_human", "escalated"}:
        return "当前：等待人工；下一步：群主处理裁决或求助"
    if status == "approved":
        return "当前：验收通过；下一步：管理员确认被审提交后发起合并"
    if status == "merging":
        return "当前：合并中；下一步：平台合并工作器给出结果"
    if status == "merged":
        return "当前：已合并；流程完成"
    if status in {"abandoned", "stale"}:
        return "当前：执行中断；下一步：管理员诊断后改派，或由群主重新打开"
    if status == "cancelled":
        return "当前：已取消；需要继续时由群主重新提出目标"
    return "当前状态已记录；下一步责任人与动作未记上"


__all__ = ["next_action"]
