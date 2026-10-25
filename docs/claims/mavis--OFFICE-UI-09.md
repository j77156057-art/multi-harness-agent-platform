# 认领：OFFICE-UI-09

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-09` |
| Harness | Mavis |
| 认领时间 | 2026-10-24 08:05 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-02/a1` |
| 状态 | done（已合入 main：`4c8ea6c` → merge `c3c1642`） |

目标：**人取消一条活，管理员要知道，并就剩下的范围重新细化一版。**

为什么：现在人从办公室点「不做了」，平台只改状态 + 记一条 `human.cancel` 事件，**群里
不发消息** —— 管理员 worker 只认三种群消息（`project-goal` /
`project-goal-answer` / `reassign-request`），它根本不知道有人取消了一条活。后果：
方案快照里那条还在、分配轮照旧收口，管理员在一份已经不成立的计划上继续干活。

范围：

- `office/src/flat-office.js`：取消成功后发那条群消息；
- `backend/app/admin/worker.py`：认新消息（`task-cancelled`）→ 重新细化；
- `backend/app/admin/worker.py` 附近**只加这一类消息的最小处理**（codex 在
  `origin/agent/local/codex/OFFICE-UI-01/a1` 上有 `f3e0c3d` 也在改这个文件，
  冲突面尽量小）；
- 对应的测试与冒烟。

不碰 `backend/app/api/routes.py`（opencode 在途）、`backend/app/tasks/**`（codex 在途）、
`backend/app/pool/**`、`docs/spec/**`、不改状态机、不改事件名。

## 怎么做到不出错

1. **取消的决定权仍在人**：按钮照旧是终态、原因必填；这条消息只是**通知**。
2. **重新细化的范围要窄**：只就**被取消的那一条**的原工单重新细化（工单原文从
   平台读），不去动还在做的活 —— 否则会重复派单。
3. **复用现成的流程**：管理员 worker 收到后构造一份新的 goal payload 走
   `handle_goal`（细化 → 传阅 → 人审 → 派单）✓ 不另造一条路。
4. **管理员那边失败/读不到工单就明说**，不猜、不编。
