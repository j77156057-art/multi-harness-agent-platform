# Multi-Harness Agent Platform

面向多个 AI 编程窗口的项目级协作与调度平台。它把任务领取、状态恢复、验证、审核、合并和人类操作放进同一条可追溯链路，支持 Codex、Claude Code、OpenCode 等不同 Harness 协同工作。

这是一个适合简历展示的工程项目：后端使用 FastAPI + SQLite，前端使用 Vite + 原生 JavaScript，关键状态和事件有规范约束，测试与门禁可以在没有外部平台的情况下运行。

## 项目解决什么问题

多窗口协作最容易出现三类问题：同一任务被重复领取、窗口崩溃后任务无法恢复、未经验证的代码直接进入主分支。本项目围绕这三个问题提供：

- **任务与状态管理**：任务、尝试、租约、心跳和事件流统一落库。
- **并发领取保护**：Claim、Lease 和 fencing 约束避免重复领取与过期窗口写入。
- **验证与审核链路**：提交结果先经过验证，再进入人工或管理员审核与合并流程。
- **多 Harness 适配**：通过统一适配器接入不同 CLI，保留各工具的原生工作方式。
- **人类操作界面**：Agent Office 展示任务、成员窗口、事件、额度、输出和对话；涉及状态变更的动作仍由后端守卫。
- **可追溯性**：SQLite 是唯一事实源，事件流记录关键变化，规范检查和协作纪律检查进入 CI。

## 架构概览

```text
Browser
  └─ Agent Office (Vite, 4174)
       └─ local preview gateway
            └─ Agent Platform API (FastAPI, 8848)
                 ├─ SQLite event store
                 ├─ Claim / Lease / Verify / Review services
                 ├─ Harness adapters
                 └─ scheduler and worker processes
```

主要目录：

| 目录 | 内容 |
|---|---|
| `backend/app/` | FastAPI 服务、领域服务、数据库、适配器和 worker |
| `backend/tests/` | 后端单元测试、集成测试和规范镜像测试 |
| `backend/requirements.txt` | 后端运行依赖，版本已锁定 |
| `backend/requirements-dev.txt` | 运行依赖加测试、lint 和格式化工具 |
| `office/` | Agent Office 前端与本地预览网关 |
| `office/package-lock.json` | 前端依赖锁定文件，使用 `npm ci` 安装 |
| `tools/docstate/` | R10 规范一致性检查器 |
| `tools/discipline/` | 协作登记、任务卡和分支纪律检查器 |
| `docs/spec/0.3-R10.md` | 状态机、事件和数据约束的权威规范 |
| `verify-profiles.json` | 运行环验证策略 |

## 快速运行

### 系统前置

新电脑只需准备：

- Python 3.11 或更高版本
- Node.js 18 或更高版本（包含 npm）
- Git

项目依赖不需要手工下载或填写本机路径：Python 依赖由 `backend/requirements*.txt` 提供，前端依赖由 `office/package-lock.json` 锁定。

### Windows

在仓库根目录打开 PowerShell：

```powershell
.\setup.ps1
.\start.bat
```

`setup.ps1` 会创建仓库内的 `.venv`，安装后端依赖，并执行 `office/npm ci`。`start.bat` 会启动后端：

- 入口页：<http://127.0.0.1:8848/>
- 健康检查：<http://127.0.0.1:8848/health>
- OpenAPI：<http://127.0.0.1:8848/docs>
- 规范状态：<http://127.0.0.1:8848/spec/status>

另开一个 PowerShell 窗口启动界面：

```powershell
.\start-office.bat
```

Agent Office 地址：<http://127.0.0.1:4174>

也可以使用 `start.bat all` 同时启动后端和巡检 worker。真实 Harness worker、管理员 worker 和模型 CLI 属于高级联调能力，不是本地演示的前置条件。

### Linux / macOS

```bash
./setup.sh
./start-backend.sh
```

另开终端启动 Agent Office：

```bash
cd office
npm run dev
```

如系统禁止执行脚本，可先运行：

```bash
chmod +x setup.sh start-backend.sh
```

### 使用 Make 或 Git Bash

已有 Python 环境时，也可以使用：

```bash
make install
make dev
```

Windows Git Bash 用户可以执行 `./scripts.sh install` 和 `./scripts.sh dev`。PowerShell 用户优先使用 `setup.ps1` 与 `start.bat`。

## 接入真实平台

本地启动后可以直接查看入口页、健康检查、OpenAPI 和规范状态。要让 Agent Office 显示真实任务和成员，需要另行运行平台并设置网关环境变量：

```powershell
$env:OFFICE_FORMAL_UPSTREAM = 'http://127.0.0.1:8848'
$env:OFFICE_FORMAL_TOKEN_FILE = (Join-Path (Get-Location) 'data\operator.key')
$env:OFFICE_FORMAL_ADMIN_TOKEN_FILE = (Join-Path (Get-Location) 'data\admin.key')
$env:OFFICE_FORMAL_PROJECT = '<project-id>'
$env:OFFICE_FORMAL_GROUP = '<group-id>'
cd office
npm run dev
```

令牌只保存在本地文件中，不能提交到 Git。未设置这些变量时，Agent Office 仍可打开，但会显示尚未连接正式平台，不会伪造任务数据。

真实 Harness CLI、模型中继、Buzz relay 和用户自己的 provider key 都是可选集成；它们需要用户自行配置凭据，本项目不会把任何凭据或本机绝对路径写入代码或仓库。

## 开发与验证

后端门禁包含 `docstate` 规范一致性检查（98 项断言）、协作纪律检查、ruff 检查和 pytest：

后端门禁：

```bash
./scripts.sh check
```

它依次执行规范一致性检查、协作纪律检查、ruff 检查和 pytest。也可以分别运行：

```bash
./scripts.sh docstate
./scripts.sh discipline
./scripts.sh lint
./scripts.sh test
```

前端构建与检查：

```bash
cd office
npm ci
npm run build
npm run verify
```

`npm run verify` 包含部分需要在线平台、Playwright 或本机 Harness 的冒烟脚本；没有这些外部服务时，相关脚本会如实失败，不影响后端零配置测试和前端生产构建。

## 配置原则

所有运行配置都可以通过环境变量覆盖，并有安全默认值。常用配置包括：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `AP_HOST` | `127.0.0.1` | 后端监听地址 |
| `AP_PORT` | `8848` | 后端端口 |
| `AP_DATA_DIR` | `./data` | SQLite 和运行期数据目录 |
| `AP_CHECK_SPEC` | `true` | 启动时执行规范检查 |
| `AP_AUTH_MODE` | `off` | 本地演示默认关闭认证矩阵 |
| `AP_RUNTIME_POLICY_FILE` | 仓库内 `verify-profiles.json` | Verify/Merge 运行策略 |

运行期目录、数据库、日志、虚拟环境、`node_modules` 和凭据文件均已加入 `.gitignore`。

## CI 与项目边界

GitHub Actions 在 Windows 和 Ubuntu 上执行完整门禁，确保路径假设不会只在单一操作系统上成立。项目的规范正文是只读真相源，状态和事件的实现必须与其保持一致。

当前版本重点是平台核心链路和 Agent Office 观察面。真实生产部署、第三方模型费用、云端密钥管理和多账户隔离不属于本地零配置演示范围，相关能力需要按文档提供额外服务或凭据。

## 许可证与公开说明

仓库中的源码、测试、规范和文档用于展示本项目的工程设计与实现。公开前请根据实际使用的第三方依赖和参考材料补充适用的许可证说明。
