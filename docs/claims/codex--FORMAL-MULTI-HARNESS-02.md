# 认领：FORMAL-MULTI-HARNESS-02

| 字段 | 值 |
|---|---|
| task_id | `FORMAL-MULTI-HARNESS-02` |
| Harness | Codex |
| 认领时间 | 2026-10-15 09:00 +08:00 |
| 分支 | `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1` |
| 状态 | claimed（待 Merge Gate） |
| test_id | `MVP-ADMIN-REVIEW-001` |

## 范围

把三 Harness 协作验收补成真实的管理员闭环：管理员用 human token 创建项目、群组和计划并批准；Codex、OpenCode、Claude Code 三个已注册 Harness 分别领取独立任务、启动工作树、提交结果并通过 Verify；管理员逐项领取独立 review lease 并提交 approve。管理员审核事件必须以 `human` actor 写入任务事件流，Harness token 不能调用管理员审核面。

## 变更

- 新增 `/api/v1/human/review/claim` 与 `/api/v1/human/review/submit`，沿用既有 review service 和 R10 状态机。
- 增加 human review 权限键与 live lease、reviewed commit 绑定。
- 前端任务线程在 `awaiting_review` 显示“管理员审核”，支持领取审核、通过和要求修改。
- 新增后端端到端测试，三家 Harness 各跑一条独立任务并由管理员逐项批准。

## 边界

本任务只证明本机 loopback、隔离测试仓库和真实 API/工作树流程；不宣称公网、生产、手机设备或外部模型验收。未修改 R10 正文、生产素材或其他 Harness 工作树。
