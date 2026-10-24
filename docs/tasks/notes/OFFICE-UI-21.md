# OFFICE-UI-21

### 统一任务卡

任务编号：OFFICE-UI-21
负责 Harness：Mavis
开始时间：2026-10-24 23:40 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（会话列表；R10-fix-18 的私聊与群消息）
真相源引用：`backend/app/admin/worker.py`（管理员的消息轮询）、`backend/app/tasks/service.py`
（消息与签名）、`backend/app/cli/main.py`（成员窗口发消息的口子）、
`office/src/flat-office.js`（群聊）；R10 §19（群聊与消息）、§38（R10-fix 流程）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-036

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-036` |
| 允许修改 | `backend/app/tasks/service.py`、`backend/app/admin/worker.py`、`backend/app/cli/main.py`、`backend/tests/test_admin_worker.py`、`backend/tests/test_cli_flow.py`、`office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-conversations.mjs`、`docs/claims/mavis--OFFICE-UI-21.md`、`docs/tasks/notes/OFFICE-UI-21.md`、`docs/traceability.md`、`docs/spec/0.3-R10.md`（§38.16 R10-fix-22 + §19）、`TASKS.md` |
| 禁止修改 | `backend/app/api/routes.py`、`backend/app/pool/**`、`backend/app/tasks/service.py` 以外的任务状态机、`docs/dev-convention.md`、`tools/**`、其他 Harness 的工作区与分支 |

## 做什么

| 要什么 | 怎么做 |
|---|---|
| 成员 harness 也能发信息 | 已有（`app.cli say`）；这一卡补上 `--ref`，让它能标出「这条是要改目标」 |
| 成员改目标时管理员要知道 | 管理员的消息轮询不再把非人的消息一律丢掉：认出 `goal-change` → 记下来 + **把原话带给群主**（群里是群主读的地方）+ **不自己改目标**（目标是群主的） |
| 群主的回答接得住 | 管理员把「当前目标」摆成待答状态；群主在群里回答 → 管理员据此重新细化 → 方案随之修订 |
| 办公室里看得出来 | 群聊里带 `goal-change` 的消息标出「要改目标」 |

## 为什么

仓库主人：「另外成员harness也可以发信息，不过当成员更改目标时，管理员要知道」。

## 怎么做到不出错

1. **目标不是成员能改的**：成员只能提；改不改由群主说（R10 §19）。
2. **不静默**：群里要看得见「谁说的、要改什么」。
3. **一条消息只处理一次**（消息每轮重放）。
4. **闲聊不惊动人**：只有「要改目标」才惊动群主。

## 完成标准

1. `app.cli say --ref goal-change` 能标出「要改目标」。
2. 管理员收到这样的成员消息 → 记一次、把原话带给群主、群里出现那条转述。
3. 群主回答 → 管理员重新细化（方案修订）。
4. 办公室群聊里那条消息标着「要改目标」。
5. 断言通过；办公室冒烟无回归；构建通过；`./scripts.sh check` 全绿。
6. `docs/traceability.md` 登记 `MVP-UI-036`；R10 §38.16 R10-fix-22 与实现同一次提交。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_admin_worker.py` | 待回填 |
| `backend/tests/test_cli_flow.py` | 待回填 |
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 真跑（8849 真平台 + 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
