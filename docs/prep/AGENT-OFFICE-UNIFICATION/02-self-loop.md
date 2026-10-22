# Agent Office 自循环（02）

日期：2026-10-19
状态：实施中；不改冻结规范、不新增事件名/状态/迁移边。

## 1. 目标链路

```
群主（人）给出项目目标
        │
        ▼
管理员 Harness（外部 CLI）细化目标 → 分派任务
        │
        ▼
成员 Harness worker 轮询 → 领取 → 执行 → 提交 → 机器验证
        │
        ▼
管理员 Harness 领取审核 → 读产物 → 裁决
        │
        ▼
Merge Worker 合并
        │
        ▼
平台助手只巡检、只上报；Agent Office 只观察
```

群主只在两个地方输入：

1. 新建项目时选择项目名、数据源、成员；
2. 开工向导里给出一句自然语言目标，并选择管理员 Harness。

任务标题、任务内容、允许修改路径、验收口径全部由管理员 Harness
根据目标生成；前端不再提供这些输入框。

## 2. 为什么现在不是自循环

- `formal-dispatch.js` 同时承担观察面和写入面：管理员面板里有人工分派表单、
  领取审核、提交审核、人工 resume/reopen/release/cancel/adjudicate。
- 开工向导的 `onDispatch` 直接按“每个成员一条任务”写 `dispatch`，
  任务内容来自群主在项目配置里填的路径/验收，而不是管理员的细化。
- 后端只有成员 `formal_worker.py`，没有管理员 worker；审核只能由人在面板里点。
- 项目配置的“允许修改路径/验收口径”只存在浏览器 localStorage，
  管理员 Harness 读不到，等于让群主替管理员写任务规格。

结果：平台看起来有“角色”，实际每一步都要人代点；这不是角色自循环。

## 3. 本次改动

### 3.1 前端只留观察面

- `formal-dispatch.js` 改为只读任务观察：状态、指定执行者、实际持有者、
  验证回执、产物差异、事件、项目筛选、状态筛选。
- 删除管理员人工分派表单、领取审核、提交审核、人工决策按钮。
- 开工向导确认后只写一条 `project-goal` 群消息，不直接创建任务。
- 项目配置删除“允许修改路径/验收口径”；这两项由管理员 Harness 产出。
- 成员候选排除在任管理员、其它管理员窗口和平台助手 `office-admin`。
- 开工向导每次打开都重新进入配置页：同一标签页里开第二个项目时，不再沿用
  上一个项目已完成的“开工流程已交宿主”折叠页。摘要里的“每个 Harness 一条
  任务”旧文案由宿主覆盖为“只提交一条项目目标”；哈希锁定的组件文件不改。
- 保留角色在线状态、任务进度、回执/产物、平台助手上报。
- 管理员 worker 用 `start-admin.bat` 单独启动；先 `--once --dry-run` 只读检查。

### 3.2 管理员 Harness worker

新增 `app.admin.worker`，它只是管理员 Harness 的运行器，不是第二个调度器：

1. 轮询群消息，发现 `refs` 含 `project-goal` 且 `admin` 指向自己的新目标；
   首次启动默认只把游标锚定到最新消息，不重放历史目标（需要重放时显式
   `--replay-latest`）。读取消息时用 API 返回的 `sender_type` 判断是否
   `human`（写入接口的字段名是 `author_kind`，两者兼容）；只读
   `author_kind` 曾让真实群主目标被当成非人类消息而静默跳过；
2. 调用管理员自己的模型通道（`AdminService.refine`）把目标细化成任务；
   成员候选排除所有 `AP_ADMIN_SUBJECTS` 里的管理员窗口、`self.harness_id`
   以及平台助手 `office-admin`；同时排除与管理员**注册 provider 相同**的成员。
   `review_policy=cross_provider` 比较的是注册表 provider，把任务派给同 provider
   的成员只会得到一条管理员自己无法审核的任务；
3. 用管理员令牌调用现有 `/groups/{group}/dispatch` 写入任务；
4. 轮询 `awaiting_review` 任务：先读真实 diff / 验证回执并调用管理员模型，
   拿到合法裁决后才领取审核租约；
5. 用管理员令牌调用现有 `/review/submit` 提交结论。模型失败或租约被别的
   审核者占用时不写任何结论，并对该 attempt 做本地退避，避免每 5 秒重跑模型。

管理员模型输出不合法、CLI 超时、跨提供方规则拒绝时，worker 只记录并等待，
不伪造通过、不替管理员猜、不绕过 `review_policy`。codex 作为管理员通道时需要
`--skip-git-repo-check`（scratch 不是 git 仓库），已加入 codex argv 并锁进测试。

### 3.3 成员 worker 超时修复

`formal_worker.py` 在超时证据里引用了不存在的 `scoped_runner.worktree`，
导致 `AttributeError` 覆盖原始超时，任务无法按既有租约/回收路径恢复。
改为记录 `start_task` 返回的真实 worktree 路径。

### 3.4 审计归属

`dispatch.py` 的 `_approve_one` 以前固定 `decided_by_type="human"`。
管理员 Harness 分派时也必须记录为 `harness`，否则台账会把管理员动作
写成人类动作。新增可选参数，由路由按真实 principal 传入。

## 4. 不做什么

- 不新增事件名、状态、schema 或状态迁移边。
- 不改冻结 spec、不改 `contributions/office-opening-phone/office-opening-phone.js`。
- 不让 UI 代替管理员分派/审核，不让 UI 代替成员领取/提交。
- 不在 worker 里硬编码管理员身份；身份来自 `AP_ADMIN_SUBJECTS` 与令牌。
- 不自动绕过跨提供方审核规则；不能审就如实停下。

## 5. 验收

- 新建项目 + 开工向导只产生一条 `project-goal` 消息，不产生任务。
- 管理员 worker 读取目标后，任务由管理员细化并分派；任务标题带项目名前缀。
- UI 任务观察面板没有分派/审核/人工决策写入控件。
- 任务进入 `awaiting_review` 后，管理员 worker 能领取并提交模型裁决；
  模型失败时任务保持原状态。
- 成员超时证据包含真实 worktree 路径，不再抛 `AttributeError`。
- 2026-10-20 实活验收：群内 44 条任务时，两个新建项目各自只显示自己的 1 条
  任务（“当前项目 1 / 1 条（群共 44）”），旧 `GAME-COLLAB` / `112` / 另一个
  项目的任务都不进入观察面板；项目 B 的任务由管理员分派给 `claude-code`
  （provider `anthropic`），`plan.approve` 的 actor 是 `harness` /
  `codex-window-admin`，不是人类。项目 A 是修复同 provider 过滤前创建的，
  仍指派给 `codex`（provider `openai`），管理员无法按 `cross_provider` 审核，
  保持 `ready`，未擅自改平台任务。
- `python -m compileall`、相关后端测试、前端构建通过。
- 工作区容量：live 平台曾卡在 `8/8`，两条 `changes_requested` 的旧 attempt 仍
  占用 active workspace。已按平台自己的回收定义（`sweep_worktree_recycle`：
  只回收 `merged/cancelled/abandoned/changes_requested/escalated`，或已被后继
  attempt 取代的旧树）先把两棵树的目录复制到 `data/archives/<attempt_id>/`，再
  把 workspace 行标成 `archived`；活跃任务（含 `in_progress`、`awaiting_review`）
  的 6 棵树全部保留。没有改任务状态、事件或规范。
- 成员 worker 在“领取后、provision 前”失败时，原来只把 `attempt_id` 记在
  `on_claimed` 回调里，回调抛出就永远拿不到该 id，任务会停在 `claimed`。
  `CliHarnessWorker.work_on` 现在在 claim 成功、provision 前先通过
  `on_error_attempt` 把 attempt 交给调用方，`formal_worker` 用它走既有的
  abandon + 回收路径；新增回归
  `test_a_provisioning_failure_still_names_the_claimed_attempt`。
- 平台助手的面板范围此前取的是网关 `/status.project_members`。该字段是**整群**
  任务提到过的窗口并集（同一 group 下并列多个前端项目），加上 `/tasks` 也按
  group 返回，于是 44 条任务、12 个窗口（含管理员）都被算成“本项目”，出现
  “排队待领取 24”和别的项目的在办任务。现在面板与正式任务观察面板同一判定：
  当前项目的 `taskIds` ∪ 标题前缀「项目名 · 」；成员范围只取「新建项目」时
  勾选的成员；管理员窗口与 `office-admin` 不进负担表；只列真的有排队/在办/
  运行中的 harness，全零的折成一句“其余 N 个成员空闲”，积压表也只显示
  count > 0 的项。`verify-platform-assistant.mjs` 增加「本项目 0 条 / 本群共 N 条」
  与“不出现管理员窗口”的断言。

## 6. 平台级收口（2026-10-20）

### 6.1 集成检查档：`docs-check`

canonical 仓库（`canonical.git` 的 integration 分支）只有 `README.md`，而
`verify-profiles.json` 的集成档是 `backend-default`（`python -m pytest -q`）。
pytest 在没有任何测试时退出码是 5，Merge Worker 把非零当红：合入 → 红 → 回滚
→ `merge_post_commit_failure_escalated`。项目 B 的 `FD-4670…` 就是这样进
`escalated` 的；`escalated` 在状态机里没有出边，不能重试也不能取消。

处理：

- 新增集成档 `docs-check`，脚本 `backend/app/verify/docs_profile.py`。它检查
  canonical 仓库真正承诺的东西：`README.md` 存在、每个 Markdown 是合法
  UTF-8、没有空 Markdown；不联网，也不读仓库之外的状态。
- `verify-profiles.json` 顶层 `integration_profile` 指向 `docs-check`。该文件
  注明须人类维护，本次按用户明确指示修改。
- 只影响之后的合并；B 已经 `escalated`，不会被这条改动救回。
- 测试：`tests/test_docs_profile.py` 钉住通过 / 缺 README / 空文件 / 非法
  UTF-8 / 无 Markdown / 忽略 `.git` 六种判定；对 `a329fbd` 解包实测
  `docs-profile: ok`（同一棵树跑 pytest 是 `no tests ran`、exit 5）。

### 6.2 管理员取消：`task:cancel` 委托

项目 A 是修同 provider 过滤前建的，指派给 `codex`（与管理员
`codex-window-admin` 同 provider），`cross_provider` 永远审核不了。§17 的
`task:cancel` 只给 HUMAN，`HumanOpsService.cancel` 还把 `actor_type` 写死成
`human`：管理员 harness 取消会把台账写成“人类取消”。

处理：

- `ADMIN_HARNESS_KEYS` 增加 `task:cancel`，只对 `AP_ADMIN_SUBJECTS` 里的
  管理员 harness 生效；成员 harness 仍是 `role_not_allowed`。
- `HumanOpsService.cancel` 增加 `actor_type` 参数（默认 `human`），人类路由
  行为不变。
- 新增 `POST /tasks/{task_id}/cancel`（management 路由），用
  `_actor_type(principal)` 记录真实主体。没有新增事件名、状态或迁移边：用的
  就是既有的 `ready → cancelled`（`CANCEL`）。
- 管理员 worker 增加 `--cancel-task <id> --reason <text>` 一次性入口，由管理员
  harness 进程自己发起取消，不由助手代点。
- 测试：`test_human_ops.py::test_cancel_records_the_actor_type_it_was_called_with`
  钉住事件里是 `harness/codex-window-admin`；`test_admin_harness.py` 钉住
  管理员可用、成员不可用。

### 6.3 集成健康度的恢复出口：`--recheck-health`

R10 §21 把非绿的 `integration_health` 定成整个项目合并队列的
stop-the-line，而回到绿色的唯一途径是"再跑一轮合并/回滚"。集成检查档配错时
这就是死锁：队列被停掉，而唯一能解开它的合并又恰好被停掉——项目 112 就卡在
这里，B 的 `escalated` 只是表象。

处理（操作员侧，窄口径）：

- `MergeWorker.recheck_integration_health(project_id, apply=False)`：在 canonical
  当前 tip 上跑**配置好的那个**集成检查档（与合并闸门同一条命令）。
