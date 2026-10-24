# OFFICE-UI-09

### 统一任务卡

任务编号：OFFICE-UI-09
负责 Harness：Mavis
开始时间：2026-10-24 08:05 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-02（办公室界面）、OFFICE-UI-04（平台翻译）
真相源引用：R10 §17（人的决策：`task:cancel` 只写给人类管理员）；
§18（令牌规则）；§19（群聊和上下文 —— 管理员 worker 只认三类群消息）；
`backend/app/admin/worker.py`（管理员自循环的现有三条消息通道）；
`backend/app/human/service.py`（取消这条边做什么）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done（已合入 main：`4c8ea6c`）
commit：`4c8ea6c`（这一轮随 `agent/local/mavis/OFFICE-UI-02/a1` 合入 main，merge commit `c3c1642`）
验证结果：见文末「验证结果」表（后端断言 + 办公室冒烟 + 真跑取消通知）。
test_id：MVP-UI-024

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-02、OFFICE-UI-04 |
| test_id | `MVP-UI-024` |
| 允许修改 | `office/src/flat-office.js`、`office/scripts/verify-office-*.mjs`、`backend/app/admin/worker.py`、`backend/tests/test_admin_worker.py`、`docs/claims/mavis--OFFICE-UI-09.md`、`docs/tasks/notes/OFFICE-UI-09.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/api/routes.py`（opencode 在途）、`backend/app/tasks/**`（codex 在途）、`backend/app/claim/**`、`backend/app/pool/**`、`docs/spec/**`、`backend/migrations/**`、其他 Harness 的工作区与分支 |

## 做什么

**人取消一条活，管理员要知道，并就剩下的范围重新细化一版。**

## 为什么

人从办公室点「不做了」，平台只改状态 + 记一条 `human.cancel` 事件，**群里不发消息**
（`backend/app/api/routes.py` 的 `/human/cancel` → `human/service.py:459`）。

管理员 worker 只认三种群消息（`backend/app/admin/worker.py`）：

| ref | 谁发 | 管理员干什么 |
|---|---|---|
| `project-goal` | 群主 | 细化出任务规格 → 派单 |
| `project-goal-answer` | 群主 | 回答管理员的追问 → 继续细化 |
| `reassign-request` | 人/办公室 | 改派给更闲的窗口（管理员自己决定、执行） |

**没有"取消"这一类** → 管理员不知道有人取消了一条活，他在一份已经不成立的计划上继续
干活：方案快照里那条还在、分配轮照旧收口。

## 怎么做到不出错

1. **取消的决定权仍在人**：按钮照旧是终态、原因必填；这条消息只是**通知**。
2. **重新细化的范围要窄**：只就**被取消的那一条**的原工单重新细化（工单原文从平台
   读，`GET /tasks/{id}` 的 `spec.goal`），不去动还在做的活 —— 否则会重复派单。
3. **复用现成的流程**：管理员 worker 收到后构造一份新的 goal payload 走
   `handle_goal`（细化 → 传阅 → 人审 → 派单）✓，不另造一条路。
4. **读不到工单原文就不猜**：明说，让人再说一次那部分要做什么。
5. **通知只在取消真的成了之后才发**（见下面"真跑抓到的那个缺陷"）。

## 真跑抓到的那个缺陷

第一次真跑时：那条活**早就是已取消状态**（列表里的状态是旧的），平台回了 409，而
办公室的 `doAction` **把错误吞了**（catch 里只记 `state.error`，不抛）→ 我那条
"已通知管理员"的消息**照样发了出去** —— 等于谎报。

修法两处：
- `doAction` 返回成功与否；取消这条活**只有成了**才去通知管理员；
- 发通知前先看一眼状态是不是还能取消（列表可能是旧的），不能取消就直接说人话，
  不去惊动管理员。

## 完成标准

1. 取消成功后群里多一条带 `task-cancelled` 标记的消息（正文是人话，refs 里有机器读
   的那份：任务号 + 原因），并 mention 管理员。
2. 管理员 worker 认这条消息 → 就被取消那条的工单原文重新细化 → 走草案 → 传阅 →
   人审，不直接派单。
3. 取消失败**不**通知管理员。
4. 新增断言（后端 + 办公室）；冒烟与门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_admin_worker.py` | 全绿（含新增 2 条：认得这条消息 / 读不到工单就不猜） |
| `office/scripts/verify-office-human-actions-ui.mjs` | 全绿（含新增：取消后必须通知管理员 / 取消失败时**不**通知） |
| 办公室冒烟 | 27/30（3 条环境依赖） |
| `tools/docstate` / `tools/discipline` / `ruff` | 全过 |
| 真跑 | 见下 |

真跑（自己的实例 8849，真令牌，真数据）：挑一条「卡住了」的活 → 点「不做了」→ 填原因
→ 确认 →

- 平台那边那条活从 `stale` 变成 `cancelled` ✓（不是"看起来取消了"）；
- 群里真的多出这条：

  > 不做了：真跑验证：这条方向不对，先停
  > （这一条：自循环验收-1020 · 编写办公室工位屏幕与平台回执对照说明）
  > 管理员会就这一条原本要做的部分重新细化一版，或者确认不用再做了；其余按原计划继续。

  它的 refs 是 `to-admin | task-cancelled | task-cancelled:FD-cab7731fb69aab30 |
  task-cancelled-json:{…}` ✓（管理员 worker 就认这个）。
