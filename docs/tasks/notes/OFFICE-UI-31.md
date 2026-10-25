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
当前状态：done
commit：`fb819ba`（助手的深取证档）
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
| 后端新增单测 | `backend/tests/test_forensics.py` 新增 6 项（脱敏 + 计数、有界 + 整个日志的大小、读面的出处与遮挡、未知任务是 404、助手工具是只读）；连同 `test_assistant_loop.py` 与 `test_assistant_bases.py` 全过 |
| 真跑（8849 真平台 + 4174 预览） | **读面**：`GET /api/v1/tasks/FD-554d39dd92e7ec10/forensics` 对 4.98MB 的真实日志给出读到的 882 行、整个日志 4,987,478 字节、stderr 另有 103 字节、出处指向 `formal-runs/FD-554d39dd92e7ec10.FD-554d39dd92e7ec10.a1.log`、轮次 `a1`、harness `claude-code`。**脱敏**：把两行凭据（`OPENAI_API_KEY=` 前缀式、`token =` 空格式）混进真实日志，两行都被遮挡、原文不在输出里、其余日志行原样保留；整段 882 行扫描 0.078s。**助手真的用上了这个工具**：助手循环（真实池子模型 + 真实工具表 + 真实读面 + 真实日志）的 trace 里，`task.forensics` 被实际调用 **8 次**，每次都取回同一份真实材料（attempt `a1` / harness `claude-code` / 4,987,478 字节 / 来源日志路径） |
| 办公室全部冒烟 | 30/33（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace，与 OFFICE-UI-28/29/30 同） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44；`ruff check` + `ruff format --check` 全绿 |
| `pytest`（全量） | EXIT=0 |
| 已知限制（如实记下，不掩饰） | 池子里的**免费模型**在「先取证再作答」这件事上不稳定：实测有的模型根本不调工具直接答「未记上」，有的调完工具后上游 502/超时拿不到最终答案。工具、读面、提示词三样都已就位并被真跑验证过；模型肯不肯用、用得稳不稳，取决于当下池子里挂的是哪个模型 —— 那是**模型与上游**的现状，本卡不替它兜底。 |
