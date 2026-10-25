# OFFICE-UI-37

### 统一任务卡

任务编号：OFFICE-UI-37
负责 Harness：Mavis
开始时间：2026-10-26 05:00 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~36 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/adapter/harnesses/generic.py`（声明式适配器）、
`backend/app/pool/sources.py`（池子四小坑）、`backend/app/adapter/formal_worker.py`（接线）、
`docs/tasks/notes/pool-generic-adapter.md`（被收尾的那张卡）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-ADAPTER-004

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-ADAPTER-004` |
| 允许修改 | `docs/tasks/notes/pool-generic-adapter.md`、`docs/claims/codex--pool-generic-adapter.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-37.md`、`docs/tasks/notes/OFFICE-UI-37.md` |
| 禁止修改 | `backend/app/**`（代码已在 main 上；核实出缺口就另开卡，不在结卡里顺手改）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `pool-generic-adapter` 写着 waiting，说「实现未合入 main，待复核移植/合并方式」（过时：`c4c16a1` 那条单独提交不在任何分支，但内容经 `2c02969` 进了 main） | 逐条核实完成标准 + 真跑，状态改成 done，并写清实际合入路径 |

## 为什么

仓库主人说「你接手 codex 的活」。接手的第一件事是把**已经完成但没关**的卡收掉 ——
留着 waiting 会让人以为通用适配器还没进 main（我的 UI-28/30/31 都挂在它下面）。

## 怎么做到不出错

1. 不碰它的实现；核实出缺口就另开卡。
2. 逐条对得上它自己写的完成标准。
3. 真跑一遍，不只看代码在不在。

## 完成标准

1. 完成标准逐条核实完，证据写进它的卡片。
2. 状态改成 done，写清实际合入路径。
3. 真跑一遍声明式适配器。
4. 门禁全绿；`docs/traceability.md` 登记 `MVP-ADAPTER-004`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 逐条核实 | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| `tools/docstate` / `tools/discipline` | 待回填 |
| `pytest`（全量） | 待回填 |
