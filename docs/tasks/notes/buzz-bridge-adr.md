### buzz-bridge-adr：Buzz 单向桥的边界裁决（✅ done）

~~~text
任务编号：buzz-bridge-adr
负责人 Harness：Qoder
开始时间：2026-10-09 02:20
依赖任务：paperclip-buzz-source（✅ 553ee0f；wire 事实与未证实清单的来源）；
          rules-tighten（✅ 0c59630；路线图第 4 项，本卡为第 5 项前置）
工作区：.worktrees/qoder/
真相源引用：协调者路线图第 5 项原文（"将平台 JSONL 事件发布到 Buzz 频道。
            Buzz 只作为沟通和展示层，R10 继续作为唯一业务真相源"）；
            ADR-DEV-003 决策 4（外部协议须另立 ADR）；ADR-DEV-004（把
            "Nostr relay 或任何外部协议运行时"留在闸外——本 ADR 兑现该预留）；
            源码核实报告 §1/§5/§7（kind:9+h tag、NIP-42 AUTH、buzz-sdk
            不持钥、countdown-bot 同款示例、未证实清单）；R10 §15/§17/§37.7
范围声明：
  - **本切片（纯决策文档，零代码）**：新增
    `docs/adr/ADR-DEV-005-buzz-one-way-bridge.md`：
    ①授权范围=报告候选 a（单向发布；候选 b/c 与 NIP-34/DM/workflow 仍闸外）；
    ②单向铁律（只写不读，Buzz 状态不入平台）；③发布源裁决（DB `task_events`
    游标读，JSONL 仍是导出面——含与路线图"JSONL 事件"措辞的显式对齐）；
    ④wire（kind:9 + `["h", channel]` + NIP-42 AUTH + NIP-10 e-tag 线程，
    允许 v1 平铺降级并存档）；⑤消息=投影（字段白名单渲染，**不逐字转发
    payload**；secret 不出境）；⑥幂等（Nostr id=内容哈希 + 游标表对齐
    `alert_cursors` 形状，至少一次投递+去重）；⑦配置（`AP_BUZZ_*`，未配置
    显式 no-op；私钥=受控凭据文件）；⑧身份/准入由人类在 Buzz 侧完成；
    ⑨诚实缺口（HTTP bridge 认证、sdk 签名边界、relay 实机行为→实现卡以
    countdown-bot 同款路径做最小真机验证）。
  - **显式不做**：不写任何桥代码、不建表、不引依赖；不改 R10 与白名单；
    不接触运行中的 Buzz/relay（本机没有目标 relay，实机验证属实现卡）。
  - **状态：提议（Proposed）**——授权外部数据出口的裁决应由协调者确认后
    转「已接受」；在此之前实现不开工。
允许修改目录：docs/adr/ADR-DEV-005-buzz-one-way-bridge.md（新增）、
            docs/traceability.md（新增 MVP-ARCH-002 行）、TASKS.md、
            docs/tasks/notes/buzz-bridge-adr.md、docs/claims/qoder--buzz-bridge-adr.md
禁止修改目录：backend/**（零代码）、backend/migrations/**、frontend/**、
            docs/spec/**（只引用）、tools/**
交付分支：agent/local/qoder/buzz-bridge-adr/a1
完成标准：
  1. ADR 按仓库先例格式落成（状态/日期/关联任务/影响范围 + 背景/决策/后果/
     回退条件/兼容性保证/关联），与 ADR-DEV-003/004 的闸门关系写清
  2. 每条决策可执行、可反驳：wire 事实全部标注来源（报告节号）；未证实项
     单列并指定验证路径；回退条件含"任何双向需求=另立 ADR"的铁律
  3. 兼容性保证成立：零权威面 schema 变更、零写路径触碰、未配置=no-op
  4. `MVP-ARCH-002` 登记进 traceability.md；`PYTHONUTF8=1 ./scripts.sh check`
     全绿（docstate + discipline；本卡零代码，pytest 计数不变）
test_id：MVP-ARCH-002（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；ADR 状态=提议，待协调者裁决）
完成时间：2026-10-09 02:35
commit：`e1a839b`（交付提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（零代码，仅格式轮询）
  pytest      : 513 passed（本卡零代码；较 rules-tighten 的 511 多 2 项是
                并入 main 时吸收的 gate-summary-fix 测试），exit 0
  交付物       : `docs/adr/ADR-DEV-005-buzz-one-way-bridge.md`——按 ADR-DEV-001/
                003/004 先例格式；与 DEV-003 决策 4（闸）及 DEV-004（把
                "Nostr relay 另立 ADR"的预留）的关系在背景与关联节写明；
                wire 事实逐条标注报告节号（§1/§5），未证实清单（§7）在决策 9
                单列并指定验证路径。
  traceability : `MVP-ARCH-002` 已登记（⚠️ 仅声明——决策文档，无机械断言）
已知问题：
  - **ADR 状态=提议**：授权的是"平台事件发到第三方平台"这条数据出口，按
    流程应由协调者确认后转「已接受」；在此之前不写桥代码；
  - 实现未开工（另卡）：`buzz-cursors` 表、`app/buzz` 模块、最小真机验证
    （countdown-bot 同款路径）、消息映射细表（默认发布/默认静默的精确集合）
    都留给实现卡；
  - 本机无目标 Buzz relay——实机验证需要协调者提供 relay 地址/频道/agent
    身份（或确认用本地自建 relay 做验收）；
  - 排障留痕：合流期间发现另一 agent 在**根工作区**留了未跟踪目录
    `.workbuddy/`，使根侧 discipline 判红、挡住所有合并；经协调者确认按
    "本地忽略"处理（`.git/info/exclude` 一行，未动其文件、未进仓库）。
  - 排障留痕（二）：首轮全量门禁 1 红——`test_cli_once_runs_a_round` 的
    verify 结果为 `status='error'`（驱动对未预期异常的设计标签）；单跑
    3/3 绿、全量重跑绿——与早前"64 秒 179 红"同源的**机器级瞬时抖动**
    （负载下的进程创建/文件锁，drill F12 家族）。运行时环对此**自愈**：
    未落 receipt 的 verifying 任务下一轮仍是候选，自动重试；单轮断言的
    测试仅暴露该瞬时性，不代表产品回归。留档：若再现，先看
    `verify[0].detail` 的异常原文。
~~~
