# 多 Harness Agent 协作与调度平台 —— MVP

统一协调多个 AI Harness（Codex、Claude Code、OpenCode 等）的项目级协作平台。

**MVP 要验证的三件事**（来自 `docs/spec/0.3-R10.md` §0）：

1. 任务不会重复领取；
2. 任务状态可恢复（调度器重启、owner 崩溃都能恢复）；
3. 代码经过验证后才能合并。

**当前状态**：MVP-01（项目骨架与规范绑定）、MVP-02（SQLite schema 与事件事实源）
已完成并合入 `main`。后端已有 `/health` 与 `/spec/status` 两个运维端点，
业务端点从 MVP-08 开始。进度以 [`TASKS.md`](TASKS.md) 为准。

---

## ⚠️ 如果你是来干活的 AI Harness

**不要在仓库根目录 `new agent/` 编辑文件。** 每个 AI 在自己的隔离工作区里干活：

~~~powershell
./scripts/new-ai.ps1 -Harness <你的名字>    # 幂等；已存在则拒绝且不改动
cd .worktrees/<你的名字>
git switch -c agent/<group-short>/<harness>/<task-id>/<attempt-id> main
~~~

理由：多个 AI 共享一个工作区会导致提交被无声丢弃 —— 任何人的 `git checkout` /
`git reset` 都会改变所有人的工作树，正在编辑的文件会消失而 `git status` 仍显示
干净。本项目已因此丢失 4 个提交。仓库根目录只用于合并与跑门禁。

然后按顺序：

1. 在 [`TASKS.md`](TASKS.md) 的「认领登记」登记 `task_id` / Harness / 时间 / 分支，
   **然后**才动手（登记让其他 AI 看得见任务占用）；
2. 从 [`docs/spec/mvp-task-checklist.md`](docs/spec/mvp-task-checklist.md) 领任务，
   复制其中的「统一任务卡模板」填写；
3. 提交前 `./scripts.sh check` 三道门禁全绿，**禁止在 `main` 上直接提交**。

完整规则见 [`AGENTS.md`](AGENTS.md)（会话自动载入）与
[`docs/dev-convention.md`](docs/dev-convention.md)（H-1～H-7 硬约束）。

---

## 快速开始

前置：Python 3.11+（开发用 3.13）、Git。

### Windows（Git Bash）

~~~bash
# 1. 创建 venv 并装依赖
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt

# 2. 跑门禁（必须全绿）
./scripts.sh check
#   等价于 make check（需装了 make）

# 3. 启动后端
cd backend && ../.venv/Scripts/python.exe -m app.main
~~~

### Windows 一键启动（cmd / 双击 `start.bat`）

~~~bat
start.bat          rem 后端 → http://127.0.0.1:8848（等价 make dev）
start.bat all      rem 后端 + 巡检循环（两个窗口）
start.bat workers  rem 只起巡检循环（app.scheduler --loop）
start-admin.bat    rem 管理员 harness 自循环（目标细化/分派/审核）
start.bat help     rem 用法
~~~

前置仍是一次性的（venv，见上一节）；缺 venv 时 `start.bat` 会打印修复命令
并以 exit 2 退出。人类界面是 Agent Office（4174，独立仓，单独启动）。

`start-admin.bat` 是管理员 harness 的运行器，不是第二个调度器：它读取群里
新的 `project-goal` 消息，让管理员自己的模型通道细化目标，用管理员 token
调用现有分派路由；随后对 `awaiting_review` 任务领取审核租约、读真实 diff，
再把模型裁决提交到现有审核路由。默认身份 `codex-window-admin`、群
`office-formal-2`，可用 `AP_ADMIN_HARNESS_ID` / `AP_ADMIN_GROUP` /
`AP_ADMIN_TOKEN_FILE` 覆盖。首次启动默认不重放历史目标；需要时加
`--replay-latest`。模型失败或跨提供方规则拒绝时只记录并等待，不会伪造通过。
成员 worker 仍由各自窗口单独启动。管理员 worker 与成员 worker 一样要求后端以
`AP_AUTH_MODE=enforce` 运行，并且 token 文件属于已注册的管理员窗口。
先用 `start-admin.bat --once --dry-run` 做只读连接检查，不会写平台。
若管理员是 `codex-window-admin`，它用本机 `codex` CLI；codex 的 provider 中继
（例如 `http://127.0.0.1:8402`）必须可用，否则模型调用 fail-closed 并在群里报告，
不会伪造结论。必要时用 `AP_ADMIN_MODEL_CHANNEL` 显式指向另一条已装 CLI 通道。

