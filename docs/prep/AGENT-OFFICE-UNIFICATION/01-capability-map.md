# Agent Office 单壳 · 能力映射（01）

日期：2026-10-19
状态：设计输入，未改代码。

## 结论

以 **Agent Office（`D:\w\office-preview`）为主壳**，把平台控制台的独占能力并入
`formal-dispatch.js` 的「正式任务」面板，最后删除 `frontend/` 独立控制台。
不合并代码库：Agent Office 是 vanilla JS + 3D/手机场景，控制台是 Vue 3；
硬合并等于重写一端。融合发生在**同一入口 + 同一 API**，不是同一套构建。

## 能力对照

| 能力 | 控制台（`frontend/`） | Agent Office（`formal-dispatch.js` / 场景） | 结论 |
|---|---|---|---|
| 新建项目 / 群组 | `Shell.vue` 表单 | `project-workspace.js` 开工向导 | 保留 Agent Office，删控制台表单 |
| Harness 连接 / 成员 | 占位（无成员列表端点） | `project-workspace.js` + `/api/formal/harnesses` | 保留 Agent Office |
| 群聊 / 线程 | `ChannelPane.vue` | 办公室消息 + `/api/formal/messages` | 保留 Agent Office，删控制台面板 |
| 任务列表 / 状态 | `TaskPane.vue` | `formal-dispatch.js` 任务列表 | 保留 Agent Office |
| 任务差异 | `TaskPane.vue` diff | `formal-dispatch.js`「查看提交产物」 | 保留 Agent Office |
| 任务事件 | `TaskPane.vue` 事件列表 | 缺 | **补到 Agent Office** |
| 人工决策 resume/reopen/release/adjudicate/cancel | `TaskPane.vue` | 缺 | **补到 Agent Office** |
| 预算覆盖（release） | `TaskPane.vue` checkbox | 缺 | **补到 Agent Office** |
| 审核领取 / 提交 | `TaskPane.vue` | `formal-dispatch.js` 已有 | 保留 Agent Office，删控制台审核 |
| 管理员分派 | `PlanForm` / `TaskPane` | `formal-dispatch.js` 分派表单 | 保留 Agent Office |
| 方案快照 / 审批 | `PlanForm` | `plan-runtime.js` / `plan-phone-host.js` | 先核对，再保留 Agent Office |
| 平台助手巡检 / 上报 | 无 | `admin-agent.js` | 保留 Agent Office |
| 数据源（正式 / 沙盘） | 无 | 开工向导 | 保留 Agent Office |

## API 缺口（网关层，不改后端状态机）

`server/formal-dispatch-gateway.mjs` 当前：

- `safeTask()` 未带 `attempt_no`、`max_attempts`、`budget_override_by`、`escalated_at`；
  预算覆盖 UI 无法判断。
- `/status` 未返回人类操作者身份；人工动作的 `decided_by` 只能由网关注入，
  前端需要只读展示。
- `/human` 只透传 `resume/reopen/release/cancel`；`release` 不允许 `reason` 和
  `budget_override`（当前 `allowed` 是 `['task_id','answer']`，控制台实际发
  `reason`/`budget_override`，这是既有缺口）。
- 缺 `/human/adjudicate`（`question` + `options`）。
- `GET /tasks/:id` 只返回 `{task,diff}`；控制台的事件列表走
  `GET /tasks/:id/events`（返回 `{events,next_seq,has_more}`）。

以上全部是**网关透传/字段补全**，不新增事件名、状态或 schema；不改
`backend/app/state/machine.py`。

## 动作状态集（与控制台 `TaskPane.vue` 对齐）

- `RESUME_STATES`：`awaiting_human`、`plan_drift`、`impl_drift`
- `REOPEN_STATES`：`awaiting_human`、`plan_drift`、`impl_drift`、`abandoned`
- `CANCEL_STATES`：`queued`、`draft`、`ready`、`claimed`、`in_progress`、
  `verifying`、`needs_help`、`blocked`、`changes_requested`、`plan_drift`、
  `impl_drift`、`awaiting_review`、`approved`、`merging`、`stale`、`reclaiming`、
  `awaiting_human`
- `escalated`：只给 `release` + `adjudicate`，不给 `cancel`（后端既有缺陷，
  不在本次扩大）。
- `release` 在 `attempt_no >= max_attempts` 且 `budget_override_by` 为空时，
  必须勾选 `budget_override`；否则按钮 disabled。

## 实施顺序

1. 网关：补 `safeTask` 字段、`/status.operator`、`/human/adjudicate`、
   `release` 的 `reason/budget_override`、`/tasks/:id` 的 `events`。
2. `formal-dispatch.js`：任务卡增加「人工决策」区（草稿缓存，避免 5s 刷新丢输入）
   和「事件」展开；复用 `.formal-review` 的样式语言。
3. 验证：网关 stub 测试 + Playwright stub（不写真任务）+ 真实平台只读截图。
4. 删除 `frontend/` 独立控制台；5173 停止。
5. 端口：4174 = Agent Office 单入口；4175 仅迁移期对照，最后停止。

## 非目标

- 不修改 `contributions/office-opening-phone/office-opening-phone.js`（哈希锁定）。
- 不新增状态迁移边；`escalated` 不可取消保持现状。
- 不把 Vue 组件硬搬进 vanilla JS；只搬行为和 API 调用。
