### MVP-02-amend：修正 R10 §8 唯一键自相矛盾 + 补机械断言（done）

~~~text
任务编号：MVP-02-amend
负责人 Harness：Claude Code
开始时间：2026-10-05 06:09
完成时间：2026-10-05 06:32
依赖任务：MVP-02（✅ 已验收）
真相源引用：R10 §8（canonical schema）、§11（Claim 幂等）、§37.6（schema 补充清单）、
            §29.6（R11 复审请求）、§38（修订映射与增量规范）
允许修改目录：docs/spec/0.3-R10.md、tools/docstate/**、README.md、
            docs/dev-convention.md、TASKS.md
禁止修改目录：backend/migrations/001_initial.sql（已冻结，checksum 校验）、
            docs/spec/mvp-task-checklist.md、backend/**
交付分支：agent/local/claude/MVP-02-amend/a1
test_id：DOC-SCHEMA-001、DOC-STATE-001
测试命令：./scripts.sh check
当前状态：done
commit：8faf9e2、e253406、0a8d06b + 本卡收尾

### 完成标准与验证结果

| # | 完成标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | §8 `claim_requests` 行自洽 | ✅ | 删 `unique(claim_request_id, task_id)`，注明 per-task 唯一性由 `task_claims` 承担 |
| 2 | 新增机械断言能复现该矛盾 | ✅ | 修复前 `docstate` 88 项 1 失败，输出 `claim_requests 引用未声明列 task_id`（原文见 §38 R10-fix-09） |
| 3 | 修复后门禁全绿 | ✅ | `docstate 88 项检查，0 项失败`；`ruff check: All checks passed!`；`ruff format: 13 files already formatted`；`pytest: 20 passed in 0.54s` |
| 4 | 应用启动自检通过 | ✅ | `[ok] 规范一致性检查通过 88 项`；`GET /spec/status -> 200`（total 88 / failed 0）；`GET /health -> 200` |
| 5 | 断言非空跑 | ✅ | 断言 B「检查 1 处 DDL」，断言 A「检查 3 张表，跳过 5 张」 |

### 实际命中 3 张表，不止 1 张 —— 计划外的范围扩大

新断言首次运行同时命中 `artifacts` 与 `reviews`：

~~~text
越界=['claim_requests 引用未声明列 task_id',
      'artifacts 引用未声明列 attempt_id,task_id',
      'reviews 引用未声明列 attempt_id,review_revision,reviewer_harness_id,task_id']
~~~

三者同为「§8 行声明复合唯一键却未在同一行列出那些列」。但只有 `claim_requests` 是真矛盾
（§11 声明单列 PRIMARY KEY，`unique()` 里的 `task_id` 无处落地）；`artifacts` 与 `reviews`
的缺失列是真实外键，本就存在于 `001_initial.sql` 且本就在各自 UNIQUE 约束内，属同类但无害。

因门禁必须绿才能合入（`main.py` 在 docstate 失败时拒绝启动），三行一并修：把列名补进
「关键字段」单元格 —— 与 R10-12/13/14 同一手法，不新增表、不新增列，§8 表清单仍 41 张。

### 与 MVP-02 验收记录（9e4605e）的关系：已收敛，非冲突

`9e4605e` 曾把修复方向写为「拆 `claim_requests` + `claim_request_results` 两张表」。
WorkBuddy 在 `0ac5365` 中**主动撤回该判定**，确认本任务的判断成立，并把 spec 修正交由
本任务执行。故 C 方案（C 拆两表）未采纳，理由与本任务一致：

1. R10 §29.6（`0.3-R10.md`）要求下一轮复审「不是继续在正文里加字段」；
2. `001_initial.sql` 的 `task_claims` 已有 `UNIQUE (claim_request_id, task_id)`，
   per-task 唯一性已被真正强制，重试回放用
   `SELECT * FROM task_claims WHERE claim_request_id = ?`，无需新表。

**R9-03 是位置迁移不是撤销**：per-task 首次结果能力未减，只是唯一性约束落在 `task_claims`。
§38.6 对 R9-03 记的「通过」结论已按此修正。

### 仓库拓扑（已解决）

开工时 `main`（`c0ba8cc`）不含 MVP-02 代码 `b154f5c`，两线自 `810d88d` 分叉。期间
`c614d51` 已把 MVP-02 合入 `main`，本分支 ff 到 `c614d51` 后基线完整，门禁计数正常
（`pytest 20 passed`）。

### 过程中的事故：未提交改动被并发 checkout 丢弃（重要）

第一版 docstate 断言在写入后、提交前被**完全丢弃**——工作区被并发 agent 的
`git checkout` / 分支切换冲掉，`git status` 显示干净，改动无声消失。当时已写的两段代码
（约 50 行）需要全部重做。

**根因**：多个 AI 共享同一个 git 工作区且无隔离，任一 agent 切分支即改变所有人的
工作树。本任务此后每改完一个文件立即 commit，不再让改动裸露在工作区。

**建议**：为每个 AI 分配独立 worktree（`git worktree add`），否则并发提交丢失是必然而非意外。

### 遗留待办（不在本任务范围）

1. `docs/traceability.md` 被 `dev-convention.md` H-4 与 README 引用，但**文件不存在**；
2. `schema-keys` 组覆盖 3 张 / 跳过 5 张，跳过的 5 张（`task_attempts`、`task_outputs`、
   `test_case_results`、`verification_receipts`、`side_effect_intents`）因 §37.6 用合并或
   散文写法而未被检查。扩到全覆盖需先把 §37.6 改为可解析列清单，属阶段 0a 冻结表工作；
3. `9e4605e`/`c0ba8cc` 等直接提交在 `main`，与登记规则第 6 条冲突；第 8 条又要求
   TASKS.md 改动尽快到 main。两条规则对纯文档 commit 的处理未定义。

已知问题：

- ⚠️ `schema-keys` 为部分覆盖，跳过表清单与计数实时输出在 check 消息里，未静默缩小。
- ⚠️ 断言 B 当前只覆盖 1 处 DDL（§11 的 `claim_requests`），覆盖面随 §11 正文增长而扩大。
- ⚠️ 本任务未走 Merge Gate（§11/§8 的 spec 修订按流程不走代码 Gate），合入方式待定。
~~~

### ✅ 并发初始化缺陷：已修（WorkBuddy，06:30）

`test_concurrent_initialization_and_events_have_one_order` 在 main 上 **0/5 失败**
（不是 flaky，是确定性失败），报
`sqlite3.OperationalError: database is locked`，位置 `app/db/connection.py:39`
的 `PRAGMA journal_mode=WAL`。

**根因**：`connect()` 每次都设 `journal_mode=WAL`，而变更 journal mode 需要排他锁，
且 SQLite 有两个限制：

1. `busy_timeout` 对 `PRAGMA journal_mode` **不生效**（不能靠超时重试）；
2. **禁止在事务内变更 journal mode**
   （`cannot change into wal mode from within a transaction`）——
   所以第一版修法用 `BEGIN IMMEDIATE` 包住，反而直接报错。

**修法**：`connect()` 不再设 journal mode；新增幂等的 `ensure_wal()`，
用 `<db>-wal.lock` 旁路文件做**跨进程互斥**（Windows `msvcrt.locking` /
POSIX `fcntl.flock`），进锁后重新确认模式，已是 WAL 则立即返回。

**验证**：并发测试 **10/10 通过**（修前 0/5）；全量 20 passed；
门禁三道全绿（docstate 88/88、ruff check、ruff format）。

**方法论**：该缺陷在 Codex 分支上 `pytest` 显示 20 passed，合并到 main 后才暴露。
**单次通过不等于通过** —— R10 要求重复验证的用例必须多次复跑（本例 10 次），
不能只看 CI 的一次绿灯。

改动文件：`backend/app/db/connection.py`（属 MVP-02 目录，跨任务改动已在此登记，
分支 `agent/workbuddy/fix-concurrent-init`）

### ✅ WAL 锁 Windows 兼容性修复（WorkBuddy，09:20）

早上修的并发初始化缺陷（`7531592`）在**根目录**复测时暴露第二个问题：

~~~text
PermissionError: [Errno 13] Permission denied:
  "...\test_concurrent_initialization0\state.db-wal.lock"
~~~

**根因**：Windows 上 `msvcrt.locking` 的 `LK_LOCK` 虽是阻塞式，但当文件已被
另一进程独占时，本进程的 **`open()` 会先抛 `PermissionError`**，而不是阻塞等待。
早上的实现只用了 `LK_LOCK`，没有重试。

**修法**（`_lock_file` + `ensure_wal`）：

1. 加锁改用**非阻塞** `LK_NBLCK` / `LOCK_NB`，配合调用侧有限超时重试
   （`errno` 为 `EACCES` / `EAGAIN` / `EDEADLK` 时重试，超时上限 10s）；
2. **打开锁文件本身也纳入重试** —— 这是 Windows 特有的第三层坑，
   只重试加锁不够；
3. 抽出 `_LOCK_TIMEOUT_S` / `_LOCK_POLL_S` 常量，避免魔法数。

**验证**：并发测试 **10/10**；全量 20 passed；docstate 88/88；ruff check + format 全绿。

**教训**：早上的"10/10 通过"是在 worktree 外的根目录测的，用的是同一个 venv
但不同路径 —— **修复必须在目标环境验证**。跨机器/跨路径的行为差异要显式复测，
这与"单次通过不等于通过"是同一类问题。

改动文件：`backend/app/db/connection.py`

### ⚠️ 更正：flaky 未能复现，撤回登记（10:10）

**原登记（09:55）**：称 `test_concurrent_initialization_and_events_have_one_order`
"连续两次全量跑时偶发失败（1/9）"，并给出两个猜测原因（句柄释放延迟、Defender）。

**撤回理由**：按 `fix-flaky-init` 任务做定向复现，**无法复现**：

| 复现方式 | 次数 | 结果 |
|---|---|---|
| 单测连跑（退出码判定） | 20 | 20/20 通过 |
| 全量连跑（退出码判定） | 15 | 15/15 通过 |
| 复刻原场景（连续两次全量跑） | 16 | 16/16 通过 |
| 模拟门禁三道（docstate + pytest ×2） | 12 | 12/12 通过 |

**原判断的错误**：09:55 那次的"1/9"来自
`pytest -q ... | tail -2` 的输出，而 `pytest -q` 成功时**只输出进度行**
（`.... [100%]`），没有 `N passed` 汇总行。我当时把另一次命令的 `FAILED` 行
误归因到这一轮，且**没抓到 `^E` 错误行**就下了根因判断。
更早一次用 `grep -q "1 passed"` 判成功也不可靠 —— 单测通过时输出是 `.` 而非 `1 passed`。

**结论**：当时**没有可归因的失败证据**。可能是：
- 上一轮 docstate 注入缺陷实验改过 R10 文件、pytest 有测试读它（我确实做过
  注入验证，还原后立即跑的那次）；
- 或其他一次性环境因素。

**给后续的纪律**：判 pytest 结果**一律用退出码**（`$?`），不要用 `tail`/`grep` 解析输出；
失败时必须同时抓 `^E` 行与 `FAILED` 行，两者缺一不可归因。`scripts.sh check`
本身用退出码，不受影响。

**与今早已修缺陷的区分仍有效**：`403e3a5` 修的 `PermissionError` 是
**确定性**失败（有明确 `^E` 行、0/5 复现），已闭环；本条是**无法复现**的
一次性现象，两者不是同一问题。
