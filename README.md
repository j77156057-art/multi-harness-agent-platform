# Multi-Harness Agent Platform

> 一个面向多 AI 编程窗口的任务协作与交付平台：让多个 Harness 并行工作时，任务领取、崩溃恢复、验证、人工审核和合并都可控、可追溯。

这是一个适合作为简历项目展示的工程系统。它不是简单的“给模型发提示词”脚本，而是把 AI 编程协作中最容易失控的部分做成了有状态、有约束、可恢复的后端流程，并配套一个可观察的 Agent Office 界面。

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

| 能力 | 项目中的实现 | 面试时可以追问的点 |
|---|---|---|
| 并发领取 | SQLite 事务、条件更新、请求幂等记录 | 两个真实数据库连接同时抢同一任务时，为什么只能有一个 winner |
| 租约与接管 | lease token、平台时钟、过期扫描、epoch 递增 | 为什么不能只靠心跳字段，旧窗口如何被拒绝 |
| 防止旧写入 | guarded write + fencing epoch + attempt 校验 | 接管发生在请求途中时，如何避免 stale writer 覆盖新 owner |
| 结果验证 | verify profile、命令白名单、verification receipt | 为什么提交结果不能直接进入 review 或 merge |
| 人工审核 | review lease、commit 一致性、权限矩阵 | Harness 为什么不能冒充管理员审核者 |
| 受控合并 | canonical repo、隔离 worktree、merge worker | 如何保留可复现的合并边界和证据 |
| 事件追踪 | append-only event store、全局 seq、replay | 当前投影损坏时如何从事件恢复 |
| 多 Harness 适配 | 注册表、统一 adapter protocol、CLI 输出解析 | 不同 CLI 的差异如何收敛到适配器边界 |
| 运行期巡检 | runtime driver、sweeper、liveness/budget 检查 | 为什么 worker 存在还不够，谁负责推进实际状态 |
| 可观察界面 | Agent Office + 本地预览网关 | 如何查看任务、成员、事件、输出和额度，而不暴露内部凭据 |

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

这是一个本地优先、强调状态一致性和可验证流程的项目。SQLite 能在不依赖外部数据库的情况下提供事务、WAL、约束和单文件数据存储，适合面试官在新电脑上直接启动；FastAPI 则让 API、鉴权和集成测试保持清晰。

这不是对生产部署的结论。多副本部署、远程数据库、密钥托管、队列和高可用需要额外的架构选择，当前版本明确把范围放在本地协作平台核心链路和可复现验证上。

## 可验证证据

README 不把“有代码”当成“已验证”。仓库中已有针对关键风险的测试和门禁：

- `backend/tests/test_claim_concurrency.py`：使用两个真实 SQLite 连接同时抢同一任务，断言只能创建一个 attempt 和一条 claim 事件；相同请求重放只得到一个结果。
- `backend/tests/test_adapter.py`：覆盖 claim → start → commit → publish → verify → review → merge 的完整链路，并覆盖 stale attempt 被接管后的旧 epoch 拒绝。
- `backend/tests/test_database_events.py`：验证迁移幂等、WAL、事件序号、事件重放、追加写入约束和事务一致性。
- `backend/tests/test_admin_multi_harness_review.py`：用三个独立 Harness 走管理员派发与逐项审核，验证 Harness 不能冒充管理员 review。
- `backend/tests/test_runtime_driver.py`：用真实 Git 和隔离 worktree 驱动任务从 claim 到 publish/submit，而不是只手工拼数据库状态。
- `tools/docstate/docstate.py`：检查权威规范与实现约束，目前 98 项通过。
- `tools/discipline/discipline.py`：检查任务登记、认领文件、任务卡、依赖和合并纪律，目前 44 项通过。
- `office/npm run build`：验证前端生产构建。

在本仓库中运行统一门禁：

```bash
./scripts.sh check
```

也可以单独运行：

```bash
python tools/docstate/docstate.py
python tools/discipline/discipline.py
cd office && npm ci && npm run build
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
backend/app/          FastAPI 服务、领域服务、数据库、worker、适配器
backend/tests/        单元、并发、集成、规范镜像和真实 Git 流程测试
backend/migrations/   SQLite schema migrations
office/               Agent Office 前端和本地预览网关
tools/docstate/       R10 规范一致性检查器
tools/discipline/     协作登记和任务纪律检查器
docs/spec/            状态机、事件和 schema 的权威规范（只读）
docs/adr/             架构决策记录
verify-profiles.json  Verify / runtime 的检查策略
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

## 面试时可以从哪里深入

这个项目适合从以下问题展开，而不是只展示一个页面：

1. 两个执行者同时 claim 时，数据库事务和幂等记录如何保证只有一个 attempt？
2. 为什么需要 lease token 和 fencing epoch 两层保护？
3. 任务被接管后，旧进程的延迟请求如何失败？
4. 为什么 verify、human review 和 merge 必须是三个独立阶段？
5. 事件流和当前投影各自承担什么职责？如何重放？
6. 不同 Harness 的差异放在哪里，怎样避免业务层出现大量 CLI 特判？
7. 为什么本地版本选择 SQLite，生产化时哪些部分必须替换或扩展？
8. 如何在不提交凭据和机器路径的前提下，让面试官在新电脑上运行？

## 当前边界

当前版本的重点是协作平台核心链路、状态一致性、可恢复执行和 Agent Office 观察面。后端依赖安装、pytest 和真实 HTTP health 检查仍受运行机器网络条件影响；真实生产部署、高可用、第三方模型费用和云端密钥管理不属于本地演示范围。

权威状态、事件和数据约束见 [`docs/spec/0.3-R10.md`](docs/spec/0.3-R10.md)。实现偏离技术栈的原因见 [`docs/adr/ADR-DEV-001-stack-deviation.md`](docs/adr/ADR-DEV-001-stack-deviation.md)。
