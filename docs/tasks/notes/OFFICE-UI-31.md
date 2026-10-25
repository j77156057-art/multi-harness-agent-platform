# OFFICE-UI-31

### 统一任务卡

任务编号：OFFICE-UI-31
负责 Harness：Mavis
开始时间：2026-10-25 15:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-30（助手的适配体检）、OFFICE-UI-17（token 度量）
真相源引用：`backend/app/adapter/cli_output.py`（日志读与脱敏）、
`backend/app/api/routes.py`（新读面）、`backend/app/assistant/tools.py`（工具表）、
`backend/app/assistant/prompts.py`（姿势与红线）、`backend/app/security/scan.py`（凭据识别）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-046

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-30、OFFICE-UI-17 |
| test_id | `MVP-UI-046` |
| 允许修改 | `backend/app/adapter/cli_output.py`、`backend/app/api/routes.py`、`backend/app/assistant/tools.py`、`backend/app/assistant/prompts.py`、`backend/tests/**`、`docs/claims/mavis--OFFICE-UI-31.md`、`docs/tasks/notes/OFFICE-UI-31.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/auth/**`（权限矩阵不动）、`backend/app/admin/worker.py`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `task.diagnose` 给 32KB/200 行的输出尾巴 + 3 条失败事件 | `task.forensics`：更长的输出（有界）、stderr、失败事件、**出处**（任务/轮次/文件/字节/行数/时间）、**已脱敏**（遮挡了几行） |
| 助手可能把像凭据的行原样转述 | 平台先遮挡，助手看不到原文；回答要说明遮挡了几行 |

## 为什么

仓库主人圈了「助手的深取证档」。

## 怎么做到不出错

1. 原文，不翻译不改写。
2. 脱敏在平台侧，回答里说明遮挡了几行。
3. 尾巴有界。
4. 每份材料带出处。

## 完成标准

1. `GET /tasks/{task_id}/forensics` 返回带出处与脱敏的深取证材料。
2. 助手多一个只读工具 `task.forensics`，提示词写清姿势与红线。
3. 后端全量 pytest 通过；门禁全绿。
4. `docs/traceability.md` 登记 `MVP-UI-046`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