- 默认只验证、不写任何东西；`apply=True` 时**只有检查通过**才把投影写回
  `green`，失败则原样不动。
- 没有配置集成检查档时**拒绝**执行：没跑过检查就没有资格宣布队列健康。
- 不写事件：`integration_health` 是投影，§21 没给它事件。
- 出口是 `python -m app.runtime --recheck-health <project_id> [--apply]`；
  退出码 0 = 通过，1 = 跑了但失败，2 = 拒绝。
- 实测：项目 112 先只读跑（`result: passed`、`applied: false`、行未动），确认
  后 `--apply` 把 `112` 写回 `green`（`integration_commit = a329fbd`）。
- 测试：`tests/test_integration_health_recheck.py` 钉住只读不改行、通过才写绿、
  失败不写绿、无健康行拒绝、无检查档拒绝；`tests/test_runtime_driver.py` 钉住
  CLI 的三种结局（只读 / 应用 / 失败退出 1）。

这条出口是平台能力的新增，属于待规范复审的项（与 6.2 的 `task:cancel` 委托
同批），**未宣称已获批准**。

## 7. 工位屏幕接平台回执（2026-10-20）

办公室 3D 场景里每个工位的显示器，之前画的是**写死的演示文案**（`SIMULATED`、
`$ ON BREAK / LOCAL DEMO / NO BACKEND CONNECTION`）。那条路子的含义是"看起来
像在干活"，不是"真的在干"。

现在屏幕接的是平台回执：

- 新增 `src/harness-screens.js`：每 10 秒读一次 `/api/formal/tasks`（群任务表）
  与 `/api/formal/tasks/{id}`（任务 + 改动 diff + 事件流水），为每个工位拼一段
  屏幕文本：任务号、标题、状态、attempt/epoch、改动文件数与增删、最近 5 条事件
  （带时间与真实 actor）。
- 选任务的顺序：先取该 harness 的在办任务（owner 或指派），没有就取它最近一条
  任务并**明说**"该任务当前不在执行中"。
- 手工绑定终端会话时，终端帧优先（那才是真正的终端文本）；平台帧只在没有绑定
  时补位，不覆盖用户已绑定的画面。
- 屏幕没有在办任务时显示"当前没有与该 harness 相关的任务"，不编造。
- 工位状态（编码中/休息中）也改为按平台回执判定：有在办任务就是"编码中"。
- Godot 侧（`godot-office/office25.gd`）区分三种来源：`TERMINAL`（绑定终端）、
  `LIVE · 平台回执`（平台回执）、`DEMO`（演示源）；没有绑定时才落到演示文案。
  改完重新导出了 Web 产物（`public/godot/index.pck`）。
- 顺带修了一个真 bug：点工位会崩（`workforce-fixture.js` 只认 6 个示例 harness
  id，正式平台成员没有 `skills`/`persona` → `m.skills.join` 抛错），现在缺就写缺。
- 验证：`scripts/verify-harness-screens.mjs`（`npm run verify:harness-screens`）
  断言有在办任务的工位屏幕里能读到平台真实任务号、屏幕上没有 `SIMULATED` /
  `LOCAL DEMO`、HUD 写明「平台实时回执」；原有 `verify-platform-assistant`、
  `verify-project-scope`、`verify-admin-scope`、`verify-formal-dispatch-ui`、
  `verify-opening-host` 全部照旧通过。

已知边界：Godot 场景只有 6 块带屏幕的工位，平台在册 9 个成员，多出来的 3 个
工位没有显示器（场景限制，未改）；真正的终端画面仍需侧车（`127.0.0.1:879x`）
在位时用「实际工作输出」手动绑定。

## 8. harness 的"原话"：CLI 输出边跑边留痕（2026-10-20）

第 7 节的屏幕显示的是**回执**（任务、状态、改动、事件），它能说"这个 harness
做过什么"，不能说"它现在在说什么"。原因是执行面本来就是无头的：member worker
把 CLI 作为子进程拉起来，child 的 stdout 只落在 worker 的临时文件里，**跑完才
读**——一次跑了六分钟的 CLI，在平台的记录里留下六分钟静默（失败时才把尾巴写进
failure 证据）。

补上这一段，屏幕就能显示 harness 自己的原话：

- `backend/app/adapter/cli_output.py`：日志文件名的唯一出处
  （`data/formal-runs/<task_id>.<attempt_id>.log` / `.err.log`，按 attempt 命名，
  重放会续写同一份）。
- `backend/app/adapter/harnesses/cli.py`：`SubprocessRunner` 边跑边把 child 的
  stdout/stderr 从临时文件**增量抄**进日志文件（`_drain`），child 退出后再补
  最后一轮；临时文件仍是结果的唯一真相，日志只是给读者看的实时副本。
  `CliHarnessWorker.work_on` 把日志路径交给 runner。
- `GET /api/v1/tasks/{task_id}/output`（只读）：交出当前 attempt 的 stdout /
  stderr 尾巴、字节数、行数与更新时间；没有 attempt、文件不存在、或还没输出，
  都如实返回空，不报错。
- 网关 `/api/formal/tasks/{id}/output` 透传（限本 group）。
- 前端：屏幕在回执之后追加「CLI 实时输出（stdout 尾部 N 行 / M 字节）」，原话
  放在最后是因为 Godot 只显示文本尾部；没有输出时回落到第 7 节的回执视图。

测试：`tests/test_cli_output.py`（日志命名、缺文件如实为空、尾部行数、真 child
的输出被抄进日志、读路由三种情形）、`test_real_adapters.py` 新增
`test_the_worker_streams_the_cli_output_into_the_data_dir`（worker 交出的路径
与落盘内容）、`scripts/verify-harness-screens.mjs` 追加断言（按 worker 的写法落
一份标记输出 → 平台路由交出它 → 屏幕出现这段原话 → 清理）。

这一段不新增事件、状态或迁移边，也没有新增权限键：读的是 worker 自己写的
文件，路由挂在既有 `read` 组下，属于只读观察面。

### 8.1 claude-code 改用流式输出（否则"实时"是假的）

真跑一次 `claude` 才发现：旧的 `--output-format json` 是**结束时才写一个
对象**——用平台自己的 `SubprocessRunner` 跑，5 秒时日志 0 字节，退出时才有
2039 字节。也就是说 tee 机制是对的，但那个 CLI 本身不说话，屏幕还是只能等
到结束才有一行。

换成 `--output-format stream-json --verbose`（`--print` 下 `stream-json` 必须
配 `--verbose`，CLI 否则直接拒绝）之后实测：6 秒时已经 40 KB，30 秒时 47 KB，
共 207 个事件（`system` / `assistant` / `user` / `result`），最后那个 `result`
事件仍然带 `result`（最终消息）、`session_id`、`total_cost_usd`、`is_error`
——与从前单个对象提供的字段一致。

因此：

- `app/adapter/harnesses/claude_code.py` 的 argv 改为
  `-p --output-format stream-json --verbose --permission-mode <mode>`；
- `parse_output` 改为读最后一个 `result` 事件（`type == "result"`），同时仍然
  接受单个 JSON 对象（老 CLI / 只输出一行）与跳不过的脏行；
- 测试：`test_real_adapters.py` 的 argv 契约与 stdout 夹具改成流式，新增三条
  （读最后一个 result 事件、兼容单对象、跳过脏行）。

这是适配器实现细节（输出格式），不涉及事件、状态、schema 或权限键；但它改动了
被测试钉住的 argv 契约，属于**刻意**的契约变更，已在此登记。

### 8.2 codebuddy：同样的流式切换 + 一个 Windows 真 bug

codebuddy 的 `--help` 里写明 `--output-format` 支持 `"json" (single result)`
与 `"stream-json" (realtime streaming)`，实测 `--output-format stream-json` 也
成立：12 行 / 16 KB，事件类型与 claude 相同（`system` / `assistant` / `user` /
`result`），最后一个 `result` 事件带 `result` / `session_id` / `is_error`。
它的 `parse_output` 本来就遍历整份 transcript 里的 JSON 载荷，所以换成流式
之后不用改解析。

顺带修了一个真 bug：`shutil.which("codebuddy")` 在这台机器上先命中 npm 装的无
扩展名 bash shim（`...\npm\codebuddy`），Windows 根本起不来这种文件——child
直接 "system cannot find the file"。现在在 `os.name == "nt"` 时优先用同名的
`.cmd` / `.exe` / `.bat`。

未改：**qoder**（这台机器上没装，无法验证它的流式格式，不能靠猜改契约），
以及 **opencode**（已经是 `run --format json`，需要实测它的流式形态再动）。

### 8.3 qoder 也装了（先前是我没找到）：它的流式形态与 claude 同构

qoder CLI CN 装在 `~/.qoder-cn/`（`entry/qodercn.cmd`、`bin/qoderclicn/qoderclicn.exe`，
版本 1.1.71）——不在 PATH 上，但适配器本来就有这个本地兜底路径
（`~/.qoder-cn/bin/qoderclicn/qoderclicn.exe`），所以能起得来。

实测（真 CLI）：

| 输出格式 | 3 秒 | 6 秒 | 12 秒 | 15 秒 | 结束 |
|---|---|---|---|---|---|
| `json` | 0 | 0 | 0 | 0 | 21.2 秒时才 1598 字节 |
| `stream-json` | 0 | 3599 | 7200 | 10033 | 14.2 秒，共 13 行 |

`stream-json` 的事件类型与 claude 相同（`system` / `assistant` / `user` /
`result`），最后一个 `result` 事件带 `result`（最终消息）、`session_id`、
`is_error`、`total_cost_usd`——与单对象格式的字段一致。它还有两处不同：

- **不接受 `--verbose`**（claude 需要）：`error: unknown option '--verbose'`；
- 13 行里有 3 行是纯文本通知（`Skill command '/goal' was renamed to '/goal1' ...`），
  不是 JSON。qoder 的 `parse_output` 本来就逐行解析并跳过非 JSON 行，所以解析
  不用改；夹具里留了这种行，防止以后有人把它当 JSON 解析。

因此 qoder 的 argv 从 `--output-format json` 改为 `--output-format stream-json`，
解析不动，测试新增 `test_qoder_reads_the_final_result_event_of_the_stream`。

### 8.4 opencode：启动路径是通的，但这台机器上它的模型调用本身是坏的

用**适配器自己的启动路径**实测（Windows 走 `cmd.exe` bundle：把工单写进文件、
命令行只带固定句子，因为 `cmd.exe` 的 `/c` 不保留换行）：

- 进程起来了，6 秒时 stdout 已有 760 字节——**运行中确实有输出**；
- 但内容是一个错误事件：`provider.invalid-output`
  （"Invalid 1/openai-compatible-chat stream event"，模型是 `deepseek-v4.1-flash`）。

也就是说这台机器上 opencode 的**模型调用**是坏的（provider 侧），不是输出格式
的问题；它成功运行时 stdout 是不是逐行流出来的，量不到。它的 `run --format`
只有 `default` / `json` 两个选择，没有 stream 选项，所以 argv 保持不变
（`--format json`），办公室照常显示它写出来的任何东西。

至此五个 CLI 的实测结论：

| CLI | 输出格式 | 运行中是否出话 | 处理 |
|---|---|---|---|
| claude-code | `stream-json --verbose` | 是（6 秒 40 KB） | 已切换 |
| codebuddy | `stream-json` | 是（6 秒 3.6 KB） | 已切换 |
| qoder | `stream-json` | 是（6 秒 3.6 KB） | 已切换 |
| opencode | `run --format json` | 未知（本机 provider 坏了） | 保持 |
| codex | `exec --json` | 未测（本机没装） | 保持 |

## 9. 真跑一次之后才知道的三件事（2026-10-20）

### 9.1 恢复一个租约已过期的 attempt，会在第一次心跳死掉

用一次性恢复入口拉起 `FD-75cea5643546153a`（claude-code 的 attempt .a1，租约
4 小时前就过期了）：

- CLI **真的跑起来了**：20 秒内日志 330 KB / 935 个事件，屏幕能看见它在读
  `src/tank.js`、`src/game.js`；