运行环（Verify/Merge 的正式驱动，F6）：`python -m app.runtime --once` 跑一轮、
`--loop` 常驻（`AP_RUNTIME_INTERVAL` 默认 10s）、`--check` 只读盘点候选；verify
命令来自 `AP_RUNTIME_POLICY_FILE` 指向的人类维护策略（未设时用仓库
`verify-profiles.json`），策略缺失=拒绝执行（exit 2）。

### Harness CLI 环境要求（adapter-hardening，drill F3/F4）

- **codex**：不在 PATH——把 `%LOCALAPPDATA%\OpenAI\Codex\bin` 加进 PATH。
  Windows 上 codex 自带的 workspace-write 沙箱起不了 shell（F3，实测
  `CreateProcessAsUserW failed: 2`），适配器默认改用 `--sandbox
  danger-full-access`（POSIX 仍是 `--full-auto`）；`AP_CODEX_SANDBOX=
  workspace-write|danger-full-access` 可显式覆盖，未知值拒绝启动。边界口径：
  Windows 上 CLI 沙箱不在防线内——防线=loopback+Host 校验+令牌层，OS ACL
  隔离属多账户阶段（适配器 docstring 有完整声明）。
- **opencode**：Windows 上适配器经 `bash -lc 'exec "$0" "$@"'` 启动（F4：Python
  直启会撞后台服务的 `Database is not empty and has no session table`）；经
  `~/.config/opencode/opencode.json` 把 provider 指向本地中继。`AP_OPENCODE_
  LAUNCH=direct|bash` 可覆盖；找不到 bash 时回落直启而不是失败。

### 有 make 的环境

~~~bash
make install
make check
make dev
~~~

### 验证

启动后访问：

| 地址 | 说明 |
|---|---|
| <http://127.0.0.1:8848/> | 入口页（常用链接 + Agent Office 地址） |
| <http://127.0.0.1:8848/health> | 存活 + 配置摘要 |
| <http://127.0.0.1:8848/spec/status> | 规范一致性检查（98 项） |
| <http://127.0.0.1:8848/docs> | OpenAPI 交互文档 |

启动时会自动跑规范检查；**不通过会拒绝启动**（`AP_CHECK_SPEC=0` 可临时跳过，
但不要在 CI 里这么干）。

---

## 常用命令

~~~bash
make docstate   # 只跑规范一致性检查（98 项断言）
make test       # pytest
make lint       # ruff check + format --check
make fmt        # ruff 自动修复 + 格式化
make check      # 完整门禁：docstate + lint + test（提交前必须全绿）
make clean      # 清缓存（不动 data/ 与 wt/）
~~~

无 make 时用 `./scripts.sh {docstate|lint|test|check|dev|install}`。

---

## 主体令牌与权限矩阵（auth-layer，默认关闭）

R10 §17 的 API 层矩阵执行按开关启用：`AP_AUTH_MODE=off`（默认，零行为变化）
或 `enforce`（adapter/review 面要 harness 令牌，human/管理面要人类令牌；
声明的主体必须等于令牌主体；每次拒绝写恰一条 `permission_denied` 审计事件）。

~~~bash
# 签发（明文只打印一次；库内只有 sha256 哈希；harness 必须先在注册表）
cd backend && ../.venv/Scripts/python.exe -m app.auth issue --type harness --id codex-1
../.venv/Scripts/python.exe -m app.auth list
../.venv/Scripts/python.exe -m app.auth revoke --token apt_...
# 请求携带：Authorization: Bearer apt_...
~~~

**切到 `enforce` 的步骤**（原独立控制台已删除；Agent Office 为唯一人类界面）：
① 签发人类令牌（`--type human --id <名字>`）；② 在调用平台 API 的客户端
（Agent Office 或脚本）设置 `Authorization: Bearer`；③ 设
`AP_AUTH_MODE=enforce` 重启后端。令牌无效时 API 返回
`auth.token_invalid` / `auth.permission_denied`，拒绝原因显示在调用方错误面。

