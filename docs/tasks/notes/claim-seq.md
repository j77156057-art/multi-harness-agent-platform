### claim-seq：序号连续断言在并发下不可满足（提案，不实施）

~~~text
任务编号：claim-seq
负责人 Harness：Claude Code
开始时间：2026-10-06 00:16
工作区：.worktrees/claude/
依赖任务：branch-visibility（✅ 已合入 main）
真相源引用：tools/discipline/discipline.py registry 组；GATE-MERGE-CHECK-01 交付记录；
            TASKS.md claim-nullable 违规记录；Qoder claims-split-review 发现 ②
允许修改目录：docs/proposals/**（本任务只产出提案，不改代码）
禁止修改目录：tools/**、backend/**、docs/spec/0.3-R10.md（只读）
            —— 撤回实现后本任务不动任何既有断言
交付分支：agent/local/claude/claim-seq/a1
test_id：MVP-PROCESS-001
当前状态：🔍 review —— 提案待 TRAE（断言交付方）或协调者裁决

### 提案内容

见 `docs/proposals/claim-seq-contiguity.md`。一句话：**「认领文件序号连续 1..N」
在并发分支下结构上不可满足** —— 两条分支各自合规，合并结果却取决于合并顺序。

  设 main 最大为 30：A 取 31、B 取 32。B 先合并 → main 变成 1..30 + 32，缺 31 → 红。
  加锁解决不了：锁能让取号串行，改变不了合并顺序。

建议：保留序号唯一性与 task_id 唯一性，把连续性换成「文件名里的 task_id 与文件内容
一致」—— 同样能抓真实错漏，且两个 agent 独立操作也能同时满足。

### 为什么是提案而不是实现

我**曾实现并测过**（负向测试：文件名 `wrong-name` 而内容 `task_id=claim-seq`
→ 断言转红）。随后在 TASKS.md 查到连续性断言是 **TRAE 的 `GATE-MERGE-CHECK-01`
刚交付、带单测、已验收**的功能（该卡写明「本任务补连续」、验证「序号不连续被拦 ✅」）。

撤销同事刚验收的功能不该由单方面完成 —— `dev-convention.md` H-5「跨边界改动需
owner 同意」。故撤回实现，只留提案，交回归裁决。

撤回前还发现并撤销了一处**我自己的无理由改动**：给 `parse_claim_table` 的 `branch`
字段加了 `.strip("`")`（原文没有，反引号本由 `branch_tokens` 处理）。这一行导致
merge 组误报 5 个 Qoder 的分支「已合入但未登记」。**顺手改无关代码**是本次最该记的
教训 —— 它让一个局部改动产生了跨组的假失败。

### 验证结果

  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败（撤回实现后与 main 完全一致，
                连续性断言仍在原位、未被削弱）
  git diff    : tools/** 零改动 —— 本任务没有动任何人的交付物
  负向测试    : 已随实现一并撤回（此处记录它的结果，不保留代码）

已撤回的实现留在本机 `D:\Temp\my_discipline_proposal.py`（**非仓库内**），
若裁决为「采纳」，可直接取用。

已知问题：
- 在裁决之前，认领取号的 churn 会继续发生（本次为让文件序号合规改了三次 31→33→34）
- 本提案不解决取号竞争本身，只主张**换一条可满足的不变量**；若要根治竞争，
  需在 main 上串行取号或引入全局序号分配器，那超出本提案范围
~~~