- 第一次心跳（20 秒一次）被平台拒绝：`lease.wrong_owner (403)`；
- worker 按既定路径放弃 attempt（事件 613 `abandon`，reason
  `formal worker error: HarnessLeaseLost`），任务进入 `abandoned`。

根因不是 bug，是**刻意的设计**（`app/claim/service.py` 的注释写得很清楚）：
"Only the hash is stored, so a replay cannot hand the original token back -- it
mints a new one instead. An expired lease is not reissued: the attempt is about
to be swept to stale and must go through reclaim, not keep its dead credential
alive."

也就是说：**租约过期的 attempt 必须走 reclaim（清扫器）这条正路，不能靠重放
claim 复活**。而这条正路此前根本没在跑——`app.scheduler --loop` 没有常驻，
所以过期的 attempt 就一直躺在 `in_progress` 里。

### 9.2 清扫器一常驻就立刻收了僵尸

把 `python -m app.scheduler --loop` 起起来（平台进程，不是角色动作）后，
`FD-77ce87a833855469` 立刻被标 `stale`（事件 614 `lease_expire`，actor
`scheduler/lease-sweeper`）——这是本次会话第一次有僵尸 attempt 被清掉。

清扫器、Merge Worker 驱动（`app.runtime --loop`）、管理员 worker 现在都在跑。

### 9.3 死掉的 attempt 说了什么，也得能看见

任务进了终态（`abandoned`）之后 `current_attempt_id` 会被清空，路由就取不到
那段 transcript 了——而那正是人最想看的东西（"它临死前在干什么"）。因此
`GET /tasks/{id}/output` 在没有当前 attempt 时**退回该任务最近一次 attempt**，
并在返回里带 `current: false`，前端据此写「CLI 最后一次 attempt 输出」而不是
「实时输出」，不把死掉的 attempt 说成活的。

前端还改了两处：

- 挑任务的顺序改成：在办 → **跑过但没在办**（`attempt_no > 0`，死掉的那条
  transcript 就在磁盘上）→ 待领取 → 最近。否则屏幕永远只显示一条 `ready`
  的空任务，看不到任何原话；
- Godot 屏幕原来只显示文本的**最后 1200 字符**，长 transcript 会把抬头（谁、
  哪条任务、状态）挤出画面。现在显示**头 620 + 尾 460**（中间标注省略），
  抬头和最新的话同时在屏上。

验证：`scripts/verify-harness-screens.mjs` 现在用**真的** transcript 来证——
从平台数据目录里真跑过的那份日志尾部取一个记号（本次是 claude 的
`thinking_tokens`），断言屏幕正文里出现它；再加一条标记输出的写入路径检查
（写完即删）。两项都通过。

`FD-75cea5643546153a` 现在是 `abandoned`（终态）。要再跑它需要人类执行
`task:reopen`（`abandoned --reopen--> ready` 这条边存在，但 `task:reopen`
按 §17 只给人类）；平台助手与 UI 都不提供这个写入面。

## 10. 手机上的群聊混入其他项目历史（2026-10-20）

手机（办公室右侧）的「正式读取」把**整群**的消息和任务都摆出来：平台的「项目」
后端其实是一个 group，同一个 group 下并列多个前端项目（GAME-COLLAB、112、
UI-ISOLATION-…），于是别的项目的派发记录整段混进当前项目的群聊（截图里是
"已向 codebuddy 派发任务（GAME-COLLAB）：做一个坦克大战游戏"）。

修法与任务观察面板、平台助手同一判定（`src/office-data-source.js`）：

- 范围 = 当前项目的 `taskIds` ∪ 标题前缀「项目名 · 」；
- 消息：带 `task_id` 的只保留本项目任务的；没有 `task_id` 的（开工目标、管理员
  细化、群聊）只在点到本项目时算——`project-goal-json:` 信封里的 `project_name`
  等于项目名，或正文点到项目名；
- 任务表同样只列本项目的；
- 抬头从「44 项任务」改成「本项目 N 项任务（本群共 M 项）」，不再把整群说成
  本项目。

数据源新增 `project: () => activeProject` 入参（与平台助手一样），换项目后
下一轮读取按新范围重算。

验证：`scripts/verify-phone-project-scope.mjs`（`npm run verify:phone-project-scope`）
用一个真实存在的项目名（GAME-COLLAB）断言 27 条消息都属于它、任务表条数等于
本项目条数；再用一个谁都不匹配的项目名断言 0 条消息、0 项任务（不泄漏别处
历史），并断言抬头写成「本项目 0 项任务（本群共 44 项）」。

### 10.1 顺带查出来的两处慢：手机为什么老停在「正在读取」

手机那几秒一轮的只读列表，实测一次要 1.6–2.2 秒，于是状态长期显示「正在读取
正式平台…」，看着像卡住。两处原因：

- **网关的 `/tasks` 给 44 条任务各拉一次校验回执**（44 个请求）。手机的任务卡
  根本不显示校验回执，于是网关加了 `?basic=1` 跳过它（正式任务观察面板仍走
  默认路径，仍看得到回执）。
- **平台把标题逐条读**：`_add_task_title` 每条任务读一次 plan snapshot，而
  平台的读助手**每个查询都开一次连接**（`Database.read` → `connect()`），44
  条就是 45 次连接 ≈ 350 ms。改成**一次查询**读出这批任务的全部快照再投影标题
  （`SchedulerService._titles_for`），同样的 44 条现在 130–600 ms，其中标题
  一个不少。

另外，手机的抬頭在已经有一次成功读取之后，不再被下一轮在飞的请求写成「正在
读取」——面板上仍是上一次的真实内容，状态就照实说。

（这两处都只是读路径的快慢与措辞，没有改事件、状态、schema，也没有改任何
权限键。）

## 11. 自循环验收：新项目走完全程（2026-10-20）

新项目「自循环验收-1020」从开工到返修，整条链真的跑了一遍：

1. **群主**在办公室开工向导里选管理员（`codex-window-admin`）+ 9 个成员 + 写目标
   → 办公室只发**一条** `project-goal` 消息（seq 620，refs 带 `project-goal-json`
   信封）。
2. **管理员 harness 第一次细化失败**：`admin harness CLI unavailable: codex: not
   found on PATH`。codex 装在 `AppData/Local/OpenAI/Codex/bin`（不在 PATH 上），
   适配器现在会自己去那个安装目录找（`_codex_binary`，与 codebuddy/qoder 同形）。
3. 管理员**没有猜**：它反问两个问题（"要求有歧义，先问清楚，不猜"），群主在群里
   回答（seq 623，办公室发的，人类消息）→ 管理员再细化 →
   **4 个不同的子任务**，各有各的标题/目标/允许路径/执行者：
   `盘点权威来源`（claude-code）、`建立工位屏幕与回执字段映射`（codebuddy）、
   `规范 CLI 原话与缺字段标注`（opencode）、`编写对照说明`（qoder）——
   **不是 9 份一模一样的"做一个游戏"**。
4. **成员自己领取**（`claim` by `harness/claude-code`）→ 真的 claude CLI 在自己的
   worktree 里干活 → 办公室屏幕**实时**显示它的原话（40 秒内 260 KB → 588 KB）。
5. 平台代提交时撞上一个真 bug：成员**只**写了允许路径里的那个文件，平台却报
   `platform commit refused: dirty path outside exclusive_paths`——因为
   `git status --porcelain` 默认把非 ASCII 路径**加引号转义**
   （`"docs/prep/\350\207\250..."`，带空格的路径也加引号），范围检查拿转义后的
   字符串去比对原始路径，必然对不上。修法：平台每次调 git 都带
   `core.quotepath=false`（`app.git.repo.run_git`），并且在范围检查前把仍带引号的
   路径还原（`formal_worker._unquote_git_path`；git 的八进制转义是**字节**，要按
   UTF-8 解）。回归：`tests/test_git_quotepath.py`。
6. 提交 → 校验通过 → **管理员审核** → 要求返修（具体指出交付物里还留着
   "相关 CLI 实现文档"这类占位符，major）→ 成员 `claim_for_revision` 返修 →
   再提交 → 再审 → 仍要求返修（新的具体意见）。返修循环是通的。
7. 管理员还派出了第 4 个任务（qoder）。

### 11.1 这一轮暴露的、还没解决的事

- ~~**管理员把任务派给了没有凭据的窗口**~~ → **已修（见 12）**。
- **opencode 的 provider 在这台机器上是坏的**：`provider.invalid-output`（上游
  返回了 CLI 不认的流事件），所以派给 opencode 的那条任务跑不动。
- **模型上游不稳**：细化卡过一次（600s 超时，relay 流没结束），审核时流断过
  5 次重连。平台都如实报了（超时/失败），没有假装成功。
- **网关与平台的一处不一致**：办公室网关的 `POST /api/formal/human/reopen` 返回过
  403，但平台那边**已经执行了**重开（事件 635 `reopen` by `human/office-operator`）。
  这条要查：要么网关吞了平台的成功响应，要么两边对"谁有权重开"的理解不同。
- 成员的交付质量是另一回事：管理员两次都要求返修，因为成员把"相关 CLI 实现
  文档"当占位符交上去了——那是成员的问题，平台如实拦住了。

## 12. "登记在册" ≠ "能领活"（2026-10-20）

管理员把两个子任务派给了 `codebuddy` 与 `qoder`——它们**登记在册**（所以进了
办公室的成员表），但平台**没有它们的凭据**（真正跑 worker 的是
`codebuddy-member-1` / `qoder-member-1/2`），那两条任务永远停在 `ready` 没人能领。

现在"能不能干活"是平台的一等事实：

- 平台的 harness 列表多了一个 `credential_count`（该窗口有多少张未吊销的
  harness 令牌）；`app/adapter/registry.py` 的 `listing()` 一次查出来。
- **管理员的细化**只把**能认证**的成员交给模型（`AdminService.refine`）：
  登记在册但没凭据的窗口不进候选；一个都不能干时**明确失败**，不派给一个永远
  领不了的窗口（错误信息里说清楚是"没有凭据"还是"同 provider 无法交叉审核"）。
- **办公室**两处成员表都只列能领活的窗口：新建项目的成员选择器、开工向导的
  成员 chip（管理员候选同样只列有凭据的窗口——管理员也必须能动作）。范围行
  写明"另有 N 个窗口登记在册但没有平台凭据，领不了活"。
- 网关把 `credential_count` 透传给前端。

实测（本机 12 个登记窗口）：有凭据 10 个（claude-code、codebuddy-member-1、
codex、codex-window-admin、office-admin、opencode×2、pi×2、qoder-member-1/2、
qoder-window-admin），**没有凭据的正好是 codebuddy 与 qoder**——就是那两条死
任务的执行者。办公室的成员选择器现在只列 7 个可领活的成员窗口（排除管理员
窗口），范围行写"7 个可领活的成员窗口（另有 2 个窗口登记在册但没有平台凭据，
领不了活）"；开工向导的成员 chip 与管理员候选同样只列能领活的窗口。

最后一道闸在**派单**这一层：`app/adapter/dispatch.py` 现在**拒绝**把任务派给
没有平台凭据的窗口（`target Harness holds no platform credential, so it could
never claim this task`），不留半条计划。一条永远没人能领的活，比一条当场报错的
派单坏得多。

回归：后端 `tests/test_admin_harness.py` 新增两条（没有凭据的成员**不进**提示词
的候选；一个能领活的成员都没有时细化**明确失败**），`tests/test_formal_dispatch.py`
新增一条（派给没有凭据的窗口被拒，且不留下任务/计划），夹具也改成"注册即发凭据"
（真实部署就是这样）。

## 13. 管理员的"改派"（2026-10-20）

12 节把"能领活"变成了硬事实，但已经派出去的死任务还在 `ready`。管理员需要一个
**改派**动作把它们指向能领活的窗口。

实现刻意**不新造事件名或状态边**：改派 = 取消旧任务（`cancel` 边本来就有）+
按同样的规格派一条新的（`dispatch` 也是既有动作），两步各自留痕，群里再补一条
回执说明为什么改。

- `AdminService.assignable_harnesses()`：**候选只有这一处定义**（在线、已登记、
  **有凭据**、不是管理员窗口、provider 与管理员不同），细化与改派共用，不会各说
  各话。
