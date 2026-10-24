# OFFICE-UI-13

### 统一任务卡

任务编号：OFFICE-UI-13
负责 Harness：Mavis
开始时间：2026-10-24 15:45 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（左边改成会话列表）
真相源引用：`office/src/flat-office.js`（办公室界面）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done（已合入 main：`1d98d01`）
commit：`bb742ef`（实现）；合入 main 的 merge commit：`1d98d01`
验证结果：见文末「验证结果」表。
test_id：MVP-UI-028

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-028` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-*.mjs`、`docs/claims/mavis--OFFICE-UI-13.md`、`docs/tasks/notes/OFFICE-UI-13.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`（数据全部现成）、`office/contributions/**`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

任务详情的上方留一条**紧凑的活列表**：点开一条活之后，中间那栏（详情）上方就是本项目
当前筛选下的活，点一条直接切过去 —— 换看另一条活不用先点「← 返回任务表」。

## 为什么

会话式布局（OFFICE-UI-12）之后，点开一条活，中间那栏就变成详情，任务表不见了。想看另一条
活只能先退回任务表再点 —— 连着看三四条活的时候，这一步多余的操作很烦。

## 怎么做到不出错

1. **跟任务表同一份筛选**：用 `state.filtered`（render() 里算好的那一份），不在详情里
   另算一遍 —— 详情里不该摆着一条被筛选掉的活。
2. **当前那条必须在列表里**：列表只取前 12 条，而当前那条可能排在 12 条之外 —— 那样它既
   不会被标出来，也点不回来。改成：它在列表外时换到末位。
3. **点击委托的位置**：`data-open-task` 的按钮没有 `data-act`，处理要放在
   `button[data-act]` 过滤**之前** —— 放在后面点了没反应（第一版就是这样，现已修）。
4. **「← 返回任务表」仍在**：紧凑列表是「少点一次」，不是「不许回去」。

## 完成标准

1. 详情上方有本项目（当前筛选下）的活列表，每条带标题与状态。
2. 当前正在看的那条被标出来。
3. 点另一条 → 详情直接切过去。
4. `verify-office-conversations.mjs` 补的断言通过；办公室全部冒烟无回归；构建通过；
   `./scripts.sh check` 全绿。
5. `docs/traceability.md` 登记 `MVP-UI-028`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-conversations.mjs` | ok（含新增三条断言：列表在、当前那条被标出、点另一条切过去） |
| 真跑（8849 真平台 + 4174 预览） | 详情上方 12 条紧凑列表，当前那条高亮，「还有 6 条，用「← 返回任务表」看全部。」；点另一条 → 详情标题真的切过去 |
| `office/` 构建 | ok（29 个模块，main 196.24 kB JS / 66.58 kB CSS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿 |
| `pytest`（全量） | `PYTEST_EXIT=0`（本轮**没碰 `backend/**`**，后端与 main 逐字节相同） |
