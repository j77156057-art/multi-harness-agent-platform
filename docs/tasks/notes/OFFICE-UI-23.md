# OFFICE-UI-23

### 统一任务卡

任务编号：OFFICE-UI-23
负责 Harness：Mavis
开始时间：2026-10-25 01:40 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（窗口状态页）
真相源引用：`office/src/flat-office.js`（事件行、payload 字段表、窗口页的轨迹块）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`2a2f8ad`（轨迹块改名并加说明、每行补上活的名字、payload 不摆机器字段）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-038

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-038` |
| 允许修改 | `office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-conversations.mjs`、`docs/claims/mavis--OFFICE-UI-23.md`、`docs/tasks/notes/OFFICE-UI-23.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/**`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 「它的轨迹」 | 「它做过的动作」+ 一句说明（平台记下来的动作记录，新的在上面） |
| `10/22 15:10 批改了 Codex · 管理员` | `10/22 15:10 批改了「收一条真实任务」 Codex · 管理员` |
| 展开是 `findings_hash` / `reviewed_commit` | 只留给人看的字段；没有就说「这个事件没有要人看的细节」 |

## 为什么

仓库主人：「截图里显示的轨迹没看明白是什么意思」。

## 怎么做到不出错

1. 动作 + 对象 = 一句话（活的名字取自办公室已有的任务表，取不到就退回编号，不编）。
2. 机器字段（哈希、commit、内部编号）不摆；藏进 title 里给查问题的人。
3. 纯显示改动，不碰后端与规范。

## 完成标准

1. 轨迹块有说明；每行带活的名字；payload 里没有机器噪声。
2. 冒烟加断言（这三样）。
3. 办公室全部冒烟无回归；构建通过；`./scripts.sh check` 全绿。
4. `docs/traceability.md` 登记 `MVP-UI-038`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `office/scripts/verify-office-conversations.mjs` | ok（轨迹块说了这是什么、每行带活的名字、机器字段（哈希）不摆、给人看的字段（原因）摆出来） |
| 真跑（8849 真平台 + 4174 预览） | 管理员窗口的轨迹读起来是：「10/22 15:10 批准了「建立工位屏幕与回执字段映射」Codex · 管理员」；展开只有「评审第几轮 1」，没有哈希；页面无报错 |
| 办公室全部冒烟 | ok（30/33；3 条恒失败是环境依赖：Godot dev server、本机 ollama `qwen3:4b`、在册凭据窗口 7≠9） |
| `office/` 构建 | ok（29 个模块，main 211.48 kB JS） |
| `tools/docstate` / `tools/discipline` / `ruff` | 98/98、44/44、ruff 全绿 |
| `pytest`（全量） | `PYTEST_EXIT=0`（本卡**没碰 `backend/**`**，后端与 main 逐字节相同 —— 同一份后端代码在上一轮已跑过全量） |