- `AdminService.dispatched_task_ids()`：改派**只动管理员自己派出去的任务**
  （`command_idempotency` 里 `admin-*` 的请求 id）。别人的历史活不是管理员该
  动的东西——第一版就是因为没限定范围，把整群的历史任务都改了，还全堆到一个
  窗口上。
- `AdminWorker.reassign_dead_tasks()`：每轮扫 `ready` 任务，执行者没有凭据就
  改派；候选按**当前负载**挑（最闲的窗口），别把活全压给一个人。
- `AdminWorker.reassign_task(task_id, target)` + CLI
  `--reassign-task <id> --to <harness_id>`：一次性手工改派（自动扫描只看
  `ready`；已经不在 ready 的任务靠它）。
- 回执里的理由**如实**：原执行者没有凭据，和"按负载重排"，是两回事。

实测（自循环验收-1020 的四条）：`盘点权威来源`（claude-code，返修中）、
`建立工位屏幕与回执字段映射` → 改派给 `codebuddy-member-1`、
`规范 CLI 原话与缺字段标注`（opencode）、`编写对照说明` → 改派给 claude-code。
四条现在都有能领活的执行者，群里各有一条改派回执。

回归：`tests/test_admin_worker.py` 新增三条（执行者没凭据 → 改派；执行者能领活
→ 不动；**不是管理员自己派的任务 → 不动**），`tests/test_admin_harness.py`
新增"没有凭据的成员不进候选 / 没有能领活的成员时明确失败"。

## 14. opencode 的 provider：远端 relay 拒了，改走本机 Ollama（2026-10-20）

派给 opencode 的那条任务一直跑不动，报 `provider.invalid-output`。查下来是
**远端 relay 的 chat/completions 路径**的问题，不是平台的问题：

- `opencode` 的配置（`~/.config/opencode/opencode.json`）默认走
  `https://790053500.com/v1`（provider `1`，模型 `deepseek-v4.1-flash`）；
- 逐个测下来：
  - provider `1` 的 key → **502 "Upstream access forbidden"**（上游直接拒绝）；
  - provider `2` 的 key → **404 "Model … is not supported by any configured
    account in this group"**（能认证，但那个模型没开）；
  - `minimax-cn/MiniMax-M3.1-Flash` → **402 "insufficient balance (1008)"**；
  - 本机 **Ollama**（`127.0.0.1:11434`，`qwen3.6:35b-a3b` / `-agent256k`）→ **通**；
- codex CLI 一直能用，是因为它走的是 `/responses`（本地代理 `127.0.0.1:8402` →
  `wire_api = "responses"`），而 opencode 的 openai-compatible provider 走的是
  `/chat/completions`——上游对后者关着门。

改法（`~/.config/opencode/opencode.json`，原配置已备份为
`opencode.json.bak-20261020`，旧的 provider 全部保留）：

- 新增 provider `local`：`@ai-sdk/openai-compatible` → `http://127.0.0.1:11434/v1`，
  `apiKey: ollama`；
- **options 里 `num_predict: 16384` 且 `think: false`**：默认那个思考模型会把每
  一步的预算烧光，于是每一步都以 `reason: length` 结束，一行交付物都交不出来
  （第一次跑就是这样失败的：`no committed change against base`）；
- 默认模型改为 `local/qwen3.6:35b-a3b-agent256k`。

实测：opencode 能正常作答（67 秒）、**能写文件**（`hello.txt`，53 秒），并且平台上
那条任务第一次走完了全程——

```
claim(opencode) → 真 CLI 干活（42 KB 原话实时落盘）→ 平台代提交 → 校验通过
→ 管理员审核通过 → Merge Worker 合入（merge_success）
```

交付物真的进了 canonical 的 integration 分支：
`docs/prep/自循环验收-1020/cli-and-missing-field-guidance.md`。这是本次会话第一
条**从领取到合入全程走通**的任务。

代价要说清楚：本地 35B 模型比远端 flash 慢很多（单步 50–100 秒），一条文档任务
要跑好几分钟；而且它只在本机 Ollama 起着的时候可用。远端 relay 恢复
（`/chat/completions` 不再 502）之后，把默认模型切回 `1/deepseek-v4.1-flash` 即可，
配置里两个 provider 都留着。

### 14.1 更好的一条路：opencode 自带的免费档

`opencode models` 里本来就有 **`opencode/minimax-m3.1-flash-free`**（opencode 自己的
免费额度），之前没试。试下来：

| 模型 | 结果 |
|---|---|
| `opencode/minimax-m3.1-flash-free` | **通，4.8 秒**，能写文件（5.7 秒） |
| `opencode/minimax-m3` | 402 余额不足（这个不免费） |
| `local/qwen3.6:35b-a3b-agent256k`（本机 Ollama） | 通，但 50–100 秒一步 |

于是默认模型改成 **`opencode/minimax-m3.1-flash-free`**：比本地 35B 快一个数量级，
也不依赖本机 Ollama 起着。`local`（Ollama）那条留在配置里当**兜底**——免费档额度
用完时，本地还能顶上。

### 14.2 顺带查清：claude-code 的 provider 是**余额不足**

claude-code 那条任务跑了 12 分钟、写了整份文档，最后一步炸在
`API Error: 402 Insufficient Balance`（`~/.codex/config.toml` 里
`ANTHROPIC_BASE_URL=https://geekspace.cloud` 那个账户）。也就是说**同一批 relay
账户里，codex 用的那个还有额度，claude/relay/opencode 直连用的那些没了**。

试过用本机 Ollama 的 Anthropic 兼容口（`/v1/messages`，返回 200）接 claude CLI：
**不通**——那个思考模型只给 thinking 块，claude CLI 拿到 0 output tokens。所以
claude-code 要么等账户充值，要么换一个不思考的模型（本地目前只有这两个 35B 思考
变体）。

在账户恢复之前，管理员用**改派**把活交给能干的窗口：项目里剩下的两条
（`盘点权威来源`、`编写对照说明`）都改派给了 opencode（免费档）。

### 14.3 opencode 还有一个坑：它会**连到桌面版的后台服务**

`opencode run` 默认 `local=false`，也就是**去连后台服务**。这台机器上跑着
**OpenCode 桌面版**（`@opencodedesktop\OpenCode.exe`，17:57 启动），它把共享的
`~/.local/share/opencode/opencode.db` 迁到了新 schema（`session_v2` 那一套），
而 PATH 上的 CLI 是 **2.0.22**（要 `session` 表）——于是每次运行都在
"Database is not empty and has no session table" 上死掉，而且**先静默好几分钟**
（CLI 在等一个建不出来的会话）。

实测：`--standalone`（CLI 自己起私有服务）就不碰那个库，4.6 秒正常作答。
所以适配器的 argv 加了 `--standalone`（两条启动路径都加：bash shim 与 cmd.exe
bundle）：

```python
argv = ["opencode", "run", "--standalone", "--format", "json", "--auto", work_order]
```

无头的 worker 本来就该用自己的服务，不该去attach一个交互式服务——那既会撞
schema，也会撞上"服务正忙"。回归：`test_real_adapters.py` 的 argv 契约与
`test_opencode_launch_shim_per_platform` 一起更新。

## 15. 失败可重试 + 保留现场 + 断点报告（2026-10-20）

之前"CLI 失败 = 这条活死掉"：成员 worker 走 ``abandon``，任务进终态，只有
人类能 ``reopen``。于是"provider 挂了"这种**跟活无关**的失败，会把一条好端端
的活判死刑。

规范里其实**早就有这条路的词**：

- ``RELEASE`` 边：``in_progress / claimed / needs_help / blocked /
  changes_requested`` → **``ready``**（"owner yields voluntarily with no
  uncommitted output"），``task:release`` 权限**本来就发给 harness** ✓，
  适配器协议里也有 ``release()`` ✓；
- ``REOPEN`` 边：``abandoned`` → ``ready``，理由写的是 "human **or scheduler**
  approves reopen"——**scheduler 也能重开**。

所以这一步是**接线**，不是新造状态或事件：

- `CliHarnessWorker.work_on(..., abandon_on_failure=False)`：CLI 挂了之后
  "这条活算不算死"交给调用方判（成员 worker 传 False，其它调用方默认保持旧的
  abandon 行为）。
- `formal_worker._classify_failure()`：把失败分成两类——
  **可重试**（harness 或它的 provider 挂了：CLI 非零退出、超时）和
  **不可重试**（平台自己拒绝了越界产物 "Refused:"、CLI 根本不在 PATH 上、
  attempt 预算用完）。可重试 → ``adapter.release()`` 放回队列；不可重试 → 仍
  ``abandon``。
- **保留现场**：可重试的失败**不立刻回收 worktree**；按 R10 §14 的规定，清扫器
  会把旧 attempt 的 worktree 归档留档（``data/archives/<attempt_id>/``），现场
  不会消失。
- **断点报告**：失败时写 ``formal-runs/<task>.handoff.json``——错误类型、错误
  原文、CLI 的 stdout/stderr 尾巴、动过的文件、与 base 的 diff 摘要、归档路径。
- **报告随工单转交**：`build_goal(..., handoff=…)` 把报告附在下一个 attempt 的
  工单里（"The previous attempt did NOT finish; here is its breakpoint report.
  Continue from it instead of starting over"）；管理员**改派**时也把上一份报告
  写进新任务的目标里，于是接手方开工就看得见上一个 harness 死在哪。

实机验证（claude-code 的 provider 挂着，正好是"可重试"的典型）：

```
claim → start → CLI 失败（provider 503）
  → release（事件 880，harness/claude-code）→ 任务回到 ready ✓
  → formal-runs/FD-fd99….handoff.json 落盘 ✓
改派给 opencode → 新任务的工单里带着报告（含 CLI 的最后一句话：
  "…check your inference gateway (www.fhl.mom)"）✓
```

回归：`tests/test_failure_retry.py` 九条（分类五种 + 报告与工单注入），另加
`test_real_adapters.py` 里"失败不自己 abandon"的用例。

### 15.1 顺带修：事件载荷不能是整个 transcript

实机验证时发现：一次 attempt 的 CLI 输出有 **1.34 MB**，被整个塞进
``submit_result`` 事件的 ``completion`` 里 → 这条任务的事件流 2 MB → 办公室读
这条任务时网关直接 **502**（`response_too_large`）。

- 事件日志是**审计轨迹**，不是 transcript：完整的话在
  ``data/formal-runs/<task>.<attempt>.log`` 与运行证据里，事件只留开头
  （``_summarize_output``，单字段上限 4000 字，超了写明截断多少）。
- 网关的响应上限从 1 MB 提到 8 MB（一次 200 条事件的页要装得下；上限仍在，
  免得把失控的上游原样转发）。

回归：`tests/test_event_payload_size.py` 四条。

## 16. 「谁现在能用」：模型 + 凭据 + 最近失败（2026-10-21）

第 1 步落地后剩的问题很具体：**provider 挂掉之后，人问"那现在谁能干活？"，平台
答不上来**。第 2 步做的是把这个答案做出来——**只读**，不派单、不改派、不换模型。

### 16.1 模型是"部署事实"，不是"平台事实"

平台手里**没有模型凭据**（模型凭据在各 CLI 自己的配置里）。所以平台不猜模型，
它读**同一个 CLI 会读的配置文件**，把配置里写的原样报出来：

| 窗口 | 配置来源 | 报出来的模型 |
| --- | --- | --- |
| claude-code | ``~/.claude/settings.json`` → ``model`` | ``claude-fable-5-1`` |
| codex | ``~/.codex/config.toml`` → ``model`` | ``gpt-6.1-sol`` |
| opencode | ``~/.config/opencode/opencode.json`` → ``model`` | ``opencode/minimax-m3.1-flash-free`` |

读不到就报"未知"，**不猜**（`backend/app/adapter/models.py`，
``configured_model(harness_id, home=…)``；``home`` 可注入，便于测试不碰真实用户目录）。

> 为什么不给 probe 载荷加 ``model`` 字段：那是 schema 变更，要先过规范复审。
> 而模型本来就是配置事实——读文件既不改 schema，也不新增事件/状态。

