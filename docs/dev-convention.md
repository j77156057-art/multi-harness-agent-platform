# 开发约定（MVP-01 交付物）

本文件是所有参与者的**强制约定**。开工前必读，违反即打回。

真相源优先级（冲突时从高到低）：

1. `docs/spec/0.3-R10.md` —— 状态机、事件名、schema、权限的唯一真相源
2. 本文件 —— 目录边界、命名、流程约定
3. `docs/spec/mvp-task-checklist.md` —— 任务范围与依赖
4. 代码里的字面量 —— **最低优先级，禁止作为依据**

---

## 约束的强制程度

**这一节回答「哪些规则真的会红」。** 做法参照 `agent-orchestrator-main` 的
`docs/documentation-map.md`，它对本仓库自己的 `AGENTS.md` 有一句诚实的自我描述：

> "**Not machine-checked.** The hard rules it names are enforced by tests… rather than by a doc gate."
> "**if an artifact in the contract layer disagrees with prose, the contract layer wins**"

R10 复审早就证明「人工判定的已闭环」与「实际自洽」差距巨大。因此纪律层必须有清单，
否则没人能说清某条规则到底靠不靠得住。

### 会被门禁红掉的

| 约束 | 由谁检查 |
|---|---|
| R10 自身不自洽 | `tools/docstate` 88 项（CI + 应用启动） |
| `app/spec.py` 的状态/事件超出 R10 集合 | `test_spec_mirror.py` 11 项 |
| 事件被篡改、失败留下孤儿事件、并发 seq 错乱 | `test_database_events.py` 9 项 + SQL 触发器 |
| 已应用的迁移被改写 | `apply_migrations` 的 checksum 校验 |
| 代码风格 | `ruff check` / `ruff format --check` |
| **登记表自身不自洽** | `tools/discipline` 16 项（CI + `scripts.sh check`） |

### 靠自觉的（写了也没人会拦）

- **禁止在仓库根目录编辑**（H-7）—— 已减到只影响"读了 AGENTS.md 的 AI"，
  对不读任何指令文件的 Harness 仍然无效
- **禁止在 `main` 上直接提交** —— 没有 hook，纯自觉
- **禁止 `force push` / `rebase` 到非任务分支** —— 无强制
- **只移动自己拥有的分支引用**（H-7 规则 2）—— 跨工作区共享引用，只能靠自觉
- **R10 正文只读** —— 任何人都能改；本项目自己就改过（合法，走 §38 复审）
- **按 test_id 判定实现是否达标** —— ID 存在且无悬空已检查，
  但"这些 ID 是否真的覆盖了要求的行为"要靠人读
- **允许/禁止修改目录边界** —— `discipline` 只检查字段非空，不检查是否越界

### 根本没实现

- Merge Gate / Review / Verify（MVP-06、MVP-07）
- 权限矩阵、分支保护、服务端 required checks
- 任务依赖的机械校验（清单 §0.1 的依赖图无人验证）
- secret 扫描先于事件落库（R10 §28 要求）

### 已知盲区

- `discipline` 只做**结构**检查，不做**内容**检查。填了 `test_id` 但没真测，拦不住
- `discipline` 只校验 **docstate** 的声明计数，**不校验它自己的**。
  加断言后必须手工同步 README 里 `discipline` 那一行的数量 —— 本项目已因此写错一次
  （写成 16，实际 17）。没做成自引用检查是刻意的：断言数包含该检查本身，
  自引用会让数字难以推理
- `docstate` 只读 R10，不读 `TASKS.md`（那是 `discipline` 的事）
- 前端尚无任何静态检查（无 eslint / prettier），`docs/` 无 markdown lint、
  无链接检查
- 没有 remote，所以 GitHub 侧的 branch protection / required checks 全部不存在

## H-1 状态机唯一真相源

R10 §10 的 23 个状态、R10 §37.7 的 88 项 allowlist（74 M / 13 O / 1 R）是**唯一**定义。
代码侧镜像在 `backend/app/spec.py`，由 `backend/tests/test_spec_mirror.py` 做机械比对。

禁止行为：

- ❌ 在业务代码里写状态名/事件名字面量（必须 `from app.spec import TaskStatus`）
- ❌ 在代码里新增状态或事件（哪怕只是"先加上"）
- ❌ 只改代码不改 R10，或只改 R10 不改代码

正确流程：

