### merge-interlock-review：OpenCode merge-interlock 分支独立评审（✅ done，退回作者修 1-4 后再合）

~~~text
任务编号：merge-interlock-review
负责人 Harness：TRAE
开始时间：2026-10-06 02:34
依赖任务：无（评审对象为在途基础设施分支；与 MVP-05-verify 遗留 5、
          MVP-06-verify 遗留观察相关）
工作区：.worktrees/trae/
真相源引用：docs/dev-convention.md H-7（工作区隔离与共享分支引用风险）；
            TASKS.md「第 4 次事故」记录（abort 他人合并现场的事故形态）
被审分支：agent/local/opencode/merge-interlock/a1（tip 9aaab42，
          7 提交，merge-base 170e56e；作者卡见本文件 L577，分支内无 claim）
允许修改目录：TASKS.md（仅本任务卡）、docs/claims/41-trae--merge-interlock-review.md
禁止修改目录：tools/**、backend/**、docs/spec/**、其他 harness 的 claim
交付分支：agent/local/trae/merge-interlock-review/a1
test_id：无编号 test_id（基础设施评审；机械证据为分支自带测试实跑 +
         git merge-tree 冲突模拟 + 双路独立代码复核）
测试命令：分支 tip 临时 worktree 内 .venv python -m pytest
          backend/tests/test_merge_guard.py（分组实跑）；
          tools/docstate/docstate.py；tools/discipline/discipline.py
当前状态：✅ done（评审结论：暂不合并；问题 1-4 修复并补测试后复审）
完成时间：2026-10-06 02:34
commit：611707a（结论提交；本行为随附回填提交）

背景与评审计划：
  该分支三件事：① git merge 改 --no-commit，门禁跑在 merge commit 落地前；
  ② 入口预检他人裸跑 git 的现场（index.lock / MERGE_HEAD / ORIG_HEAD.lock）；
  ③ merge 撞瞬时 ref 锁时 3/6/9s 有界退避重试。评审方法：不采信提交信息，
  全量 diff 逐行读 + 临时 worktree 机械实跑 + 两个独立子 agent 对全部候选
  问题双路复核。

验证结果：（2026-10-06 02:34，基线 main 8934a75；不采信提交信息，
逐行读 diff + 临时 worktree 实跑 + 两个独立子 agent 双路复核，6 条 2/2 确认）

  总评：🔍 方向正确、核心机制本机实证可用，但建议问题 1-4 修复并补测试后
  再合；问题 5 至少先做本机会话隔离，问题 6 顺手。不直接改作者分支。

  作者意图（3 个卡后新提交 c6fdc26/395fc5c/9aaab42）：
  ① merge 改 --no-commit，门禁跑在 merge commit 落地之前（修首版
     「门禁红但 commit 已落地」的假承诺，作者已在测试 docstring 记录）；
  ② 入口预检他人裸跑 git 现场（index.lock/MERGE_HEAD/ORIG_HEAD.lock）；
  ③ merge 撞瞬时 ref 锁时 3/6/9s 有界退避重试。

  机械证据：
  - git merge-tree --write-tree 模拟合入当前 main：无文本冲突；
  - 分支对 tools/docstate、tools/discipline、docs/prep/MVP-08 两脚本的
    558 行改动逐行核实全为 ruff 重排（含 -w 比对），无任何语义变更；
    main 自 merge-base 170e56e 后未改过这两个门禁脚本，检查数不变；
  - 临时 detached worktree（分支 tip 9aaab42 + 主 .venv 3.13.14）分组实跑：
    test_cli_rejects_dirty_worktree / refuses_when_not_on_main /
    detects_foreign_git_activity / keeps_conflict_and_does_not_abort
    四个 CLI 用例全过——新的预检、--no-commit 次序、冲突保留均真工作；
  - 同环境整会话跑 test_merge_guard.py 仍 100% 硬崩（1~3 dot 后 reader
    线程/进程树死亡，-s 亦然，单跑 linked-worktree 用例零输出崩）；
  - 评审当日评审者本人两次撞上瞬时 ref 锁（一次 commit 的 couldn't set
    refs、一次 worktree add 的 update_ref HEAD），痛点真实高频；
  - 第二路复核在沙箱动态实证：ORIG_HEAD 先于 MERGE_HEAD 落盘（ref 锁
    失败时现场无「我的合并」标记）、无条件 abort 可复刻「他人 MERGE_HEAD
    凭空消失」事故、refs/heads/main.lock 可独立存在并令 commit 失败。

  问题清单（编号即退回作者的修复单；行号为分支 tip 版）：
  1. [高] 门禁失败恢复指引确定性死路：cli.py 门禁红 return 5 时留下本工具
     自己的 MERGE_HEAD，提示「修好门禁后重跑本命令」，但入口预检见
     MERGE_HEAD 一律 exit 7（且误报「另一个 agent 正在跑 git」、诱导删
     锁，删后又被脏区检查挡 exit 2）。--skip-gate 路径重跑同样被挡。
     修法：预检识别本工具遗留的待提交合并（锁空闲）→ 直接续跑门禁并
     提交；或提示改为可执行的手工 commit / abort 二选一。
  2. [中] 最终 git commit（L249）无重试：有界退避只包 merge，不包更新
     refs/heads/main 的 commit；撞锁 exit 6 后与问题 1 叠加，恢复全靠
     人肉。修法：同一退避策略包 commit（MERGE_HEAD 仍在，直接重试
     commit，无需重新 merge/abort）。
  3. [中] 两条核心新行为零测试：原成功路径用例
     test_cli_merges_and_releases_lock 被删且无替代——「门禁全过→
     commit→HEAD 前进」唯一生产成功路径零覆盖（现有临时仓库夹具没有
     门禁脚本，结构上够不到该分支）；重试/退避/abort 27 行零断言。
     问题 1、4 正是借此漏入。修法：可注入门禁命令的最小夹具补成功路径；
     「首试有 ORIG_HEAD.lock、再试消失」的竞态夹具补重试与 abort。
  4. [中] 重试前无条件 git merge --abort（L194）：3/6/9s 退避窗口内若
     另一 agent 裸跑发起合并，会被静默 abort，正是作者卡 L594-596 与
     TASKS「第 4 次事故」记录的事故形态，与本文件冲突路径自己写下的
     「绝不要 abort 别人合并」相矛盾。预检是入口一次性快照，关不住该
     窗口。修法：abort 前验 MERGE_HEAD 存在且指向本进程预期 merge tip。
  5. [中] 本机硬崩未修：分支无 xfail/skip/独立进程组隔离，且比 main 多
     4 个重子进程用例；合入后 scripts.sh check 的 pytest 在本机仍必崩，
     全量门禁只能继续 --ignore（作者卡自陈其机器 15 连跑全绿，属环境
     差异）。修法：合并前给该文件做会话隔离（独立 pytest 调用/新进程组）
     或对本机 xfail，并在卡上写明环境边界。
  6. [低] 预检信号不全：漏 refs/heads/main.lock、packed-refs.lock、
     HEAD.lock——裸跑 commit/fetch 可只持 ref 侧锁，预检漏放，与问题 2
     叠加。修法：加入检测元组。
  附带观察（既有缺陷，不属本分支，路过不修）：guard 判 stale 后
     release→mkdir 非原子，第三方抢先时抛裸 FileExistsError（cli 只
     except LockError），用户看到原始 traceback/exit 1。

  流程观察（另案）：作者任务行 L43 已标 ✅ done 但分支至今未合 main、
  分支内无 docs/claims 文件；现有 discipline 的 merge/branch-visibility
  两组都照不到「未合入但标 done 且无 claim」的形态，属纪律检查盲区。

  评审后门禁（本分支仅 docs 变更）：docstate 90/0、discipline 42/0。
~~~