### 16.2 平台侧的可用性判断（``GET /api/v1/adapter/availability``）

一条只读接口（网关透传：``GET /api/formal/availability``），对每个登记窗口给：

- ``model`` / ``model_source``：上面读到的模型与来源；
- ``credential_count``：**在册 ≠ 能领活**。没有平台凭据的窗口
  （codebuddy / qoder）根本领不到任务，所以不算候选；
- ``degraded`` / ``warnings``：最近一次 probe 的自报降级（用现有字段，没造新的）；
- ``usable`` + ``reasons[]``：能不能干，以及为什么不能。

**失败要分清是谁的锅**——这是这一步最要紧的判断：

- 只有指向 **harness 或它的 provider** 的失败（``formal-runs/<task>.failure.json``
  里 ``retryable: true``）才算"这个窗口现在不能干活"；
- 平台自己出的错（``GitError``、``OperationalError``：数据库被锁、git 抖）是
  **平台的问题**，不该把窗口拉黑；
- 失败之后**又交付过**（``submit_result`` 的 seq 晚于失败的 seq）→ ``recovered``
  → 恢复可用。一条陈年失败不能永久拉黑一个窗口；
- **陈年失败不再挡路，但也不被藏起来**：一次失败是"那一刻"的证据。超过
  ``AVAILABILITY_FRESH_FAILURE_MINUTES``（30 分钟）还没人复测，它就不再说"这个
  窗口现在不能干活"，而是作为 **warning** 带着年龄报出来
  （"上次尝试失败：…（157 分钟前，尚未复测）"）。provider 修好之后不至于还被一条
  几小时前的旧账挡住；但"上次确实失败过"这件事不会被抹掉。

``reasons`` 给的是**稳定 code + 原始细节**（``no_credential`` /
``last_attempt_failed`` / …），展示层做本地化，细节原样保留——平台说的事实
不该被翻译改写。

实机（8848 后端 + 4174 网关）：

```
claude-code          claude-fable-5-1              usable=False  last_attempt_failed
codebuddy            —                             usable=False  no_credential
codex                gpt-6.1-sol                   usable=True
opencode             opencode/minimax-m3.1-flash-free  usable=True
pi / qoder-member-*  —                             usable=True
```

### 16.3 办公室的「谁现在能用」小节（只读）

平台助手面板（`admin-agent.js`）多了一节：Harness / 状态 / 模型 / 原因。
**范围仍然只来自当前项目**（成员勾选 ∪ 本项目任务证据）——登记表是全局共享的，
整表贴出来等于把别的项目的窗口算成本项目的人手；范围外的窗口只报个数、不列名。

这一节**只有读，没有按钮**：换模型、改派、接手都是管理员/人的决定，助手不代劳
（`scripts/verify-platform-assistant.mjs` 断言该节内不得出现任何按钮）。

回归：`tests/test_availability.py` 六条（模型来自 CLI 配置且不猜、无凭据不算
候选、provider 失败算不可用、平台自身错误不拉黑、失败后交付即恢复、离线不算
候选）。

### 16.4 窗口开始自报（probe 真的发生起来了）

之前 ``capability_probes`` 一直是空表：谁都没探过，"是否降级"这个字段就没有
事实。现在**成员 worker 开机就探一次，之后每 5 分钟探一次**：

- 自报内容：适配器、适配器版本、``host_mode``、自报能力（用的是既有字段，没造
  新的）；
- 平台侧照旧算"自报能力 vs 登记表"的差：超出登记范围的算**降级**
  （``degraded`` + ``warnings``），办公室的「谁现在能用」里就会写"自报降级"；
- **探针失败不许拖垮 worker**：它只是观察面，探不上就不探，worker 照常干活；
- 探针**不改任务状态、不派单、不领取**——它写的是 ``capability_probes``，
  不是任务。

实机：``opencode`` 窗口的探针记录（``registration_version=reg-001``、
``host_mode=native_cli``、``degraded=0``）已在库里。

## 17. 平台自己的"额度池"：谁的额度能给谁用（2026-10-21）

需求原话：**"把模型额度从 harness 里面取出来，放到自己的池里面；哪个 harness
没有额度了，模型可以直接转接额度进去"**。harness A 有免费模型还有额度，harness
B 没额度了，平台就把 A 的额度接给 B 应急。

这不是"再写一个中转站"，而是**平台自己要有的能力**——所以它长在平台里：
`/api/v1/pool/*`，和任务、事件、权限同一套底座。

### 17.1 额度从哪来：不是写死的，是从各 CLI 自己的配置里"取"出来的

平台不持有模型凭据（凭据在各 CLI 手里），所以池子**读它们本来就读的那份配置**
（`app/pool/sources.py`）：

| 池里的额度源 | 协议 | 从哪发现 | 实测 |
| --- | --- | --- | --- |
| codex 链（8402 中转站） | OpenAI **responses** | `~/.codex/config.toml` | ✓ 8 个模型 |
| claude-code 的 provider | Anthropic | `~/.claude/settings.json` 的 `env` | ✓ 已充值，1s 出结果 |
| opencode 的 provider | OpenAI chat | `~/.config/opencode/opencode.json` | 790053500 那两个 key 已废（502/404） |
| 本地 Ollama | OpenAI chat | 同上（`local` provider） | ✓ 能用但慢 |
| **CLI 槽**（见下） | **CLI 亲自跑** | 池配置里**声明** | ✓ |

**免费额度那一格是最有意思的**：OpenCode 的免费档明说 *"OpenCode's free tier
can only be used from within OpenCode"*——它**不能**被当普通 API 调用。所以池子
把它做成一个 **CLI 槽**：由 CLI 自己去花这份额度，池子只负责把它包成
OpenAI/Anthropic 的形状给别的 harness 用（`app/pool/cli_model.py`）。

这正是"代用必须落在执行槽上"那条约束的落地：**额度可以换，执行槽不换**——
干活的还是被派到那个 harness 的 CLI（在自己的 worktree 里），只是它背后的模型
来自池子。

**CLI 槽现在是五个**（都是"只有 CLI 自己能花"的额度）：

| 槽（池里的模型 id） | 模型 | CLI | 实测 |
| --- | --- | --- | --- |
| `opencode-cli/minimax-m3.1-flash-free` | opencode 免费档 | opencode | ✓ 15s |
| `codebuddy/hy3` | 混元 hy3 | codebuddy | ✓ 15.7s |
| `codebuddy/minimax-m3.1-flash` | minimax 3.1 flash | codebuddy | ✓ 同一账号 |
| `qoder/Qwen3.8-Flash` | qwen 3.8 flash | qoderclicn | ✓ 10.9s |
| `qoder/DeepSeek-Flash` | deepseek flash | qoderclicn | ✓ |
| `mmx/MiniMax-M3.1-Flash-Preview` | minimax M3.1 flash | `mmx`（MiniMax CLI） | **待登录** |

每个槽都带**这个 CLI 自己的 argv 契约**（它们真的不一样）：codebuddy 的问题走
**stdin**（多行位置参数会被截断），qoder 的问题走位置参数，opencode 可以
`--file` 挂一个文件。模型 id 在池子里**带槽名前缀**（`codebuddy/hy3`）——两个
CLI 都提供 "deepseek-v4.1-flash" 时不能混成一份额度。

池子现在一共有 **16 个模型 / 9 个额度源**（再加 MiniMax 那份就是 17 / 10）。

### 17.1b MiniMax 的 CLI 是 `mmx`（订阅额度两条路进池子）

搜到的结论（2026-10-21）：

- **MiniMax CLI = `mmx`**（npm 包 `mmx-cli`，Node ≥ 18）：
  `npm install -g mmx-cli` → `mmx 1.0.28`；
- 命令形状正好是池子要的：`mmx text chat --model MiniMax-M3.1-Flash-Preview
  --message "…" --output json`（还支持 `--messages-file -` 走 stdin、
  `--system`、`--stream`）；
- 登录：`mmx auth login --api-key sk-…`（订阅 key 在
  `platform.minimax.io/console/plan`，形如 `sk-cp-…`；按官方说明，**key 由人自己
  在终端里输入，不给 agent**），凭据落在 `~/.mmx/config.json` 的 `api_key`；
  `mmx quota` 看 M Plan 剩余额度；
- MiniMax 的 API 有 **Anthropic 兼容端点**（`<base>/anthropic/v1/messages`，
  base 是 `api.minimax.io` 或 `api.minimax.cn`）——所以这份额度**两条路都能进池子**：
  ① 作为一个普通上游（Anthropic 协议，快）；② 作为一个 CLI 槽（`mmx text chat`）。

两条都接好了，**都在登录后才出现**：

- 上游：池子读 `~/.mmx/config.json` 的 `api_key`（没登录就当"这里没有 MiniMax
  额度"，不装作有）；
- CLI 槽：`mmx/MiniMax-M3.1-Flash-Preview`，槽自己声明"必须有凭据"，
  没登录就不进模型表——**报一个花不掉的额度就是撒谎**。

人要做的一步（官方流程也是"agent 装、用户自己登录"）：

```
mmx auth login --api-key sk-…     ← 在你自己的终端里跑
mmx quota                          ← 看 M Plan 额度
```

**已登录（2026-10-21）**，而且登录方式是 **OAuth**（不是 api_key）：
`~/.mmx/config.json` 里是 `oauth.access_token` / `oauth.refresh_token` /
`oauth.resource_url`（本机 region=cn，`resource_url=https://api.minimaxi.com`）。
池子按 **mmx SDK 自己的解析顺序**取（`base_url` → `oauth.resource_url` →
按 region 的默认域），免得把 token 送到错的区。

实测两条路都通（池子 → MiniMax）：

| 路径 | 形状 | 实测 |
| --- | --- | --- |
| **直连上游** | Anthropic 兼容 `https://api.minimaxi.com/anthropic/v1/messages` + OAuth token | **1.3s**，"OK"，用量 65 in / 2 out |
| **CLI 槽** | `mmx text chat --model MiniMax-M3.1-Flash-Preview --message … --output json` | 2.6s，"OK" |

**一句实话（token 会过期）**：OAuth 的 access_token 是有有效期的（本机到
2026-10-23T02:47Z），刷新是 **CLI 自己做的**（`mmx auth refresh`，刷新后重写
config.json）。所以：

- **CLI 槽是耐久的那条**——它自己管自己的登录，过期了 CLI 自己刷新；
- 直连上游那条靠池子每次现读 config.json，**只要 CLI 跑过一次就新鲜**；若
  一直没人调 CLI，token 过期后直连会 401，而 CLI 槽照常能用。

### 17.1c 额度分三档，抽的确实是"订阅那一份"

用户问的是"能不能把订阅免费的 minimax 3.1 flash 抽出来"。答案是**已经抽出来了**，
而且有证据——**付费余额全程 0.00**：

```
account/query_balance → {"available_amount":"0.00","cash_balance":"0.00",
                         "voucher_balance":"0.00","credit_balance":"0.00"}
池子打两发（CLI 槽 2.4s / 直连 1.7s，都 "OK"）
再读一次 → 余额还是 0.00
```

也就是说：这两份额度**完全由订阅承担**，一分钱没动付费余额（订阅那部分本来
就要付，"不用也是白不用"）。MiniMax 的 CLI 目录里也只有
`MiniMax-M3.1-Flash-Preview / M3 / M2.7 / M2.7-highspeed`——**没有另一个
"*-free" 模型**；订阅免费的那一份就是 M3.1-Flash-Preview 本身。

于是池子里的额度分成**三档**，路由按档位挑（`tier`）：

| 档位 | 是什么 | 池里有什么 | 自动挑吗 |
| --- | --- | --- | --- |
| `free` | CLI 自己的免费额度（不要钱、不要订阅） | codebuddy/hy3、codebuddy/minimax-m3.1-flash、opencode-cli/minimax-m3.1-flash-free、qoder/DeepSeek-Flash、qoder/Qwen3.8-Flash（5 个） | ✓ 第一顺位 |
| `reserve` | **兜底**：订阅里已经付过钱的额度（不用也是白不用，但有速率窗） | **MiniMax-M3.1-Flash-Preview**（直连）+ **mmx/MiniMax-M3.1-Flash-Preview**（CLI 槽，**同一份 M Plan 额度**）（2 个） | ✗ **不自动动用** |
| `metered` | 按量计费 / 中转站账号池（每发都花钱） | codex 链 8 个、DeepSeek、Ollama、已废的 790053500（11 个） | ✓ 第二顺位 |

