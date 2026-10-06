### claim-seq-adopt：撤销序号连续性断言（done）

~~~text
任务编号：claim-seq-adopt
负责人 Harness：Claude Code
开始时间：2026-10-06 09:36
工作区：.worktrees/claude/
依赖任务：无
真相源引用：docs/proposals/claim-seq-contiguity.md（协调者裁决采纳）；
            tools/discipline/discipline.py registry 组
允许修改目录：tools/discipline/**、docs/proposals/**、docs/traceability.md、
            docs/dev-convention.md、TASKS.md、docs/claims/**
禁止修改目录：backend/**、docs/spec/0.3-R10.md（只读）
交付分支：agent/local/claude/claim-seq-adopt/a1
commit：f39fe37
test_id：MVP-PROCESS-002
当前状态：done

### 改了什么

| 动作 | 内容 |
|---|---|
| **撤销** | registry 组的「认领文件序号连续为 1..N」断言 + 单测 `test_claim_serials_must_be_contiguous` |
| **保留** | 序号唯一性断言（它抓的是真问题：同一序号两个文件） |
| **新增** | 「认领文件名里的 task_id 与文件内容一致」+ 单测 `test_claim_filename_must_match_its_content` |
| **新增** | 单测 `test_gap_in_serials_is_accepted` —— 跳号不再报错，把这次撤销钉住 |
| **同步** | `dev-convention.md` 强制程度表、`traceability.md` 的 `MVP-PROCESS-002` 行、提案文档的「裁决与实施」 |

### 支持裁决的机械证据：34 次重命名

`git log --all --diff-filter=R -- 'docs/claims/*'` 统计到 **34 次**认领文件改名，
**四方都中招**：TRAE、Qoder、OpenCode、Claude Code。关键几条：

  `34-trae--MVP-05-verify` → `36-`    （TRAE，断言的交付方，跳 2）
  `31-qoder--MVP-06`       → `34-`    （Qoder，跳 3）
  `36-claude-code--claim-seq` → `39-` （Claude Code，跳 3）
  `38-opencode--root-worktree-clean` → `40-`（OpenCode，跳 2）
  `39-qoder--MVP-07`       → `38-`    （Qoder，**降号**）

三条结论写进了提案：
1. **四方都撞** —— 不是某一方不守规矩
2. **出现跳 2、跳 3 与降号** —— 连续性断言不只没防住撞号，还**逼着人往回改号**
3. **改号发生在合并时** —— 与论据一一致：取号者控制不了合并顺序

### 对 TRAE 的交付不作否定

`GATE-MERGE-CHECK-01` 的断言**确实按设计工作**（注入缺号文件时正确报红）。
问题不在实现，在**该不变量本身在并发下无解**。该交付的另一半（merge 组、
commit 反向核对、fail-closed、`fetch-depth: 0`）**全部保留**，一行未动。

### 验证结果

  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败（撤销 1 条、新增 1 条，净增 0）
  ruff check  : All checks passed!
  pytest tools: 28 passed（原 27：−1 连续性单测，+2 新单测）
  负向测试    : `test_claim_filename_must_match_its_content` 造一个文件名
                `01-h--t1.md` 而内容 `task_id=t2` 的文件 → 断言转红
                并指出「文件名 01-h--t1.md」；`test_gap_in_serials_is_accepted`
                注入 `01-` 与 `03-`（缺 02）→ 不再报错

已知问题：
- 序号前缀**保留**（人读时便于排序），只是不再断言连续。认领顺序本就在每个文件
  内部有「认领时间」字段，不依赖文件名
- 取号竞争本身**未解决** —— 本任务只换掉了一条不可满足的不变量。根治需在 main 上
  串行取号或引入全局分配器，超出本提案范围
~~~
