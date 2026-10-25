# OFFICE-UI-27

### 统一任务卡

任务编号：OFFICE-UI-27
负责 Harness：Mavis
开始时间：2026-10-25 11:35 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-26（成员自报的用量 —— 那张卡里发现了这些坏字符）
真相源引用：`backend/app/auth/policy.py`、`backend/app/human/service.py`、
`backend/app/db/connection.py`、`backend/app/scheduler/sweeps.py`、
`backend/app/tasks/service.py`、`backend/migrations/002_task_claims_nullable.sql`、
`backend/tests/test_git_quotepath.py`、`backend/tests/test_pool_user_keys.py`、
`docs/claims/**`、`docs/incidents/**`、`docs/tasks/notes/**`；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-042

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-26 |
| test_id | `MVP-UI-042` |
| 允许修改 | 上表列出的 15 个文件（只改写坏的中文）、`docs/claims/mavis--OFFICE-UI-27.md`、`docs/tasks/notes/OFFICE-UI-27.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `data/**`、`docs/spec/**`、`backend/app/api/routes.py`、`backend/app/adapter/**`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `Windows 上还有第三��坑：` | `Windows 上还有第三个坑：` |
| `锚在���任务最后一个事件上` | `锚在该任务最后一个事件上` |
| `它���工位还占着容量` | `它的工位还占着容量` |
| `办公室加人时带���它` | `办公室加人时带上它` |
| `在数据层就��区分开` | `在数据层就能区分开` |
| `同���个 key 只留一个问题` | `同一个 key 只留一个问题` |
| policy.py 里连续 5 行全坏的注释 | 按代码实际行为重写的 4 行说明 |

## 为什么

仓库主人：「另开一卡清掉」。

## 怎么做到不出错

1. 只改文字，不改行为；注释按代码实际行为重写，不猜原文。
2. 改完全仓再扫一遍，受版本控制的文件里 U+FFFD 归零。
3. 不碰 codex 在途的文件。

## 完成标准

1. 受版本控制的文件里没有 U+FFFD 了。
2. 后端全量测试通过；门禁全绿。
3. `docs/traceability.md` 登记 `MVP-UI-042`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 全仓扫描（受版本控制的文件） | 待回填 |
| 后端全量 `pytest` | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