应急转接的挑法：**先 free，再 metered**——`reserve` **不在自动挑选的名单里**。
按所有者（2026-10-21）的决定：订阅那份**留在兜底**，平台不自己花，要用得
**点名要**（"用 MiniMax-M3.1-Flash-Preview" 才走它）。两条顺序都有测试钉着。

注意 mmx 的 CLI 槽和直连**花的是同一份 M Plan 额度**（CLI 就是用那个账号登
的），所以它们**同属 reserve 档**——不能一边说"CLI 槽是白拿的"、一边偷偷把
周额度吃掉。

实机（claude-code 的老 provider 失败 → 自动转接）：

```json
{"mode":"pool","model":"opencode-cli/minimax-m3.1-flash-free",
 "upstream":"opencode-cli:opencode/minimax-m3.1-flash-free"}
```

→ 挑的是**免费档**，MiniMax 那份没被动（点名要它时仍然能用：3.1s，"OK"）。

池子现在 **18 个模型 / 11 个额度源**（5 free + 2 reserve + 11 metered）。

### 17.2 池子对外就是一个 OpenAI 通用接口（密钥不出池子）

`/api/v1/pool/v1/{models,chat/completions,messages,responses}`：

- 调用方是 **harness 自己的平台 token** → 平台精确知道**谁花了谁的额度**
  （记账在 `data/pool/usage.jsonl`）；
- 上游的 provider key 由池子**在服务端注入**，被服务的 harness 只拿到池子自己的
  身份；
- 协议自动翻译：Anthropic 上游 ↔ OpenAI 调用方、responses 上游（codex 链）
  ↔ OpenAI 调用方；**文本不许丢**（翻译丢了文本就是丢活）。

实机（8848 平台自己的接口，opencode 的 token）：

```
GET  /api/v1/pool/v1/models   → 11 个模型，每个都标着它属于哪份额度
POST /api/v1/pool/v1/chat/completions  {"model":"minimax-m3.1-flash-free", …}
     → 200，15.4s，"OK"   （走 CLI 槽，opencode 的免费额度）
记账 → {"consumer":"harness/opencode","upstream":"opencode-cli","requests":1}
```

### 17.3 应急转接：平台决定这条活走谁的额度

成员 worker 干活前问平台一句（`GET /api/v1/pool/plan`），平台答：

- **direct**：这条活用这个窗口自己的 provider（默认）；
- **pool**：上一次就是**这个窗口自己的 provider**挂的（provider 类失败），这次
  改走池子。

三条硬规则：

1. **不回送同一个 provider**：失败的那份额度正是不能用的那份，转回去只是再失败一次
   （`_blocked_upstreams`）；
2. **优先用别人的免费额度**：CLI 槽（免费档）排在付费链前面——这正是"应急"该用
   的额度；
3. **别的窗口接手的活不算这个窗口的锅**：失败证据里的 harness 不是我，我就用自己的
   provider（不乱转接）。

实机（claude-code 的老 503 失败记录）：

```json
{"mode":"pool","model":"minimax-m3.1-flash-free","upstream":"opencode-cli",
 "reason":"the last attempt failed on this window's own provider (CliFailed: …);
           this attempt runs on the platform's pool instead"}
```

### 17.4 怎么让 CLI 听池子的：每次尝试单独改，不动人的配置

执行槽仍在原 harness 的 CLI 里，所以要做的只是**这一次**把 CLI 指向池子：

- **claude-code**：`--settings <本次尝试的临时文件>`，里面写
  `ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_MODEL`——命令行上的
  settings **盖过**用户自己的 `settings.json`（实测：指向一个死端口，CLI 果然
  连不上），所以**人的配置一个字都不用改**；
- **codex**：`-c model=… -c model_provider=pool -c model_providers.pool.base_url=…`
  之类的逐次覆盖 + `OPENAI_API_KEY`；
- **opencode**：`--model openai/<模型>` + `OPENAI_BASE_URL/OPENAI_API_KEY`
  （内置 openai provider 认这两个环境变量）。

runner 因此多了一个 `env` 形参（**只加不覆盖**工作进程自己的环境）。

**实机端到端**（claude-code 指向池子，问 opencode 的免费额度）：

```
claude -p --settings <本次尝试的 settings>  "Reply with exactly: POOL OK"
  → 25.2s，"POOL OK"
  → 池子的记账：{"consumer":"harness/claude-code","upstream":"opencode-cli",
                 "model":"minimax-m3.1-flash-free","duration_ms":10364}
```

也就是说：**B（claude-code）自己的 provider 不行了，平台把 A（opencode）的免费
额度接给了 B**，而干活的执行槽仍然是 claude-code 自己的 CLI。

### 17.5 边界（写在明面上的限制）

- 池子**不是调度器**：它发模型请求，不领活、不改任务归属、不替任何人说话；
- 池子**有自己的权限键**：`harness:pool`（harness 主体，2026-10-21 由平台所有者
  批准后加进权限矩阵）——"借别人的额度"和"问自己有什么能力"不是一回事，人类
  token 用池子会被拒（`tests/test_pool.py` 有断言）；
- 免费额度只能经 CLI 花，所以慢（~15s 一次），且 CLI 槽是**声明**出来的
  （`data/pool/config.json`），不是从配置里嗅出来的；
- 790053500 那两个 key 现在是废的（502/404），池子如实把它们列出来，不装作
  健康；
- 池子**会流式回答**了（2026-10-21 补上）：`/v1/chat/completions` 与
  `/v1/responses` 都认 `stream: true`。codex 链那条 **responses 流是透传**的
  （`response.created` / `response.in_progress` / … 原样过来，实测 29s）；从 chat
  或 Anthropic 上游转出来的流会被翻译成 responses 事件（`response.output_text.delta`
  → `response.completed`）；CLI 槽的流是"一次答完"（它本来就不是流式模型）。

回归：`tests/test_pool.py` 二十五条（翻译不丢文本、额度源从 CLI 配置里发现、指向
池子自己的源要跳过、CLI 槽的问答形状、路由的三条硬规则、chat 流 → responses
流、`harness:pool` 这个键、人类用池子被拒、MiniMax 的额度从 `mmx` 的凭据里读、
没登录就没有这份额度）。

### 17.6 办公室看得见这个池子

平台助手面板多了一节「平台额度池」：每个额度源（名字、协议、模型数、说明），
以及**谁的额度被谁花了**（"harness/opencode 用了 opencode-cli ×4"）。网关用
`GET /api/formal/pool` 透传平台的 `/api/v1/pool/status`（用管理员 harness 的
凭据读——池子的接口只认 harness 主体）。

这节仍然只读：池子不派单、不改派、不替谁决定换不换 provider。表里多一列
**档位**（免费额度 / 订阅额度 / 按量计费）——人一眼能看出"哪些是白拿的、哪些要
花钱"。

### 17.7 顺手修的一个真问题：观察面被自己的请求饿死了

加完额度池这一节，办公室的面板**不渲染了**（一直停在"正在检查连接…"）。查下来
不是池子的问题，是**浏览器每源 6 条连接的配额**被工位屏幕吃光了：

- 每个 harness 屏幕要 2 个请求（任务 + CLI 原话）；
- 网关**每个请求**都重新问一遍平台"我是谁、哪个群"（whoami + groups）；
- 于是 12 个窗口 × 每轮几十个上游请求，把浏览器到 4174 的连接池占满，平台助手的
  面板**排在队尾永远轮不到**。

修法在网关：**scope 缓存 5 秒**（whoami + groups 不该每请求重问）。效果：

```
GET /api/formal/tasks/<id>          0.76s → 0.16s
GET /api/formal/tasks/<id>/output   0.18s → 0.06s
```

面板恢复渲染，并且 `verify-platform-assistant.mjs` 现在会同时断言「谁现在能用」
和「平台额度池」两节**都存在、都只读、都按项目圈定范围**。

## 18. 第 3 步：「失败与接手」面板 + 「请求管理员改派」（2026-10-21）

### 18.1 办公室只列事实、只递请求

平台助手面板多一节「失败与接手」：本项目**失败或停滞**的任务（`abandoned` /
`stale`），每条列出——谁倒的（上次持有者）、现在**还有哪些窗口能领活**（带它们
当前的模型，来自第 16 节的可用性视图）。旁边一个「请求管理员改派」按钮（外加一个
"接手窗口"下拉，默认"由管理员按负载挑"）。

按钮递的是**请求**，不是动作：它往管理员的群聊通道发一条消息（refs 里带
`reassign-request`，选定了窗口再加 `reassign-to:<harness_id>`），消息正文写清
任务、状态、上次持有者、平台侧算出来的候选。**办公室不派单、不改任务归属**。

### 18.2 管理员自己决定、自己执行

管理员 worker 收到这种消息（`refs` 里带 `reassign-request`）时：

1. 确认任务确实需要换人（`abandoned` / `stale` / `ready` / `changes_requested`；
   还在跑的活**不动**）；
2. 目标窗口：人指定了就用指定的（且它**得是能领活的窗口**），没指定就按负载挑
   一个（候选来自平台同一套 `assignable_harnesses` 规则）；
3. 执行既有的改派动作：`cancel` 旧任务 + 按**同样的规格** `dispatch` 一条新的
   （断点报告随工单转交），两步各自留痕——**没有新造事件名或状态边**。

请求是**消息**，消息会被每一轮轮询重放，所以处理过的 seq 记在 worker 状态里
（同一条请求不会把任务改派两次）。

实机（办公室发请求 → 管理员接手）：

```json
{"reassigned": "FD-f5b79418810cdf01", "new_task": "FD-7dd953f12412d552", "to": "opencode"}
```

### 18.3 顺手修的两个真 bug（都是"自循环其实早就断了"）

**（a）改派的幂等键会撞车**：改派用的 `request_id` 只有任务号 + 计数，而计数
在 worker 的状态文件里。状态文件一丢（换机器、重置），计数从 0 开始，就会和
**历史**的 request_id 一样 → 平台按幂等重放**旧的那条派单**，返回旧任务 id，
看起来"改派成功了"，其实什么都没变。现在 request_id 带上**这次请求自己的
消息 seq**，每次改派都独一无二。

**（b）管理员其实审不了稿**（更严重）：管理员的审核工单里塞了**整个 diff**，
实机测到 **45 KB**。Windows 的命令行上限是 **32,767 字符** → CreateProcess 直接
失败 → 适配器把 FileNotFoundError 报成 "CLI 不在 PATH" → 群里每 5 分钟一条
"管理员 Harness 无法裁决 FD-…"。也就是说**审核这一步断了好几个小时，谁都没发现**。

修法与 CodeBuddy 那边一致：**工单走 stdin**（`codex exec -`），argv 里不再带
prompt。管道没有长度上限。修完立刻见效：

```json
{"reviewed": "FD-cab7731fb69aab30", "verdict": "request_changes", "status": "request_changes"}
```

回归：`tests/test_admin_worker.py` 十六条（新增：请求式改派要真的改派、活还在跑
就不动、请求 id 唯一、同一条请求不重复改派）、`tests/test_real_adapters.py` 的
argv 契约（codex 的工单现在走 stdin）。

## 19. 传阅 + 方案定稿（2026-10-21）

第 2 步的"可用性"、第 3 步的"失败与接手"补上之后，还差一段：**管理员细化完的
方案该给谁看、看完谁来拍板**。现在这条链是通的：

```
群主要求 → 管理员 harness 细化 → 方案草案 + 开一轮传阅（成员各自评审）
        → 意见并回方案（新快照）→ 生成 MD/HTML 定稿 → **人最终审核**
        → 批准 → 任务才创建、才可被领取
```

### 19.1 传阅用的是平台自己的机器（不新造事件/状态）

R10 §39 的传阅机器本来就在平台里（`circulation_rounds` /
`circulation_reviews`），只是**没接进自循环**。这一轮接的是"接线"：

