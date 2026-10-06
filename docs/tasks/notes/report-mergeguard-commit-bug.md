### report-mergeguard-commit-bug：mergeguard 的 commit 成功却判失败（✅ done，报告已交付）

~~~text
任务编号：report-mergeguard-commit-bug
负责人 Harness：WorkBuddy（报告方）；修复方 @opencode
开始时间：2026-10-06 09:00
当前状态：done —— 本task 的交付物是**报告**，已交付；修复是另一件事，由 @opencode 认领
依赖任务：无
工作区：.worktrees/workbuddy/
真相源引用：R10 §26（VERIFY-RECEIPT-001 的验证要求）
允许修改目录：docs/**、TASKS.md
禁止修改目录：tools/mergeguard/**（属 opencode 所有）、backend/app/**、docs/spec/**
交付分支：agent/workbuddy/report-mergeguard-commit-bug
test_id：MVP-PROCESS-001
测试命令：./scripts.sh check
当前状态：done
commit：ad25535
验证结果：
  - 复现：2026-10-06 09:00 合并 slow-test-visibility 时抛 CalledProcessError
  - 但 merge commit 5dbb597 实际存在、两个父提交、内容全在位、门禁全绿
  - 根因：cli.py:168 用 check=True，撞上本机间歇性的
    fatal: unable to write new index file 就直接判失败
  - 附带查到 unable to write new index file 的根因：TASKS.md 已涨到 249795 字节，
    git 对 >128KB 的文件不做 delta 压缩，整块写入 object，写index 窗口变长
后续（不在本 task 范围内）：
  @opencode 需要**单独认领一个 task** 来修 tools/mergeguard/cli.py：
  在 commit 调用处捕获
  CalledProcessError 后先核实「工作区是否干净 + %P 是否两个父」，
  确认已提交则视为成功；或用合并前的 ORIG_HEAD 是否推进来判断。
  建议修法已写在 docs/claims/52-workbuddy--report-mergeguard-commit-bug.md
已知问题：
  TASKS.md 过大（250 KB）是 git add 间歇性失败的诱因，建议拆分为
  「任务表 + 关键路径」与 docs/tasks/notes/<task-id>.md。这与 2026-10-05 的
  claims 拆分同类——847cc69 只拆了认领表，复核记录和任务卡留在原地。
~~~

### 本项目已发生的协作失误（4 次）

前 3 次是登记与合并纪律问题，共同根因：把"提交"当成了终点。共享协作文档的终点是
**main 上可读**。

| # | 失误 | 后果 | 修法 |
|---|---|---|---|
| 1 | MVP-01 干活前未登记 | Codex 看不到 MVP-01 已占用 | 登记规则第 0 条 |
| 2 | MVP-01 直接提交 main | 未经 Merge Gate 的 commit 成为基线 | 登记规则第 6 条 + `MVP-RETRO-001` |
| 3 | 两次登记都只提交在任务分支 | main 上读不到，等于没登记 | 登记规则第 8 条（本次新增） |
| 4 | **多 AI 共享同一 git 工作区** | **提交被无声丢弃 4 次** | **`docs/dev-convention.md` H-7 工作区隔离（本次新增）** |

### 第 4 次事故：共享工作区导致提交丢失（2026-10-05，代码未受损）

与前 3 次不同，这次是**代码/提交丢失**，不是纪律问题。全部发生在 MVP-02-amend 期间：

| 时刻 | 发生了什么 | 损失 |
|---|---|---|
| 1 | 我的 docstate 断言已写入未提交，另一 agent `git checkout` 到别的分支 | 约 50 行改动无声消失，`git status` 干净；已重做 |
| 2 | 我提交「MVP-04 提示」时 HEAD 已被切到 `agent/workbuddy/fix-concurrent-init` | 提交落在**别人分支**上，不在我的分支也不在 main |
| 3 | 某次 `git reset a43baac` 丢弃了 `a0fd0cd` | commit 对象成为孤儿，**但内容未丢** —— 其作者已把「并发初始化缺陷：已修」重新记录到 main 的 TASKS.md（`j77156057-art`，06:41）。无需恢复 |
| 4 | 同类 reset/移动分支再次清掉已作废的 `e0d60f7` | 无实质损失（该提示内容已过时，其纠正版本已随 `247ddc7` 进 main） |

**根因**：所有 AI 共用仓库根目录这一个工作树，任一 agent 的 `git checkout` / `git switch`
/ `git reset` 都会改变所有人的 HEAD 与工作树。这不是"可能出错"，是**必然出错**。

**修法**：`docs/dev-convention.md` 新增 H-7 —— 每个 AI 一个 `git worktree`
（`.worktrees/<harness>/`），仓库根目录只做合并与门禁、不编辑文件。
已为 workbuddy / codex / claude 建好，并共享 `.venv` 联接使门禁可直接运行。

**仍未解决**：分支引用跨工作区共享。`git branch -f` / `git reset` 到别人的分支仍会
删掉别人的工作（事故 3 即此）。H-7 规则 2 要求"只移动自己拥有的分支引用"，
但这靠自觉，无机制强制。
