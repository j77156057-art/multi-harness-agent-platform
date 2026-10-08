### landing-page：根路径落地页 + favicon（✅ done）

~~~text
任务编号：landing-page
负责人 Harness：Qoder
开始时间：2026-10-08 13:08
依赖任务：start-bat（✅ 70e5d03；协调者用 start.bat 起服务时发现 / 404 的体验缺口）
工作区：.worktrees/qoder/
真相源引用：R10 §5（v1 只绑 loopback——Host 校验中间件已存在，根路径同受
            其约束）；README「验证」表（链接清单的事实源）
范围声明：
  - **本切片**：`app/api/app.py` 加两个根级路由（`include_in_schema=False`）：
    ①`GET /` → 静态 HTML 落地页：平台名+一句说明、链接清单（/health 存活、
    /docs OpenAPI、/spec/status 规范一致性、/api/v1/ API 前缀说明、前端
    控制台 http://localhost:5173 与它的启动方式提示）；内联极小 CSS 沿用
    控制台主题（bone/ink/等宽）；纯静态、无 JS、不读库。
    ②`GET /favicon.ico` → 204（消除浏览器噪音 404；不放图标资源）。
    README「验证」表补一行 `/` = 入口页。
  - **显式出范围**（记录不静默）：不做控制台功能（前端 5173 才是人用的面）；
    不放真实 favicon 图形（204 即可）；不做根路径的 API 文档重定向。
  - **零迁移、零状态**（只读静态响应；Host 校验中间件对 `/` 同样生效——
    测试含非 loopback Host 拒绝断言）。
允许修改目录：backend/app/api/app.py（仅加路由）、backend/tests/**、
            README.md（验证表一行）、TASKS.md、docs/traceability.md、
            docs/tasks/notes/landing-page.md、docs/claims/qoder--landing-page.md
禁止修改目录：backend/app/routes.py 的既有动词面、backend/app/state/**、
            frontend/**、docs/spec/**、tools/**
交付分支：agent/local/qoder/landing-page/a1
完成标准：
  1. `GET /` → 200 `text/html; charset=utf-8`，含 /health、/docs、/spec/status、
     5173 四个链接/提示（pytest 断言）；不出现在 OpenAPI schema（include_in_schema）
  2. `GET /favicon.ico` → 204 空体
  3. 非 loopback Host 请 `/` → 400 `auth.host_header_rejected`（中间件覆盖
     根路径的回归断言）
  4. 实机 curl：临时实例（非 8848，避免打扰协调者正在跑的实例）下三个请求
     逐项留档
  5. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增 `MVP-UI-003`
test_id：MVP-UI-003（新登记，形态同 MVP-UI-001/002）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 13:22
commit：`a6237dc`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（format 120 文件）
  pytest      : 455 passed（本任务净增 4 项：test_landing.py），exit 0
  实机 curl    : 临时实例 127.0.0.1:8870（独立 AP_DATA_DIR，不打扰协调者
                8848 实例）逐项留档：①`GET /` → 200 `text/html; charset=utf-8`，
                响应体含 /health、/docs、/spec/status、5173 四处链接；
                ②`GET /favicon.ico` → 204 空体；③`Host: evil.example` 请 `/`
                → 400 `auth.host_header_rejected`（中间件覆盖根路径）。
                实例已停、临时数据目录已删。
已知问题：
  - 入口页里「规范一致性（90 项）」与 README 同为字面量：若 docstate 检查
    数变化，两处需要一起改（spec 总项数变化极少，暂不做模板化）。
  - 入口页是静态 HTML 字符串（不读库、无模板引擎）——单文件改动面最小；
    若以后要动态渲染（如显示最近事件），需换模板方案，属新卡范围。
~~~
