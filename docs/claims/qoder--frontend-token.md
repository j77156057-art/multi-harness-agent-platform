# 认领：frontend-token

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `frontend-token` |
| Harness | Qoder |
| 认领时间 | 2026-10-08 16:20 |
| 分支 | `agent/local/qoder/frontend-token/a1` |
| 状态 | ✅ done（实现 b26680a；待合入） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `frontend-token`。

## 变更记录

- 2026-10-08 16:20：认领（协调者一句"前端 token 接线"）。这是 auth-layer
  留下的**唯一推广前置**：`AP_AUTH_MODE=enforce` 之下，控制台不带 human
  token，管理/决策操作会 403。本卡把控制台接上：令牌存 localStorage、请求带
  `Authorization: Bearer`、一个只读自省端点 `GET /api/v1/auth/whoami` 让界面
  知道"我是谁/认证状态"，决策与审批表单的主体字段跟随令牌主体，401/403 有
  明确的界面呈现。后端只加这一个自省端点（不审计、不拒绝），其余全在前端。
  切不切 enforce 由协调者决定——本卡只把开关的代价降到零。
- 2026-10-08 16:36：实现完成并提交 `b26680a`（whoami 端点 + 前端接线 +
  vitest 55 / pytest 493）。实机 enforce 8894+vite 5186：无令牌「需要令牌」→
  粘令牌「已认证为 human:operator」→带令牌建项目/取消任务全通 → 清令牌写=
  401 且草稿保留（留档 evidence-frontend.md）。状态 → done。
