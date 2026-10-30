# Multi-Harness Agent Platform

> 一个面向多 AI 编程窗口的任务协作与交付平台：让多个 Harness 并行工作时，任务领取、崩溃恢复、验证、人工审核和合并都可控、可追溯。

项目将 AI 编程协作中容易失控的部分实现为有状态、有约束、可恢复的后端流程，并配套一个可观察的 Agent Office 界面。

## 先看它解决什么问题

当 Codex、Claude Code、OpenCode 等多个编程窗口同时为同一个项目工作时，常见问题不是“模型能不能写代码”，而是协作边界不清：

- 两个窗口同时领取同一任务，最后谁的结果有效说不清。
- 窗口崩溃或网络中断后，任务卡在“有人负责”的状态，其他执行者无法接手。
- 旧窗口恢复后继续写入，可能覆盖已经接管任务的新窗口结果。
- 测试通过和代码被允许合并之间缺少明确的证据链。
- 不同 Harness 的 CLI、输出格式、工作区和模型选择方式不同，接入一个就要写一套特殊逻辑。
- 只看最终状态无法解释“谁在什么时候做了什么”，故障排查只能依赖日志猜测。

本项目用一条明确的交付链路处理这些问题：

```text
任务计划
  │
  ▼
Claim（领取） → Lease / Heartbeat（租约与存活） → Harness 工作区
  │                                             │
  │                                             ▼
  └────────────── 崩溃恢复 / 接管 ← Verify（验证提交结果）
                                                   │
                                                   ▼
                                  Human Review（人工审核）
                                                   │
                                                   ▼
                                          Merge（受控合并）
```

## 这个项目的核心价值

### 1. 把“谁能写入”变成可检查的状态，而不是约定

领取任务会创建 attempt、lease 和 fencing epoch。真正写入任务状态的接口会同时检查任务、attempt 和 epoch；旧窗口即使恢复，也不能凭旧 epoch 继续操作。

这解决的是分布式协作中的 **stale writer** 问题：任务已经被新执行者接管时，旧执行者的延迟请求不能覆盖新状态。

### 2. 把重试设计成幂等操作

Claim 使用请求级和任务项级两层记录。同一个请求重放会返回原结果；同一个幂等键携带不同内容则拒绝，而不是悄悄创建第二个 attempt。

这使网络重试、CLI 重试和进程恢复可以安全进行，也让“请求发过但客户端没收到响应”的场景有确定行为。

### 3. 把崩溃恢复设计成正式流程

租约由平台自己的时钟管理，而不是相信 Harness 上报的时间。租约过期后，任务可以进入恢复/接管流程，新 attempt 获得递增的 fencing epoch，旧 attempt 的心跳和写入会被拒绝。

因此“执行窗口消失”不会等价于“任务永久丢失”，而是一个可检测、可接管的状态转换。

### 4. 把验证、审核和合并分开

执行者提交的是结果和 commit，不是直接写入主分支。平台先验证，再进入人工审核；审核还要求对应的 review lease 和被审核 commit 一致，最后才允许合并。

这条链路把三个容易混在一起的问题拆开：

```text
执行者完成 ≠ 验证通过 ≠ 人工批准 ≠ 已合并
```

### 5. 用事件流保留可重建的事实

SQLite 保存当前投影，`task_events` 保存关键状态变化。事件序号单调递增、事件记录追加写入，并支持从事件重放状态。任务状态、执行者、审核者、commit 和错误原因因此可以回溯。

这让系统不仅能回答“现在是什么状态”，还能回答“为什么变成这个状态”。

## 具体展示了哪些工程能力

| 能力 | 项目中的实现 | 设计关注点 |
|---|---|---|
| 并发领取 | SQLite 事务、条件更新、请求幂等记录 | 两个真实数据库连接同时抢同一任务时，只创建一个 attempt |
| 租约与接管 | lease token、平台时钟、过期扫描、epoch 递增 | 租约过期后允许恢复，旧执行窗口不再拥有写入权 |
| 防止旧写入 | guarded write + fencing epoch + attempt 校验 | 接管发生在请求途中时，避免 stale writer 覆盖新 owner |
| 结果验证 | verify profile、命令白名单、verification receipt | 未验证的提交不能直接进入 review 或 merge |
| 人工审核 | review lease、commit 一致性、权限矩阵 | 审核身份和被审核 commit 都必须可确认 |
| 受控合并 | canonical repo、隔离 worktree、merge worker | 合并边界和证据可以复现 |
| 事件追踪 | append-only event store、全局 seq、replay | 当前投影损坏时可以从事件重建 |
| 多 Harness 适配 | 注册表、统一 adapter protocol、CLI 输出解析 | 将不同 CLI 的差异限制在适配器边界 |
| 运行期巡检 | runtime driver、sweeper、liveness/budget 检查 | 持续推进状态并发现卡住的执行 |
| 可观察界面 | Agent Office + 本地预览网关 | 查看任务、成员、事件、输出和额度，不暴露内部凭据 |

