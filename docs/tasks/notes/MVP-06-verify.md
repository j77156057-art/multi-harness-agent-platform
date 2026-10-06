### MVP-06-verify：MVP-06 独立验收（✅ done，通过附遗留清单 6 项）

~~~text
任务编号：MVP-06-verify
负责人 Harness：TRAE
开始时间：2026-10-06 00:45
依赖任务：MVP-06（代码已合 main；验收基线取最新 main 513de49）
工作区：.worktrees/trae/
真相源引用：R10 §16（完成证据和 Verify：receipt 字段/append-only/精确 commit
            全新 detached worktree/baseline differential/verify_jobs/环境隔离）、
            §10（verifying 的出入口、differential_not_applicable 正反两边）、
            §28（failure_reason 不变量）、§26（VERIFY-DIFF-001/002）、
            docs/spec/mvp-task-checklist.md MVP-06（交付 8 条/完成标准 2 条/
            MVP 简化口径）、docs/prep/MVP-08/error-codes.md（verify.* 与
            review.requires_verify_receipt / merge.receipt_missing）、
            docs/adr/ADR-DEV-002-single-account-acl.md
允许修改目录：TASKS.md、docs/claims/37-trae--MVP-06-verify.md（验收者不改被验代码）
禁止修改目录：docs/spec/**、backend/**、tools/**、docs/adr/**、docs/prep/**、
            docs/claims/ 下非本任务的文件
交付分支：agent/local/trae/MVP-06-verify/a1
背景：MVP-06 由 Qoder 完成并随 main 170e56e/513de49 合入（先合后验，第四次）。
      claims/35-qoder--MVP-06 已 done，本任务补机械核查，决定 MVP-07 能否以它
      为基线。同时复核 MVP-05 验收遗留 7（alternates CRLF）在 verify overlay
      路径是否同样存在，以及 MVP-08 遗留第 4 项的 verify 半边是否真关闭。
核查计划（逐条双证据：代码行 + 机械断言，不采信卡片自陈）：
  VERIFY-DIFF-001（baseline differential 语义）：
    1. verify 在精确 head_commit 的全新 detached overlay 执行
       （--separate-git-dir + fetch 精确 commit + checkout --detach +
       git status 为空断言），base/head 各一个 overlay；
    2. head 必须 0 退出才进 awaiting_review；head 非 0 →
       changes_requested 且事件流无 verify_passed（结构性，不靠自觉）；
    3. MVP 简化：baseline 一侧只记录 baseline_exit_code 不强制；
       flaky_registry_ids 恒 []、diff_intersection 恒 None 且明示阶段 2；
    4. failure_reason 区分 code_failed / env_failed / timeout
       （env_failed 自环留 verifying、timeout 进 escalated）；aborted
       产生路径是否存在需核实（作者卡称崩溃重排未实现）；
    5. new_test_files 取自 base..head diff 的 A 类测试文件；
    6. differential:not_applicable 正反两边都实现，理由 + 静态证据必填、
       epoch 校验；
    7. 命令根来自人类维护 allowlist（argv 非 shell），越界 profile 拒绝加载。
  VERIFY-RECEIPT-001（receipt 与待合并 commit 不一致时 Merge Gate 拒绝）：
    1. verification_receipts append-only（001 触发器）+ UNIQUE 四元组；
       触发器是否有机械断言（UPDATE/DELETE 被拒）需核实；
    2. 同 attempt 重复 verify 返回首次 receipt 且不增行；不同 commit 独立
       receipt；merge_is_verifiable 按精确 commit 查 passing（failure_reason
       IS NULL），base/错误 commit 查不到即拒；
    3. receipt §16 字段齐全（receipt_json），权威副本在库、仓库侧三件
       （completion.md / test-results.md / verification/receipt.json）只是
       带 hash 的只读视图并落 artifacts；
    4. 迁移 005 只 ADD COLUMN 不改约束；旧库升级可应用；
    5. overlay 成功回收、失败保留；
    6. Review 侧防护口径：submit_review 不直接核对 receipt（作者称留
       MVP-07），但 awaiting_review 只能由 verify_passed 进入——核实这条
       结构保证与测试。
  契约对齐：不新增 HTTP 动词（§7 无 verify）的口径是否成立；
  ADR-DEV-002：环境隔离（低权限/网络白名单/canonical 拒写）随单账户延后的
  声明是否如实。
test_id：VERIFY-DIFF-001、VERIFY-RECEIPT-001
测试命令：cd backend; ..\.venv\Scripts\python.exe -m pytest tests/test_verify.py -v；
          全量 ..\.venv\Scripts\python.exe -m pytest -q
当前状态：✅ done（通过附遗留清单 6 项，无 P1）
完成时间：2026-10-06 00:47
commit：0eed6c9（验收证据提交；本行为随附回填提交）
验证结果：
  结论：✅ 通过。VERIFY-DIFF-001/002 逐条双证据全部成立，证据套件
  tests/test_verify.py 17/17 退出 0；另以一次性探针补得两条套件外证据
  （append-only 触发器 UPDATE/DELETE 双 ABORT；overlay alternates CRLF
  与精确 checkout 实证）。完整逐条结论、门禁数字与 6 项遗留已回填于
  本文件 MVP-06 卡末「验收意见（TRAE，2026-10-06 00:47，基线
  513de49）」，此处只摘门禁：
  - docstate 90/0、discipline 42/0、pytest tools 27 passed；
  - backend：test_verify.py 17/17；除 tests/test_merge_guard.py 外
    全量 152 项退出 0（merge_guard 本机硬崩见 MVP-05 卡遗留 5）；
  - ruff check 全过、ruff format 56 files already formatted。
  跨项确认：MVP-08 遗留第 4 项 verify 半边真关闭（真 receipt 链）；
  MVP-05 遗留 7 扩到 overlay.py（同源 CRLF）。
~~~
