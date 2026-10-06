### MVP-07：Review、Merge Gate 与集成分支（✅ done）

~~~text
任务编号：MVP-07
负责人 Harness：Qoder
开始时间：2026-10-06 00:10
依赖任务：MVP-06（已合入 main `e742882`，✅ done）、MVP-04（✅ 已验收）、
          MVP-05（✅ done）
工作区：.worktrees/qoder/
真相源引用：R10 §21（Review 与 Merge Gate：交叉断言、集成健康状态机、revert recovery、
            Merge Queue 背压）、§10（awaiting_review / approved / merging / changes_requested
            的单值迁移边）、§13（分支冻结与 Push Proxy）、§16（receipt 唯一键与精确 commit）、
            §28（不变量）、§26（MERGE-EXACT-001、MERGE-REVERT-001/002）
允许修改目录：backend/app/merge/**、backend/app/tasks/**、backend/app/api/**、
            backend/tests/**、docs/traceability.md
禁止修改目录：docs/spec/0.3-R10.md、tools/docstate/**、tools/discipline/**、
            backend/app/db/**、backend/app/events/**、backend/app/state/**、
            backend/app/plan/**、backend/app/claim/**、backend/app/lease/**、
            backend/app/git/**、backend/app/verify/**、docs/claims/ 下非本任务的文件
交付分支：agent/local/qoder/MVP-07/a1
完成标准：
  1. Reviewer Claim 拿到独立于开发 Lease 的 review Lease；同一 attempt 的重复 claim 不产生第二条
  2. 自审被拒：owner 不能审自己的 attempt，同 harness 也不能
  3. Merge Gate 做三条交叉断言：receipt.commit == review.reviewed_commit == 待合并分支 head、
     receipt.fencing_epoch == task.current_epoch、receipt.base_commit == task.base_commit；
     任一条不成立则拒绝，且**不写集成分支**
  4. 未经 Verify 或未经 Review 的代码进不了集成分支（有断言，不是靠调用方自觉）
  5. 合并失败区分三类：merge_conflict / merge_conflict_needs_owner / merge_post_commit_failure，
     且三条边各自的 next 与 R10 §10 单值一致（不出现「approved 或 changes_requested」）
  6. revert recovery 机械断言 tree(revert_commit) == tree(first_parent(failed_merge_commit))，
     断言失败即 escalated
test_id：MERGE-EXACT-001、MERGE-REVERT-001、MERGE-REVERT-002
测试命令：./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-06 01:05
commit：c41282a
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 42 项检查，0 项失败
  ruff        : All checks passed，61 files already formatted
  pytest      : 182 passed（MVP-07 新增 16）
  关键断言     : 三条交叉断言各有独立拒绝用例（无 receipt / 无 review / review 的 commit 不符 /
                 epoch 不再当前 / base 不符 / 分支 head 不符 / health 红）；
                 **每一次拒绝都同时断言集成分支 ref 未移动、且没有新增 merge intent**；
                 合并成功后断言 merge commit 的 ^1 / ^2 与内容；
                 revert 断言 tree(revert)==tree(merge^1)、revert 是 merge 的直接子提交、
                 merge..revert 只含 1 个提交
已知问题：
  - **在 MVP-08 的代码里发现一个真 bug**：`submit_result` 把 `tasks.current_attempt_id` 也清空了。
    R10 §13 只说提交后释放 **Lease**，attempt 仍是被验证/审查的那一个（`receipt.attempt_id`
    以它为准）。清掉它会让 Merge Gate 查不到当前 attempt。已改为只清 owner，保留 attempt。
    这条是 MVP-07 的门禁逼出来的 —— 上一层的测试覆盖不到它。
  - **`commit` 又是 SQL 保留字**：新写的 `review_claim` 里 `SELECT commit FROM
    verification_receipts` 报 `near "commit": syntax error`。这是本任务第二次踩，说明
    001 建表时给该列加引号这件事没有被任何东西提醒 —— 值得在 dev-convention 里写一句。
  - **receipt 不可变导致交叉断言只能从任务侧构造**：`verification_receipts` 是 append-only
    （有 UPDATE/DELETE 触发器），所以"receipt 与任务不符"的用例只能通过推进
    `tasks.current_epoch` / 改 `tasks.base_commit` 来造。这反而更真实（reclaim 推进 epoch
    正是让旧 receipt 失效的场景），但它意味着**没有任何测试能构造"被篡改的 receipt"** ——
    因为那在实现上不可能发生，这是好事，只是不要误以为测试覆盖了篡改场景。
  - review Lease 的**过期回收巡检器未实现**：Lease 到期后任务会卡在 awaiting_review，
    只能靠 `review_lease_expire` 的巡检器回收（属 MVP-04 的巡检器扩展）。当前只有
    submit 时的到期拒绝。
  - §21「相同 Provider 的两个 Reviewer 需额外人类确认」未实现：harnesses 表还没有
    provider 数据，无法判定同 provider；`reviews.reviewer_provider` 恒为 'unknown'。
  - Merge Queue 的容量与背压（§21：并发上限、最大队列长度、磁盘水位、hotfix 配额）在 MVP
    只实现「队列满则 queued」这一条，其余限流属于阶段 2；不静默跳过，写在这里。
  - 集成健康状态机的完整形态（merge_failure_detected / reverting / retesting / escalated
    四个中间态与各自出边）MVP 只实现到能跑通 revert recovery 的程度，其余状态留待 MVP-11
    的集成测试覆盖。
~~~