## 一条完整的端到端路径

系统中一条任务从创建到合并大致经过以下步骤：

1. 管理员发布并批准任务计划，形成不可随意修改的 snapshot。
2. Harness 通过统一 adapter 请求 claim；平台使用幂等键处理重复请求。
3. 平台为成功领取的任务创建 attempt、development lease 和 fencing epoch。
4. runtime driver 为 attempt 准备隔离 Git worktree，Harness 在其中实现任务。
5. Harness 发布分支和 commit，提交结果；平台保存对应的 receipt。
6. Verify worker 根据计划快照中的 profile 执行检查，失败结果不会进入审核。
7. 管理员获取 review lease，针对指定 commit 提交 approve 或 findings。
8. 只有验证通过、审核通过且 commit 仍匹配时，Merge worker 才执行合并。
9. 每个关键动作写入事件流，Agent Office 可读取当前投影和操作轨迹。

## 架构

```text
┌──────────────────────────────────────────────────────────┐
│ Agent Office                                             │
│ Vite + 原生 JavaScript · 任务 / Harness / 事件 / 输出观察 │
└───────────────────────┬──────────────────────────────────┘
                        │ local preview gateway
┌───────────────────────▼──────────────────────────────────┐
│ FastAPI API                                              │
│ auth · tasks · claim · lease · verify · review · merge   │
└───────┬───────────────────┬───────────────────┬──────────┘
        │                   │                   │
   SQLite + WAL       Worker / Scheduler    Adapter Registry
   domain tables      sweeper / runtime     Codex / Claude /
   append-only events driver                OpenCode / ...
        │                   │                   │
        └─────────────── Canonical Git Repo ───────────────┘
                         isolated worktrees
```

### 为什么选择 FastAPI + SQLite

这是一个本地优先、强调状态一致性和可验证流程的项目。SQLite 能在不依赖外部数据库的情况下提供事务、WAL、约束和单文件数据存储，便于快速启动和复现；FastAPI 则让 API、鉴权和集成测试保持清晰。

这不是对生产部署的结论。多副本部署、远程数据库、密钥托管、队列和高可用需要额外的架构选择，当前版本明确把范围放在本地协作平台核心链路和可复现验证上。

## 可验证证据

README 不把“有代码”当成“已验证”。仓库中已有针对关键风险的测试：

- `backend/tests/test_claim_concurrency.py`：使用两个真实 SQLite 连接同时抢同一任务，断言只能创建一个 attempt 和一条 claim 事件；相同请求重放只得到一个结果。
- `backend/tests/test_adapter.py`：覆盖 claim → start → commit → publish → verify → review → merge 的完整链路，并覆盖 stale attempt 被接管后的旧 epoch 拒绝。
- `backend/tests/test_database_events.py`：验证迁移幂等、WAL、事件序号、事件重放、追加写入约束和事务一致性。
- `backend/tests/test_admin_multi_harness_review.py`：用三个独立 Harness 走管理员派发与逐项审核，验证 Harness 不能冒充管理员 review。
- `backend/tests/test_agenttools.py`：平台工具的三条下限（路径 / 凭据 / 命令）、文本协议循环的"拒绝回灌"、以及审阅只读。
- `backend/tests/test_agenttools_mcp.py`：MCP 协议本身，以及"别人的尝试借不走、过期尝试自动关闭、工作树指到工位目录外就拒"。
- `backend/tests/test_agenttools_wiring.py`：把工具交给 CLI 的那一层——令牌不进 argv、写进工作树的配置算临时文件、**声明不许覆盖授权落点**（否则交付物能被藏进临时文件删掉）。
- `backend/tests/test_runtime_driver.py`：用真实 Git 和隔离 worktree 驱动任务从 claim 到 publish/submit，而不是只手工拼数据库状态。
- `office/npm run build`：验证前端生产构建。
- `git diff --check`：检查提交中的空白和补丁格式问题。