> ⚠️ **全量 enforce 还差 harness 侧**：Adapter worker / 演练脚本 / 真实 adapter
> 测试尚未携带令牌（它们仍在 off 模式跑），接线前请让它们继续连 off 实例
> （原 frontend-token 卡内已声明）。

---

## Buzz 单向桥（buzz-bridge，默认未配置即 no-op）

按 [ADR-DEV-005](docs/adr/ADR-DEV-005-buzz-one-way-bridge.md) 实现：把平台事件
**单向**发布到 Buzz 频道（kind:9 + `["h", channel]`，WS + NIP-42 AUTH，BIP340
签名）。**Buzz 只是展示层**——桥不订阅、不回读，Buzz 侧任何状态都不进平台库、
不参与状态判定（只写不读，结构上杜绝第二事实源）。

~~~bash
# 生成 agent 私钥（受控凭据文件：不进库/不进仓库/不进日志）
python -c "import secrets; print(secrets.token_hex(32))" > ~/.ap-buzz-key.hex
export AP_BUZZ_RELAY_WS=wss://relay.example          # Buzz relay
export AP_BUZZ_CHANNEL=<频道 uuid>                    # NIP-29 group
export AP_BUZZ_KEY_FILE=~/.ap-buzz-key.hex
cd backend && ../.venv/Scripts/python.exe -m app.buzz --check   # 配置+游标+积压，exit 0/2
../.venv/Scripts/python.exe -m app.buzz --once                  # 单轮发布，exit 0/1
../.venv/Scripts/python.exe -m app.buzz --loop --interval 10
~~~

三项一起配才启用：**三项全空 = 显式 no-op（exit 0，不连 WS）**；只配一半 =
拒绝（exit 2，fail-closed）。发布集 = §37.7 迁移事件 − `escalation_reminder`
+ `permission_denied`/`secret_scan_blocked`；消息是**字段白名单投影**（不含
payload 任何内容）。游标 `buzz_cursors` 只在整批 OK 后推进；事件 id = 内容
哈希（时间取源事件），重放同 id → relay 去重（至少一次 + 去重 ≈ 恰好一次）。

---

## 目录结构

~~~text
backend/app/          业务代码
  spec.py             R10 常量的代码侧镜像（状态名/事件名唯一来源）
  config.py           环境变量配置
  main.py             FastAPI 入口
backend/tests/        测试（含与 R10 正文的交叉验证）
office/              UI：Agent Office 数据版界面（Vite + 原生 JS，4174）；
  src/              应用外壳与办公室（任务表 / 详情 / 额度池 / 群聊 / 任务书 / 成员动态）
  server/           预览层网关（读平台事实，按群裁剪）
  scripts/          冒烟脚本（需 node + playwright，**不在本仓门禁内**）
docs/
  spec/0.3-R10.md     ★ 唯一真相源（只读！改动走复审流程）
  spec/mvp-task-checklist.md  任务清单（0.4 重排版）
  dev-convention.md   开发约定（硬约束 H-1～H-6）
  state-projection.md 状态投影声明（MVP 实现哪些/省略哪些）
  adr/                架构决策记录
  ao-capability-audit.md  AO 源码能力核对报告
  prep/               各 AI 的准备工作（共享区，不进主分支）
tools/docstate/       规范一致性检查器（零外部依赖）
data/                 运行期数据（git 忽略）
wt/                   worktree 物理目录（git 忽略）
canonical.git         调度中心持有的 bare 仓库（git 忽略）
~~~

---

## 硬约束（违反即打回）

完整版见 `docs/dev-convention.md`，摘要：

| 编号 | 约束 |
|---|---|
| **H-1** | 状态机/事件名唯一真相源是 R10 §10 / §37.7。禁止在代码里写字面量或新增状态 |
| **H-2** | `docstate` 是门禁不是工具。CI 和应用启动都会跑，失败拒绝启动 |
| **H-3** | 技术栈偏离 R10 §5 已记录在 `docs/adr/ADR-DEV-001-stack-deviation.md` |
| **H-4** | 没有 `test_id` 的完成标准不可验收 |
| **H-5** | 并行任务修改范围不得重叠；跨边界改动需owner 同意 |
| **H-6** | 状态变更只能走 API，禁止裸 SQL 改状态字段 |

