### human-ops：§18 人类决策面（首个切片）（✅ done）

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
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-07 12:0x
commit：`514c4b2`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 351 passed（本任务净增 37 项：test_human_ops.py；另同步改钉
                4 个既有测试文件的 runner 顺序与 drift 断言），exit 0
  关键断言     : ①六操作全矩阵（含拒绝路径，被拒时零事件零决策行）；②ask 释放
                开发租约、resume 同 attempt 同 epoch 重签（drift 残留租约先清
                后签，恰一活跃）；③决策行开行/消费/未决保持 NULL（answer 原文 +
                decided_seq 指向事件，作者在事件 payload）；④drift 无 owner 走
                human.reopen_no_owner → ready，且随后的 fresh claim 真实可成
                （覆盖 current_attempt_id 清零这条 claim 的隐式前提）；⑤cancel
                按态选三事件（queue_cancel/human.cancel/cancel）+ 开发/资源租约
                释放 + claimed review 置 expired；⑥提醒退避 30→60→120→240
                封顶、只写 next_reminder_at（waiting_since / state_changed_seq
                零进展，R10-fix-11）；⑦HTTP 端到端含 §13 fencing 409 与错误
                信封；CLI 五个 human-* 端到端（EXIT_OK 与 EXIT_API_ERROR 两态）。
  覆盖收官     : abandoned/escalated 移出 WAIVERS_NO_EXIT → RECOVERY（出口断言
                5 条指向 test_human_ops.py：reopen / human.release /
                human.adjudicate / 两提醒自环）；21 态豁免 3 → 1
                （仅 draft_rejected，出口=plan 修订流，仍出范围）。
已知问题：
  - cancel 副作用按卡收窄：开发/资源租约释放 + claimed review 置 expired；
    verify/review/merge 三类独立租约行与在途 intent 对账归 verify/merge 线，
    downstream 传播归 MERGE-REVERT-002，worktree 回收归操作员（需 archive_root）
    ——均已在范围声明先记，不静默。
  - escalation_reminder 的锚点：无 escalated_at 的历史产出（merge worker 的
    升级边）以"任务最近事件"兜底（入场时即升级事件本身）；首提后
    next_reminder_at 接管，锚点不随提醒自身漂移。
  - 身份=显式参数（decided_by），§18 pinned 决策与 harness token 403 语义仍待
    认证层（同 capabilities() 先例）；决策进入 verify receipt 输入清单归
    verify 线。
~~~
