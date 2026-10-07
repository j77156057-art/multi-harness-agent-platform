### frontend-thread：前端首切片——三栏壳 + 项目群 + 任务线程（✅ done）

~~~text
任务编号：frontend-thread
负责人 Harness：Qoder
开始时间：2026-10-07 23:37
依赖任务：paperclip-buzz-adr（✅ 8b6d98f；ADR-DEV-003 的对照表把「前端」钉为
          真缺口）；协调者八步计划第 6 步
工作区：.worktrees/qoder/
真相源引用：R10 §22（桌面三栏「左：项目/群组/Harness；中：群聊/方案/系统事件；
            右：任务/负责人/Lease/审查」；「前端不能直接修改 owner、lease、
            status、receipt 或 review approval，所有操作调用 API」）；
            ADR-DEV-001（Vue3+Vite 技术栈偏离已记录）；MVP-UI-001（验收尺）
范围声明：
  - **本切片**：①三栏壳（左侧列表 / 中间项目群 / 右侧任务+线程）+
    vue-router 三条路由；②项目群视图：群聊读（`GET /groups/{id}/messages`）
    与发（`POST /groups/{id}/messages`，非权威字段，走 API）；③任务线程视图：
    任务看板（`GET /groups/{id}/tasks`）+ 任务详情 + 活动记录（`GET
    /tasks/{id}/events`，即 Buzz 的「线程=事件投影」）；④后端补两个只读端点
    （`GET /projects`、`GET /groups`——左栏列表的唯一依赖，目前不存在）；
    ⑤验证面：vitest 组件/单元测试 + `vue-tsc` typecheck + `vite build` +
    对比度脚本（AA 4.5:1，无依赖 node 脚本）四件可重复证据；真实浏览器
    端到端实测（后端+vite 起真服务）一次性留档。
  - **显式出范围**（记录不静默）：Harness 成员视图（无 list 端点，且属下一
    切片）；方案/Diff/审查/待办页（§22 其余页）；WebSocket 订阅（R10 §22
    提「只做事件订阅」——本切片用轮询/手动刷新，避免引入传输层决策）；
    认证；`scripts.sh check`/gate.yml 接入（本仓无 remote、CI 不构成验证面；
    组件测试目前**不在共享门禁**——这是本切片的记录缺口，接入与否需 fleet
    决策：本地无 node_modules 时门禁会红，CI 无 remote 时不跑）。
  - **零迁移**（两个新端点纯读）。
允许修改目录：frontend/**、backend/app/api/**、backend/tests/**、
            docs/traceability.md、TASKS.md、docs/tasks/notes/frontend-thread.md、
            docs/claims/qoder--frontend-thread.md
禁止修改目录：backend/migrations/**、backend/app/state/**、docs/spec/**、
            tools/**、.github/**、scripts.sh（共享门禁线不动）
交付分支：agent/local/qoder/frontend-thread/a1
完成标准：
  1. 三栏壳按 R10 §22 版式落成：左栏项目→群组→（Harness 占位注明出范围）、
     中栏群聊读/发、右栏任务列表 + 选中任务的活动记录
  2. 后端两个只读端点 + pytest（含 404/排序）；
  3. vitest 覆盖：api 客户端（URL 拼接/错误信封）、至少两个组件状态
     （加载/空/错误/数据）、消息发送后列表刷新
  4. `npm run typecheck`、`npm test`、`npm run build`、`npm run check:contrast`
     四件全绿；对比度脚本对声明的 text/background token 对逐对断言 ≥4.5:1
  5. 真实浏览器实测留档：真后端（临时 data 目录 + 种子数据）+ vite dev，
     浏览器里完成「选群组→看群聊→发一条消息→选任务→看事件流」并截图、
     记录 console/network 无错
test_id：MVP-UI-001
测试命令：cd frontend && npm run typecheck && npm test && npm run build &&
          npm run check:contrast（+ 仓库侧 PYTHONUTF8=1 ./scripts.sh check）
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 00:28
commit：`5905f4a`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 382 passed（本任务净增 1 项：test_api_listing.py），exit 0
  前端四件     : ①`vue-tsc` 0 错；②vitest **21 passed / 6 文件**（api 客户端
                URL 编码+错误信封、ProjectRail/ChannelPane/TaskPane 状态矩阵、
                Shell 路由编排〔落点跳群 + 深链〕、对比度脚本子进程）；
                ③`vite build` 39 modules，dist JS 107KB（gzip 41.5KB）；
                ④`check:contrast` **9 对 token 全 ≥4.5:1**（最低 4.79:1，
                --ok/bone；早先 --muted #6b6b6b 对 panel 恰好 4.50 线，已加深到
                #5f5c56 留余量）。
  浏览器实测   : 真后端（临时 data 目录 + 种子：P-DEMO/G-DEMO、T-101 claimed /
                T-102 ready / T-103 escalated、2 条初始消息）+ vite dev：
                ①打开 5173 自动落入 /g/G-DEMO，三栏渲染真实数据；
                ②composer 发消息 → 列表出现（operator 16:12:40）且输入框清空；
                ③点 T-101 → 深链 /g/G-DEMO/t/T-101，线程显示 #1 plan.approve /
                #4 claim（含 payload 折叠）；
                ④刷新冷启动深链，全部数据自 API 重建；
                ⑤console 零错误零警告、网络请求全 200；
                ⑥直连后端 API 复核：3 条消息、末条即 UI 所发。
                截图：`backend/data/fe-demo-shot-{1-board,2-thread}.png`
                （gitignored，留档于工作区）。
已知问题：
  - **组件测试未入共享门禁**（记录缺口）：本仓无 git remote、gate.yml CI 当前
    不构成验证面；`scripts.sh check` 不跑 npm（本地无 node_modules 时 `/test`
    无意义）。是否接入（CI job vs scripts.sh 条件跑 vs 保持卡内命令）需 fleet
    决策——前端若无门禁按本表标准是「仅是名字」，本切片以"卡内四件可重复命令
    + 浏览器留档"过渡，不冒充已接入。
  - 依赖安装走 **npmmirror**（本机对 npmjs 直连 10s 超时）：`package-lock.json`
    的 resolved 指向镜像——环境事实，后装者用默认源可能慢，用 `--registry=
    https://registry.npmmirror.com` 可复现。
  - 出范围（卡内范围声明）：Harness 成员列（无 list 端点）、§22 其余页
    （方案/Diff/审查/待办）、WebSocket 订阅（本片用加载时拉取，无断线补投）。
  - 骨架遗留清理：App.vue 原先 ping 的 `/api/spec/status` 路径本不存在
    （真实路由是 `/spec/status`，不在 /api/v1 下）——已随重写移除。
~~~
