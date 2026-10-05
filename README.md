# 多 Harness Agent 协作与调度平台 —— MVP

统一协调多个 AI Harness（Codex、Claude Code、OpenCode 等）的项目级协作平台。

**MVP 要验证的三件事**（来自 `docs/spec/0.3-R10.md` §0）：

1. 任务不会重复领取；
2. 任务状态可恢复（调度器重启、owner 崩溃都能恢复）；
3. 代码经过验证后才能合并。

**当前状态**：MVP-01（项目骨架与规范绑定）已完成。
后端只有 `/health` 和 `/spec/status` 两个运维端点，业务端点从 MVP-02 开始。

---

## 快速开始

前置：Python 3.11+（开发用 3.13）、Git、Node 20+（仅前端需要）。

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
| <http://127.0.0.1:8848/health> | 存活 + 配置摘要 |
| <http://127.0.0.1:8848/spec/status> | 规范一致性检查（88 项） |
| <http://127.0.0.1:8848/docs> | OpenAPI 交互文档 |

启动时会自动跑规范检查；**不通过会拒绝启动**（`AP_CHECK_SPEC=0` 可临时跳过，
但不要在 CI 里这么干）。

---

## 常用命令

~~~bash
make docstate   # 只跑规范一致性检查（88 项断言）
make test       # pytest
make lint       # ruff check + format --check
make fmt        # ruff 自动修复 + 格式化
make check      # 完整门禁：docstate + lint + test（提交前必须全绿）
make clean      # 清缓存（不动 data/ 与 wt/）
~~~

无 make 时用 `./scripts.sh {docstate|lint|test|check|dev|install}`。

---

## 目录结构

~~~text
backend/app/          业务代码
  spec.py             R10 常量的代码侧镜像（状态名/事件名唯一来源）
  config.py           环境变量配置
  main.py             FastAPI 入口
backend/tests/        测试（含与 R10 正文的交叉验证）
frontend/             Vue3 前端（MVP-09 才实质开发）
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
| `docstate` | 88 项断言 | R10 文档自洽性：allowlist 封闭性、迁移表单值 next、schema 字段、唯一键列已声明、test_id 无悬空 |
| `discipline` | 16 项断言 | 协作纪律：`TASKS.md` 两张表互相覆盖、任务卡有 test_id 与真相源引用、已完成卡有 commit 与验证证据、文档声明的断言数与实际相符 |
| `test_spec_mirror.py` | 11 项 | 代码侧枚举与 R10 正文的交叉验证 |
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

按 `docs/spec/mvp-task-checklist.md`（0.4 重排版）：

| 任务 | 负责人 | 状态 |
|---|---|---|
| MVP-01 骨架与规范绑定 | AI-1 | ✅ 本次交付 |
| MVP-02 SQLite schema 与事件事实源 | AI-2 | 🔜 下一个（关键路径） |
| MVP-03 状态机与 Plan 校验 | AI-3 | 等待 MVP-02 |
| MVP-04 Claim/Lease/fencing | AI-4 | ★ 瓶颈，MVP-02 后立即开工 |
| 其余 | — | 见清单 |

**关键路径**：`MVP-01 → MVP-02 → MVP-04 → MVP-05 → MVP-06 → MVP-07 → MVP-11`

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
