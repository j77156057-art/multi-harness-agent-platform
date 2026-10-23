# 认领：R10-fix-17

| 字段 | 值 |
|---|---|
| task_id | `R10-fix-17` |
| Harness | Codex |
| 认领时间 | 2026-10-22 11:20 +08:00 |
| 分支 | `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1` |
| 状态 | doing（实现与 R10 正文（§9、§38.12）已就位；**规范复审由仓库主人 2026-10-22 拍板**；待 Merge Gate） |
| test_id | `DOC-SCHEMA-001` `DOC-STATE-001` `DOC-TRACE-001` |

## 范围

把「改派时谁接替了谁」从**群消息里的一句话**变成**平台字段**：派单请求可声明
`supersedes`，该字段写进新任务的**计划快照**（任务的不可变契约），任务列表与
详情都投影它。管理员的改派动作（`cancel` + `dispatch` 两步既有动作）因此留下
一条机器可读的接替链。

规范侧见 `docs/prep/AGENT-OFFICE-UNIFICATION/R10-FIX-17-DRAFT.md`（登记输入）与
`docs/spec/0.3-R10.md` §9、§38.12（2026-10-22 仓库主人拍板后落定的正文；本文的
实现与该正文在**同一次提交**内）。

## 变更

- `backend/app/adapter/dispatch.py`：派单接受可选 `supersedes`；三条校验（旧任务
  存在、同群、工作未交付）；写进任务快照的 spec。
- `backend/app/api/schemas.py`：`FormalDispatchRequest.supersedes`（可选）。
- `backend/app/tasks/service.py`：`_titles_for` 在同一次快照查询里多投影
  `supersedes`；`get_task` 顶层投影。
- `backend/app/admin/worker.py`：改派时传 `supersedes=旧任务 id`。
- 前端网关与办公室：任务投影带 `supersedes`；「这条活的来路」优先用平台字段，
  改派前的老数据回落到群消息并标明那是管理员的说法。
- `backend/tests/test_formal_dispatch.py`：4 条定点测试。

## 边界

- **不新增事件名、状态、状态迁移边**；不新增 canonical 表、不新增列。
- **不改 `docs/spec/0.3-R10.md`**：规范正文是只读的，本条走 R10-fix 复审流程，
  复审通过前正文不动（AGENTS.md §2）。
- 不改 `backend/migrations/**`；不改生产项目素材与外部 Harness 工作树。
- 与 `agent/workspace/codex-model-switch` 工作区的未提交改动同改
  `backend/app/adapter/dispatch.py`（它加的是 `model_id`/`paid_consent`），
  合并时可能冲突，冲突面已核对为不同参数、不同行。
