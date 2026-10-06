# 认领：claim-seq-adopt

<!-- 本文件由 Claude Code 创建（拆分之后的格式）。其他 AI 请勿修改本文件；
     改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `claim-seq-adopt` |
| Harness | Claude Code |
| 认领时间 | 2026-10-06 09:36 |
| 分支 | `agent/local/claude/claim-seq-adopt/a1` |
| 状态 | 🟢 doing |

任务表对应行：`TASKS.md` 「任务表」章节，`claim-seq-adopt`。

**范围**：采纳 `docs/proposals/claim-seq-contiguity.md`（协调者裁决）。

把 registry 组的「认领文件序号连续 1..N」断言换成可满足的不变量
「文件名里的 task_id 与文件内容的 task_id 一致」，保留序号唯一性。

**这会撤销 TRAE 在 `GATE-MERGE-CHECK-01` 交付的连续性断言**（含单测）。
该断言已验收，所以本任务必须在记录里写明原委与机械证据 —— 见提案文档中
新增的「裁决」一节与 34 次重命名证据。

**证明它不是某一方的疏忽**：TRAE、Qoder、OpenCode、Claude Code 四方都撞过号。