运行后端测试：

```bash
cd backend
python -m pytest
```

运行前端构建与冒烟脚本：

```bash
cd office
npm ci
npm run build
npm run verify
```

## 五分钟本地演示

### 前置环境

- Python 3.11 或更高版本
- Node.js 18 或更高版本，包含 npm
- Git
- 首次安装时可访问 Python Package Index 和 npm registry

不需要预先安装真实 Harness CLI，不需要 API key，也不需要连接模型中继即可运行本地基础演示。真实 Harness、模型服务、Buzz relay 和 provider key 属于可选集成。

### Windows

在仓库根目录打开 PowerShell：

```powershell
.\setup.ps1
.\start.bat
```

另开一个 PowerShell 窗口启动 Agent Office：

```powershell
.\start-office.bat
```

### 从空白到能用：一条命令

上面那两步之后，**后端能跑，但界面会显示「未配置」** —— 这不是坏了，是设计如此：Agent Office 能派活、能批方案，所以它的网关拒绝连到没有强制认证的后端（`formal_authentication_required`）。要让界面真的能用，还差：强制认证、两个令牌文件、一个项目与分组、五个 `OFFICE_FORMAL_*` 变量。

这些一条命令做完（幂等，重复跑不会出问题）：

```powershell
.\start-demo.ps1
```

它按顺序做四件事：

1. 以 `AP_AUTH_MODE=enforce` + `AP_ADMIN_SUBJECTS=codex-window-admin` 起后端（端口 `AP_PORT`，默认 8848）
2. 签发两个令牌文件：`data\office-operator.key`（人）与 `data\codex-window-admin.key`（管理员窗口）
3. 登记管理员窗口，并建一个演示项目 `demo-project` 与分组 `demo-group`（已存在就复用）
4. 带着 `OFFICE_FORMAL_UPSTREAM` / `TOKEN_FILE` / `ADMIN_TOKEN_FILE` / `PROJECT` / `GROUP` 起 Agent Office

跑完窗口里会打印两个地址与启动 worker 的命令。界面端口用 `OFFICE_PORT` 改（默认 4174）。

**已经跑过 `start.bat` 的话**：它会以 `auth_mode=off` 占着 8848，脚本会明确报错让你先停掉，而不是偷偷连上去。

### 空白机器实测

在一台只装了 Python 3.11+ 与 Node 18+ 的机器上，从 `git clone` 开始的实际结果：

| 步骤 | 结果 |
|---|---|
| `.\setup.ps1` | 建 `.venv`、装后端依赖、`npm ci` 装前端依赖，输出 `Setup complete.` |
| `.\start-demo.ps1` | 后端 `auth_mode=enforce`、两个令牌、项目与分组、界面全部就绪；`/api/formal/status` 返回 `configured:true, connected:true` |
| 数据放在哪 | 全部在这个克隆里：`data\state.db`（首次启动自动建）、`wt\`（工作树根）、`data\*.key`（令牌，不进仓库） |

没有预先存在的令牌、数据库或仓库：`data\`、`wt\` 由后端自己创建，`canonical.git` 由正式模式的脚本创建。产品代码里没有写死的本机路径。


### Windows 正式 Harness 模式

本地基础演示默认使用 `AP_AUTH_MODE=off`，不需要令牌；真实 Harness worker 和正式 Office 网关必须使用强制认证。面试官如果要运行真实成员协作，需要先拥有平台已登记的 Harness 令牌文件，并放在本机 `data\*.key` 中。令牌内容不会写入仓库。

在仓库根目录执行：

```powershell
.\setup.ps1
.\start-formal.ps1
```

`start-formal.ps1` 会先启动后端并确认 `/health` 返回 `auth_mode=enforce`，然后启动六个成员 worker 和 `codex-window-admin` 管理员 worker。默认正式群组为 `office-formal`，默认地址为 `http://127.0.0.1:8848`。另开窗口运行 `start-office.bat` 后，按 `office/README.md` 配置正式 Office 环境变量。

没有平台登记的令牌时，请使用上面的本地基础演示；不要把示例令牌或个人凭据提交到 GitHub。

## 在一台新电脑上装齐 Harness CLI

