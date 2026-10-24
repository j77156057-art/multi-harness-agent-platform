# OFFICE-UI-24

### 统一任务卡

任务编号：OFFICE-UI-24
负责 Harness：Mavis
开始时间：2026-10-25 07:10 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-23（轨迹讲成人话）
真相源引用：`office/src/flat-office.js`（事件行、窗口页的轨迹块、任务详情的跳转）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`b7b8c87`（轨迹每行带「看这条活」；展开的状态活过 15 秒一次的重画；事件行重新排了列）
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

顺带修掉一个真缺陷：办公室每 15 秒整页重画一次。原来轨迹行与窗口页的活卡片没有接上
`data-fold` / `state.openFolds` 这套「记住折叠状态」的机制，用户点开的详情在下一轮刷新里
被重建成收起的 —— 正在看的东西从手里跑掉。现在两处都接上了：轨迹行有稳定的折叠键
（聚合号 + 序号 + 事件名 + 时间）；活卡片按 `task_id` 记住展开状态，重画之后仍然是展开的，
并且重新读一遍任务书。

事件行重新排了列：时间 · 动作 · 活的名字 · 谁做的 · 看这条活，一行读完（原来「谁做的」
被挤在 76px 的第一列里，截断成「Codex · 管理…」）。

## 为什么

仓库主人：「轨迹要能点开，查看详细任务」。

## 怎么做到不出错

1. 只给真能跳的行加按钮（聚合号要在任务表里）。
2. 点按钮不冒泡（不顺手把行展开）：按钮在 `<summary>` 里，点了要 `preventDefault`。
3. 跳过去落在任务详情，不是任务表的一行。
4. 展开状态走办公室**已有**的 `data-fold` / `state.openFolds` 机制（不新造一套），
   折叠键对同一条事件必须稳定，否则重画之后会对不上行。
5. 活卡片恢复展开时要**重新读一遍任务书**（否则展开的是一张空壳）。

## 完成标准

1. 轨迹行有「看这条活」；点了跳到那条活的详情（能看到目标原文）。
2. 冒烟加断言。
3. 办公室全部冒烟无回归；构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-039`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-conversations.mjs` | ok（轨迹行有「看这条活」、按钮文案是「看这条活」、点后落在那条活的详情（目标原文在）、点按钮不顺手展开那一行、**16 秒重画之后展开的活仍然是展开的**且任务书重新读到了） |
| 真跑（8849 真平台 + 4174 预览） | 管理员窗口的轨迹 7 行带「看这条活」，聚合号不在本项目任务表的那一行（`FD-4feae21cb6ab4880`）**没有**按钮；点「看这条活」跳到该活详情（目标原文、验收标准、只允许改的路径都在）；点按钮后轨迹行没被顺手展开；Claude Code 窗口的活卡片展开后 16 秒（跨过一次 15 秒重画）仍然是展开的、目标原文还在；页面无报错 |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖：Godot dev server、本机 ollama `qwen3:4b`、在册凭据窗口 7≠9） |
| `office/` 构建 | ok（29 个模块，main 212.17 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿（`ruff check` 与 `ruff format --check` 两条） |
| `pytest`（全量） | `PYTEST_EXIT=0`（本卡**没碰 `backend/**`**，后端与 main 逐字节相同 —— 同一份后端代码在上一轮已跑过全量） |
