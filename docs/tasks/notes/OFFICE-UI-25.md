# OFFICE-UI-25

### 统一任务卡

任务编号：OFFICE-UI-25
负责 Harness：Mavis
开始时间：2026-10-25 08:15 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-24（轨迹能点开看详细任务）
真相源引用：`office/src/flat-office.js`、`office/src/main.js`、`office/src/project-workspace.js`、
`office/src/direct-chat.js`、`office/src/outputs.js`、`office/src/admin-agent.js`；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`c7aa857`（办公室与平台的文案统一成书面口径：任务、任务详情、验收标准、允许修改的路径……）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-040

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-24 |
| test_id | `MVP-UI-040` |
| 允许修改 | `office/src/**`（只改界面上给人看的文字）、`office/scripts/**`（文案断言）、`backend/app/wording/service.py`（人话词表）、`backend/app/admin/worker.py`（两句人话）、`backend/tests/test_wording.py`、`docs/claims/mavis--OFFICE-UI-25.md`、`docs/tasks/notes/OFFICE-UI-25.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/**`、`backend/app/api/routes.py`、`backend/app/tasks/**`、`backend/app/pool/**`、其他 Harness 的工作区与分支 |

## 做什么

把界面上说给人看的文字统一成书面口径：

| 原来 | 现在 |
|---|---|
| 看这条活 | 查看任务详情 |
| 这条活要做什么 | 任务目标 |
| 做到什么程度算完成 | 验收标准 |
| 只允许改这些地方 | 允许修改的路径 |
| 它做过的活 | 它做过的任务 |
| 本项目里的活 | 本项目里的任务 |
| 它还没有交出活。 | 暂无任务。 |
| 正在读这条活的任务书… | 正在读取任务详情… |

## 为什么

仓库主人：「不是淡，是太大白话了」「里面有些地方用的都是大白话，看起来不专业，都修一下文字描述」。

## 怎么做到不出错

1. 只改文字，不改行为：代码标识符、接口路径、事件名、payload 字段一律不动。
2. 统一名词：任务（不是「活」）、方案、成员、窗口、尝试、验收标准、动作记录。
3. 同屏一起统一（半口语半书面比全口语更不专业）。
4. 改完逐屏看真跑画面。

## 完成标准

1. 界面上不再有「活」这种大白话当任务讲；按钮与说明是书面口径。
2. 真跑截图留证（逐屏）。
3. 办公室全部冒烟无回归；构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-040`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 真跑（8849 真平台 + 4174 预览） | 顶部「待领取 1 · 执行中 0 · 待处理 0 · 未完成 13 · 已完成 4」；左栏「执行中 / 空闲 / 暂时无法领取任务」；任务表「尚无人领取 / 待领取 / 未完成 4 条」；轨迹「已批准 / 方案已批准 / 已取消 / 已要求返工」+「查看任务详情」（7 个）；失败原因显示「平台内部出错了（RuntimeError）」（不再是原始机器文本）；右栏「18 条任务（1 条尚未领取）」「查看全部待办」；页面无报错 |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖：Godot dev server、本机 ollama `qwen3:4b`、在册凭据窗口 7≠9；17 个脚本的文案断言随词表一起改） |
| `office/` 构建 | ok（main 212.00 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿（`ruff check` 与 `ruff format --check` 两条） |
| `pytest`（全量） | `PYTEST_EXIT=0`（后端改了 `backend/app/wording/service.py` 与 `backend/app/admin/worker.py`，全量跑过） |
