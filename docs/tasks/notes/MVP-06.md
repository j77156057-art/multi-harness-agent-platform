### MVP-06：Verify Worker 与完成证据（✅ done）

~~~text
任务编号：MVP-06
负责人 Harness：Qoder
开始时间：2026-10-05 22:26
依赖任务：MVP-05（已合入 main `e68a8d5`，✅ done）、MVP-04（✅ 已验收，提供 attempt/fencing）
工作区：.worktrees/qoder/
真相源引用：R10 §16（完成证据和 Verify：baseline differential、receipt 字段、verify 环境、
            verify_jobs、Verify Worker Lease 与重试）、§10（verifying 的出入口，含
            differential_not_applicable 的正反两条边）、§13（Lease/epoch）、
            §15（事件与 seq）、§28（failure_reason 不变量）、
            §26（VERIFY-DIFF-001、VERIFY-RECEIPT-001）
允许修改目录：backend/app/verify/**、backend/app/tasks/**、backend/app/api/**、
            backend/tests/**、backend/migrations/005_verify_fields.sql、
            docs/traceability.md
            ── 跨任务改动（已在此登记，仿 R10-fix-11 加 002_*.sql 的先例）：
               backend/migrations/005_verify_fields.sql —— 补 verification_receipts 与
               verify_jobs 在 R10 §16 里要求、而 001_initial.sql 没有的列。
               只做 ADD COLUMN，不动既有列、不加表。
禁止修改目录：docs/spec/0.3-R10.md、tools/docstate/**、tools/discipline/**、
            backend/app/db/**、backend/app/events/**、backend/app/state/**、
            backend/app/plan/**、backend/app/claim/**、backend/app/lease/**、
            backend/app/git/**、docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/MVP-06/a1
完成标准：
  1. Verify 在**精确 commit** 的全新 detached worktree 上执行，base 与 head 各一个
  2. receipt append-only，唯一键 (task_id, attempt_id, commit, verify_profile)；
     同 attempt 重复 verify 返回首次 receipt；不同 commit 生成独立 receipt
  3. receipt 记录 R10 §16 列出的字段，且 failure_reason 严格区分
     code_failed / env_failed / timeout / aborted
  4. differential: not_applicable 的正向（verifying → awaiting_human）与反向
     （awaiting_human → verifying）两条边都实现
  5. 测试未通过不能进入 Review：head 退出码非 0 时任务进 changes_requested，
     且不存在 awaiting_review 的可达路径
  6. Merge Gate 判据可断言：按待合并 commit 精确查 receipt，不一致即查不到
test_id：VERIFY-DIFF-001、VERIFY-RECEIPT-001
测试命令：./scripts.sh check
当前状态：done（作者完成并待合入；外部验收待认，参照 MVP-04-verify 形式）
完成时间：2026-10-05 22:55
commit：1ab608d
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 39 项检查，0 项失败
  ruff        : All checks passed，55 files already formatted
  pytest      : 149 passed（MVP-06 新增 17）
  关键断言     : head 通过才进 awaiting_review；head 失败进 changes_requested 且事件流里
                没有 verify_passed；receipt 按精确 commit 查，base commit 查不到即判定不可合并；
                 重复 verify 返回首次 receipt 且不新增行；超时记为 timeout（进 escalated）
                 而不是 code_failed；env_failed 是自环、任务留在 verifying；
                 differential_not_applicable 的正反两条边都跑通
  真实链路     : 测试夹具走的是 claim→start(建 worktree)→commit→publish→submit→verify，
                 不手搓 verifying 行 —— 否则「diff 的 base 是不是冻结的那个」根本没被验到
已知问题：
  - **需要一次迁移**：`verification_receipts` 只有 11 列、`verify_jobs` 缺 7 列，而 R10 §16
    列出的 receipt 字段约 19 项。已加 `005_verify_fields.sql`（只 ADD COLUMN，不改既有列、
    不加表），并在本卡允许目录里声明为跨任务改动。**没有**把 receipt 塞进 artifacts 绕过：
    §16 要求权威副本在调度器数据库里，而 artifacts 只存 uri+hash，那样仓库侧的
    verification/receipt.json 会与它地位相同，权威副本就不存在了。
  - **`commit` 是 SQL 保留字**：001 建表时写作 `\"commit\"`，所以任何引用该列的语句都必须
    带引号，否则 `near "commit": syntax error`。本次在 receipt 与 verify_job 的读写上各踩了
    一次，由测试抓到。
  - **发布路由原先从 settings 取 canonical 仓库**，忽略注入，导致测试的 alternates 与路由
    期望的仓库不是同一个（`publish.alternates_invalid`）。已改为 `app.state.canonical_repo`，
    与 database 同样注入。
  - 跨任务改动（已在此登记）：`tests/test_database_events.py` 里
    「`applied == 1`」改为「断言 003 已被应用」。该断言硬编码了「待应用迁移恰为 1」，
    任何新迁移都会打破它 —— 被本次的 004 打破时，被测行为其实仍然正确。
  - **R10 §7 同样没有 verify 动词**（与 MVP-05 的 `start` 同类）：verify 是 Verify Worker
    的职责，不是 Harness 调用的接口，所以本任务**不新增 HTTP 路由**，沿用 MVP-08 立下的
    「路径逐字对齐 §7」。
  - **R10 §16 说 Verify 账户「尝试写 .git/config、hooks、refs、objects 必须被 ACL 拒绝」，
    单账户下无 ACL 可依**。overlay 仍按新 gitdir + alternates 构造（结构上隔离），
    但强制隔离随 ADR-DEV-002 一起延后。
  - **Verify Worker 崩溃/重排路径未实现**（§16 的 intent 对账、新 job_id、新 overlay）。
    当前只有单次执行与失败保留 overlay；对账依赖 MVP-04 的 intent 巡检，尚未接入。
  - MVP 简化（清单要求写明，不得静默跳过）：baseline differential 的「base 必须失败」
    一侧在 MVP 不强制，只记录 baseline_exit_code；「baseline 也失败时的 flaky 放行」
    整体标为阶段 2 交付，因此 receipt 的 flaky_registry_ids 恒为空、
    diff_intersection 恒为 None（R10 §21 规定该字段缺失即阻断合并，MVP 下正好是
    「flaky 放行不可用」，行为与未实现一致而不是假装实现）。
  - Verify 账户隔离（§16 的「低权限隔离 / 网络白名单 / canonical 对 Verify 账户拒绝写入」）
    随 ADR-DEV-002 的单账户降级一起延后；overlay 本身按新 gitdir + alternates 构造。

验收意见（TRAE，2026-10-06 00:47，基线 513de49；不采信卡片自陈，逐条找代码行+机械断言）：
  总评：✅ 通过（附遗留清单 6 项，无 P1）。两条 test_id 的不变量均取得
  「代码行 + 机械断言」双证据，证据套件 17/17 实跑退出 0；作者卡 6 条
  已知问题逐条核实属实。

  VERIFY-DIFF-001（baseline differential 语义）✅：
  - 精确 commit 全新 detached overlay：app/verify/overlay.py create 用
    init --separate-git-dir + fetch <精确 sha> + checkout --detach
    FETCH_HEAD，checkout 后 is_clean（git status --porcelain 为空）否则
    回收并抛 OverlayError；base/head 各一个 overlay（worker._execute，
    base 用同一 job_id 的 -base 后缀并在退出时回收）。验收探针实测
    head_commit == 请求 commit、is_clean == True；
  - head 必须 0 才进 Review：worker 按 head 退出码定 failure_reason，
    状态机里 verify_passed 是 verifying→awaiting_review 的唯一边
    （app/state/machine.py）；test_failing_head_cannot_reach_review
    断言 changes_requested + 事件流无 verify_passed +
    merge_is_verifiable 为 False——结构性保证而非调用方自觉；
  - MVP 简化单侧：required 模式跑 base 但只记录 baseline_exit_code、
    不强制其失败（通过用例实测 baseline=1 仍放行）；receipt 里
    flaky_registry_ids 恒 []、diff_intersection 恒 None 并附阶段 2
    注释，符合 checklist「记录 + 标注、不得静默跳过」口径；
  - failure_reason 三分类有负向测试：timeout→escalated、
    env_failed→verifying 自环、code_failed→changes_requested；
    new_test_files 取自 git diff --name-status base..head 的 A 类；
  - differential:not_applicable 正反两边（tasks/service.py +
    machine）：理由与静态证据必填、epoch 陈旧拒绝、human 恢复回
    verifying，4 个用例钉死；
  - 命令根人类 allowlist：profile.py 校验 command[0] ∈
    command_roots、argv 元组非 shell，策略文件为仓库根
    verify-profiles.json；越界根 VerifyPolicyError 有用例。

  VERIFY-RECEIPT-001（receipt 与待合并 commit 不一致即拒）✅：
  - append-only：001_initial.sql 的 receipt_no_update / no_delete
    触发器 + UNIQUE(task_id, attempt_id, commit, verify_profile)；
    worker 全程只 INSERT。验收探针实测 UPDATE/DELETE 均
    "verification_receipts is append-only" ABORT 且行不变
    （仓库测试套件本身无此断言，见遗留 1）；
  - 幂等与精确匹配：四元组查重 + _replay 返回首次 receipt 不增行；
    merge_is_verifiable 按精确 commit 查 failure_reason IS NULL 的
    receipt，base commit/全零 sha 查不到即拒（2 个用例）；
  - §16 字段：receipt_json 覆盖 commit/base/两个退出码/new_test_files/
    differential_mode/diff_hash/verify_env_hash/toolchain/fencing_epoch/
    failure_reason/log_hash/flaky/diff_intersection 等（用例逐字段断言）；
    权威副本在库，completion.md / test-results.md /
    verification/receipt.json 只是落 artifacts(uri+hash) 的只读视图；
  - 迁移 005 只 ADD COLUMN（receipt 5 列、verify_jobs 7 列），
    未改约束未加表，旧库升级路径随全量测试通过；
  - overlay 成功即回收（目录断言为空）、失败保留现场（2 个用例）。

  跨项核实：
  - MVP-08 遗留第 4 项的 verify 半边已真关闭：tests/api_support.py 的
    advance_to_awaiting_review 走真 commit → HTTP publish → HTTP
    submit_result → 真 VerifyWorker + 真 receipt，无伪造 receipt；
    start_task 半边仍由调度器驱动（§7 无动词，MVP-05 卡已裁定）；
  - MVP-05 验收遗留 7 范围扩大：app/verify/overlay.py 写 alternates
    与 worktree.py 同源文本写法，验收探针实测尾字节 0D 0A，
    修复时两处一并改；
  - 无新增 HTTP 动词（routes.py 15 条路由无 verify/start），与
    「路径逐字对齐 §7」一致；ADR-DEV-002 单账户延后声明如实。

  门禁实测（2026-10-06 00:47，513de49 worktree，Python 3.13.3 重建 venv）：
  - docstate 90/0；discipline 42/0；pytest tools 27 passed；
  - backend：证据套件 tests/test_verify.py 17/17 退出 0（真 git）；
    除 tests/test_merge_guard.py 外全量 152 项退出 0（一次通过，
    未复现 upload-pack 竞态；merge_guard 本机硬崩详见 MVP-05 卡遗留 5）；
  - ruff check All checks passed、ruff format 56 files already formatted。

  遗留/观察（不阻塞本验收）：
  1. P2 append-only 触发器无仓库内测试钉死：触发器经本次验收探针实测
     有效（UPDATE/DELETE 双 ABORT），但 test_verify.py 等套件无一条
     断言它；003_global_seq.sql 曾重建 task_events 并重建触发器，
     receipt 表未来若重建，触发器丢失不会被任何测试发现。建议 MVP-07
     接入 merge.receipt_missing 时补 UPDATE/DELETE 拒绝用例。
  2. P2 aborted 分类无产生路径：checklist 交付要求 failure_reason 区分
     code_failed/env_failed/timeout/aborted 四类，STATUS_ABORTED 常量
     存在但 worker 不会产出（Verify Worker 崩溃/原地重排未实现，作者卡
     已自陈）。阶段 2 worker 池任务必须补齐 aborted 路径与用例，建议在
     checklist/traceability 显式挂账，防止被误读为四分类已交付。
  3. P3 Review 不核对 reviewed_commit 与 receipt 的精确匹配：
     submit_review docstring 明示留 MVP-07，当前仅靠「awaiting_review
     只能由 verify_passed 进入」结构兜底；Merge Gate 的精确 commit
     核对（merge_is_verifiable 已备好待接）是 MVP-07 必须落地的一项。
  4. P3 alternates CRLF：overlay.py 与 worktree.py 两处文本写法，
     git 当前容忍（探针实证 objects? 警告路径见 MVP-05 卡遗留 7），
     阶段 2 严格传输前改二进制写入。
  5. P3 verify_jobs 池字段闲置：created_seq/updated_seq 写 NULL、
     worker_id 硬编码 "verify-worker-1"，lease_until/boot_id/
     resource_requirements/pid 未使用；§16 的 worker 注册/独立 Lease/
     排队 SLA/concurrent_verify=3/崩溃原地重排均未实现（重排作者卡已
     自陈，其余 checklist 交付未列，登记为阶段 2 观察项）。
  6. P3 预置策略瑕疵：verify-profiles.json 的 lint-only profile 命令
     为 python -m compileall -q src，仓库内无 src/ 目录（后端在
     backend/app），该 profile 真跑会失败；人类维护文件，不阻塞，
     下次改策略时修正路径。
~~~
