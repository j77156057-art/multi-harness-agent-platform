# OFFICE-UI-38

### 统一任务卡

任务编号：OFFICE-UI-38
负责 Harness：Mavis
开始时间：2026-10-26 05:40 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~37 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/git/repo.py`（`has_object`）、`backend/app/tasks/service.py`
（`diff_for_task`）、`docs/tasks/notes/diff-uninitialized-repo.md`（被收尾的那张卡）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-002

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-002` |
| 允许修改 | `docs/tasks/notes/diff-uninitialized-repo.md`、`docs/claims/codex--diff-uninitialized-repo.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-38.md`、`docs/tasks/notes/OFFICE-UI-38.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上；核实出缺口就另开卡）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `diff-uninitialized-repo` 写着 doing（实现已就位，待合并门禁），让人以为正式仓库路径不对时那个 500 还没修 | 逐条核实 + 真跑（真的把 `AP_CANONICAL_REPO` 指到不存在的目录），状态改成 done |

## 为什么

仓库主人说「你接手 codex 的活」。 OFFICE-UI-37 已按同样办法结掉
`pool-generic-adapter`；这是同一类收尾的第二张。

## 怎么做到不出错

1. 不碰实现。
2. 真跑要真的制造那个条件（不存在的仓库路径）。

## 完成标准

1. 逐条核实 + 真跑。
2. 状态改成 done。
3. 门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 逐条核实 | 待回填 |
| 真跑（端点回什么） | 待回填 |
| 定点测试 | 待回填 |
| `tools/docstate` / `tools/discipline` | 待回填 |
| `pytest`（全量） | 待回填 |
