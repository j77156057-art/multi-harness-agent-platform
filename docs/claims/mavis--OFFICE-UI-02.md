# 认领：OFFICE-UI-02

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-02` |
| Harness | Mavis |
| 认领时间 | 2026-10-22 19:20 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | doing |

目标是把「人自己的动作」补回办公室界面：旧的人操作面（已随 Vue 控制台删掉的
`frontend/`）里有 §18 五个决策动词与「驳回计划」，迁到 Agent Office 时整批只剩
API 与 CLI，界面上一个按钮都没有 —— 于是「等人回答」的任务在界面上是死状态。

范围只在 `office/`（界面、网关、冒烟）与登记文档。不碰 `backend/`：后端端点
（`/human/resume`、`/human/reopen`、`/human/release`、`/human/cancel`、
`/plans/{id}/reject`）早就存在，只是没有人从界面上够得着。

不碰 `docs/spec/**`（R10 正文只读），不改状态机、事件名与 schema。
