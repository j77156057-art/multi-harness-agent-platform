# OFFICE-UI-28

### 统一任务卡

任务编号：OFFICE-UI-28
负责 Harness：Mavis
开始时间：2026-10-25 12:45 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-26（加新 harness 的接口面）、pool-generic-adapter（声明式适配器）
真相源引用：`backend/app/adapter/harnesses/generic.py`（声明与模板）、
`backend/app/api/routes.py`（`GET /harnesses`）、
`office/server/formal-dispatch-gateway.mjs`（办公室本地网关的基座列表与登记）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-043

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-26、pool-generic-adapter |
| test_id | `MVP-UI-043` |
| 允许修改 | `backend/app/adapter/harnesses/generic.py`、`backend/app/api/routes.py`、`backend/tests/**`、`office/server/formal-dispatch-gateway.mjs`、`office/server/formal-gateway-admin.test.mjs`、`docs/claims/mavis--OFFICE-UI-28.md`、`docs/tasks/notes/OFFICE-UI-28.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/auth/**`（权限矩阵不动）、`backend/app/admin/worker.py`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 基座列表只有写死的 5 家 | 内置 5 家 + 声明出来的（平台给的模板） |
| 登记声明出来的基座报 `unknown_base_harness` | 按声明的模板登记，认得 |
| 给声明出来的基座注册窗口被 `BASE_HARNESSES` 拦住 | 基座 id 以**平台知道的**为准 |

## 为什么

仓库主人问「加入新的 harness 以后可以通用目前的接口吗」→ 选「办公室本地接声明表」。

## 怎么做到不出错

1. 模板同源：办公室本地不自己拼 provider/adapter。
2. 声明仍是受信输入：这一卡不写声明文件。
3. 不新增端点与权限行。

## 完成标准

1. 声明出来的基座出现在办公室的「连接工作窗口 · 基座」列表里。
2. 登记基座与给基座注册窗口都用声明的模板。
3. 后端全量 pytest、网关测试、办公室冒烟全绿；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-043`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 后端新增单测 | 待回填 |
| 网关测试（`node --test server/formal-gateway-admin.test.mjs`） | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
