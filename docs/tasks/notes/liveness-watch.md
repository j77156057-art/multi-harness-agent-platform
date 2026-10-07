### liveness-watch：存活观测面 + 零 commit 告警 + stuck 误杀修复（🚧 doing）

~~~text
任务编号：liveness-watch
负责人 Harness：Qoder
开始时间：2026-10-08 02:36
依赖任务：alert-webhook（✅ 5b60e9c；本卡的告警走它的投递队列）；
          paperclip-buzz-source（✅ 553ee0f；机制学习来源）
工作区：.worktrees/qoder/
真相源引用：R10 §13 L706「零 commit：连续 2 个 heartbeat 窗口无新 commit 时
            告警」（本卡字面实现）；§13 L704「连续 3 次巡检无进展 → stuck」
            （本卡修正其"进展"证据缺心跳的缺口）；R10 §15（异步后置、
            重试队列先例）；学习机制出处：Paperclip
            `active-run-watchdog/domain/policy.ts`（silence 时钟回退链
            lastOutput→processStart→runStart→created；两阈值→三档；抑制优先级
            先例）——本会话已逐行核读，代码原生重写
范围声明：
  - **本切片**（三件）：
    ①`app/liveness`：纯政策（`silence_started_at` 回退链映射为
      最近事件→attempt 开始→任务创建；`classify_silence`：ok/suspicious/
      critical + not_applicable）+ 扫描（owner 态任务证据=活跃租约剩余秒、
      silence 秒、零 commit 窗口数、head_commit 有无）+ CLI
      `python -m app.liveness [--once|--check|--loop]`（--check 纯读；
      有 critical 时 exit 1——cron 可脚本化）。
    ②**零 commit 告警**（R10-706 字面）：owner 态 + 租约仍活 + attempt 尚未
      发布任何 commit（head_commit IS NULL）且 attempt 年龄 ≥ 2 个 heartbeat
      窗口 → 经 alert 队列投递 `run.no_commit`；**一次/attempt**（去重键
      =attempt_id，经新增 `source_key` 列；新 attempt 重新计）。
      声明近似：R10 的"新 commit"以平台可见的发布为准（harness 工作树内的
      增量 commit 平台不可见——如实记录，不假装）。
    ③**stuck 误杀修复**：`_stuck_round` 指纹计入活跃租约的 `lease_until`
      （任一 kind，`MAX(lease_until) WHERE released_at IS NULL`）——心跳续租
      即"进展证据"。理由与分责：健康长跑（心跳在续、未发布 commit）此前
      3 个巡检周期即被误升级；R10-706 已为该情形定义了更细的"告警"档
      （2 个心跳窗口、走告警面），stuck 档留给"连租约都无人续"的实质静止
      （彼时 lease 到期路径本来也会接手）。**僵尸心跳**（只续租不干活）此后
      由零 commit 告警（人）而非 stuck（状态机）兜底——取舍如实记录。
  - **显式出范围**（记录不静默）：snooze/dismiss 抑制（Paperclip 有，我们无
    操作面承载——不做，不假装）；产出质量分类（Paperclip `classifyRunLiveness`
    的 advanced/plan_only/empty_response 族——我们的产出由 verify/review 门禁
    判定，平台不存 run 文本，另案评估）；liveness 进 API/前端（后续小卡）；
    告警源仍只有 escalated（①）+ no_commit（本卡）。
  - 迁移 010 为纯加列 + 部分唯一索引（告警去重键扩展）。
允许修改目录：backend/app/liveness/**（新增）、backend/app/alerts/**（enqueue
            扩展）、backend/app/scheduler/sweeps.py（仅指纹）、
            backend/app/config.py、backend/migrations/010_*.sql、
            backend/tests/**、docs/traceability.md、TASKS.md、
            docs/tasks/notes/liveness-watch.md、docs/claims/qoder--liveness-watch.md
禁止修改目录：backend/app/state/**（不动状态机）、backend/app/tasks/**、
            docs/spec/**、tools/**
交付分支：agent/local/qoder/liveness-watch/a1
完成标准：
  1. 纯政策可测：回退链顺序、阈值边界（=suspicion/critical 恰好触发）、
     not_applicable
  2. 扫描证据正确：租约剩余/silence（最近事件为钟源，_latest_event_iso 复用）/
     零 commit 窗口数（window=heartbeat_seconds）；--check 有 critical 时 exit 1
  3. 零 commit 告警：2 窗口前不告、到 2 窗口告且**恰一次**（重跑不重复、
     续跑实例不重复、新 attempt 重新告）；投递经 alert 队列（真 HTTP 断言
     一次）；未配置 webhook 时不入队（告警通道=opt-in）
  4. stuck 修复：心跳续租的任务 4 轮巡检不被升级（**变异验证：去掉租约项必红**）；
     租约不续的任务仍按 3 轮升级（旧行为不回归）；无租约状态（needs_help 等）
     行为不变
  5. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增 `OPS-LIVENESS-001`
test_id：OPS-LIVENESS-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
