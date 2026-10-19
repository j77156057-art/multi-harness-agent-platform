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
| R10 自身不自洽 | `tools/docstate` 98 项（CI + 应用启动） |
| `app/spec.py` 的状态/事件超出 R10 集合 | `test_spec_mirror.py` 11 项 |
| 事件被篡改、失败留下孤儿事件、并发 seq 错乱 | `test_database_events.py` 9 项 + SQL 触发器 |
| 已应用的迁移被改写 | `apply_migrations` 的 checksum 校验 |
| 代码风格 | `ruff check` / `ruff format --check` |
| **登记表自身不自洽** | `tools/discipline` **44** 项（CI + `scripts.sh check`）。含「一个 task_id 只对应一个认领文件（**大小写不敏感**）」—— 序号已于方案 C 取消（`docs/proposals/claim-denum.md`），唯一键是 task_id；判重按大小写不敏感比对，因为文件名在 Windows 上大小写不敏感，`MVP-10a.md` 与 `mvp-10a.md` 在 Windows 上是同一个路径。另含「认领登记表注释行指向的文件均存在」—— 补上 CLAUDE 提案点出、WorkBuddy 建议补的盲区 |
| **有人在仓库根目录（合并区）做开发** | `tools/discipline` rootworktree 组。**判定对象是占用者本人**：你在根工作区跑 `check` → 红灯（`f.txt` 之类会被点名）；你在自己的 `.worktrees/<harness>/` 跑 → 只出一条 **WARN 告警**（默认就打印、也进 `--json` 的 stdout），不判红、不阻塞你干活。定位不到主工作区 / git 不可用时对任何人都 fail-closed 判红。<br>**为什么不是「一律判红」**：那会让代价落在所有人身上而只有一个人是原因——实测堵了十几次合并，其中几次是无关 agent 的。今天的形态是：占用者想跑 check 必须**待在根工作区里**，所以他一定拿得到红灯，而别人只是看得见。 |
| **依赖图与关键路径自洽** | `tools/discipline` deps 组（从依赖图算出，不信手写数字） |
| **两个 agent 同时合并 `main`** | `tools/mergeguard` 互斥锁 —— **但仅在走 `./scripts.sh merge` 时** |
| **已合入 main 的任务未登记 / 未完成 / 缺卡缺证据** | `tools/discipline` merge 组：`git log --merges` 反向核对认领表与任务卡；认领序号唯一（registry 组）。浅克隆 / 无 git 环境 fail-closed，故 CI `fetch-depth: 0` |
| **认领文件名与内容指向不同任务** | `tools/discipline` registry 组：文件名里的 task_id 必须与文件内容的 `task_id` 一致。**这条替代了原先的「序号连续 1..N」**——连续性在并发分支下结构上不可满足（各 agent 从 main 最大值往后取号，合并顺序不由取号者决定），而它没有防住唯一性没防住的东西。裁决与 34 次重命名的机械证据见 `docs/proposals/claim-seq-contiguity.md` |
| **未合入 main 但已开工的分支，其认领是否已登记** | `tools/discipline` branches 组。**只对「分支有提交、main 仍写 waiting、且该分支自己也没有认领文件」变红**；已在自己分支登记只是待合并的**不报** —— 那是正确流程的中间态，报红等于惩罚正确行为。**只在多分支的本地克隆下有牙齿**，CI 只检出当前分支时近乎空跑 |

### 合并必须走受控入口

**7 个 worktree 共享同一个 `.git`。** 只有 `HEAD` 与 index 是 per-worktree 的，
`refs/`、`ORIG_HEAD`、锁文件全部共享。而根目录按 H-7 是「协调者专用合并区」——
所有合并挤在那里，曾经没有任何互斥。

已实际发生两次，不是理论风险：

1. 一次 `git merge` 解完冲突后 `MERGE_HEAD` 凭空消失、工作区恢复干净——
   被并发的 git 操作静默 abort，冲突解决成果零提交丢失；
2. 立即重试则 `fatal: update_ref failed for ref 'ORIG_HEAD'`。

因此**合并 `main` 一律走**：

~~~bash
./scripts.sh lock                        # 先看锁状态
./scripts.sh merge <branch> --task-id T-1   # 在根工作区执行
~~~

它与裸 `git merge` 的差别：先抢互斥锁（拿不到就明确失败并打印持有者，
**不静默等待**——本项目的事故恰恰是静默的）、脏工作区直接拒绝、
冲突不自动 abort、门禁红则不提交 merge commit。

**它不能做什么（别误以为已经安全了）：**

- **不阻止裸跑 `git merge`。** git 没有「禁止某命令」的机制，wrapper 只能提供
  受控入口与协作约定。裸跑仍会撞锁。
- **不保护 `git commit` 到 `refs/heads/main`。** 那条仍靠分支策略自觉
  （见下方「靠自觉的」）。
- **锁只挡合并，不挡 `git checkout` / `git reset`。** 在根工作区跑这两条
  依然能移动所有人的 HEAD —— H-7 规则 1 的老问题，锁管不到。

