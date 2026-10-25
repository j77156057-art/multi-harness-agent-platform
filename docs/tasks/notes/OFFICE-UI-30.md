# OFFICE-UI-30

### 统一任务卡

任务编号：OFFICE-UI-30
负责 Harness：Mavis
开始时间：2026-10-25 14:30 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-28（办公室接声明表）、pool-generic-adapter（声明式适配器）
真相源引用：`backend/app/adapter/harnesses/generic.py`（声明与缺口）、
`backend/app/api/routes.py`（新读面）、`backend/app/assistant/tools.py`（工具表）、
`backend/app/assistant/prompts.py`（姿势与红线）、`docs/harness-onboarding.md`（流程手册）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-045

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-28、pool-generic-adapter |
| test_id | `MVP-UI-045` |
| 允许修改 | `backend/app/api/routes.py`、`backend/app/adapter/harnesses/generic.py`、`backend/app/assistant/tools.py`、`backend/app/assistant/prompts.py`、`backend/tests/**`、`docs/claims/mavis--OFFICE-UI-30.md`、`docs/tasks/notes/OFFICE-UI-30.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/auth/**`（权限矩阵不动）、`backend/app/admin/worker.py`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 「这个新 CLI 怎么接进来」只能人去翻代码 | 助手一个只读工具给出：声明出来的基座各带一份缺口清单（缺 `review_args`／池子侧没槽位／槽位没声明 `tier`） |
| 没有草案 | 助手给一份**声明草案**（JSON 片段），人审阅后自己装 |

## 为什么

仓库主人圈了「助手的适配体检 + 声明草案」。

## 怎么做到不出错

1. 助手只出草案，不写/不装声明文件。
2. 缺口清单只讲事实，查不到就说查不到。
3. 新读面放在 read 区块，与 codex 在途的改动不重叠。

## 完成标准

1. `GET /harnesses/bases` 返回声明出来的基座 + 缺口清单 + 内置那几家的名字。
2. 助手多一个只读工具 `harness.bases`，提示词里写清姿势与红线。
3. 后端全量 pytest 通过；门禁全绿。
4. `docs/traceability.md` 登记 `MVP-UI-045`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