1. 在 R10 正文提出修订（走复审流程，升 schema_version）
2. 同一次提交内更新 `app/spec.py`
3. `make check` 必须全绿

### MVP 允许的状态投影

MVP 只实现部分状态，这是**允许的**，但必须在 `docs/state-projection.md` 登记省略项与理由。
未登记的省略 =违规。

---

## H-2 规范检查器是门禁，不是工具

`tools/docstate/docstate.py` 对 R10 跑 88 项机械断言。CI 与应用启动都会跑它。

```bash
make check          # docstate + pytest + ruff
python tools/docstate/docstate.py --verbose   # 看单项
python tools/docstate/docstate.py --json      # 机器可读
```

启动时校验失败会**拒绝启动**（除非 `AP_CHECK_SPEC=0`）。这是刻意的：
带着不自洽的规范写代码，产出的实现必然继承那些矛盾。

---

## H-3 技术栈偏离已记录

R10 §5 规定 Go + React。本项目用 Python FastAPI + Vue3，偏离理由与回退条件见
`docs/adr/ADR-DEV-001-stack-deviation.md`。

---

## H-4 没有 test_id 的完成标准不可验收

每个任务的完成标准必须引用 R10 §26 的检查 ID 或新增 `MVP-*` ID，并登记到
`docs/traceability.md`。

"功能可用"、"跑通了"、"基本没问题"这类描述**不被接受**为完成标准。

---

## H-5 文件所有权与并行安全

多个 AI 并行时，**修改范围不得重叠**。每个任务卡里的"允许修改目录"是硬边界。

跨边界改动必须：

1. 在群里说明；
2. 取得该目录 owner 同意；
3. 单独提交，不混入本任务 commit。

`docs/prep/` 是共享区，各 AI 的准备工作放这里，**不进主分支**，避免互相污染。

---

## H-6 状态变更只能走 API

（R10 §0 总协调规则第 8 条）

任何任务状态、owner、lease、epoch、receipt、review 结论的变更，**禁止直接改数据库**。
测试里也禁止用裸 SQL 改状态字段（schema migration 除外）。

原因：R10 的 fencing 与幂等语义建立在"所有写都经过同一事务入口"之上。
绕过 API 的写入会同时绕过 fencing 检查和 `task_events` 记录，导致投影无法重建。

---

## 目录边界

~~~text
backend/app/          业务代码
backend/tests/        测试
frontend/             前端
docs/spec/            R10 正文（只读！改动走复审流程）
docs/adr/             架构决策记录
docs/prep/            各 AI 的准备工作（共享区，不进主分支）
tools/docstate/       规范检查器
data/                 运行期数据（DB、日志），git 忽略
wt/                   worktree 物理目录（R10 §14 短 ID 口径），git 忽略
canonical.git调度中心持有的 bare 仓库，git 忽略
~~~

## 分支命名

R10 §14 统一口径：

~~~text
任务分支：agent/<group-short>/<harness>/<task-id>/<attempt-id>
help 分支：agent/<group-short>/<harness>/<task-id>/help-<n>
物理目录：wt/<短ID>            # 不用完整分支名，避免 Windows 长路径
~~~

R10-09 要求 §14 口径、§17 权限矩阵、Push Proxy 白名单三处**必须一致**。
MVP-05 实现时以本节为准。

## Commit 格式

~~~text
<type>(<task-id>): <祈使句摘要>

<空行>

<body：为什么改，不是改了什么>

Refs: <R10 章节或 test_id>
~~~

type取值：`feat` `fix` `docs` `test` `refactor` `chore` `perf` `build` `ci`

例：

~~~text
feat(mvp-02): add tasks table with attempt_no and hotfix

R10 §8 的 tasks 最小定义要求这两个字段，缺任一个都会让 MVP-04 的
条件 UPDATE 编译失败（R10-12）。

Refs: DOC-SCHEMA-001
~~~

规则：

- 摘要用祈使句，≤ 72 字符，不加句号
- body 写**为什么**，特别是"为什么必须这样"（引用 R10 条款）
- 涉及状态机/事件/schema 的改动**必须**在 body 引用 R10 章节号
- 一个 commit 只做一件事

## 分支策略

- `main`：只接受通过 Merge Gate 的合并（R10 §21）
- `agent/<harness>/<task-id>/<attempt-id>`：任务分支，唯一允许写入的分支
- ❌ 禁止直接 push `main` / `integration`
- ❌ 禁止 `force push`
- ❌ 禁止 `rebase` 到非任务分支