### 靠自觉的（写了也没人会拦）

- **禁止在仓库根目录编辑**（H-7）—— 已减到只影响"读了 AGENTS.md 的 AI"，
  对不读任何指令文件的 Harness 仍然无效
- **禁止在 `main` 上直接提交** —— 没有 hook，纯自觉。**但经合并提交入主线的路径
  已被 discipline merge 组机械拦截**（已合入的任务必须已登记、已完成、卡证据完整）；
  残余盲区只剩直提 main：没有合并提交可供映射，要等 GitHub 分支保护（B 方案）
- **禁止裸 `git merge`** —— 走 `./scripts.sh merge` 才有互斥锁，绕过它等于回到
  事故前那套纯自觉
- **禁止 `force push` / `rebase` 到非任务分支** —— 无强制
- **只移动自己拥有的分支引用**（H-7 规则 2）—— 跨工作区共享引用，只能靠自觉
- **R10 正文只读** —— 任何人都能改；本项目自己就改过（合法，走 §38 复审）
- **按 test_id 判定实现是否达标** —— ID 存在且无悬空已检查，
  但"这些 ID 是否真的覆盖了要求的行为"要靠人读
- **允许/禁止修改目录边界** —— `discipline` 只检查字段非空，不检查是否越界

### 根本没实现

- Merge Gate / Review / Verify（MVP-06、MVP-07）
- 权限矩阵、分支保护、服务端 required checks
- ~~任务依赖的机械校验~~ → 已实现（`tools/discipline` deps 组，2026-10-05）
- secret 扫描先于事件落库（R10 §28 要求）

### 已知盲区

- `discipline` 只做**结构**检查，不做**内容**检查。填了 `test_id` 但没真测，拦不住
- merge 组只认 git 默认的带引号合并标题（`Merge branch 'x'`）；手写标题无法映射，
  故合并一律保留默认标题（`git merge --no-ff <分支>` 不改 -m）
- merge 组看不见**直提 main**（无合并提交即无映射锚点）；四次缺卡/先合后验全部
  出自直提或搭乘合并，在分支保护启用前只能靠 worktree 约定与合并者复核
- `discipline` 只校验 **docstate** 的声明计数，**不校验它自己的**。
  加断言后必须手工同步 README 里 `discipline` 那一行的数量 —— 本项目已因此写错一次
  （写成 16，实际 17）。没做成自引用检查是刻意的：断言数包含该检查本身，
  自引用会让数字难以推理
- `docstate` 只读 R10，不读 `TASKS.md`（那是 `discipline` 的事）
- 平台仓已无独立前端（UI 统一为 Agent Office，4174）；其静态检查不在本仓
  门禁内。`docs/` 无 markdown lint、无链接检查
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

`tools/docstate/docstate.py` 对 R10 跑 98 项机械断言。CI 与应用启动都会跑它。

```bash
make check          # docstate + pytest + ruff
python tools/docstate/docstate.py --verbose   # 看单项
python tools/docstate/docstate.py --json      # 机器可读
```

启动时校验失败会**拒绝启动**（除非 `AP_CHECK_SPEC=0`）。这是刻意的：
带着不自洽的规范写代码，产出的实现必然继承那些矛盾。

---

## H-3 技术栈偏离已记录

R10 §5 规定 Go + React。本项目用 Python FastAPI + Agent Office（独立前端，
4174），偏离理由与回退条件见
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
UI                    Agent Office（独立仓，4174；本仓不再包含前端）
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

该脚本会建 `.worktrees/<名字>/`、切出驻留分支 `agent/workspace/<名字>`、**建一个
独立的 `.venv`**（从 `backend/requirements-dev.txt` 安装，约 1 分钟）、并冒烟验证
解释器与 `pytest`/`fastapi` 可导入。**幂等**：工作区已存在时报错退出，不做任何改动。
需要与主目录共享 venv（更快更省，但会一起坏）时加 `-SharedVenv`。
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
4. **每个工作区有自己的 `.venv`（实体目录，不是联接）**。代价是约 106 MB × N。
   `./scripts.sh check` 在每个工作区都能直接跑。存量 junction 用
   `./scripts/migrate-venvs.sh` 迁移（先跑 `--dry-run` 看清楚）。
   独立的三条实际收益：
   - **环境隔离**：某个 AI 装/卸包不会影响其他人；
   - **装依赖只付一次**：加新依赖时各自装，不必重装 N 次（迁移时体感明显）；
   - **版本可分化**：必要时某个 AI 可以先升一个包做实验，不影响别人。
   需要共享时用 `new-ai.ps1 -SharedVenv` 走 junction；
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

## H-8 改共享文件前，先确认别人没在改同一处

**规则**：任何要改`TASKS.md`、`docs/traceability.md`、`docs/dev-convention.md`、
`tools/discipline/**`、`tools/docstate/**` 这类共享文件的动手之前，先查其他 AI 的
在途工作；查不出来就等，不要先做了再说。

查法（一条命令）：

