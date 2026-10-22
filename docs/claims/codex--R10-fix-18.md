# 认领：R10-fix-18

| 字段 | 值 |
|---|---|
| task_id | `R10-fix-18` |
| Harness | Codex |
| 认领时间 | 2026-10-22 20:40 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（实现与定点测试已就位；**规范条目待仓库主人与其他 AI 复核**） |

## 做什么

给平台加一条**定向消息**（R10 §19）：一条带 `direct` 签名的群消息，**只有发送方与收件人
看得到**，其他成员读不到。并在办公室给它一个**独立的私聊窗口**。

## 为什么

用户要"直接和管理员对话"。查下来：平台**从来没有过**私聊通道——

- schema 里只有 `task_messages` 与 `message_deliveries` 两张消息表；
- R10 正文与 backend 里都没有 direct/private/dm 的概念；
- 旧的"定向求助"是 `help-messaging-mock` 那个**模拟**组件，不是平台事实。

而群消息是**整群可见**的：成员的 task 上下文里会出现它（§19 的"最近 N 条消息"）。
所以"在群里 @ 管理员"不是私聊——除非读路径真的按收件人过滤。

## 修法（与 §38.13 一致）

- **落在消息的 refs 里**，不新增表、不新增列、不新增事件名/状态/迁移边 →
  `schema_version` 不动（§37.7 只对新增事件提升）；
- **可见性在读路径上判**：服务层 `list_messages` 按观看人过滤（发送方/收件人可见），
  路由层用令牌主体作观看人；识别不出身份时**看不到**（失败关闭）；
- 收件人沿用 mention 的投递记录（`message_deliveries`）；
- CLI 的 `say` 加 `--mention` / `--direct`，管理员侧读得到、回得了。

## 边界

- 办公室仍然是观察面：私聊窗口只提供**人自己的动作**（说话），不代管理员决定任何事；
- 前端只是把平台已有的定向消息摆出来，不自造可见性规则。

## 完成标准（机械可判）

1. `backend/tests/test_api_events_messages.py` 2 条绿：只有两端看得到、没有收件人时拒绝；
2. `backend/tests/test_cli_flow.py` 1 条绿：CLI 能发定向消息；
3. `office/scripts/verify-office-direct-chat.mjs` 绿：私聊页只列定向消息、发出去带
   `direct` + `mentions`；
4. `tools/docstate`、`tools/discipline`、`ruff`、全量 `pytest` 全绿。
