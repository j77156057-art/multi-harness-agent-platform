### sweeper-drift：漂移巡检（收官项）（✅ done）

~~~text
任务编号：sweeper-drift
负责人 Harness：Qoder
开始时间：2026-10-07 08:10
依赖任务：sweeper-workers（✅ 已合入 2b1beca）；设计 §6 第 5 项（巡检器家族收官）
工作区：.worktrees/qoder/
真相源引用：docs/proposals/sweeper-family.md §2.10（含 O3）；R10 §10 守卫列
            （plan_hash_changed/impl_hash_changed 七态同源：claimed/in_progress/
            changes_requested/awaiting_review/verifying/approved/merging）
O3 裁定（协调者 2026-10-07，按建议）：
            - plan_hash_changed = 任务绑定 plan_snapshot_id 的 snapshot_hash ≠
              plans.plan_hash（plans 行当前值）
            - impl_hash_changed = 任务 base_commit 不再是 integration HEAD 的祖先
              （正常前进保持祖先、不误报；revert/失效才触发——与 §21 revert 影响
              链的语义对齐）
            - 两者**只标记**（进 plan_drift/impl_drift 态等待人类裁决），不自动动作
允许修改目录：backend/app/scheduler/**、backend/tests/**、TASKS.md、
            docs/tasks/notes/**（本卡）、docs/claims/62-*.md
禁止修改目录：backend/app/plan/**、backend/app/git/**、backend/app/claim/**、
            backend/app/api/**、docs/spec/**、tools/**、docs/claims/ 下他人文件
交付分支：agent/local/qoder/sweeper-drift/a1
完成标准：
  1. runner 支持 repo 注入：Sweep 签名统一加可选 `repo=None` 第三参；
     run_all_sweeps(database, *, now=None, repo=None)；`__main__` 传
     CanonicalRepo.from_settings()（缺失时不崩，impl 检查以
     detail 注记 skipped-no-repo 声明）
  2. `sweep_drift`：七态逐任务双检查——plan（DB 内可判定）与 impl（git 祖先判定，
     `merge-base --is-ancestor`）；命中置 drift 态 + 事件；控制组不误报
  3. 状态覆盖套件同步：plan_drift/impl_drift 移出 WAIVERS_UNREACHABLE
     （该集合清空并重钉），进 RECOVERY 并附"出口可达"测试（drift 态经既有
     lease 巡检出口 → stale，证明新状态不是死胡同）
  4. 测试：plan 分歧触发/一致不动；impl revert 触发/正常前进不动；
     drift→stale 出口；repo 缺失跳过的声明；幂等
  5. PYTHONUTF8=1 ./scripts.sh check 全绿
test_id：SCHED-SWEEP-005
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 09:2x
commit：`41ab35d`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 314 passed（本任务净增 7 项：test_sweeper_drift.py），exit 0
  关键断言     : plan 分歧触发/一致不动；impl 在 integration 被改写为非祖先时触发、
                 正常前进不触发（对照组）；drift 态经既有 lease 巡检出口→stale
                 （不是死胡同）；repo 缺失时 impl 检查声明 undecided 不猜测；幂等。
  覆盖收官     : WAIVERS_UNREACHABLE 清空并重钉；21 态豁免 7 → 3（全部人类面出口）。
已知问题：
  - **夹具与真实流程的不一致被新探测器抓出并已修**：claim_support.seed_task 曾在
    plans.plan_hash 与 plan_snapshots.snapshot_hash 写两个不同假哈希，而真实
    create_plan 两处同哈希——夹具已镜像真实不变量。
  - plan 探测守着"修订流落地"的那天（现行生产者不动 plans.plan_hash）；impl 探测
    即 §21 revert 影响链的读侧信号，写侧（下游闭包）仍属 MERGE-REVERT-002 缺口。
~~~