~~~bash
git worktree list                                  # 各AI 在哪个工作区
for d in .worktrees/*/; do
  echo "$(basename $d) | $(git -C $d branch --show-current) | 未提交: $(git -C $d status --short | wc -l)"
done
git log --oneline main..<某个分支>                  # 各分支未合入的 commit
git diff --name-only main..<某个分支>              # 它们改了哪些文件
~~~

**为什么**：2026-10-05 的认领登记拆分（`847cc69`）踩了这个坑。我把认领表拆成
per-task 文件时，从表里的「序号」列取号；**Qoder 在我拆分期间往同一张表插了新行**
（`claims-split-review` 占了 22 号），我按旧快照取号就撞了号 —— 产出里出现序号
`14`/`15` 各重复一次、`claim-nullable` 两个逐字相同的文件。

Qoder 的独立复核（`claims-split-review`）指出 4 项，**全部成立**：拆分本身未登记、
序号重复、同task 双文件、`discipline` 缺唯一性断言。后者尤其刺眼：
**`discipline` 当时 24/24 全绿，而这些缺陷真实存在于树里** ——
断言没覆盖的地方，绿灯不含信息。

**这条与 H-1 的区别**：H-1 说「真相源在哪」，H-8 说「动之前先看别人」。
两者都指向同一个毛病：**只看自己，不看全局**。

**推论**：改动共享结构（拆分、重排、批量重编号）之前的那次检查，不是"谨慎"，
是**必要步骤** —— 就像提交前跑门禁，不是可选的好习惯。


## 提交前自检

```bash
make check
```

必须全绿。`docstate` 失败说明规范被改坏了或代码与规范脱节，**不要绕过**。

### 在 WorkBuddy harness 里跑门禁：pytest 必须走沙箱外

**这条不是项目约定，是 harness 环境的坑，踩了会浪费一整轮排查。**

在 WorkBuddy 里跑 `pytest`，进程可能以这个结束：

```
File "...\cli\vendor\shim\sitecustomize.py", line 848,
    in _exit_bulk_guard_control
    raise SystemExit(1)
```

**报错里没有任何测试名、没有断言失败** —— 看着像测试崩了，实际一个测试都没跑完。

#### 真因（钩子自己打的日志）

```
[safe-delete][SAFE_DELETE_FAIL_CLOSED] {
  "target": "\\\\?\\D:\\Temp\\pytest-docmind\\pytest-of-<user>\\garbage-<uuid>",
  "reason": "trash-failed",
  "detail": "[Errno 53] 找不到网络路径。"
}
```

`garbage-<uuid>` **不在仓库任何 `.py` 里**，是 safe-delete 钩子自己建的暂存目录。

因果链：harness 注入 `PYTEST_DEBUG_TEMPROOT=D:\Temp\pytest-docmind`、
`TMPDIR`/`TEMP`/`TMP=D:\Temp` → pytest 在 D 盘建临时目录并清理 → 钩子用
`\\?\` 扩展长路径前缀把它挪进回收站 → 搬运失败 `[Errno 53]` →
safe-delete **fail-closed** → 打死整个 pytest 进程。

#### 判据

| 现象 | 结论 |
|---|---|
| `SystemExit: 1` + `[SAFE_DELETE_FAIL_CLOSED] ... "reason": "trash-failed"` | 环境层，**不是测试失败**，别去改断言 |
| `SystemExit: 1` + 无 safe-delete 日志 | 才是真的测试崩了 |
| `ModuleNotFoundError: No module named 'fastapi'` | 解释器不对，见下 |

#### 解法

**门禁走沙箱外**（WorkBuddy 里即 `dangerouslyDisableSandbox: true`）。
钩子仍打日志但不再杀进程。

| 场景 | 结果 |
|---|---|
| 沙箱内 | `SystemExit: 1`（被钩子打死） |
| 沙箱外 | **52 passed** |

**耗时不给结论**：同一命令重复跑实测 11s–82s，差异达 7 倍但**归因未确认**
（怀疑与 MITM 代理有关，未做单变量验证）。所以这里只写确定的那条事实——
「沙箱内跑不完」，不写「沙箱外更快」。**给自己一条禁令：不要把未复现的
benchmark 写进本文件**（这正是 `check_counts` 组防的那类「文档开始骗人」）。

`docstate` 与 `discipline` 是纯读脚本，**不受影响**，只有 pytest 受影响。

#### worktree 里没有自己的 venv

要用根工作区的解释器，否则 8 个 backend 测试文件全部 collection error：

```bash
../../.venv/Scripts/python.exe -m pytest -q tools/discipline tools/docstate tools/mergeguard
```

### 门禁绿灯不等于缺陷不存在

`discipline` 只做**结构**检查，不做**内容**检查（见上面「已知盲区」）。
实物样本：方案 C 的占号机制被删干净之后，`docs/claims/` 里仍有**两组重复前缀**
（`58-` 与 `59-`）——门禁 44/44 全绿时，树上确实存在重复编号。

细节见 `docs/tasks/notes/gate-sandbox-pitfall.md` 与
`docs/tasks/notes/claim-denum-coordinator-review.md`。
