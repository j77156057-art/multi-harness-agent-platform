### alert-webhook：出站告警 webhook——学到机制、原生实现（✅ done）

~~~text
任务编号：alert-webhook
负责人 Harness：Qoder
开始时间：2026-10-08 02:13
依赖任务：paperclip-buzz-source（✅ 553ee0f；机制学习来源=其报告 §2.8/§6）；
          jsonl-export（✅ 967dc7f；本卡关闭其记录的「无外部告警通道」缺口）
工作区：.worktrees/qoder/
真相源引用：协调者方向（源码学机制→原生重写，不搬代码）；报告
            docs/reports/paperclip-buzz-source-review.md（Paperclip 出站 webhook
            模式：HMAC 签名、仅 HTTPS、投递可重试）；R10 §15（异步后置写、
            重试队列的先例模式=jsonl_exports）；R10 §10（升级边全集——
            escalation 事件集合从冻结迁移表推导，不手抄）
范围声明：
  - **本切片**：①迁移 009：`alert_deliveries`（含 `UNIQUE(kind, source_seq)` 去重）
    与 `alert_cursors`（事件订阅游标）；②`app/alerts`：
    `AlertNotifier.scan()`（游标推进 + 升级事件入队；事件集合由
    `default_transition_table()` 里 `next is ESCALATED` 的边推导）、
    `deliver()`（HMAC-SHA256 签名 POST；2xx=delivered，失败 attempts+1、
    到上限停靠 `failed` 可观察）、`run_once()` 报告；③CLI
    `python -m app.alerts [--once|--loop] [--interval N]`（同 exporter 形态；
    未配置 URL = 显式 no-op；缺库/缺表 exit 2）；④测试含**本地真 HTTP 服务端**
    （断言签名/载荷/重试/停靠/跨实例续跑）。
  - **显式出范围**（记录不静默）：只接 `task.escalated` 一个告警源（后续源：
    导出失败、integration_health 转红——机制可复用，各为小卡）；不做投递侧
    背压/批量合并；不做 webhook 接收方向（入站属未来安全评审）；不做重试退避
    曲线（每次运行重试一次，节奏由 `--loop` 间隔或 cron 决定——重试上限即停靠）。
  - **零代码复制**：实现为本仓原生（stdlib urllib + 既有事件/表模式）；
    「参考机制来源」在卡与模块 docstring 注明即可，无需第三方许可文件。
  - 迁移 009 为纯加表。
允许修改目录：backend/app/alerts/**（新增）、backend/app/config.py（告警配置）、
            backend/migrations/009_alert_deliveries.sql、backend/tests/**、
            docs/traceability.md、TASKS.md、docs/tasks/notes/alert-webhook.md、
            docs/claims/qoder--alert-webhook.md
禁止修改目录：backend/app/state/**、backend/app/scheduler/**（告警不是巡检，
            刻意独立——巡检保持"读条件→条件写、零网络"）、docs/spec/**、tools/**
交付分支：agent/local/qoder/alert-webhook/a1
完成标准：
  1. 检测：任务事件流里出现任一升级边事件（集合=迁移表中 next==escalated 的
     事件，含 stuck/aging/block/verify/review/merge/budget 全族）→ 恰一条
     delivery 入队（`(kind, source_seq)` 去重）；游标推进幂等，重跑/重启续跑
  2. 投递：POST JSON + `X-AP-Signature: sha256=<hmac(secret, body)>` 头；本地
     真服务断言签名与载荷；2xx→delivered；5xx/超时→attempts+1 重试；达上限
     （默认 5）→ failed 停靠且不再自动重试
  3. 安全底线：非 loopback 的 `http://` URL 被拒绝（仅 https 或 loopback http）；
     未配置 URL 时 run 显式 no-op（报告 configured=false）
  4. CLI：--once 输出一行 JSON 报告（detected/delivered/failed/pending）；缺库/
     缺表 exit 2；`--loop` 可用
  5. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增 `OPS-ALERT-001` 行
test_id：OPS-ALERT-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 02:31
commit：`3c27193`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（106 files already formatted）
  pytest      : 400 passed（本任务净增 10：test_alerts.py），exit 0
  关键断言     : ①升级事件集合由 `default_transition_table()` 推导（含
                merge_timeout/stuck_threshold_reached/aging_timeout，不含
                claim/release——表变集合变）；②scan 每事件恰一投递 +
                游标幂等重扫为 0 + 跨实例续跑（状态全在库）；③**本地真 HTTP
                服务端**断言 X-AP-Signature == sha256=hmac(secret, 收到的原样
                body)、X-AP-Delivery 存在、载荷字段（alert/task_id/source_seq/
                payload.event_name/payload.payload）；④500 跨轮重试：attempts
                1→2→3 后停靠 failed、第 4 轮不再发（请求数恰 3）、last_error
                记 "HTTP 500"；⑤安全底线：http 非 loopback 与缺 secret 拒跑
                （AlertConfigError），https 与 loopback http 放行，不跟随重定向
                （_NoRedirect）；⑥未配置 URL 显式 no-op（configured=false 一行
                JSON，零副作用）；⑦CLI 端到端：无库 exit 2 / 未配置 exit 0 /
                坏目标 exit 2（"refused"）/ 配置后真投递（裸 SQL 种子升级事件→
                本地服务端收到 1 条→重跑 delivered=0）、投递失败 exit 1。
已知问题：
  - 只接 `task.escalated` 一个告警源（后续源=导出失败、integration_health 转红，
    机制可复用，各为小卡）；不做投递侧批量合并/背压；重试节拍=每次运行一次
    （cron 或 --loop 间隔即节奏），无指数退避曲线。
  - urllib 会把自定义头大小写规范化为 `X-ap-signature`（HTTP 头大小写不敏感；
    消费方按大小写无关读取即可——测试亦如此断言）。
  - 入站 webhook（接收方向）显式未做——属未来安全评审范围。
~~~
