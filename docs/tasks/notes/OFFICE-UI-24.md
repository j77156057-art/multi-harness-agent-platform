# OFFICE-UI-24

### 统一任务卡

任务编号：OFFICE-UI-24
负责 Harness：Mavis
开始时间：2026-10-25 07:10 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-23（轨迹讲成人话）
真相源引用：`office/src/flat-office.js`（事件行、窗口页的轨迹块、任务详情的跳转）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-039

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-23 |
| test_id | `MVP-UI-039` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-conversations.mjs`、`docs/claims/mavis--OFFICE-UI-24.md`、`docs/tasks/notes/OFFICE-UI-24.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

轨迹（它做过的动作）的每一行带一个「**看这条活**」的动作：点了跳到那条活的详情
（目标原文、验收标准、只允许改的路径、这条活的经过）。

只给**确实在**任务表里的那些行加 —— 方案级的事件（聚合号不是活）不给按钮。

## 为什么

仓库主人：「轨迹要能点开，查看详细任务」。

## 怎么做到不出错

1. 只给真能跳的行加按钮（聚合号要在任务表里）。
2. 点按钮不冒泡（不顺手把行展开）。
3. 跳过去落在任务详情，不是任务表的一行。

## 完成标准

1. 轨迹行有「看这条活」；点了跳到那条活的详情（能看到目标原文）。
2. 冒烟加断言。
3. 办公室全部冒烟无回归；构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-039`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 真跑（8849 真平台 + 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填（本卡**没碰 `backend/**`**，后端与 main 逐字节相同） |
