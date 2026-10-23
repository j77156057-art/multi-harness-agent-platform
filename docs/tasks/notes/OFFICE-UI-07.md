# OFFICE-UI-07

### 统一任务卡

任务编号：OFFICE-UI-07
负责 Harness：Mavis
开始时间：2026-10-23 21:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-02（办公室界面）、OFFICE-UI-04（平台翻译）
真相源引用：R10 §9（方案、任务和产出契约 —— 工单是任务的契约）；§15（事件、事实源）；
`backend/app/adapter/formal_worker.py` 的 `build_goal`（现场报告怎么拼到工单后面）；
`backend/app/admin/worker.py` 的 `_goal_with_handoff`（同一句话的另一个出处）；
`backend/app/wording/service.py`（失败原因的人话表）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done（已合入 main：`96bdbaf`）
commit：`96bdbaf`（这一轮与前几轮一起合入 main）
验证结果：工单里夹着的机器 JSON 拆开：`office/scripts/verify-office-handoff.mjs` 通过；真跑：2957 字的工单拆成 80 字工单 + 一句人话（谁做的、哪儿错了、有没有产出），机器那部分收进折叠。
test_id：MVP-UI-022

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-02、OFFICE-UI-04 |
| test_id | `MVP-UI-022` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/server/formal-dispatch-gateway.mjs`、`office/scripts/verify-office-*.mjs`、`backend/app/wording/**`、`backend/tests/test_wording.py`、`docs/claims/mavis--OFFICE-UI-07.md`、`docs/tasks/notes/OFFICE-UI-07.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/app/api/routes.py`、`backend/app/tasks/**`、`backend/app/claim/**`、`backend/app/pool/**`、`backend/app/adapter/**`、`docs/spec/**`、`backend/migrations/**`、其他 Harness 的工作区与分支 |

## 做什么

任务详情里的「这件事要做什么」说人话：把夹在工单里的那坨机器 JSON **拆出来**，
工单只剩人写的那段，现场另起一节，一句话讲清谁做的、哪儿错了、有没有产出。

## 为什么

真数据里那条工单有 **2957 字**，后面 2900 字是平台追加的**现场报告**（一整份 CLI
报错转储）。人看到的是一堵墙 —— 得先在 JSON 里找"到底哪儿错了"。

但**不能整段翻译**：工单本身是人写的（"综合权威来源和前置整理，完成
docs/…md，说明每块工位屏幕显示哪些平台回执字段…"），翻译会把契约改坏。

## 怎么做到不出错

1. **按平台自己的标记拆**，不猜：标记是
   `The previous attempt did NOT finish; here is its breakpoint report.`
   —— 出处是 `backend/app/adapter/formal_worker.py` 的 `build_goal` 与
   `backend/app/admin/worker.py` 的 `_goal_with_handoff`。平台哪天改了这句话，这里
   就拆不开 —— **退回今天的样子（工单里带机器那坨），不会丢东西**。
2. **人话句子从结构化字段来，不问模型**：谁做的（`failed_harness` → 登记名）、
   哪儿错了（`error` → 平台的失败原因规则表）、有没有产出（`files_touched`）。
   问模型反而会编 —— 现场报告里的字段都是事实。
3. **机器那坨收在后面**：「看它自己的原话」与「看原始现场（机器记的）」两个折叠，要查的
   人自己展开。它自己最后说的那句（从机器日志里挑出来的原话）摆在折叠上面 ——
   那不是我们翻译的，是**它自己说的**，所以照原样摆。
4. **拆在网关那侧做**（网关本来就替界面问平台），界面只负责显示。

## 完成标准

1. 网关把工单与现场报告拆开（工单干净、`handoff` 是解析出来的对象、原始 JSON 也留着）。
2. 现场报告里的失败原因翻成人话（平台的规则表；认不出的原样留着）。
3. 界面上：工单干净；现场一节一句话；机器那坨折叠。
4. 新增断言钉住以上四条；办公室冒烟全绿；`office/` 构建通过；`./scripts.sh check` 全绿。
5. `docs/traceability.md` 登记 `MVP-UI-022`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-handoff.mjs`（新增） | ok |
| `backend/tests/test_wording.py` | 全绿（加了"失败原因带任务号前缀"两条） |
| 办公室冒烟 | 见下 |
| `tools/docstate` / `tools/discipline` / `ruff` | 全过 |
| 真跑 | 8850 真数据副本（FD-0a286df60752dc98，工单 2957 字） |

真数据上的效果：

- 改之前：「这件事要做什么」= 2957 字（一段中文工单 + 2900 字机器 JSON）。
- 改之后：
  - 「这件事要做什么」= 80 字（人写的那段）；
  - 「上一次没做完，窗口贴回来的现场」= 上一次是 Claude Code 干的，窗口的命令退出
    了（退出码 1），没有产出文件；
  - 它自己最后说的是：API Error: 503 No available accounts…（**它自己说的**，照原样摆）；
  - 「看它自己的原话」「看原始现场（机器记的）」两个折叠收着机器那部分。
