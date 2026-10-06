### merge-interlock：给共享 main 合并加互斥锁（✅ done）

~~~text
任务编号：merge-interlock
负责人 Harness：OpenCode
开始时间：2026-10-05 20:40
依赖任务：discipline（✅ done，工具形态参照它）
工作区：.worktrees/opencode/
真相源引用：AGENTS.md §0与 H-7（共享工作区会丢提交，本项目已丢 4 次）、
            dev-convention.md「会被门禁红掉的」/「靠自觉的」两张清单、
            TASKS.md 违规记录章节
允许修改目录：tools/mergeguard/**、backend/tests/test_merge_guard.py、
            scripts.sh、Makefile、docs/dev-convention.md、docs/traceability.md、
            TASKS.md
禁止修改目录：backend/app/**、backend/migrations/**、docs/spec/**、tools/docstate/**
交付分支：agent/local/opencode/merge-interlock/a1
背景：今天实际发生两次，不是理论风险。
      (1) `git merge` 在根工作区解完冲突后，`MERGE_HEAD` 凭空消失、工作区恢复
          干净 —— 被并发 git 操作静默 abort，我的冲突解决成果零提交丢失。
      (2) 再次尝试直接 `fatal: update_ref failed for ref 'ORIG_HEAD'`。
      根因：7 个 worktree 共享同一个 `.git`，但只有 `HEAD` 与 index 是per-worktree
      的，`refs/`、`ORIG_HEAD`、锁文件全是共享的，而根工作区按 AGENTS.md 是
      「协调者专用合并区」——**没有任何互斥保护**。dev-convention.md 的
      「靠自觉的」清单里已列「禁止在 main 上直接提交 —— 没有 hook，纯自觉」，
      合并这一环连自觉都没有。
完成标准：
  1. 合并互斥锁落在 git common dir（跨 worktree 共享），非per-worktree   ✅
  2. 用原子 `mkdir` 实现；已有锁时**明确报错并打印持有者与时间**，不静默等待 ✅
  3. 锁持有者进程已死且超过 TTL 时可判定为 stale 并接管                  ✅
  4. `scripts.sh merge <branch>` 受控入口：脏工作区拒绝、冲突不自动 abort、
     门禁红则不提交merge commit✅
  5. `status` 子命令可查当前锁状态                                     ✅
  6. `backend/tests/test_merge_guard.py` 覆盖并发拒绝、stale 接管、
     脏工作区拒绝、锁释放                                             ✅
  7. **修`Makefile` 的 `check` 不含 discipline**（与 `scripts.sh` 不等价） ✅
  8. `docstate` / `discipline` / `ruff` / `pytest` 全绿                 ✅
test_id：MVP-MERGE-LOCK-001、MVP-PROCESS-001
测试命令：./scripts.sh check（等价分步：docstate + discipline + ruff + pytest）
当前状态：done
commit：d5fb2fd（登记）+ 本卡收尾
完成时间：2026-10-05 21:20
验证结果：
  - docstate  : 90 项检查，0 项失败
  - discipline: 24 项检查，3 失败 —— **全部继承自 main**
    （`verify-mvp-04`孤儿 / `MVP-04-inject` 未登记 / 缺 `verify-mvp-04` 卡），
    本任务未引入任何新失败
  - ruff check : All checks passed!
  - ruff format: 41 files already formatted
  - pytest     : 125 passed（本任务前 111）
  - `test_merge_guard.py` **连跑 15 次 0 失败**（初版有~55% flake，见下）
  - 目录边界  : tools/mergeguard/**、backend/tests/test_merge_guard.py、
                scripts.sh、Makefile、docs/dev-convention.md、
                docs/traceability.md、TASKS.md

### 工具自身在开发中被抓出的 4 个缺陷（全部已修）

1. **锁的临界路径上 spawn `git rev-parse`**。初版 `git_common_dir()` 每次调用都
   起一个 git 子进程。并发 8 线程抢锁时随机 `CalledProcessError(0xC000013A)`
   （STATUS_CONTROL_C_EXIT），实测 **11/20 次失败**。已改为纯文件探测
   （读 `.git` 是目录还是文件），子进程只作兜底。**互斥原语的热路径上不该有
   任何子进程**——它必须能在 git 不可用时也能工作。
2. **线程异常静默吞掉**。并发测试第一版只统计 winners/losers，结果 8 个线程里
   有 1 个抛了非 `LockError` 的异常后**静默消失**，pytest 默认不会因线程抛异常
   而失败，于是断言「losers == 7」变成 6，看起来像业务 bug。已改为显式收集
   `errors` 并先断言它为空。教训：线程里的异常必须显式收集。
3. **`subprocess(text=True)` 在 Windows 上按 GBK 解码**。本仓库 commit message
   全是中文，第一次真合并 `git log` 就会抛 `UnicodeDecodeError`。已在
   `guard.py` / `cli.py` /测试 helper 全部显式指定
   `encoding="utf-8", errors="replace"`。
4. **`REPO_ROOT` 硬编码成脚本所在仓库**。合并的语义是「把你脚下这个仓库的 main
   合进去」，取脚本路径会在跨仓库调用时**静默合错地方**。已改为
   `git rev-parse --show-toplevel` 取 cwd 所属仓库。

前两个是单测抓到的，后两个是**CLI 端到端演练**抓到的 —— 演练脚本一度被我
判定为「环境问题」，但它暴露的是自己的真bug，所以最后把演练固化成了
`test_cli_*` 5 个用例。**只做单测会漏掉 3 和 4**：它们只在真跑 `cli.py` 时
才发生。

### 对「安静窗口」一词的更正

我在提出本任务时用了「等一个安静窗口」的说法。那是错的表述——它把「机制缺失」
包装成了「操作方案」。本任务就是那个机制：等待是运气，锁才是控制。
  - 本任务**不阻止**直接在根工作区跑裸 `git merge`。git 没有提供「禁止某命令」
    的机制，wrapper 只能提供受控入口与协作约定。它能保证的是：用受控入口时不会
    互相踩，且踩了会明确失败；裸跑仍然可能撞锁。这一条必须写进 dev-convention，
    否则会给人「已经安全了」的错觉。
  - 锁只保护合并，不保护 `git commit` 到 `refs/heads/main`。后者由分支策略
    （禁直接提交 main）约束，目前仍无机制强制。
  - 完成标准 7 是顺带发现的另一个洞：`make check` 只跑 docstate+lint+test，
    **不跑 discipline**，比 `./scripts.sh check` 弱。AGENTS.md 要求跑
    `scripts.sh check`，所以弱点未造成实际损失，但两者同名不同义是陷阱。
  - 本任务完成后仍需单独合并 `gate-baseline-repair`（9 commits，纯文档）。
    届时应改用 `./scripts.sh merge` 走受控入口，顺带验证本工具在真实场景下可用。
~~~
