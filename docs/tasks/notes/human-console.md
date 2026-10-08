### human-console：前端人操作面——管理链 + §18 五决策动词（✅ done）

~~~text
任务编号：human-console
负责人 Harness：Qoder
开始时间：2026-10-08 13:30
依赖任务：frontend-thread（✅ 5905f4a；三栏壳是承载面）；human-ops（✅ 514c4b2；
          §18 路由与决策台账是消费面）；rework-loop（✅ af80d4e；changes_requested
          可再领，决策动作要对它可见）
工作区：.worktrees/qoder/
真相源引用：R10 §22（三栏「左：项目/群组/Harness；中：群聊/方案/系统事件；
            右：任务/负责人/Lease/审查」；「前端不能直接修改 owner、lease、status、
            receipt 或 review approval，所有操作调用 API」）；§18（human.resume/
            reopen/release/adjudicate/cancel 五决策动词的语义、守卫状态与决定必须
            带 decided_by）；§7（动词面——前端只调 API，不发明代理写口）；§10
            （动作合法性以后端冻结迁移表为准：前端按状态上下文给按钮，非法状态
            不给选项）；§13（决策动词不带 owner fencing 语义由后端承担，前端不
            提前筛掉——服务端仍是唯一裁判）；ADR-DEV-001（Vue3+Vite 技术栈）
范围声明：
  - **本切片**：前端补「人操作面」，只消费后端既有端点，**零后端改动**：
    ①`api.ts` 新增 10 个方法：createProject / createGroup / createPlan /
    approvePlan / rejectPlan / humanResume / humanReopen / humanRelease /
    humanAdjudicate / humanCancel（路径/方法与既有路由一一对应）。
    ②`session.ts`：对应 store actions（成功即刷新受影响数据：rail、群任务表、
    线程事件；`selectedGroupId` 进状态；新增 `notice` 成功面——动作结果
    可见，不再只有 error 单面）。
    ③`ProjectRail.vue`：新建项目、按项目新建群组（折叠表单；R10 §22 左栏
    职责）；创建群组成功后跳到新群组。
    ④`ChannelPane.vue`：新增「方案」折叠区（R10 §22 中栏职责）：新建计划
    （plan_id + 快照 JSON，含合法示例模板；本地 JSON.parse 校验失败即拦）+
    审批（plan_id + 操作者 + 通过/驳回，驳回带理由）。
    ⑤`TaskPane.vue`：线程内「人工决策」卡：动作按后端守卫状态上下文出现——
    resume/reopen（awaiting_human、plan_drift、impl_drift；abandoned 只
    reopen）/ release、adjudicate（escalated）/ cancel（queued、draft、ready、
    claimed、in_progress、verifying、needs_help、blocked、changes_requested、
    plan_drift、impl_drift、awaiting_review、approved、merging、stale、
    reclaiming、awaiting_human）；操作者字段默认 operator；awaiting_human 时
    从事件投影里取最新 `human.question` 的问题文本展示（不新读端点）；
    adjudicate 带问题+选项（逗号分隔）；cancel 带理由。
    ⑥`Shell.vue`：编排接线（动作回调走 store）+ `notice` 横幅（`aria-live`
    成功/结果面）；`style.css` 仅加选择器，不改任何 token。
    ⑦验证面：vitest 契约与交互矩阵 + `vue-tsc` + `vite build` +
    `check:contrast`；真实联调：独立后端实例（非 8848）+ vite dev，浏览器走
    管理链与决策链（留档或如实披露截图能力）。
  - **显式出范围**（记录不静默）：不新增/修改任何后端端点；后端没有的读面
    如实缺——**plans 列表、harness 成员列表、task_decisions 读取**（审批按
    plan_id 输入，成员列表保持 rail 既有注释）；adapter 十六动词与 review
    claim/submit 是 Harness 面向，不进前端；不引入轮询/WebSocket（维持加载
    时拉取）；认证（decided_by 是自报主体，v1 无认证层——与 human-ops 卡同
    口径记录，属未来 auth 卡）；组件测试入共享门禁一事维持 frontend-thread
    卡记录的状态（本卡不改 scripts.sh）。
  - **零迁移、零状态变更**（前端只读+调既有端点；后端树不动）。
