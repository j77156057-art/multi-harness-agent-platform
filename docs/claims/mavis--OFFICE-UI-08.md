# 认领：OFFICE-UI-08

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-08` |
| Harness | Mavis |
| 认领时间 | 2026-10-23 22:40 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | doing |

目标：**群聊给一段「最近群里在说什么」摘要** —— 办公室底部的项目群聊现在有 180 条，
新人进去只能自己从头翻。给它一段人话，说清最近谁在说什么、有多少条、多久以前。

为什么：群聊是办公室里唯一"只能自己读"的地方 —— 任务列表能分组、能筛选、能摘要，
群聊什么都不能。180 条消息里往往只有最近几条是要紧的，但**哪几条要紧**人一眼看不出来。

范围：

- `office/src/flat-office.js` + `office/src/flat-office.css`：群聊那一块的摘要；
- `office/src/wording.js`（如需要）；
- `office/scripts/verify-office-*.mjs`：跟着改的断言（按意图改，不放松判据）。

不碰 `backend/app/api/routes.py`（opencode 在途）、不碰 `backend/app/tasks/**`
（codex 在途）、不碰 `backend/app/pool/**`、`docs/spec/**`、不改平台接口、不改状态机、
不改事件名。

先做的事：先看群聊里**都是什么**（人写的消息？平台转述？），再决定摘要能诚实地
说什么 —— **算不出来的就不写**。不许为了好看而编一句。
