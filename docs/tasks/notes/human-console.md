### human-console：前端人操作面——管理链 + §18 五决策动词（🚧 doing）

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
当前状态：doing
~~~
