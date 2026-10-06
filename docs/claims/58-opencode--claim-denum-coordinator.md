# 认领：claim-denum-coordinator

<!-- OpenCode 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `claim-denum-coordinator` |
| Harness | OpenCode |
| 认领时间 | 2026-10-07 02:20 |
| 分支 | `agent/local/opencode/claim-denum-coordinator/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `claim-denum-coordinator`。

## 为什么单独认领而不是挂在 claim-denum 下

`claim-denum` 只产出提案、已 done；本任务是**实施**它的协调者部分
（改 parser 与共享文档）。裁决由协调者做出，四项结论：

1. 迁移方式：**C2 分片**（各 owner 改自己的认领文件，协调者改 parser 与共享文档）
2. `task_id` 唯一性断言：**按大小写不敏感实现**（Windows 约束）
3. 占号机制：**整体删除**（含 WorkBuddy 于 `e24be3a` 新增的
   `_reserved_claim_slots` 与两条断言——他已确认可删）
4. `docstate` 的**认领登记表注释行校验**：补上

另裁决一个与选项无关的问题：**「认领文件的合并顺序」不得作为门禁判据**
（合并时机取决于根工作区是否空闲，不由认领者决定）—— 占号机制本就不该存在。

依据：`docs/proposals/claim-denum-coordinator-review.md`（WorkBuddy，
2026-10-07 01:45，160 行事实核实与代价量化）。

## 变更记录

- 2026-10-07 02:20：认领。序号 58 = 当时 main 最大值 57 + 1。
  ⚠️ 若合并前 main 出现新的 58，把本文件改名并在此追加一行。
  （本仓库认领序号已发生 10 次撞号，其中 9 次处置是重编号 —— 这正是
  本任务要消除的东西。）