# OFFICE-UI-29

### 统一任务卡

任务编号：OFFICE-UI-29
负责 Harness：Mavis
开始时间：2026-10-25 13:55 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-28（办公室本地接声明表）、pool-generic-adapter（声明式适配器）
真相源引用：`docs/harness-onboarding.md`（本卡的产出）、
`backend/app/adapter/harnesses/generic.py`（声明格式与拒绝规则）、
`backend/app/adapter/formal_worker.py`（worker 参数与实例 id 规则）、
`backend/app/auth/__main__.py`（令牌签发）、`backend/app/pool/sources.py`（池子槽位）、
`office/server/formal-dispatch-gateway.mjs`（办公室登记路径）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-044

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-28、pool-generic-adapter |
| test_id | `MVP-UI-044` |
| 允许修改 | `docs/harness-onboarding.md`（新增）、`docs/claims/mavis--OFFICE-UI-29.md`、`docs/tasks/notes/OFFICE-UI-29.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/**`、`office/**`（本卡只写文档）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 流程散在代码、卡片与对话里，换机器要自己拼 | `docs/harness-onboarding.md`：五步、文件与机器的对应、两个坑、什么时候才要写代码、排错入口、真相源索引 |

## 为什么

仓库主人问「放到一台新电脑上，怎么添加新的 harness」，并把「把这份流程写进仓」圈了出来。

## 怎么做到不出错

1. 每个事实对着代码核过。
2. 只写流程，不发明规矩（该人声明的写清是人的动作）。
3. 命令用仓库里真实的入口。

## 完成标准

1. `docs/harness-onboarding.md` 存在，且照着能做完。
2. 手册里的路径与端点在仓库里都存在（写一条脚本核过）。
3. 门禁全绿；`docs/traceability.md` 登记 `MVP-UI-044`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 手册里的路径/端点核对 | 待回填 |
| `tools/docstate` / `tools/discipline` | 待回填 |
