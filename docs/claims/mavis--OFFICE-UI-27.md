# 认领：OFFICE-UI-27

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-27` |
| Harness | Mavis |
| 认领时间 | 2026-10-25 11:35 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-27/a1` |
| 状态 | doing |

目标：**清掉仓库里写坏的中文**（U+FFFD 替换字符）。

为什么：仓库主人：「另开一卡清掉」。

现象：15 个**受版本控制**的文件里有 166 个 U+FFFD 替换符（每个都是写坏的字节）—— 写文件时编码不对，
坏掉的字节被替换成了 U+FFFD，那些地方的中文读不出来。`backend/app/auth/policy.py`
一处就占 131 个（连续 5 行注释全坏）。

范围：逐处按**上下文与代码实际行为**重写，**不猜原文**（原文已经丢了）：

- `backend/app/auth/policy.py`（5 行注释，按 `grant_attempt_budget_override`
  与 `release` 的实际行为重写）
- `backend/app/db/connection.py`、`backend/app/human/service.py`、
  `backend/app/scheduler/sweeps.py`、`backend/app/tasks/service.py`
  （各 1 处单字）
- `backend/tests/**`（各 1 处）
- `backend/migrations/002_task_claims_nullable.sql`：**故意不改** —— 已应用的迁移按
  **checksum** 校验（`backend/app/db/migrations.py:67`，文件变了就 `MigrationError`，
  平台起不来）。那 2 个坏字符留着，比全平台起不来划算。
- `docs/claims/**`（4 处）、`docs/incidents/**`（1 处）、`docs/tasks/notes/**`（2 处）

**不碰** `data/`（运行期产物，不受版本控制）、不动其他 Harness 的工作区与分支。

## 怎么做到不出错

1. **只改文字，不改行为**：坏字符全在注释与文档里；`policy.py` 那 5 行按**代码实际
   行为**重写（豁免只人能给、一条活只能一次、决定行锚在任务最后一个事件上）。
2. **不碰已应用的迁移**：迁移按 checksum 校验，改一个字平台就起不来 —— 这一处留着。
3. **改完再扫一遍**：全仓（受版本控制的文件）U+FFFD 归零（迁移那一处除外）。
4. **H-8 已查**：`codex-model-switch` 在途改的是 `api/routes.py`、`api/schemas.py`、
   `adapter/**`；本卡不碰这些。`backend/app/tasks/service.py` 那处是注释，与 codex
   已提交分支的改动位置不重叠。
