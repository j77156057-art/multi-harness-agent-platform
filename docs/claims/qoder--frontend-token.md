# 认领：frontend-token

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `frontend-token` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 16:20 |
| 分支 | `agent/local/qoder/frontend-token/a1` |
| 状态 | 🚧 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `frontend-token`。

## 变更记录

- 2026-10-08 16:20：认领（协调者一句"前端 token 接线"）。这是 auth-layer
  留下的**唯一推广前置**：`AP_AUTH_MODE=enforce` 之下，控制台不带 human
  token，管理/决策操作会 403。本卡把控制台接上：令牌存 localStorage、请求带
  `Authorization: Bearer`、一个只读自省端点 `GET /api/v1/auth/whoami` 让界面
  知道"我是谁/认证状态"，决策与审批表单的主体字段跟随令牌主体，401/403 有
  明确的界面呈现。后端只加这一个自省端点（不审计、不拒绝），其余全在前端。
  切不切 enforce 由协调者决定——本卡只把开关的代价降到零。
