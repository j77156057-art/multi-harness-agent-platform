### mergeguard-fix：续跑路径的「认错人」缺陷（✅ done）

~~~text
任务编号：mergeguard-fix
负责人 Harness：OpenCode
开始时间：2026-10-07 03:10（认领 59 号）
依赖任务：root-worktree-clean（其任务行所载）；实质承接 2026-10-07 03:05 实测事故
工作区：未登记（认领 59 号无此字段；本代笔不做推定）
交付分支：agent/local/opencode/mergeguard-fix/a1
真相源引用：tools/mergeguard 自身三原则（冲突不 abort、绝不接管别人的合并）；
            认领 59 号的实测复盘；docs/incidents/mergeguard-open-defects-2026-10-07.md
完成标准（摘自认领 59 号）：
  续跑路径必须证明「pending 记录与本次请求是同一次合并」，不能只比对
  merge_head 相等——否则会把别人的遗留合并当成自己的续跑（最坏：门禁恰好绿时
  把他人的合并提交成自己的）。有机械测试。
test_id：MVP-MERGE-LOCK-001
当前状态：done（合入 main；本卡由 Qoder 按协调者授权代笔，2026-10-07 05:5x）
完成时间：2026-10-07 04:35（合入）
commit：`2c85a9c`（合并提交；分支内实现提交 `a80c5ad` fix(mergeguard): 续跑必须
        证明「记录与请求是同一次合并」）
验证结果：
  - 合入时门禁：docstate + discipline 绿（当时的检查集下通过并提交）
  - 新增回归：backend/tests/test_merge_guard.py +137 行（合并路径的身份判据用例），
    随本次合并入 main
  - 代笔注：代笔未独立复跑该 137 行；以 main 合入后的全量门禁为准
已知问题：
  - 认领 59 号文件自身仍写 🟢 doing（本代笔的范围是卡与任务行；认领文件保持
    OpenCode 原文不动）
  - 与本卡无关、同一文件的其他缺陷（冲突路径不 stage、release 返回值被忽略）
    见 incident 文档，按需另行开单
~~~

> **代笔归因**：本卡由 Qoder 于 2026-10-07 05:5x 按协调者授权代笔。缘由：
> mergeguard-fix 已合入（`2c85a9c`）但缺卡、任务行未收敛，卡与 merge 两组
> discipline 判红，且 Qoder 自己的待提交合并现场已把全队合并冻住一小时。
> 内容全部取材于认领 59 号的原文与 git 记录，未添加任何未发生的验证声明。
## OpenCode 原卡补充（卡迁移时并入，2026-10-07）

> 本节是 OpenCode 原写在 TASKS.md 内联的那张卡的补充部分。方案 C2 把任务卡搬到
> `docs/tasks/notes/` 后，两份并存会触发 `cards` 组的「同一 task 不该有两张卡」。
> 卡片本体（`~~~text` 断言块）以上面的代笔版为准；这里只保留代笔版未收录的内容。
> **未改动上面的断言块**——那是 Qoder 的交付。

### 事故经过（2026-10-07 03:05，实测，不是设想）

1. Qoder 用 mergeguard 合并 `agent/local/qoder/sweeper-deps-resources/a1`，
   写下 `merge-pending.json`（`merge_head=eb7d8d36`）+ `MERGE_HEAD`。
2. 他的进程退出（pid 38616 已死），合并悬在「已合并、未提交」。
3. 随后有人请求合并**另一条分支**。`_classify_pending_merge()` 只比
   「pending 的 `merge_head` == 当前 `MERGE_HEAD`」，两者都是 `eb7d8d36`
   → 判定「本工具自己的遗留合并」→ **跳过 merge 步骤**，直接在
   **别人的工作树**上跑门禁。
4. `git merge-base --is-ancestor` 确认：**那个分支根本没被合并**。

**最坏情况不是这一次**：门禁若恰好是绿的，工具会报成功并提交 ——
**把别人的合并当成自己的合并提交掉**。这正是本工具存在的目的所防的事故
（docstring 第 3 条「绝不接管别人的合并」），而我在续跑这条路径上漏掉了。

### 修法

续跑需要**两条独立证据**，缺一不可：

1. `MERGE_HEAD` 内容 == 记录里的 `merge_head`（是同一次合并）
2. **`MERGE_HEAD` == `rev-parse(本次请求的分支)`**

第 2 条刻意**不**比对记录里的 `branch` / `task_id` 标签 —— 那是上次运行写下的
字符串，是标签不是事实，会被改、被抄错、被手工编辑。而
`MERGE_HEAD == rev-parse(请求的分支)` 是 git 自己的事实：它精确证明
「现在悬着的这次合并，合并的正是我要求合的那个分支的那个 commit」。

### 判据选型是被一条测试推翻的（值得记）

第一版写成「记录的 branch 或 task_id 与请求相符」。结果
`test_pending_record_of_another_task_same_branch_is_refused` 直接红 ——
分支相同、task_id 不同时，悬着的合并**确实就是**请求的那一笔
（MERGE_HEAD 就是那个分支的 tip），提交它就是完成请求。task_id 只是锁信息里
的标签，不构成合并的身份。

于是那条测试被我改成「仍然续跑」，并成为判据选型的对照。**用标签当身份，
会多拒一次本该接受的续跑，而且拒得毫无理由。**

### 顺带挡住的第二类现场

请求的分支在悬着那次合并之后又前进了（别人从别处 fetch 或强推了那个 ref）
→ 也拒绝续跑。所以「事实比对」同时挡住了「别人的现场」与「过期的现场」。

构造顺序有讲究：必须**先推进分支再造 MERGE_HEAD**，反过来做不到 ——
`git checkout` 会顺手清掉 MERGE_HEAD，悬置现场也就没了。

### 这次 bug 的教训（比代码本身更值得记）

我给续跑机制写的第一条用例
（`test_cli_gate_failure_does_not_commit_and_rerun_resumes`）只测了
「**自己的**记录能续跑」，**没测「别人的记录不能被当成自己的」**。

续跑机制的风险全在「认错人」这一侧，而我的用例全在「认对人」这一侧 ——
整整一天的测试都建在另一半上。它也不在任何人的评审清单上：TRAE 评的是
merge-interlock（早于续跑功能），WorkBuddy 报的是 commit 误报（另一半问题）。

**新功能的风险不在「能不能用」，而在「会不会认错人」；而我给新功能写的第一
批用例，全都在验证前者。**

### 尚未修（不在本任务范围）

`_run_gate` 取门禁输出的最后一行做摘要，对「门禁自己在结果之后还打印别的东西」
没有防护。实测吃过：discipline 打印的 SyntaxWarning 出现在结果之后，摘要变成
那条警告，**真正的检查结果被吃掉**，而退出码仍是 0 —— 门禁是绿的，绿的证据是
错的。正确做法是让门禁把摘要写进机器可读位置（`--json` 已有），或让 `_run_gate`
识别 `结果: ` 前缀。