平台能驱动的每个 CLI，其官方安装入口都登记在
`backend/app/adapter/installers.py`，并通过一个只读接口暴露出来：

```text
GET /api/v1/harnesses/installers
```

它回答两个问题，都不需要先配好令牌：

1. **官方从哪里装** —— 厂商自己的文档页、按操作系统给出的安装命令、装完的验证命令。
   平台只把命令交给人复制执行，自己不下载、不运行任何安装脚本。
2. **这台机器上装了没有** —— 对每个 CLI 跑一次 `<命令> --version`，读一行的版本号。
   探测复用适配器自己的二进制解析（`PATH`、Codex 的安装目录、Qoder 的
   `~/.qoder-cn/bin`、npm 写的 `.cmd` 中间层），所以「探测说找不到」与
   「worker 起不来」是同一个判据，不会出现界面说缺、实际能跑的情况。

状态只有这五个，含义不重叠：

| 状态 | 意思 |
|---|---|
| `ready` | 找到命令，且版本号读得出来 |
| `missing` | `PATH` 与平台已知安装目录里都没有 |
| `version_failed` | 命令在，但版本读不出来（还不可用） |
| `unsupported` | 厂商没有这个系统/架构的官方版本 |
| `unknown` | 没有探测（只请求了安装方式，或问的是另一个系统） |

两点刻意不做：

- **不把「装好了」说成「能干活」。** 版本号读得出来只证明命令存在；登录、provider
  配置归人（接口里的 `login_note` 说清还差什么）。平台不读凭据，因此也不会
  声称某个窗口已经登录。
- **不猜地址。** 没有核实过的官方入口留空，界面写「平台不猜」，而不是填一个
  看起来合理的链接。

查看本机现状（后端跑起来之后）：

```powershell
# 只看安装方式，不跑任何子进程
Invoke-RestMethod 'http://127.0.0.1:8848/api/v1/harnesses/installers?probe_local=false'

# 探测这台机器（enforce 模式下需要带操作者令牌）
Invoke-RestMethod -Headers @{ Authorization = "Bearer $op" } `
  'http://127.0.0.1:8848/api/v1/harnesses/installers'
