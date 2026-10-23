# 认领：OFFICE-UI-04

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-04` |
| Harness | Mavis |
| 认领时间 | 2026-10-23 13:50 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | doing |

目标：**平台自己有"翻译"这件事** —— 把机器写的、源码一样的文本（失败原因、事件名、
状态、id、绝对路径、方案原文）翻成简洁明了的��话，界面上只显示人话，机器那一份
留在后面（title / 折叠）。

为什么不是"办公室再翻一次"：现在人话表散在**前端各处**（`state-labels.js`、
`admin-agent.js` 的 `REASON_LABEL`、`flat-office.js` 的 `reasonText`），三个页面
各翻各的，改一次要改三处；而**平台**才是这些文本的出处（失败原因是平台写的、
事件名是平台的事件、窗口登记名在平台的注册表里）。翻译要放在出处那一层。

范围：

- 新增 `backend/app/wording/**`（翻译表 + 服务 + HTTP 面）；
- `backend/app/api/app.py`：注册这个 router（一行 import + 一行 include）；
- `backend/app/auth/policy.py`：加一行 `wording:read`（只给 human）——
  **这个文件另有在途改动**（opencode 的 R10-fix-13 也在改它），本次只**加一行**、
  纯加性、不改任何已有键；
- `backend/app/admin/service.py`：给管理员服务开一个公开的"翻一句"入口
  （自由文本走模型通道；这个文件当前没有在途改动）；
- `backend/tests/test_wording.py`；
- `office/**`：网关转发 + 前端调用（`src/wording.js`）。

不碰 `backend/app/api/routes.py`（opencode 的 R10-fix-13 在途）、不碰
`backend/app/tasks/**`（codex 的 R10-fix-17 在途）、不碰 `docs/spec/**`、
不改 schema、不改状态机、不改事件名。
