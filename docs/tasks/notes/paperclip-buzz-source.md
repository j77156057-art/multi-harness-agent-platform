### paperclip-buzz-source：源码核实——Paperclip / Buzz 深读 + ADR-DEV-003 勘误（🚧 doing）

~~~text
任务编号：paperclip-buzz-source
负责人 Harness：Qoder
开始时间：2026-10-08 01:07
依赖任务：paperclip-buzz-adr（✅ ADR-DEV-003 初稿——其"借鉴点"列来自协调者描述，
          未经源码核实）；协调者提问「已集成吗/看过源码吗」+ 选定「完整读 + 修订对照表」
工作区：.worktrees/qoder/
真相源引用：block/buzz @ c96d707f6efd359e6cfdc34bd93c8b5fb36f1d5d（Rust）；
            paperclipai/paperclip @ ceabc3bc880676c49eee907a8d736f961a1358eb（TS）
            ——两仓文本快照在仓外 `D:\study\tools\agent\_refs\`（github.com 被墙，
            经 api.github.com trees + raw.githubusercontent.com 逐文件抓取）
范围声明：
  - **本切片**：①两仓源码深读（Buzz：kind 注册表/频道线程/身份/git/workflow/
    集成面；Paperclip：数据模型/状态机/调度/adapter/heartbeat/预算/权限/集成面），
    全部结论带 `文件:行号`，关键条目人工抽查复核；②产出
    `docs/reports/paperclip-buzz-source-review.md`（三方对照：提议名 vs R10 vs
    Buzz vs Paperclip + 校正点 + 集成面候选 + 未证实清单）；③ADR-DEV-003 加
    「源码核实修订」addendum（不改写正文，事实以 addendum 为准）；④traceability
    MVP-ARCH-001 行同步。
  - **显式出范围**（记录不静默）：**不做任何集成**（真对接按 ADR-DEV-003 决策 4
    另立 ADR）；不 vendor 两仓任何代码进本仓；快照只存仓外（`_refs/`）。
    两仓各有一小批文件因 404/超限未取（Buzz 14 个、Paperclip 12 个，含个别被
    引模块——相应"未能证实"项已在报告标注）。
  - **零代码改动**（本卡只动文档）。
允许修改目录：docs/reports/paperclip-buzz-source-review.md（新增）、
            docs/adr/ADR-DEV-003-paperclip-buzz-adoption.md（仅追加 addendum）、
            docs/traceability.md、TASKS.md、docs/tasks/notes/paperclip-buzz-source.md、
            docs/claims/qoder--paperclip-buzz-source.md
禁止修改目录：backend/**、frontend/**、docs/spec/**、tools/**、
            docs/adr/ADR-DEV-001/002/004（各自独立）
交付分支：agent/local/qoder/paperclip-buzz-source/a1
完成标准：
  1. 两仓深读报告落成：每条结论带快照内 `文件:行号`；两仓各给出 kind/实体/
     状态机/adapter/预算/权限/集成面的事实清单；11 个提议事件名三方对照表
  2. 关键条目经人工抽查复核（kind:9 / NIP-34 全族 / ALL_KINDS=134 /
     ISSUE_STATUSES / BUDGET_SCOPE_TYPES / 16 内置 adapter / WF-08 TODO）
  3. ADR-DEV-003 addendum 写明：初稿"借鉴点=按协调者描述"、勘误点
     （11 个事件名并非 Buzz 现有事件名；task.*/审批事件在 Buzz 属"已定义未
     发射"；两处文档偏差）、集成面候选与风险（单向发布低风险 vs Paperclip
     external-task-protocol 天然双向）
  4. traceability MVP-ARCH-001 行同步；`PYTHONUTF8=1 ./scripts.sh check` 全绿
test_id：MVP-ARCH-001
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