允许修改目录：frontend/**、TASKS.md、docs/traceability.md、
            docs/tasks/notes/human-console.md、docs/claims/qoder--human-console.md
禁止修改目录：backend/**（本卡零后端改动）、tools/**、docs/spec/**、
            scripts.sh（共享门禁线不动）、其他 harness 的卡与分支
交付分支：agent/local/qoder/human-console/a1
完成标准：
  1. 管理链在界面上可走通：建项目→建群组→建计划→审批/驳回；审批通过后
     任务列表自动出现 ready 任务（不手动刷新）；驳回显示 rejected 计数
  2. 决策链在界面上可走通：awaiting_human 显示问题文本与 resume/reopen/
     cancel；escalated 显示 release/adjudicate；动作成功 → 线程事件流即时
     含对应 human.* 事件、任务状态徽章同步；后端 409（状态已变）按错误
     信封出现在界面而不是静默
  3. 非法状态不出现对应按钮（按冻结迁移表的守卫集）；表单本地校验
     （空 id、坏 JSON、adjudicate 空问题）先于网络请求拦下
  4. vitest：api 契约 10 方法（path/method/body）＋三组件动作矩阵＋Shell
     编排（notice、建群后跳转、动作后刷新）；全绿
  5. `npm run typecheck`、`npm test`、`npm run build`、`npm run check:contrast`
     四件全绿
  6. 真实联调留档：真后端（独立 AP_DATA_DIR 与端口，不打扰协调者 8848 实例）
     + vite dev：管理链真实走通并留 API 复核；awaiting_human 场景（生产服务
     层种子：claim→start_task→ask_human）经浏览器 resume 恢复；截图或如实
     披露截图能力
test_id：MVP-UI-004（新登记，形态同 MVP-UI-001/002/003）
测试命令：cd frontend && npm run typecheck && npm test && npm run build &&
          npm run check:contrast（+ 仓库侧 PYTHONUTF8=1 ./scripts.sh check）
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 13:47
commit：`c771867`（实现提交；登记 `f2405df`；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（format 120 文件）
  pytest      : 455 passed，exit 0（本卡后端零改动，全量为回归）
  前端四件     : ①`vue-tsc` 0 错；②vitest **46 passed / 6 文件**（api 契约
                10 方法 path/method/body、ProjectRail 表单成功清空/失败保留/
                建群归属、ChannelPane 计划 10 例〔含坏 JSON 本地拦截〕、
                TaskPane 决策矩阵 15 例〔按键集按状态、答复/理由/选项载荷、
                busy 与空操作者禁用、终态无动作〕、Shell 恢复编排+横幅、
                对比度子进程）；③`vite build` 39 modules，dist JS 123.90KB
                （gzip 46.33KB）/ CSS 6.41KB；④`check:contrast` **9 对 token
                全 ≥4.5:1**（本卡仅加选择器，未动 token）。
  浏览器实测   : 独立实例 127.0.0.1:8892（AP_DATA_DIR/AP_CANONICAL_REPO/
                AP_WORKTREE_ROOT 全在 backend/data/hc-drill/，巡检器关；
                协调者 8848 实例未接触）+ vite dev 5185（AP_BACKEND 代理）。
                证据 `backend/data/hc-browser-evidence.md`（gitignored）：
                - 管理链 5/5 全 UI：建项目 P-UI → 建群 G-UI（自动跳转）→
                  建计划 PLAN-UI → 通过（横幅「2 个 ready、0 个等待中」，
                  任务栏即时出现）→ 驳回 PLAN-UI-3（横幅「1 个 draft 任务
                  被拒」，T-UI-4 → draft_rejected）；每步横幅与列表均更新。
                - 决策链真状态：生产服务层种子 claim→start_task→ask_human
                  把 T-UI-2 推到 awaiting_human；线程决策条显示
                  「待答：契约要冻结吗？」（human.question 事件投影）与
                  恢复/重开/取消三键；答复「冻结 v1」→ 恢复 → 横幅
                  「已恢复 T-UI-2 → in_progress」+ 事件 #6 human.resume；
                  T-UI-3 awaiting_human → 重开 → 横幅「已重开 → ready」+
                  事件 #13；T-UI-1/T-UI-2 取消（带理由）→ cancelled。
                - 独立 API 复核（curl 直连 8892）：终态 [T-UI-1 cancelled,
                  T-UI-2 cancelled, T-UI-3 ready, T-UI-4 draft_rejected]；
                  cancel payload 理由 UTF-8 完整；决策台账
                  answer="冻结 v1：字段名不再变"、decided_seq=6（=resume
                  事件 seq，§18 答复闭环）。
                - 截图不可用（本机 in-app 浏览器界面隐藏，visible=false）：
                  以无障碍树快照 + DOM 探针 + API 复核替代并如实披露；
                  表单填写走 CDP 设值、按钮派发真实 click 事件。
已知问题：
  - **escalated 域两动词未真跑**：`释放`/`裁决` 的按键集与载荷在 vitest
    矩阵 + api 契约测试里有断言，但真实 escalated 状态由巡检器编排
    （block SLA / merge 超时 / review 等待），本卡未布线到真实实例——
    留待 runtime-driver 接线或规则收紧时顺带补真跑。
  - **plans 无读端点**：审批只能按 plan_id 手工输入（计划列表端点不存在，
    如实留缺不发明 UI）；harness 成员列表同理（rail 注释保持）。
  - 验证管线注意（非应用缺陷）：Windows 控制台 `curl | python` 以 GBK 解
    stdin，会把 UTF-8 中文显示成乱码；首轮复核曾据此误判「原因串损坏」，
    改「落文件 + 显式 UTF-8」后载荷全对（含 DB 直读复核）。已记入
    hc-browser-evidence.md。
  - 组件测试未入共享门禁（沿用 frontend-thread 卡记录的状态；本卡未动
    scripts.sh）。
  - 决策条表单不跨任务重置操作者名——有意（同机操作者通常不变）；
    答复/理由/裁决字段成功即清空。
~~~
