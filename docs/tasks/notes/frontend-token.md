### frontend-token：控制台令牌接线（enforce 推广前置）（✅ done）

~~~text
任务编号：frontend-token
负责人 Harness：Qoder
开始时间：2026-10-08 16:20
依赖任务：auth-layer（✅ afca400；令牌与矩阵已落，前端是唯一未接的消费面）；
          human-console（✅ f509b4d；管理面与决策面的表单是接线对象）
工作区：.worktrees/qoder/
真相源引用：R10 §17（API 层矩阵执行=本卡的对接面；前端「所有操作经 /api/v1」
            ——§22 的既定口径不变）；auth-layer 卡「已知问题①」=本卡闭环对象；
            §18（决策必须带 decided_by——令牌主体应成为默认值来源）
范围声明：
  - **本切片**（后端一个只读端点 + 前端接线）：
    ①**后端最小面**：`GET /api/v1/auth/whoami`——只读自省，**不拒绝也不审计**
      （语义="我若出示令牌，我是谁"）：off→`{"mode":"off","subject":null}`；
      enforce 有合法令牌→`{"mode":"enforce","subject":{type,id}}`；无/坏/吊销
      → 200 且 `subject:null` + `token_reason`（token_missing/invalid/revoked）
      供界面给准确提示。刻意不走 require_for（那是"保护面"，自省不是资源访问，
      浏览器加载时的探测不应制造审计噪音）。
    ②前端 `api.ts`：模块级令牌持有者 + setter；`request()` 有令牌即带
      `Authorization: Bearer`；`whoami()` 客户端。
    ③session store：`token`（localStorage `ap.humanToken` 持久化）+ `authState`
      （off / authenticated{subject} / missing / invalid / revoked）；`loadAuth()
      加载时自省；`setToken/clearToken`（设置后立即自省并把主体同步给表单默认
      操作者）；动作遇 401/403 时状态与错误面联动（不改既有错误信封展示）。
    ④UI：ProjectRail 底部「认证」面板——状态徽标（认证关 / 已认证为 X / 需要
      令牌 / 令牌无效）+ 令牌输入（密码框，仅本地保存）+ 清除；Shell 把
      `operator`（whoami 主体）传给 ChannelPane/TaskPane，其操作者字段在**未被
      手改**时跟随（改过就不覆盖）。
    ⑤测试：vitest（api 头有无、store 自省与持久化、Shell 面板状态、401 面）；
      pytest（whoami 四态：off / enforce+无 / enforce+合法 / enforce+吊销）。
    ⑥实机：独立 enforce 实例 + vite——无令牌时界面状态与 401 呈现；粘贴 CLI
      签发的 human 令牌后管理链（建项目/群/计划/审批）全通、决策条操作者自动
      对齐主体；坏令牌→令牌无效态。截图不可用按既有披露。
  - **显式出范围**（记录不静默）：
    - **切 enforce 的决策不在本卡**：协调者自己定（README 有步骤）；
    - **harness 侧令牌接线**（CliHarnessWorker/演练脚本/真实 adapter 测试仍在
      off 模式跑）——enforce 全量推广时它们也要带令牌，另立卡；
    - 登录页/账号体系（不在 v1 范围；令牌仍由 CLI 签发）、令牌轮换 UI
      （revoke 在 CLI）、读族认证（矩阵不覆盖，维持开放）；
    - 不改后端矩阵/policy（auth-layer 已定），不加其它端点。
  - **零迁移**（localStorage + 既有 auth_tokens）。
允许修改目录：backend/app/api/**（仅加 whoami 路由与所需小助手）、
            backend/tests/**、frontend/**、README.md（enforce 步骤补前端已接线）、
            TASKS.md、docs/traceability.md、docs/tasks/notes/frontend-token.md、
            docs/claims/qoder--frontend-token.md
禁止修改目录：backend/app/auth/**、backend/app/state/**、backend/migrations/**、
            docs/spec/**、tools/**、其他 harness 的卡
交付分支：agent/local/qoder/frontend-token/a1
完成标准：
  1. 前端所有写操作在设过令牌后自动带 `Authorization: Bearer`（api 层断言）；
     未设令牌时不带（off 模式行为不变）
  2. whoami 四态正确（pytest）+ 界面状态四态可见（vitest + 实机）：认证关 /
     已认证为 X / 需要令牌 / 令牌无效（含吊销）
  3. 贴入合法 human 令牌后，enforce 实例上管理链与决策链**无需手填操作者**
     全通（主体自动对齐，且手改不被覆盖）；错误信封里的 401/403 在界面有
     对应提示
  4. 令牌仅保存在 localStorage，不写入日志/不硬编码；清除令牌后回到"需要
     令牌"态
  5. 前端四件全绿（typecheck/test/build/contrast）+ 全量门禁绿；
     traceability 新增 `MVP-UI-005`，DOC-AUTH-001 的"前端 token 接线"缺口
     行移除（其余缺口保留）
test_id：MVP-UI-005（新登记）
测试命令：cd frontend && npm run typecheck && npm test && npm run build &&
          npm run check:contrast（+ 仓库侧 PYTHONUTF8=1 ./scripts.sh check）
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 16:36
commit：`b26680a`（实现提交）+ `f6c345c`（伴随的测试去时序化，见下；登记
        `1c58a00`；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed
  pytest      : 493 passed（本任务净增 2：whoami 四态 + off 态），exit 0
  前端四件     : typecheck 0 错；vitest **55 passed / 6 文件**（+9：api 头有无、
                ProjectRail 面板四态/应用与拒收/清除按钮、Channel+Task 的
                操作者跟随与手改不覆盖、Shell 的 whoami 徽标/需要令牌态/
                持久化+Bearer 回放）；build 39 modules（JS 126.6KB gzip 47.2KB）；
                contrast 9 对 ≥4.5:1（仅加选择器）。
  实机 enforce : 8894 + vite 5186（同一 auth-drill 库）：首次加载面板
                「需要令牌」→ 粘贴 CLI 签发的 human 令牌 → 「已认证为
                human:operator」+ localStorage 落键；**带令牌建项目成功、
                取消任务成功（操作者自动=主体）**；清除令牌 → 回「需要令牌」，
                再建项目 → 错误面 `auth.token_invalid: permission denied`、
                草稿保留、库内无新项目。留档
                `backend/data/auth-drill/evidence-frontend.md`。
  文案打磨     : 401 的静态 message 由「permission denied」改为
                「authentication required」（403 不变）；重启实例复测报文
                （D-8 口径不变：静态模板 + details 只带权限键）。
已知问题：
  - 门禁途中**发现并修掉一个负载敏感的既有测试**（非本卡引入）：
    `test_sweeper_workers` 的三个"过截止线"断言依赖模块级 `NOW`（=导入时刻）
    与锚点事件真实时钟的差值——`merge retry`（40min vs 30min 租约）余量只有
    10min，套件慢跑 12m43s 时翻红；`_past_anchor()` 把时钟改从锚点事件
    created_at 推算（`f6c345c`，产品代码零改动，单测文件全绿）。
  - **harness 侧仍不带令牌**：CliHarnessWorker/演练脚本/真实 adapter 测试在
    off 模式跑；全量 enforce 前需接线（另立卡，README 已警示）。
  - 令牌轮换仅 CLI（revoke+issue），无界面轮换；登录/账号体系不在 v1。
  - 读族与群聊发言保持开放（矩阵不覆盖），enforce 下控制台只读面照常。
  - 令牌失效的**中途**感知：动作失败会走错误面（code 可见），但徽标要等下次
    自省（加载/设置令牌）才刷新——记录为后续可选打磨。
  - 浏览器为 in-app（隐藏），截图/指针不可用；证据=结构快照+DOM 探针+API
    复核，同 human-console 卡披露口径。
~~~