## H-7 工作区隔离（每个 AI 一个 git worktree）

**多个 AI 共享同一个 git 工作区会丢代码。** 本项目已因此丢失提交 4 次，其中一次是
别人的提交被 `git reset` 丢弃。任何人在共享工作区执行 `git checkout` / `git switch` /
`git reset`，都会改变**所有人**的 HEAD 和工作树——正在编辑的文件会被无声冲掉，
`git status` 看起来还是干净的。

因此每个 AI 有且只有一个入口目录。**新增 AI 不要手工建目录，用一条命令：**

~~~powershell
./scripts/new-ai.ps1 -Harness <名字>          # 例如 -Harness qoder
~~~

该脚本会建 `.worktrees/<名字>/`、切出驻留分支 `agent/workspace/<名字>`、把主 `.venv`
以目录联接接进去、并冒烟验证解释器可用。**幂等**：工作区已存在时报错退出，不做任何改动。
本机多个 AI 并发跑 git 时 ref 锁会瞬时冲突，脚本内置 3 次重试；仍失败则报错并输出
git 原文，不会留下半成品。

当前已分配（以 `git worktree list` 为准，本表可能滞后）：

| Harness | 工作区 | 驻留分支 |
|---|---|---|
| WorkBuddy | `.worktrees/workbuddy/` | `agent/workspace/workbuddy` |
| Codex | `.worktrees/codex/` | `agent/workspace/codex` |
| Claude Code | `.worktrees/claude/` | `agent/workspace/claude` |
| 协调者 | 仓库根 `new agent/` | **只做合并与门禁，不编辑文件** |

同一任务拆给多个 AI 时（如 MVP-04 建议的 Claim/幂等 与 Lease/fencing 分工），每个 AI
各用自己的工作区与分支，分支名靠 `<harness>` 与 `<attempt-id>` 区分：

~~~text
agent/<group-short>/<harness-a>/<task-id>/a1
agent/<group-short>/<harness-b>/<task-id>/a1
~~~

规则：

1. **只在分配给自己的工作区里编辑文件。** 需要新任务时在该工作区内
   `git switch -c agent/<group-short>/<harness>/<task-id>/<attempt-id>`（从 `main` 起）；
2. **只移动自己拥有的分支引用。** 分支引用仍是跨工作区共享的，
   `git branch -f` / `git reset` 到别人的分支等于删别人的工作；
3. **仓库根目录只用于合并和在 `main` 上跑门禁**，不要在那里编辑；
4. `.venv` 通过目录联接共享，所以 `./scripts.sh check` 在每个工作区都能直接跑，
   无需各建 venv；
5. `.worktrees/` 已在 `.gitignore`，不会被提交（它与 `wt/` 不同：`wt/` 是 MVP-05 的
   worktree 物理目录）。

回收：`git worktree remove .worktrees/<harness>`（先确认其中无未提交改动），
分支随之用 `git branch -D agent/workspace/<harness>` 删除。

**每个 AI 各自的工作区不解决共享分支引用的问题** —— 见规则 2。本项目至今发生过
「A 在 B 的分支上提交」和「`git reset` 丢弃 A 的提交」两次，都源于此。

## 依赖管理

- `backend/requirements.txt` 运行时依赖，**必须钉死版本**
- `backend/requirements-dev.txt` 开发依赖
- 新增依赖必须写明用途；大依赖（如 playwright）在 PR 里单独论证
- `tools/docstate/` 保持**零外部依赖**（纯标准库），保证任何环境都能跑

## 测试

- 每个 MVP 任务至少一个 `test_id`，并登记到 `docs/traceability.md`
- 失败必须能定位到 `task_id` / `attempt_id` / `event seq`（R10 §清单 MVP-11）
- 慢测试标`@pytest.mark.slow` 或 `@pytest.mark.integration`
- ❌ 禁止为了让测试通过而放宽断言

## 本地启动

见 `README.md`。最短路径：

~~~bash
make install
make dev        # 启动后端 http://127.0.0.1:8848
make check      # 全量门禁
make test
make lint
make fmt
make docstate   # 只跑规范检查
~~~

## 提交前自检

```bash
make check
```

必须全绿。`docstate` 失败说明规范被改坏了或代码与规范脱节，**不要绕过**。
