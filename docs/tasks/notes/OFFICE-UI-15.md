# OFFICE-UI-15

### 统一任务卡

任务编号：OFFICE-UI-15
负责 Harness：Mavis
开始时间：2026-10-24 17:25 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（左边改成会话列表）
真相源引用：`office/src/flat-office.js`（办公室界面）、`office/src/flat-office.css`；
平台 `/tasks/{id}` 的 `spec`（目标原文、验收标准、只允许改的路径）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-030

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-030` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-*.mjs`、`docs/claims/mavis--OFFICE-UI-15.md`、`docs/tasks/notes/OFFICE-UI-15.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`（这一卡纯前端）、`office/contributions/**`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

窗口页给这个窗口的活列一份**详细清单**：

- 每条活一张**可展开的卡片**（`<details>`）；
- 收起时：状态徽章 + 标题主句 + 做到第几次 + 现在什么状态；
- **点开即看**：这条活**完整的目标原文**（不截断）+ 验收标准 + 只允许改的路径 +
  这条活的经过。

## 为什么

仓库主人：「给各个 harness 页面添加任务详细清单，要写明具体是做什么的，不要截断，
收缩成卡片，点开即看」。现在窗口页只有一行被切过的主句，看不出这条活到底要做什么。

## 怎么做到不出错

1. **不截断**：目标原文整段摆出来（`pre-wrap` + `overflow-wrap: anywhere`），不做
   「前 N 字 + …」。
2. **点开即看**：点开那一刻才去读 `/tasks/{id}`（缓存 60 秒），读不到要说清「读不到」，
   不能给一个空框让人以为这条活没有目标。
3. **收起时仍有用**：不展开也知道这条活是什么。
4. **事件按这条活取**：卡片里是**这条活**的经过（`aggregate_id` 匹配），不是全局轨迹。

## 完成标准

1. 窗口页的活清单是一张张可展开的卡片，收起时带状态/标题/进度。
2. 展开后显示完整目标原文（不截断）、验收标准、只允许改的路径、这条活的经过。
3. 新增断言通过；办公室全部冒烟无回归；构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-030`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 真跑（8849 真平台 + 4174 预览） | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填（本轮**没碰 `backend/**`**） |
