### budget-guard：任务级 token 预算 + 调度前预留（🚧 doing）

~~~text
任务编号：budget-guard
负责人 Harness：Qoder
开始时间：2026-10-08 03:25
依赖任务：liveness-watch（✅ ebe25cb；alerts enqueue 的 source_key 幂等先例由它落地）；
          paperclip-buzz-source（✅ 553ee0f；机制学习来源）；
          split-closure（✅ dd92851；本卡闭合它声明的"预算 guard 无模型可评"缺口）
工作区：.worktrees/qoder/
真相源引用：R10 §8（tasks 计划内容 `budget_tokens` 字段）、§9（计划校验器
            "预算齐全、合计不超上限"——validator.py 已实现静态面）、
            §10 L679（split 子任务"继承未使用预算"）、L1146（"超预算…进入
            escalated"）、§11（Claim 流程含"预占资源"，本卡把预算预占作为
            其一种）；学习机制出处：Paperclip
            `server/src/services/budget-reservations.ts`（调度前预留，
            committed=observed+held，`committed>=limit || committed+amount>limit`
            即拒，零额行也拦重复派发）、`run-cost-accounting.ts`（held→
            settled/released；未证实不落账）、`schema/budget_policies.ts`+
            `budget_incidents.ts`（阈值/事故/解决）——已逐行核读，代码原生重写
范围声明：
  - **本切片**（六件）：
    ①**usage 来源**：`submit_result` 的 `completion.harness_output.usage`
      （input_tokens/output_tokens，可选）为唯一上报口——CLI 侧
      `parse_output` 已有 `cost_usd` 先例（claude 适配器），本卡只消费
      tokens；**缺失=未上报（NULL），不发明零**。绝不让平台替 harness 估算。
    ②**迁移 011**：`budget_reservations`（attempt_id 唯一、state
      held/settled/released、amount_tokens、actual_tokens 可空、source/note）
      + `budget_incidents`（task_id+threshold 开/解、observed/limit、note）。
      均为投影表，**不新增事件名**（§37.7 白名单不动）。
    ③**app/budget**：政策纯函数（committed 三桶=已结算 actual +
      已结算未上报按预留额保守计 + held；classify ok/warning/exhausted 含界）
      + 预留/结算/释放 + reconcile（held 滞留且 attempt 已非当前 → 按
      "证据或保守"收口）+ 阈值事故（warn_percent，默认 80）+ 告警入队
      （复用 alerts enqueue，`source_key=budget:<task>:<threshold>` 幂等）
      + CLI `python -m app.budget [--check|--scan|--settle <attempt> --tokens N|
      --release <attempt>|--resolve <task>]`（--check 有 open 事故 exit 1）。
    ④**claim 接入**（唯一执行点）：任务 `budget_tokens>0` 时，授予前检查
      `committed>=limit || committed+amount>limit`（amount=设置项
      AP_BUDGET_RESERVATION_TOKENS，默认 50000）→ 拒（新拒绝码
      `claim.token_budget_exhausted`；拒绝同时开 budget.exhausted 事故）；
      授予即插 held 行（同事务）。**budget=0/未声明 → 不启用**（向后兼容：
      存量计划与全部现有用例不受影响）。
    ⑤**结算点**：submit_result 同事务结算（usage 有→settled+actual；
      无→settled+actual NULL）；reconcile 巡检兜底 abandon/超时/租约丢失
      （attempt 已非当前且仍 held）。**未上报按预留额保守计入 committed**
      ——宁可高估不发明数字；CLI `--settle/--release` 是人类纠偏口。
    ⑥**split 缺口闭合**：父预算启用时，子任务预算合计 > 父剩余
      （limit−committed）→ Conflict 拒绝 split；≤ 放行。父未启用→不查。
      （对 R10 L679"继承未使用预算"的最小可判定解释；口径写进卡与测试。）
  - **显式出范围**（记录不静默）：cost_usd 计价（claude 报了但本卡 metric
    单一=tokens）；project/agent 级预算（R10 只定义任务级 budget_tokens）；
    预算 API/前端；自动升预算（人类动作，走 CLI/未来 human 面）；
    snooze/dismiss 抑制；read_context 的 50k 上下文预算（§12 独立机制，
    与本卡无关）；把 attempt_budget_exhausted（次数预算，已有）与 token
    预算混用（两个概念，两个拒绝码）。
允许修改目录：backend/app/budget/**（新增）、backend/app/claim/**（仅预留接入
            与拒绝码）、backend/app/tasks/**（仅 submit_result 结算 +
            split 校验）、backend/app/alerts/**（仅常量如需）、
            backend/app/scheduler/{sweeps,runner}.py（新增 budget 巡检）、
            backend/app/config.py、backend/migrations/011_*.sql、
            backend/tests/**、docs/traceability.md、TASKS.md、
            docs/tasks/notes/budget-guard.md、docs/claims/qoder--budget-guard.md
禁止修改目录：backend/app/state/**（不动状态机）、docs/spec/**、
            backend/app/adapter/**（usage 消费发生在服务端；适配器口径不动）
交付分支：agent/local/qoder/budget-guard/a1
完成标准：
  1. 政策纯函数：三桶 committed 含界；classify 边界（恰好==warn% 即 warning、
     ==limit 即 exhausted）；释放不参与 committed
  2. 预留：budget>0 时 claim 成功即见 held 行；`committed+amount>limit` 或
     `committed>=limit` → 拒绝且**零状态写入**（无 claim 事件、无 held 行）；
     拒绝码=claim.token_budget_exhausted；重复请求下预留仍恰一行/attempt
  3. 结算恰一次：submit 后 held→settled、actual=上报值；无上报 → actual NULL、
     **不被当 0**（committed 按预留额计入，纯函数层断言）；replay/重复提交
     不产生第二个结算
  4. reconcile：attempt 已非当前（如 abandon 后）仍 held → 收口；
     **变异验证：去掉巡检该任务必红**
  5. 阈值告警：跨 warn% → incident open + 恰一条告警（重跑不重复、跨进程
     重放不重复）；穷尽拒绝 claim → budget.exhausted incident；未配 webhook
     时 incident 仍开、队列零行
  6. split：父启用时超额 → Conflict；恰好等于剩余 → 放行；父未启用 → 兼容
     放行（既有 split 用例全绿）
  7. CLI：--check 干净 exit 0 / 有 open 事故 exit 1；--scan/--settle/--release
     行为可断言（settle 人类纠偏后 committed 下降）
  8. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增 `OPS-BUDGET-001`
test_id：OPS-BUDGET-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
