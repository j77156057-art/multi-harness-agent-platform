### rework-loop：返工链 claim_for_revision 实现（🚧 doing）

~~~text
任务编号：rework-loop
负责人 Harness：Qoder
开始时间：2026-10-08 12:16
依赖任务：e2e-harness-drill（✅ f54420c；F1 发现来源与验收语境）
工作区：.worktrees/qoder/
真相源引用：R10 §10 L449「changes_requested | claim_for_revision | 原 owner 或
            新 owner 重新通过依赖、资源和能力检查 | claimed」；L523 边副作用
            「创建新 attempt、epoch、开发 Lease 和 worktree」；L670 「以下事件
            创建新 attempt：…changes_requested 后重新 Claim 并重新提交」；
            L451 resubmit 行（见下方语义裁决②）；§11 条件 UPDATE 家族；
            §16 verify receipt（审查前必须有 receipt）
范围声明：
  - **两处 R10 语义裁决（如实记录）**：
    ① **base 语义**：返工的新 attempt 与首领同口径——claim 时重新冻结
       `base_commit` = integration head（R10 L523 只说创建新 attempt/epoch/
       Lease/worktree，未提"继承失败 attempt 的头"；失败 commit 不进
       integration，从它续建会引入第二条基线）。失败 attempt 的产出仍可在
       事件流/read_context 里读到。
    ② **resubmit 边不落实现**：冻结表 L451 的 `changes_requested→
       awaiting_review`（guard「新 attempt、开发 Lease 有效且证据完整」）会
       **跳过 §16 的 verify receipt**——而 review 面要求 receipt 必须对
       **本 attempt** 的 commit 成立（reviewer 领取前的门禁）。返工的完成
       因此走标准链：`claimed→in_progress→submit_result→verifying→
       verify_passed→awaiting_review`（与首轮同链）。该行**保持未使用**并
       在此记录理由，不引入绕门禁的旁路。（若协调者将来要字面实现该边，
       需要随 R10-fix 重审 §16 关系——另立卡。）
  - **本切片三件**：
    ①`claim_for_revision` 路径（`app/claim/service.py`）：claim 请求命中
      `status=changes_requested` 时走新分支——attempt_no+1、epoch+1、新
      Lease；资源检查（专用资源/独占锁同一套 `_exclusive_resources`）与
      次数预算（`attempt_no < max_attempts OR budget_override_by`）同首领；
      **budget-guard 同口径**（预留检查 + held 行）；条件 UPDATE 以
      `status='changes_requested' AND current_attempt_id=<旧 attempt>` 做
      竞态护栏；事件 = `claim_for_revision`（payload 含 revised_from）；
      `task_claims` 行照写（R10 §11 每项留行）。
    ②**资源被占时的语义**：changes_requested 没有 defer 边（queued 的入口
      是 ready/queued 家族）——资源被占则**拒绝**该项（新拒绝码
      `claim.resource_unavailable`），任务留在 changes_requested（SLA 巡检
      兜底升级）。
    ③**可发现性**：`GET /adapter/list_ready_tasks` 纳入 changes_requested
      （它现在可领了）——否则 harness 的拉模式看不见返工任务。
  - **显式出范围**（记录不静默）：`REASSIGN` 边（人类指派新 owner，需 human
    面/调度指派语义，本仓无该操作面——另案）；resubmit 字面边（见裁决②）；
    review 侧 `request_changes` 产生的 changes_requested 的差异化处理
    （同一 claim_for_revision 路径适用，无需区分——但"review_round 与返工
    次数"的联动不做）。
  - **零迁移**（表与事件名全部现成）。
允许修改目录：backend/app/claim/**、backend/app/api/routes.py（仅
            list_ready_tasks 过滤）、backend/tests/**、docs/reports/
            e2e-real-harness-drill.md（F1 行加"已实现"指针）、
            docs/traceability.md、TASKS.md、docs/tasks/notes/rework-loop.md、
            docs/claims/qoder--rework-loop.md
禁止修改目录：backend/app/state/**（迁移表已含两边，不动）、
            backend/migrations/**（零迁移）、docs/spec/**、tools/**
交付分支：agent/local/qoder/rework-loop/a1
完成标准：
  1. **全环回归**：verify 失败 → changes_requested →（新 claim_request_id）
     claim → `claim_for_revision` 事件 → claimed/新 attempt(a2)/新 epoch/新
     Lease → start（新 worktree）→ 提交 → verify 通过 → review → merge 全链
     在集成测试里跑通；旧 attempt 行保持历史可读
  2. **幂等**：同 claim_request_id 重放 → 返回同一 a2（不烧第三次 attempt）；
     并发两请求恰一成功（条件 UPDATE rowcount 护栏）
  3. **拒绝矩阵**：次数预算耗尽（无 override）→ `claim.attempt_budget_exhausted`；
     override 后放行；资源被占 → `claim.resource_unavailable` 且**零状态写入**
     （无事件/无 attempt/无 task_claims claimed 行）；未领取时无预算任务
     行为不变（回归）
  4. **budget-guard 联动**：budget>0 的任务返工领取 → held 行；预算被占 →
     拒绝 + 事故口径不变
  5. **list_ready_tasks 可见**：ready 与 changes_requested 一并列出（API 断言）
  6. **变异验证**：注掉 changes_requested 分支 → 全环回归必红
  7. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增
     `CORE-REWORK-001`；报告 F1 行更新为"已实现（rework-loop）"
test_id：CORE-REWORK-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