- **管理员细化后不直接派单**，而是把方案变成**草案**并开一轮传阅
  （新路由 `POST /api/v1/adapter/admin/plan_draft`，用的既有
  `admin:refine` 权限键——**没有新造权限键**）；
- 草案里的任务停在 `draft`，**谁也领不了**（这正是"传阅完再派"的意义）；
- **成员自己评审**：成员 worker 看到"有草案等我评审"，就用**自己的模型通道**
  给出意见（走既有的 `circulation/review` 写面）；
- **管理员把意见并回方案**：自己的模型通道再跑一次，产出**新快照**
  （`POST /api/v1/adapter/admin/plan_revise` → 计划仍是草案，任务指向新快照）；
- 收口传阅（`circulation/complete`）→ **生成定稿文档**。

**评审是只读的，但不是"不能看"**：评审那次 CLI 调用走 `review_command`——
opencode 用 `--agent plan`、codex 用 `--sandbox read-only`、claude 用
`--permission-mode plan`。**读的工具都还在**（读文件、搜代码、列目录），去掉的
只有**会改东西的**（写文件、跑命令、往群里发消息）。

这一条是**实测踩出来的**：第一次评审时，成员自己的模型拿着工具**真的去改了
文件、还在群里发了消息**（"已更新 … 并发了 seq=991 的交付消息"）——它把"评审"
当成了"把活干了"。一个只该说话的窗口替别人动手、还替别人宣布交付，审计线索
当场就脏了。收紧之后实测：scratch 目录**没变** ✓，而它**读到了代码**——甚至
读出了评审路径里的一个真 bug（"方案被截断：评审没有先走 `prepare_work_order`，
Windows 的 cmd 包装丢了第一个换行之后的内容"）✓。这条是它自己读出来的。

顺带修掉的两个真 bug：评审的工单要走 `prepare_work_order`（否则方案被截断，
等于闭着眼评审）；提交意见的 `post` 少了 `json=`（httpx 把 dict 当 body 发，
直接 TypeError）。

### 19.2 定稿文档：给人看的 Markdown + HTML

`app/review_doc.py` 把方案（新快照）+ 传阅记录（每一轮谁说了什么）渲染成
**Markdown** 与 **HTML** 两个文件，落在 `data/exports/plans/` 下；平台有只读路由
`GET /api/v1/plans/{plan_id}/document`（办公室经网关 `/api/formal/plan` 读）。

办公室面板多一节「方案定稿（等人最终审核）」：文档正文（Markdown 原文）+
一个 **「批准并派单」** 按钮——**这是人的动作**（用 operator 身份，平台的
`plan:approve` 本来就给人），批准后那批任务才从 `draft` 变成可领取。

### 19.3 读接口补齐

传阅原来**只有写接口没有读接口**（管理员和成员都没法问"有草案等我吗"），这一轮
补了三条**只读**路由：`GET /allocation/circulation`（按群/状态列轮次）、
`GET /allocation/circulation/{id}`（一轮连同意见）、
`GET /groups/{group_id}/plans`（本群的方案与草案的文档）。

### 19.4 「失败与接手」补上失败原因

面板现在说**为什么倒的**：平台的任务列表多了一个 `last_failure`
（最后一条 abandon/release 事件的理由，一次查询读完整个列表），办公室显示
"失败原因：the harness CLI failed before it could deliver" 这种**原话**，
而不是只说"已放弃"。

## 20. 跑通一条完整链路，带出 10 个真 bug（2026-10-21）

第 19 节把"传阅 + 定稿"接上了，这一节是**真的跑一条链**（不是单测，是平台上
真的一条要求、真的模型、真的工单），以及它逼出来的 10 个 bug。

### 20.1 实跑的链路

```
群主要求（一句大白话）
  → 管理员 harness 细化：它**拒绝猜**，反问 2 个问题（"指哪个系统/产品？"）
  → 人回答 → 管理员再细化（28s）→ 规格成形
  → 方案变成**草案** + 开一轮传阅（任务停在 draft，谁也领不了）
  → 成员用**自己的模型**评审（opencode 给了真意见，还读代码读出了评审路径里的 bug）
  → 管理员把意见并回方案（新快照：工单被改写成带**文件行号和字段口径**的版本）
  → 传阅收口 → 生成定稿文档（MD + HTML）
  → **人批准** → 任务创建 → 成员领活 → 交付（60 KB 文档）→ 校验通过
  → 管理员审核 → 要求返修（changes_requested）
```

事件轨迹（同一条任务）：

```
claim → start → submit_result → verify_passed → request_changes
```

### 20.2 这条链逼出来的 10 个 bug

**传阅/定稿那 5 个：**

1. **模型会自己编 task id** → 修订被平台拒（"task … is not part of plan"）。
   修订必须是**同一批任务**说得更清楚，所以平台现在**强制沿用草案的任务 id**。
2. `circulation/complete` 的请求体字段名错了（要 `harness_id`，不是
   `completed_by`）→ 400。
3. **状态机顺序搞错了**：R10 §39 的路是 `circulating → close → revising →
   completed`（`revised` 是"改完再开一轮"，不是收口）→ 409。
4. 文档路径写错（`/read/plans/…` → `/plans/…`）→ 404。
5. 修订的提示词要 **JSON 数组**，而平台的解析器要 **JSON 对象** → "no balanced
   JSON object"。现在要对象、数组也兼容。

**工位/容量那 5 个**（这几个更狠：平台会把自己锁死）：

6. **清扫器漏了 `stale` 任务的工位** → 容量只涨不落。实机：8 个并发工位被
   10-14 到 10-20 的**幽灵 attempt** 占满 → 新任务连工位都开不出来
   （"cap reached (8/8)"）。修完立刻从 8 降到 4 ✓。
7. **"没有空闲工位"被当成失败** → 好活被判死。平台腾不出手不是这条活的错，
   现在它**可重试**：占位还回去、任务留在队列。
8. **CLI 没开跑就失败时去 abandon** —— 但 `abandon` 不是从 `claimed` 出发的边
   → 任务变成僵尸 claim（没人干活、租约还挂着）。
9. **失败路径不释放租约** → 窗口一直"满员"（`parallel_limit`），一条活都领不到。
10. **人放回队列（escalated → ready）不摘掉死 attempt** → 任务看着是 ready，
    实为隐形僵尸：可领列表会跳过还挂着 attempt 的任务。

第 6、10 两条的回归在 `tests/test_rules_tighten.py` 与 `tests/test_human_ops.py`，
第 7、8 条在 `tests/test_failure_retry.py`。

### 20.3 顺带说明：worktree 上限是设计，不是泄漏

每个 attempt 要自己一份 git worktree（隔离），所以平台给"同时活着的工位"设了上限
（`AP_CONCURRENT_WORKTREE`，默认 8）。限制本身是对的；今天的问题是**旧工位没人
回收**：清扫器漏了 `stale`、失败路径不释放租约、人放回队列不摘 attempt。三个都修
了，容量会自己回落。

## 21. 「请求再给一次机会」：只有人能给的一次机会（2026-10-21）

第 20 节那条链走到"管理员返修"就卡住了：**尝试预算用完**（3/3）→ 平台拒绝再次
claim（R10 §8：普通回环会被同一个预算闸门再拒一次）→ 而**没有人能救它**：

- 管理员的 release 只吃 `escalated` 的任务，而这条是 `changes_requested`；
- 几个清扫器都不看 `changes_requested`（stuck 看的是 in_progress/needs_help/
  blocked/verifying ✗），所以它连"升级给人看"都不会发生；
- 结果：一条返修中的活**没有合法的人来接手** ✗ ——自循环在这里静默地卡住。

### 21.1 这条动作是什么

**人给一次机会**（R10 §8 的 `attempt_budget_override`）：

- 平台**不给**自己豁免，管理员 harness **也不代劳** ✗ → 它是**人自己的动作** ✓；
- **不改任务状态**（任务还在原地，只是 claim 的预算闸门放行一次）→ 不需要新状态边 ✓；
- **不新造事件名**（规范里的事件集是闭集 ✗）→ 审计落在 `task_decisions` 的决定行
  上，锚在任务最后一个事件的序号 ✓；
- 权限面：新增 HUMAN 键 `task:attempt_budget_override`（与 2026-10-21 批准的
  `harness:pool` 同路径：所有者批准的加性平台面，注释里写明来源 ✓）。

### 21.2 办公室里的位置

「失败与接手」面板里，**尝试预算已经用完**的那条任务，会多一个按钮：

```
请求再给一次机会（预算豁免）
```

按下去 → 网关 `POST /api/formal/task/budget_override` → 平台
`POST /api/v1/human/attempt_budget_override`（用 operator 身份 = 人 ✓）→
任务记下 `budget_override_by` ✓ → 成员 worker 就能再领一次 ✓。

实机：`{"attempt_budget_override":true,"budget_override_by":"office-operator",
"attempt_no":3,"max_attempts":3}` → 成员立刻领到并开工 ✓（`in_progress`）。

### 21.3 为什么这类按钮重要

今天这条链暴露了一个模式：**平台里的"死局"往往不是逻辑错，而是"只有人能做
的动作"没有入口** ✗。工位容量、租约、僵尸 attempt 是这一类；而"人给一次机会"
是另一类（预算、豁免、重开）——它们都不该由平台或管理员代劳，但**必须有一个
人能按的地方** ✓。办公室是观察面，**但观察面也是人做决定的地方** ✓。

## 22. 办公室 · 数据版（3D 场景撤下，2026-10-21）

3D 场景那套（角色、动作、动画适配）要打磨的东西很多，而**屏幕上真正要回答的
问题**其实只有几个：现在有几条活、谁在做、卡在哪、平台还有谁能用、有没有方案
在等我审、我现在能做什么。于是另做了一份**数据版**办公室（`src/flat-office.js`），
并把 3D 从 GitHub 撤下（**本机保留**，后续再启动计划）。

### 22.1 一版真贴数据的界面

- **顶栏**：项目 · 群 · 连接状态 + 五个计数（待领取/进行中/待审核/失败·停滞/已完成）；
- **左**：任务表（按状态筛选），每行：状态签 · 标题 · 持有者 · 第几次尝试 ·
  **失败原因原话**；
- **中**：选中任务的 **工单 / 验收 / 允许改动的路径**、**事件轨迹**、**CLI 原话**，
  以及**人自己的动作**（预算用完时的「请求再给一次机会」）；
- **右**：**额度池**（额度源 + 谁用了谁的额度）、**现在能干活**（已排除管理员窗口
  与平台助手）、**等我审核的方案**（+「批准并派单」）；
- **底部**：**项目群聊**（平台的群，不是本地假消息）+ 输入框；
- **顶栏右侧**：**数据源切换**（`正式平台` / `本地样例`）——本地样例用
  `office-read-fixture` 的合成输入（`SYN-OFFICE/SYN-TEAM`），**只是给没平台
  的人看界面用**，不与正式数据混在一起。

### 22.2 为了让人看得懂方案，平台多投影了一个字段

任务详情里的**工单/验收正文**来自计划的快照，任务路由以前只投影**标题** ✗。
现在 `get_task` 一并投影**规格**（`spec`：工单、验收契约、允许改动的路径）✗ ——
"让人拿着标题去审方案"是没有意义的 ✗。

### 22.3 3D 撤下的方式

GitHub 仓库里跟踪的 3D 原型（`docs/prototypes/office-anim/`，42 个文件）用
`git rm -r --cached` **只从索引里撤** ✗ + 写进 `.gitignore` ✗ → **文件留在本机**
✗，以后不会被误带回去 ✗。前端默认不再加载 Godot 产物（`src=about:blank` ✗），
想看 3D 版（本机还在）：`?scene=3d` ✗。

### 22.4 三处打磨

- **额度池展开明细**：以前只有源名与协议，现在每个额度源点开有
  **上游地址 / 来源 / 备注 / 模型（带档位：免费·兜底·计量）/ 已用请求数与
  token / 谁用过**，另有一栏「谁用了谁的额度」（按消费者聚合）。
  额度池是「谁的钱」的问题，只列源名是不够的 ✗。
