# OFFICE-UI-40

### 统一任务卡

任务编号：OFFICE-UI-40
负责 Harness：Mavis
开始时间：2026-10-26 06:45 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~39 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/pool/service.py`（usage 解析与记账）、
`backend/app/pool/balance.py`（provider 自己报的余额）、
`backend/tests/test_pool_metered.py`、`docs/tasks/notes/pool-metered-amount.md`（被收尾的
那张卡）、`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：DOC-SCHEMA-001

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `DOC-SCHEMA-001` |
| 允许修改 | `docs/tasks/notes/pool-metered-amount.md`、`docs/claims/codex--pool-metered-amount.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-40.md`、`docs/tasks/notes/OFFICE-UI-40.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `pool-metered-amount` 写着 doing（实现与定点测试已就位，待 Merge Gate）；那张卡还是 GBK 编码，在 UTF-8 仓库里显示为乱码 | 6 条完成标准逐条核实 + 定点测试真跑；卡转 UTF-8；状态改成 done |

## 为什么

仓库主人圈了顺序「先做 1，接着 2、3」。这是 3。

## 怎么做到不出错

1. 不碰实现。
2. 转码先按 GBK 读原文、再按 UTF-8 写回（不能直接改字节）。
3. 逐条核实它自己写的完成标准。

## 完成标准

1. 6 条逐条核实 + 定点测试真跑。
2. 卡转 UTF-8，状态 done。
3. 门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 逐条核实（6 条） | 待回填 |
| 定点测试真跑 | 待回填 |
| 转码 | 待回填 |
| `tools/docstate` / `tools/discipline` | 待回填 |
| `pytest`（全量） | 待回填 |
