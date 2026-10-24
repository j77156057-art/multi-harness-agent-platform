# OFFICE-UI-18

### 统一任务卡

任务编号：OFFICE-UI-18
负责 Harness：Mavis
开始时间：2026-10-24 19:35 +08:00
工作区：`.worktrees/mavis/`
依赖任务：无（办公室界面的一处显示）
真相源引用：`office/src/cli-transcript.js`（CLI 转录的解析与人话）、
`office/src/outputs.js`（输出页）、`office/src/flat-office.js`（办公室的输出块）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`6036b5d`（输出页的绝对路径压短：命令说成「在 wt\69d9 里跑了 git log」）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-033

| 项 | 内容 |
|---|---|
| 依赖 | — |
| test_id | `MVP-UI-033` |
| 允许修改 | `office/src/cli-transcript.js`、`office/src/outputs.js`、`office/src/flat-office.js`、`office/scripts/verify-cli-transcript.mjs`、`docs/claims/mavis--OFFICE-UI-18.md`、`docs/tasks/notes/OFFICE-UI-18.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

输出页与办公室输出块里，那行整条绝对路径压成短形式：

| 原来 | 现在 |
|---|---|
| `cd D:\study\tools\agent\new agent\wt\69d9 && git log --oneline -5` | `在 wt\69d9 里跑了 git log --oneline -5` |
| `git -C D:\...\wt\69d9 log` | `在 wt\69d9 里跑了 git log` |
| `D:\...\wt\69d9\src\main.py` | `wt\69d9\src\main.py` |

## 为什么

仓库主人：「输出页那行绝对路径压缩成「在 wt\69d9 里跑了 git log」」。绝对路径是给写
代码的人看的：它把卡片撑变形，而且没人靠它找文件。人要的是**在哪儿做了什么**。

## 怎么做到不出错

1. **只压认得出的**：路径里有 `wt\` 这一段时才压；压不动的原样留着（不猜）。
2. **压的是显示，不是数据**：原始记录仍在「看原始记录」里。
3. **没有工作目录的命令照旧**：「跑了命令：npm test」这类既有说法不许变（既有 12 条
   断言不许变弱）。

## 完成标准

1. `shortenPath()` 把工作目录里的绝对路径压成 `wt\<id>\...`。
2. 带工作目录的命令说成「在 wt\69d9 里跑了 <命令>」（`cd … && …` 与 `git -C …` 两种）。
3. 输出页、办公室输出块、CLI 转录三处都压。
4. `office/scripts/verify-cli-transcript.mjs` 13 条通过；办公室全部冒烟无回归；
   构建通过；`./scripts.sh check` 全绿。
5. `docs/traceability.md` 登记 `MVP-UI-033`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-cli-transcript.mjs` | ok（13/13；新加一条：路径压短、命令说成「在 wt\69d9 里跑了 git log」、没有工作目录的命令照旧） |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖） |
| `office/` 构建 | ok |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿 |
| `pytest`（全量） | `PYTEST_EXIT=0`（这一件本身只碰前端；同一条分支上的 OFFICE-UI-16 才碰后端） |
