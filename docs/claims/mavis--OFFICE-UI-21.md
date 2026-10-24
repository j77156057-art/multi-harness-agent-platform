# 认领：OFFICE-UI-21

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-21` |
| Harness | Mavis |
| 认领时间 | 2026-10-24 23:40 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-21/a1` |
| 状态 | doing |

目标：**成员 harness 也能发消息；当成员要改目标时，管理员要听见**（并把话带给群主）。

为什么：仓库主人：「另外成员harness也可以发信息，不过当成员更改目标时，管理员要知道」。

现在的事实（查过代码才敢这么说）：

- **成员其实能发消息**：`app.cli say` 就是成员窗口发消息的口子，平台的
  `POST /groups/{id}/messages` 也收（`author_kind='harness'`）。
- **但管理员听不见**：`backend/app/admin/worker.py` 的消息轮询里有一句
  `if _sender_type(message) != "human": continue` —— **不是人的消息一律丢掉**
  （连序号都推进了，所以再也不会被读到）。成员说什么，管理员都不知道。
- **而且没法标记「这条是要改目标」**：CLI 的 `say` 没有 `--ref`，成员即使说了
  「这个目标要改」，管理员也分不出它和别的闲聊。

范围：

- `backend/app/tasks/service.py`：新增消息签名 `goal-change`（与 `direct` 同住）。
- `backend/app/cli/main.py`：`say` 加 `--ref`（可重复）—— 成员能标出「这条是要改目标」。
- `backend/app/admin/worker.py`：
  - 消息轮询不再把成员的消息一律丢掉；认出 `goal-change` 的那条 → **管理员要知道**：
    记下来、**把原话带给群主**（群里是群主读的地方），并且**不自己改目标**
    （目标是群主的，R10 §19）；
  - 群主的回答要接得住：把它摆成「待答」，管理员据此重新细化（方案随之修订）。
- `office/src/flat-office.js`：群聊里带 `goal-change` 的消息标出「要改目标」。
- `backend/tests/test_admin_worker.py` + `backend/tests/test_cli_flow.py` + 办公室冒烟。
- `docs/spec/0.3-R10.md`：§38.16 R10-fix-22 + §19 一条（**与实现同一次提交**）。

**不碰** §10 状态机、§37.7 事件 allowlist（不新增事件名/状态/迁移边 → 事件
`schema_version` 不动）、数据库 schema（不需要迁移）、其他 Harness 的工作区与分支。

## 怎么做到不出错

1. **目标不是成员能改的**：成员只能**提**；改不改、改成什么，由群主说（R10 §19）。
2. **不静默**：成员那条消息被管理员收下后，群里要看得见「谁说的、要改什么」——
   不能只有管理员知道。
3. **一条消息只处理一次**：消息每轮都会重放，处理过的不再处理（管理员不重复喊人）。
4. **成员的其他闲聊不惊动人**：管理员**看见**但不逐条回应（那要花钱）—— 只有
   「要改目标」才惊动群主。
