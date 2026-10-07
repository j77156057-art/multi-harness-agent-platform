### paperclip-buzz-adr：借鉴 Paperclip / Buzz 的边界与唯一真相源（🚧 doing）

~~~text
任务编号：paperclip-buzz-adr
负责人 Harness：Qoder
开始时间：2026-10-07 21:15
依赖任务：gate-format-repair（✅ 已合入 203d49a，main 门禁全绿）；
          协调者八步计划的第 2 步（第 1 步=仓库收尾）
工作区：.worktrees/qoder/
真相源引用：协调者 2026-10-07 指令（借鉴 Paperclip/Buzz，按序推进，先 ADR 后
            实现）；R10 §10/§11/§13/§15/§16/§18/§21/§37.7（被固定的不变量）；
            docs/adr/ADR-DEV-001、ADR-DEV-002（本仓库 ADR 先例与格式）
范围声明：
  - **本切片**：新增 `docs/adr/ADR-DEV-003-paperclip-buzz-adoption.md`——
    ①声明 R10 为唯一业务真相源；②Paperclip/Buzz 两域的借鉴点、既有资产对照表
    与缺口；③协调者提议事件名 → R10 §37.7 白名单的映射表；④深度集成的
    "另立 ADR"闸；⑤回退条件与兼容性保证。附 traceability 登记 `MVP-ARCH-001`。
  - **显式不做**：不实现任何借鉴功能（Adapter 原型 / 事件层 / 前端均另案，按
    八步顺序后续推进）；不改 R10 规范本体与白名单；不引入 Nostr 或任何外部运行时。
  - **核对结论（写入 ADR 对照表）**：八步计划中 Adapter 抽象（`ae07649`）、
    Codex/OpenCode 接入（同）、统一事件模型（`task_events` + 88 项白名单）、
    帮助/接手/放弃（request_help/abandon/take_over + §18 六操作）在 main 上已有
    交付——本 ADR 把它们登记为"已有资产"，后续任务只补真缺口，不重复建设。
允许修改目录：docs/adr/ADR-DEV-003-paperclip-buzz-adoption.md（新增）、
            docs/traceability.md（新增 MVP-ARCH-001 行）、TASKS.md、
            docs/tasks/notes/paperclip-buzz-adr.md、docs/claims/qoder--paperclip-buzz-adr.md
禁止修改目录：backend/**（本切片零代码）、backend/migrations/**、
            docs/spec/**（R10 是真相源，只引用不修改）、tools/**
交付分支：agent/local/qoder/paperclip-buzz-adr/a1
完成标准：
  1. ADR 按仓库先例格式落成（状态/日期/关联任务/影响范围 + 背景/决策/理由/
     后果/回退条件/兼容性保证/关联），核心声明=**R10 唯一业务真相源**
  2. 借鉴点对照表：Paperclip 五域 + Buzz 五域逐项标注既有资产（带 commit）与
     缺口；事实均经 main 核对（不虚报"已有"）
  3. 事件名映射表：协调者提议的 11 个 Buzz 风格事件 → R10 现状（9 项已有对应、
     2 项属管理面无事件、新增须走 §37.7 白名单流程）
  4. 深度集成闸与回退条件明确（"另立 ADR"是唯一入口）
  5. `MVP-ARCH-001` 登记进 traceability.md；`PYTHONUTF8=1 ./scripts.sh check` 全绿
test_id：MVP-ARCH-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
