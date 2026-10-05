# AGENTS.md —— AI Harness 作业规则

本仓库由多个 AI Harness 并行协作。**本文件是会话开始时自动载入的入口**，
细节在 `docs/dev-convention.md`（H-1～H-8 硬约束）。两者冲突时以本文件为准。

## 0. 先做这一件事：进入你自己的工作区

**不要在仓库根目录 `new agent/` 里编辑文件。**

多个 AI 共享一个工作区会导致提交被无声丢弃 —— 任何人的 `git checkout` /
`git switch` / `git reset` 都会改变所有人的 HEAD 和工作树，正在编辑的文件会
消失且 `git status` 仍显示干净。本项目已因此丢失提交 4 次。

| 你的 Harness | 工作区 |
|---|---|
| WorkBuddy | `.worktrees/workbuddy/` |
| Codex | `.worktrees/codex/` |
| Claude Code | `.worktrees/claude/` |
| 其他（Qoder / TRAE / OpenCode …） | 先按下面创建 |

新 AI 用一条命令开工作区（幂等，已存在则拒绝且不改动）：

~~~powershell
./scripts/new-ai.ps1 -Harness <你的名字>
~~~

开好后 `cd .worktrees/<你的名字>`，然后切任务分支：

~~~bash
git switch -c agent/<group-short>/<harness>/<task-id>/<attempt-id> main
~~~

仓库根目录 `new agent/` 只用于**合并**和在 `main` 上跑门禁，不要在那里编辑。

## 1. 认领必须先于开工

在 `TASKS.md` 的「认领登记」表和「任务卡」写入 `task_id` / Harness 名称 / 时间 /
分支，**然后**才动手。理由：登记让其他 AI 看得见任务占用。本项目已因先干活后
补登记，导致另一个 AI 看不见 MVP-01 已被占用。

`TASKS.md` 的改动必须到 `main` 上可读才算登记（规则第 8 条）—— 留在任务分支上
等于没登记。

## 2. 真相源优先级

`docs/spec/0.3-R10.md` > `docs/dev-convention.md` > `docs/spec/mvp-task-checklist.md`
> 代码里的字面量（最低优先级，**禁止**作为依据）。

- **状态机与事件名的唯一真相源是 R10 §10 / §37.7。** 禁止在代码里写状态名/事件名
  字面量，禁止新增状态或事件（哪怕先加上）。
- `docs/spec/0.3-R10.md` 是只读的。改动必须走复审流程（§38 的 R10-fix 条目格式），
  并升 `schema_version`；实现与正文必须在同一次提交内一起改。
- 技术栈偏离 R10 §5（Go+React → Python FastAPI+Vue3）已记录在
  `docs/adr/ADR-DEV-001-stack-deviation.md`，不要重复争论。

## 3. 每个任务必须有 test_id

没有 `test_id` 的完成标准**不可验收**（H-4）。完成标准不能写"功能可用"，
要写可机械判定的条件。`tools/docstate` 的断言会检查 test_id 无悬空。

## 4. 提交前门禁三道全绿

~~~bash
./scripts.sh check     # docstate + ruff check + ruff format --check + pytest
~~~

任一失败都不许提交。`docstate` 失败说明规范被改坏了或代码与规范脱节，
**不要绕过、不要放宽断言**。应用启动时也会跑 `docstate`，失败则拒绝启动。

## 5. 分支与提交

- 禁止在 `main` 上直接提交；`main` 只接受通过 Merge Gate 的合并。
- 禁止 `force push`；禁止 rebase 到非任务分支。
- 禁止把别的 AI 的分支用 `git branch -f` / `git reset` 移走 —— 分支引用跨工作区
  共享，动了等于删掉对方的工作（H-7 规则 2）。
- 提交格式见 `docs/dev-convention.md`：`<type>(<task-id>): <祈使句摘要>`，
  body 写**为什么**，涉及状态机/事件/schema 的改动必须引用 R10 章节号。

## 5b. 改共享文件前，先确认别人没在改同一处（H-8）

要改`TASKS.md`、`docs/traceability.md`、`docs/dev-convention.md`、
`tools/discipline/**`、`tools/docstate/**` 这类共享文件时，**动手之前**先查其他 AI
的在途工作：

~~~bash
for d in .worktrees/*/; do
  echo "$(basename $d) | $(git -C $d branch --show-current) | 未提交: $(git -C $d status --short | wc -l)"
done
git log --oneline main..<分支>          # 未合入的 commit
git diff --name-only main..<分支># 它们改了哪些文件
~~~

2026-10-05 的认领登记拆分踩了这个坑：我按 TASKS.md 表里的「序号」取号，而 Qoder
**在我拆分期间往同一张表插了新行**并占了 22 号，撞号导致序号重复与同 task 双文件。
它的独立复核指出 4 项、**全部成立**，其中最刺眼的是：`discipline` 当时 24/24 全绿，
而这些缺陷真实存在于树里 —— **断言没覆盖的地方，绿灯不含信息**。

**这条与 H-1 同一个毛病：只看自己，不看全局。** H-1 说「真相源在哪」，
H-8 说「动之前先看别人」。

## 6. 被阻塞时不要空等

在 `docs/prep/<task-id>/` 做该任务的 `prep` 项（清单里每个任务都列了可提前做的部分）。
**prep 产出不进主分支** —— 它可能基于错误假设，合入会污染别人。真正开工时必须
重新验证，不能直接当结论用。

## 7. 遇阻怎么办

立刻把 `TASKS.md` 的状态改成 `blocked`，在「已知问题」写清**具体阻塞点**
（不要写"有难度"），并在 `main` 上可见。

## 提交前自检

~~~bash
./scripts.sh check          # 三道门禁全绿
git branch --show-current   # 确认在任务分支上，且位于 .worktrees/<harness> 内
git log --oneline -1
~~~

## 任务与真相源

| 要什么 | 去哪看 |
|---|---|
| 有哪些任务、谁在做什么、依赖是什么 | `TASKS.md` |
| 任务定义、依赖图、可提前做的 prep | `docs/spec/mvp-task-checklist.md` |
| 硬约束 H-1～H-8、目录边界、分支与提交规范 | `docs/dev-convention.md` |
| 权威规范（只读） | `docs/spec/0.3-R10.md` |
| MVP 实现了哪些状态、遗漏了哪些及理由 | `docs/state-projection.md` |
| 决策记录 | `docs/adr/` |
| Claim/幂等的实现指引（MVP-04 必读） | `docs/mvp-04-claim-guide.md` |