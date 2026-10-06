"""操作员 CLI（MVP-09）：人类不碰数据库也能走完一条任务链路。

清单对 MVP-09 的完成标准：

> 人类不直接访问数据库即可完成一条完整任务链路；**前端不能绕过 API 改权威字段**。

后半句是这个包的结构约束，也是它唯一的架构规则：**CLI 只经 HTTP 说话**，
不 import `Database`、不 import 任何 service。`tests/test_cli_flow.py` 里有一条
源码级断言守着它 —— 一旦有人在 CLI 里直接调 service 图省事，那条测试会红。

形态说明：清单允许用 CLI 代替界面（「先用一个 CLI 脚本代替人工操作 —— 因为 MVP
的验证目标是协调语义，不是界面」）。选择它的直接原因是 **CI 完全没有前端门禁**，
一个无门禁的 Vue UI 按 `traceability.md` 自己的标准属于「仅是名字」，无法被验证。
"""

from app.cli.main import main

__all__ = ["main"]