- **失败任务加「本次失败的真实日志」**：失败理由是一句话，理由背后是 harness
  真的说过的话。任务行里给一个入口，详情里给原样的 **stderr / stdout**，
  外加那一轮 attempt 的 id、harness、状态与时间。
  要看的是**失败那一轮**——重跑中的活，"当前 attempt"的输出是重跑那一轮的 ✗，
  所以平台的 `/tasks/{id}/output` 加了一个 `attempt_id` 参数（只读，
  点名要哪一次；别任务的 attempt 不给）✗。
  正在重跑的活，两边都显示：失败那一轮的日志 + 当前这一轮的原话 ✗。
- **任务表排序 / 分页**：排序（最近更新 / 状态紧急度 / 尝试次数 / 标题）与
  每页条数（20 / 50 / 200）由人自己选，底下有「第 x–y 条，共 z 条」和翻页 ✗。
  排序要诚实，所以任务投影里补了 `created_at` / `updated_at` ✗。

### 22.5 事件轨迹把 payload 摊开

事件名只是骨架，**细节都在 payload 里**——"改派给了谁"、"为什么失败"、
"谁做的裁决"、"被审的是哪个 commit"，全在那儿。以前办公室只显示
`seq / 事件名 / actor`，等于让人只看标题不看内容 ✗。

现在每条事件都能点开：

- 标题行先给一句**人话**（"原因：工位容量已释放…"、"上一步是 claimed"），
  让人知道要不要点开；
- 展开后是**字段表**：认识的键给中文（"哪一次 attempt"、"谁决定的"、
  "从哪条改派过来"），不认识的照实写英文键；
- 值原样给，长的截断并**说清楚被截了多少**（不假装是全文）；
- 成员自述（`completion`）给的是"改了哪些文件 + 哪个 commit"；
  CLI 的"最后一句话"若是它自己的事件流（opencode 就是 JSON），
  就说明白那是多长的原始流，而不是把 JSON 糊给人看；
- 超过 14 条时可以"看全部 N 条"。

### 22.6 额度池：用量是**什么时候**用掉的

"已用 19 次"只说明钱花掉了，说不清是**什么时候**花的。"是不是刚刚把某个窗口
的额度打满了"是关于时间的问题，一个总数回答不了 ✗。

平台这边，池子本来就记着每一条用量（``at`` / ``upstream`` / ``consumer`` /
tokens），现在多投影一条**按小时**的时间线（``GET /pool/status`` 的
``timeline``，默认最近 24 小时，最多 14 天）✗：

- 每个小时一桶，带该小时的请求数、token、**用了哪些额度源**、**谁用的**；
- 窗口外的旧记录不混进来，但会**说清楚**有多少条落在窗口外（``outside_window``）；
- 时间戳解析不了就跳过（不猜）。

办公室把这条时间线画成**一根柱子一小时**：池子顶部一条总览（24 根柱子），
每个额度源展开后还有它自己的一条——"它的额度是什么时候没的"。

### 22.7 额度档位库存：还剩多少

"平台已用 6 次"和"还剩多少"是两件事。池子以前只知道自己花了多少，因为
**不是所有额度都有余额可查**：

| 档位 | 是什么 | 还剩多少能不能问 |
| --- | --- | --- |
| free | 各 CLI 自己的免费额度 | **不能**：CLI 不暴露配额接口，只有用量 |
| reserve | MiniMax M Plan 订阅 | **能**：CLI 自己知道（`mmx quota show`） |
| metered | 按量计费 | **没有余额概念**，只有账单 |

所以平台多投影一条 `inventory`（`GET /pool/status`）✗：每档给**模型数**、
**平台花掉多少**、以及**能问到的余额**（问不到就写清楚为什么）✗。

- 订阅余额是**读 CLI 自己的答案**（`mmx quota show --output json`），平台
  不自己算余额；读数带**来源**与**查询时间**；
- 读数最多缓存 5 分钟（一次网络往返，余额不会按秒变）；
- 没有 MiniMax 上游就**不探**（没东西可问，别白跑一趟）；
- 读不到（没登录 / 没装 / 超时 / 输出不是 JSON）都说**为什么** ✗。

兜底档那两个上游（`mmx` CLI 槽与 MiniMax 直连）花的是**同一份** M Plan
额度，所以余额挂在**档位**上，不是挂在某一个上游上 ✗。

办公室把库存画成三行（免费 / 兜底 / 计量），**只有问得到余额的那一档**
才有进度条：

```
免费  5 个模型 · 平台已用 6 次
      各 CLI 不暴露配额接口：免费档没有可查的余额，只有用量。
兜底  2 个模型 · 平台已用 5 次   [█████████░] 93%
      本时段还剩 99% · 本周还剩 93%；本时段 10/22 01:00 重置，本周 10/28 14:29 重置
      来源 mmx quota show · 10/21 20:25 查的
      订阅额度（MiniMax M Plan）：CLI 自己知道还剩多少，平台不自动动用它。
计量  11 个模型 · 平台已用 2 次
      按量计费：没有余额概念，只有花了多少。
```

另外，页面每 15 秒轮询一次池子，所以两个缓存放到**模块级**（池服务是每请求新建的，实例上的缓存一次也不会命中）：配额读数（5 分钟）与模型发现（TTL）。
实测：``/pool/status`` 从每次 ~2.7 秒降到 **~50 毫秒**（首次读还是慢的，那是真的发现 + 真的额度）。

### 22.8 这条活的来路（改派链）

一条活被换来换去以后，"它是谁、之前是谁在干、干了什么、为什么换人"就散在
事件流里，要一条条读。办公室现在把它摆在**详情最上面**：

- **跨任务的改派**：
  `FD-9431c1cafc202714 > FD-554d39dd92e7ec10 > FD-6fd07c8a288ac72e > FD-f5b79418810cdf01 > FD-7dd953f12412d552（本条）`
- **本条活的每一次尝试**：第几次、哪个 attempt、谁领的、什么时候、结果如何
  （领了 / 开工了 / 交了东西 / 校验通过 / 交回队列：<理由> / 交了，但被要求返修），
  以及「从哪一轮改过来」（`revised_from`）。

**这里没有加任何平台事实**：

- 同一条活里的尝试链（`claim` / `claim_for_revision` 的 `revised_from`）是平台已经投影的
  事实，直接用；
- 跨任务的改派（旧任务取消 + 新任务派单）**平台没有结构化的“谁接替谁”字段**，
  链子在管理员自己写的那句群里说明里（“管理员 Harness 改派：FD-x > FD-y（原执行者…）”），
  所以界面上明写着「改派是管理员在群里说明的」——这是管理员的说法，不是平台的字段 ❌。
  要把它变成平台字段，就要加事件 payload 的新字段，而新增 schema 需要先过规范复审并登记 ❌。

### 22.9 接替关系进平台字段（R10-fix-17，待复审）

改派之前的接替关系只在**群里的一句话**里；现在它是平台字段：

- 派单请求可以声明 `supersedes`（旧任务 id）；
- 它写进新任务的**计划快照**（任务的不可变契约），
  任务列表与详情都投影它；
- 方向只挂**新任务**：终态任务的行不被改写（改它就是改历史）。
- 校验三条：旧任务必须存在、必须同群、必须处于“工作没有交付”的状态
  （`cancelled` / `abandoned` / `stale` / `changes_requested`）——**还在跑的活不能被接替**（那会变成两个主人）。
- 幂等：`supersedes` 进请求摘要，但**只在有值时**进——没有链接的请求必须与字段存在之前哈希完全一致，
  否则旧 `request_id` 的重放会变成“同 id 不同内容”的冲突。

规范侧：`docs/prep/AGENT-OFFICE-UNIFICATION/R10-FIX-17-DRAFT.md`（登记输入）；R10 正文已落：**§9**（任务接替关系 `supersedes`）与**§38.12**（R10-fix-17）——**仓库主人 2026-10-22 拍板**，五条待裁问题已落定（落快照 / 只挂新任务 / 四个可接替状态 / 字段进 §9 / 事件 payload 不带）。
实现与正文在**同一次提交**内；新增事件名 / 状态 / 状态迁移边 / canonical 表 / 列，一样没有；`plan_version` 仍是 v1，**不提升 `schema_version`**（提升规则只针对**新增事件**）。

### 22.10 计量档的“金额”：三个事实，不混在一起

代量档的“花了多少”之前答不上来，因为**流式请求一律记 0 token**：

- 上游把 usage 放在最后一个事件里（Responses 的 `response.completed`、chat 的最后一个 chunk、
  Anthropic 的 `message_start` + `message_delta`），而池子只是把那些行原样转发出去，没有看一眼；
- 后来加了一个**嗅探器**（`_UsageSniffer`）：上游线与下游线同时经过它，三种协议的 usage 都能收到；
- 测试用**真的本地 HTTP 服务**走一遍流式路径（`test_pool_stream_usage.py`）。

现在计量档能报出三个**不同的事实**，不混在一起：

| 事实 | 谁报的 | 怎么报 |
|---|---|---|
| token | **平台自己** | 每个请求记的 prompt / completion token（上面那个修复之后才是真的） |
| 余额 | **provider 自己** | 平台去问 provider 的只读余额接口（已验证：DeepSeek `/user/balance` -> `CNY 1.04`） |
| 单价 | **不知道** | 这些模型名（`gpt-6.1-sol`、`deepseek-v4-flash[1M]`）是**中转站改过的**，没有公开价格可套；平台**不猜单价**，只报 token 与余额 |

- 每条 provider 都说清楚自己的余额（或为何问不到）：平台知道的余额接口只有**那些 provider 自己公开的**（当前是 DeepSeek）；
  其余的（中转站链、opencode 的中转站、本地 ollama）面向人说“这个 provider 没有可查的余额接口”，而不是编一个 0；
- 余额读取只读、带来源与查询时间，缓存 5 分钟；读不到就说读不到的原因（没设置 / 无凭据 / 拒绝 / 超时）；
- 它**不进 R10**：不新增事件名、状态、状态迁移边、canonical 表或列；余额是 provider 的**只读事实**，
  不是新的平台语言。

### 22.11 补历史：没数过的 token 不能写成 0

补历史的结论是：**那两次请求的 token 恢复不了**（不是不该补）。

- 池子当时没记（那是缺陷）；
- 本地代理（codex 链）的日志只记请求与响应的**元信息**，没有 usage；
- opencode CLI 自己的数据库里只有**会话总量**（那个会话里还有不经过池子的调用，归到那两次请求上就是算不清的了）；
- provider 侧只有**账号层面**的汇总，无法归到单次请求。

而且原来那两条记录里的 **0 是一句假话**：它说的是“这次调用消费了 0 token”，而真相是“**没数过**”。
所以补历史做的事是：**不编数字，只把“0”改成“未记上”**：

- 日志本身不动（它是发生过什么的记录，改它就是改历史）；
- 旁边新增 `usage-corrections.jsonl`：每条修正写明哪一条的 usage 没数过、为什么没数过；
- 汇总里的 token 不再是一个 0，而是一个**未记上的次数**（`tokens_unknown`）；
- 补历史是**可重跑**的脚本（`scripts/backfill_usage_corrections.py`），而不是手改账。

实际补上之后：**19 次请求里，17 次的 token 是“未记上”**（两条 minimax 直连的真有数：98/15）。
办公室里现在说的是“**有 2 次的 token 没记上**”，而不是“用了 0 token”。

### 22.12 计量档的余额：两个 provider 都能报

补历史时找到了个惠所的发现：**codex 链的 provider 自己就有一个用量接口**（`/v1/usage`，本地代理透传），
它报的不只有余额，还有**逐日的用量与费用**：

```
balance 0.17370593 USD（钱包余额）
2026-10-20  22 次  actual_cost 0.0288 USD
2026-10-21  28 次  actual_cost 0.0477 USD
2026-10-22   1 次  actual_cost 0.0035 USD
```

这些是**账号层面**的数字（平台只是它的一个调用方），所以界面上明写“账号最近几天（provider 自己报的）”。
现在计量档的余额是两个真数字：**claude-provider CNY 1.04**（DeepSeek）与
**codex:geekspace USD 0.1737**（钱包）；其余两条说明自己没有可查的余额接口。
