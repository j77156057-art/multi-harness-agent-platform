### tasks-critical-path：校验 TASKS.md 自己那份关键路径副本 + 查重复 task_id（done）

~~~text
任务编号：tasks-critical-path
负责人 Harness：Claude Code
开始时间：2026-10-05 10:07
工作区：.worktrees/claude/
依赖任务：graph-traceability（✅ 已合入 main）
真相源引用：docs/spec/mvp-task-checklist.md §0.2；TASKS.md「## 关键路径」
允许修改目录：tools/discipline/**、TASKS.md、docs/claims/**、docs/traceability.md
禁止修改目录：backend/**、docs/spec/0.3-R10.md（只读）
交付分支：agent/local/claude/tcp3/a1
commit：见分支
test_id：MVP-PROCESS-001、DOC-PLAN-001
当前状态：done（除 claim-nullable 重复文件外全绿）

### 动机：上一轮我制造的覆盖漏洞

关键路径在两处声明：清单 §0.2 与 TASKS.md 的「## 关键路径」。上一轮我只改了
两处的**数据**，却把断言只接到清单那一处 —— TASKS.md 里的旧副本（漏了 MVP-03 与
MVP-10b）无人拦，读 TASKS.md 的人会拿到已被推翻的结论。本次把链长解析复用到两处，
各自校验长度与每一步是否为真实依赖边。

**另加两条 task_id 唯一性断言**：重复 task_id 会**静默**通过所有基于集合的比较，
必须单独查。来源是合并时两边各自追加了同一个任务的行。

### 落地时查出的真实缺陷

1. TASKS.md 关键路径仍是 7 节副本 —— 本次断言的直接目标，已修
2. `docs/claims/` 里 `claim-nullable` 曾有两个内容完全相同的文件（拆分工具按 TASKS.md
   的行生成，而该任务在表里出现了两次）。**已由 `ec7ae01` 去重并重编号为 01~24**，
   本任务落地时该文件已不存在，无需再删

### 与 main 上认领登记拆分的冲突（本任务的主要波折）

我第一次合并时带的是**旧版 discipline.py**（读 TASKS.md 的认领表）。而 main 上
Qoder 已把认领登记拆成 `docs/claims/*.md` 并同步改了 discipline.py，其理由与我在
本任务卡里 independently 得出的合并教训一致，还量化了：「当天 11.5 小时内 TASKS.md
被改 40 次、产生 19 个 merge commit，冲突 100% 集中在这张表与任务卡两处」。

**带旧文件覆盖会把这个重构倒退回去。** 处理：中止合并，从最新 main 起新分支，
只把 4 条新断言移植到他们的新版解析器上，TASKS.md 的小改动针对当时内容重写。

过程中我用脚本整体重写认领登记表，把 TASKS.md 从 1428 行截断成 27 行（已从提交恢复）。
**这个文件不能整体重写，只能逐行最小改动并立刻校验行数。**

### 验证结果

  docstate    : 90 项检查，0 项失败
  discipline  : 32 项检查，0 项失败（main 合入前为 28 项，本任务净增 4 项）
  ruff check  : All checks passed!
  ruff format : 40 files already formatted
  pytest      : 111 passed
  负向测试    : TASKS.md 关键路径改回 7 节 → FAIL；还原转绿
                任务表插入重复 task_id 行 → FAIL「任务表无重复 task_id」
                认领文件序号撞号（我的 23- 与 main 已有 23-）→ FAIL「认领文件序号唯一」
                —— 这条是 main 新加的断言，落地第一个就抓到我自己的文件

commit：`90047d5`（已合入 main）

已知问题：
- `traceability.md` 的「强制状态」列由人手写，无断言覆盖
- 本任务卡里曾把「待授权删除某文件」写成待办，实际那份文件已由 `ec7ae01` 删除 ——
  **陈旧的待办比没有待办更坏**，它让人以为还有事没做。已改为记录实际结果
~~~