---

## 当前可执行的门禁

| 门禁 | 数量 | 作用 |
|---|---|---|
| `docstate` | 98 项断言 | R10 文档自洽性：allowlist 封闭性、迁移表单值 next、schema 字段、唯一键列已声明、test_id 无悬空 |
| `discipline` | 42 项断言 | 协作纪律：`TASKS.md` 与 `docs/claims/` 互相覆盖、任务卡有 test_id 与真相源引用、已完成卡有 commit 与验证证据、**依赖图无环且与任务清单一致、关键路径长度由依赖图算出**、认领文件序号唯一连续、文档声明的断言数与实际相符、**已合入 main 的任务必须已登记/已完成/证据完整（git 历史反向核对，浅克隆 fail-closed）**、**未合入 main 但已开工的分支，其认领必须已登记（否则开工前后都无处登记）** |
| `test_spec_mirror.py` | 17 项 | 代码侧枚举与 R10 正文的交叉验证 |
| `test_database_events.py` | 9 项 | 事件只追加、并发定序、失败不留孤儿事件、迁移幂等 |
| `ruff` | check + format | 代码风格 |

**哪些规则其实靠自觉？** 见
[`docs/dev-convention.md` 的「约束的强制程度」](docs/dev-convention.md) —— 那里
逐条列明已强制 / 靠自觉 / 根本没实现。纪律层此前几乎全是散文，本表是把它
变成会红的断言的起点。

**为什么要这么多机械检查？** R10 复审的教训：R9 自评"10 条修订全部闭环"是对的，
但 R9 自身仍有 6 处机械缺口（事件未登记 allowlist、声明数字与实际不符、
关键迁移边被折行拆断……），**全部是人工核对发现不了的**。
详见 `docs/ao-capability-audit.md` §6 的方法论反哺建议。

---

## 环境变量

全部可选，有安全默认值。完整列表见 `backend/app/config.py`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `AP_HOST` | `127.0.0.1` | R10 §5：v1 只绑 localhost |
| `AP_PORT` | `8848` | |
| `AP_DATA_DIR` | `./data` | SQLite 位置 |
| `AP_SPEC_PATH` | `docs/spec/0.3-R10.md` | 规范文件 |
| `AP_CHECK_SPEC` | `true` | 启动时校验规范 |
| `AP_INSECURE_SINGLE_ACCOUNT` | `true` | MVP 临时措施，见 MVP-05 |

---

## 下一步

按 `docs/spec/mvp-task-checklist.md`（0.4 重排版）。**权威进度以
[`TASKS.md`](TASKS.md) 为准**，下表仅供快速定位：

| 任务 | 状态 |
|---|---|
| MVP-01 骨架与规范绑定 | ✅ 已合入 |
| MVP-02 SQLite schema 与事件事实源 | ✅ 已验收 |
| MVP-03 状态机与 Plan 校验 | 🔜 下一个（关键路径上唯一可开工） |
| MVP-04 Claim/Lease/fencing | ★ 瓶颈，等 MVP-03 |
| 其余 | 见 TASKS.md |

**关键路径（9 个任务）**：
`MVP-01 → MVP-02 → MVP-03 → MVP-04 → MVP-05 → MVP-06 → MVP-07 → MVP-10b → MVP-11`

该长度由 `tools/discipline` 从依赖图算出并断言，不是手写数字 —— 改依赖图而不改
清单（或反之）会立刻变红。

**被阻塞的 AI 不要空等**：每个任务卡都有"可提前做的 prep 项"
（如 MVP-08 的 OpenAPI 契约草案可以在 MVP-02 落地前完成）。

---

## 参考

| 文档 | 内容 |
|---|---|
| `docs/spec/0.3-R10.md` | 设计规范（R10 终审修订版，2403 行） |
| `docs/spec/mvp-task-checklist.md` | 任务清单（0.4 重排版） |
| `docs/dev-convention.md` | 开发约定 |
| `docs/state-projection.md` | 状态投影声明 |
| `docs/ao-capability-audit.md` | AO 源码能力核对（发现 3 条 R10 前提不成立） |