```

Agent Office 的右栏有一张对应的「Harness CLI」卡片：按状态分组（缺的排在前面）、
默认收起、每条给出官方文档与安装命令，并有一个「探测这台机器」按钮。进页面时
只取安装方式，按下按钮才真的去跑版本命令，因此打开面板不会卡住。

访问：

```text
Agent Office: http://127.0.0.1:4174
后端入口:     http://127.0.0.1:8848
健康检查:     http://127.0.0.1:8848/health
OpenAPI:       http://127.0.0.1:8848/docs
规范状态:      http://127.0.0.1:8848/spec/status
```

### Linux / macOS

```bash
./setup.sh
./start-backend.sh
```

另开终端：

```bash
cd office
npm run dev
```

如果系统没有执行权限：

```bash
chmod +x setup.sh start-backend.sh
```

### 安装内容

- 后端运行依赖：`backend/requirements.txt`
- 后端测试与开发依赖：`backend/requirements-dev.txt`
- 前端锁定依赖：`office/package-lock.json`
- Windows 安装入口：`setup.ps1`
- Linux/macOS 安装入口：`setup.sh`
- 运行期目录：仓库内 `.venv`、`data/` 和 `office/node_modules/`，均不提交

脚本使用自身目录定位，不依赖开发机绝对路径。它们会创建本地虚拟环境和依赖目录，但不会把依赖包本体塞进 Git 仓库；这比提交 `.venv` 或 `node_modules` 更适合跨 Windows、Linux、macOS 的公开项目。

## 平台自己的工具：MCP 为主路，`platform` 保底

有的窗口干不动活，不是因为模型笨，而是因为**它调不了工具**：这个 attempt 被路由到额度池里一条
CLI 顶替的模型（免费那档），那条通道只会回话，没有 `tool_calls` 可给。这是一条**通道**问题，
不是提示词问题 —— 所以在提示词里加"请使用工具"没有用。

平台因此提供同一张工具表的两种用法：

| | 谁在跑循环 | 什么时候用 |
|---|---|---|
| **MCP**（主路） | 窗口自己的 CLI | 模型能调工具 —— 平台只提供工具和执行 |
| **`platform` 基座**（保底） | 平台进程 | 模型连"调用"都产不出来 |

### 主路：MCP —— 窗口用自己的循环

```text
POST /api/v1/agenttools/mcp?task_id=<任务>&attempt_id=<尝试>
Authorization: Bearer <该窗口自己的平台令牌>
```

讲 JSON-RPC（`initialize` / `tools/list` / `tools/call`），工具带 JSON Schema 供客户端校验参数。
**worker 会自动把这份配置交给 CLI**，不需要人手工写：

| CLI | 怎么交给它 | 临时文件 |
|---|---|---|
| `claude` | `--mcp-config <文件>` | 写在临时目录，不进工作树、不进 argv |
| `opencode` | 走额度池时并进那份隔离配置；不走时写工作树里的 `opencode.json` | 声明为临时文件，跑完删除 |

没确认过机制的 CLI 不会"假装有工具"（`CliHarness.mcp_binding()` 返回 `None`）。

**四条边界，全部来自平台自己写下的事实，不由客户端声明：**

| 边界 | 从哪来 |
|---|---|
| 工作树 | `workspaces.path`（平台发给这次尝试的那一个） |
| 授权落点 | 这次尝试**认领时**的方案快照——方案后来改了也不会放大已开跑的范围 |
| 谁可以调 | 尝试的 `owner` 必须等于令牌主人，别人的窗口借用一律 403 |
| 还能不能调 | 任务当前尝试一变就关闭（409），上一轮不能再改树 |

拒绝是**内容**而不是传输错误：越界、凭据、命令被拦都以 `isError: true` 加一句能照着改的话返回。
`finish` 不在这条通道里——它是平台侧循环的交活动作，CLI 有自己的结束方式。

实测（本机、免费档 `opencode/flint-40b-free`、18 次模型调用、零计费）：opencode 用自己原来的
循环依次调用 `platform.create_file` / `platform.read_file` / `platform.run_command`，文件真落到
工作树，机器校验 1 → 0 通过，提交由平台完成。

### 保底：`platform` 基座 —— 平台替模型跑循环

基座 ID 是 `platform`（界面显示「平台工具」）。它和别的 Harness 走同一条路：
claim → 平台给它一个工作树 → 在里面跑一个进程 → 判范围 → 提交。

区别只有一个：**工具由平台执行**，模型只负责说下一步做什么，用的是一个 JSON 对象：

```json
{"thought": "先看看有什么", "tool": "list_files", "input": {"path": "docs"}}
{"thought": "写它", "tool": "create_file", "input": {"path": "docs/a.md", "content": "全文"}}
{"thought": "交活", "tool": "finish", "input": {"summary": "改了哪些文件、怎么验证的"}}
```

所以它不需要模型会 function calling —— 文本能写 JSON 就够。工具表很小，刻意的：
`list_files` / `read_file` / `grep` / `create_file` / `apply_edit` / `run_command` /
`git_read` / `finish`（`finish` 只有这条保底路有；MCP 那条没有，见上）。

### 两条路共用一套下限

三条下限写在 `backend/app/agenttools/floor.py` 里，越界一律**当场拒绝并把原因回灌给执行方**
（拒绝是观察，不是死路）：

| 下限 | 内容 |
|---|---|
| 路径 | 读写都在这次 attempt 的工作树里；写入还必须落在任务的 `exclusive_paths` 内 |
| 凭据 | `data/*.key`、`.env`、私钥按**文件名**拒绝，不进模型上下文，也不进目录清单 |
| 命令 | 批量删除 / 外网传输 / 系统操控按名单拦；`git` 写操作（commit、push、reset…）一律不跑 |

提交由平台做：工具表里没有 git 写，交活后由 `ScopedRunner` 核对范围再提交，
所以它不会把别人的改动顺手带走。

**这条规则对 MCP 那条同样成立**：拿到平台工具的窗口一律由平台提交。实测踩过——opencode 用平台的
`run_command` 去 `git commit`，被工具门槛拒了（那条通道故意没有 git 写），结果谁都没提交，交付被
判成"没有提交"而整单退回。给了平台工具就必须由平台提交，这是同一件事的两面。

**`platform` 保底路必须有一个池子里的模型。** 它没有自己的 provider，用哪个模型由人在额度池里指定
（`POST /api/v1/adapter/model-selection`，`model_mode=pool`）。没指定时进程退出并说清这一点，
不会假装交了个空活。

审阅用它时是**只读**的（`--read-only`）：写文件、改文件、跑命令都回一句「本次审阅不可用」，
只有读与搜能用 —— 会动手的审阅者不是审阅者。

一次真实运行的形状（本机实测，免费档 `codebuddy/hy3`，52 秒）：

```text
[platform] 模型通道：http://127.0.0.1:8848/api/v1/pool/v1 · codebuddy/hy3
[platform] 工作树：…/wt/2ae1
[platform] 授权落点：docs/platform-tools.md
[1] list_files → …
[2] create_file → 已写入 `docs/platform-tools.md`（7 行）。
[3] read_file → `docs/platform-tools.md` （7 行）：
[4] finish → …
```

启动一个窗口（和别的窗口一样，只是基座不同）：

```powershell
python -m app.adapter.formal_worker --url http://127.0.0.1:8848 `
  --token-file data\platform-member-1.key --harness-id platform-member-1 `
  --group <分组> --canonical-repo <repo>\canonical.git --worktree-root <repo>\wt --once
```

## Agent Office 能看什么

本地基础启动后，Office 可以访问平台入口、健康状态、规范状态和 API 文档。连接正式平台后，它还可以展示：

- 项目、任务和当前执行者
- attempt、租约和任务进度
- 验证、审核与合并状态
- Harness 注册信息和适配器状态
- 事件轨迹、输出摘要和对话
- 模型池、额度和实际用量

正式平台连接通过环境变量配置，令牌只从本地文件读取，不写入 README、源码或 Git。未配置正式平台时，界面应显示未连接状态，不伪造任务数据。

## 项目目录

```text
start-demo.ps1        一条命令从空白到「界面能用」（认证 + 令牌 + 项目/分组 + 界面）
backend/app/          FastAPI 服务、领域服务、数据库、worker、适配器
backend/tests/        单元、并发、集成和真实 Git 流程测试
backend/migrations/   SQLite schema migrations
office/               Agent Office 前端和本地预览网关
tools/                本地开发检查和合并辅助工具
docs/adr/             架构决策记录
```

## 真实集成边界

本项目把“本地基础演示”和“接入真实执行环境”分开：

**本地基础演示包含：**

- FastAPI + SQLite 服务
- 任务状态与事件流
- Claim / Lease / Verify / Review / Merge 核心流程
- 本地 Agent Office
- 规范、纪律和前端构建检查

**需要额外配置的能力：**

- 真实 Codex、Claude Code、OpenCode 或其他 Harness CLI
- 模型服务和 provider key
- Buzz / Nostr relay
- 管理员 token、项目和群组配置
- 面向生产的远程数据库、密钥托管和多副本部署

这些集成不是本地零配置运行的前置条件。仓库不包含任何 token、API key、私钥或本机绝对路径。

## 设计要点

- 并发领取通过事务、条件更新和幂等记录共同约束，而非依赖客户端互斥。
- 租约描述执行者的存活期限，fencing epoch 阻止过期执行者继续写入，两者解决不同问题。
- 验证、人工审核和合并分阶段执行，后续阶段依赖前序阶段的结果与证据。
- 事件流记录状态变化，当前投影服务查询；两者在同一事务中提交，以支持追踪和重放。
- Harness 差异由注册信息和适配器协议承接，平台服务使用统一的任务协作接口。
- SQLite 便于本地运行和测试；多副本部署等生产需求需要重新评估存储与协调组件。

## 当前边界

当前版本的重点是协作平台核心链路、状态一致性、可恢复执行和 Agent Office 观察面。后端依赖安装、pytest 和真实 HTTP health 检查仍受运行机器网络条件影响；真实生产部署、高可用、第三方模型费用和云端密钥管理不属于本地演示范围。

平台工具（`backend/app/agenttools/`）这块的边界写清楚，免得当成"什么都支持"：

- **MCP 自动接线只做了 `claude` 与 `opencode`**。`pi` / `codebuddy` / `codex` / `qoder` 的 MCP 配置方式还没核对过，它们的 `mcp_binding()` 返回 `None` —— 不假装支持。
- **模型仍要能调工具**。MCP 救不了"通道本身给不出 `tool_calls`"那种情况（额度池里 CLI 顶替的免费档），那种只能走 `platform` 保底路。
- **写操作只在本任务的工作树内**，且必须有授权落点；提交、推送、切分支都不在工具手里。
