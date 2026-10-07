### human-ops：§18 人类决策面（首个切片）（🚧 doing）

~~~text
任务编号：human-ops
负责人 Harness：Qoder
开始时间：2026-10-07 10:10
依赖任务：sweeper-drift（✅ 已合入 1973238）；巡检器家族收官后剩余 3 豁免的收口
工作区：.worktrees/qoder/
真相源引用：R10 §18（task_decisions、人类等待规则、租约暂停/重发）、§10 守卫列
            （human.question / human.resume / human.reopen(_no_owner) /
            human.release / human.adjudicate / human.cancel / human_wait_timeout /
            escalation_reminder / reopen / cancel / queue_cancel）、§7（ask_human 动词）
范围声明：
  - **本切片**：六个操作（ask_human / resume / reopen / release / adjudicate /
    cancel）+ task_decisions 台账 + 两类提醒巡检的退避实现 + /human/* 路由 +
    /adapter/ask_human（补第 14 个 §7 路由动词）+ 操作员 CLI 的 human-* 命令。
  - **显式出范围**（记录不静默）：§18 pinned 决策与 human/harness token 的 403 语义
    （无认证层，归未来认证任务，身份=显式参数如 capabilities() 先例）；"决策进入
    verify receipt 输入清单"（receipt 结构改动，归 verify 线）；cancel 的
    downstream_policy 传播（MERGE-REVERT-002 线）；worktree 回收（需 archive_root
    配置，操作员侧）。
  - **既有资产**：`task_decisions` 表 001 已建齐 §18 列，零迁移。
允许修改目录：backend/app/human/**（新增）、backend/app/api/**、
            backend/app/adapter/http.py（ask_human 从 UnsupportedVerb 转正）、
            backend/app/scheduler/**（两个提醒巡检）、backend/app/cli/main.py、
            backend/tests/**、docs/traceability.md、TASKS.md、docs/tasks/notes/**
禁止修改目录：backend/app/verify/**、backend/app/merge/**、
            backend/migrations/**（本切片零迁移）、docs/spec/**、tools/**、
            docs/claims/ 下他人文件
交付分支：agent/local/qoder/human-ops/a1
完成标准：
  1. HumanOpsService：六个操作，逐条按 §10 守卫实现（含状态允许集与事件选择矩阵）；
     ask_human/resume 的租约语义按 §18：进入 awaiting_human 释放开发租约（暂停计时），
     resume 重签（同 attempt、同 epoch，不新建 attempt）；adjudicate/reopen 的
     drift 双态与 owner 有无分支矩阵写入 docstring 与卡。
  2. task_decisions 台账：ask_human 与 adjudicate 开决策行（question/options/scope）；
     resume/reopen 落 answer + decided_seq（actor=decided_by）；未决行保持 NULL。
  3. 路由：POST /human/{resume,reopen,release,adjudicate,cancel}（decided_by 显式）；
     POST /adapter/ask_human（§7 第 14 个动词转正）；http.py 的 UnsupportedVerb 清单
     与 test_adapter 的"恰为四个"断言同步改为三个；traceability MVP-ADAPTER-001
     行缺口同步。
  4. 提醒巡检 ×2（runner 扩为十三员）：human_wait_timeout 与 escalation_reminder
     自环；**退避用事件计数实现**（第 N 次间隔 = min(base×mult^N, cap)，无需新表），
     next_reminder_at 落库可观察；只提醒不自动放行（§13）。
  5. 覆盖套件：abandoned 与 escalated 移出 WAIVERS_NO_EXIT → RECOVERY（附出口测试）；
     豁免钉死为仅 draft_rejected（其出口=plan.revise，属计划修订流，仍出范围）。
  6. cancel：按当前状态选事件（queued→queue_cancel；stale/reclaiming/awaiting_human→
     human.cancel；表列其余→cancel），副作用 = 释放开发租约与资源租约 + claimed
     review 行置 expired；worktree 回收显式跳过并注记。
  7. 操作员 CLI：human-resume/reopen/release/adjudicate/cancel 五命令（只经 HTTP，
     注入 client 可测）。
  8. 测试：六操作的状态矩阵与拒绝用例、租约释放/重签断言、决策行生命周期、
     提醒退避（首提/二次间隔增长/封顶）、CLI 端到端、HTTP 端到端；全量门禁绿。
test_id：HUMAN-OPS-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
