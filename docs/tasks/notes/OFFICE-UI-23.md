# OFFICE-UI-23

### 统一任务卡

任务编号：OFFICE-UI-23
负责 Harness：Mavis
开始时间：2026-10-25 01:40 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（窗口状态页）
真相源引用：`office/src/flat-office.js`（事件行、payload 字段表、窗口页的轨迹块）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
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
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 真跑（8849 真平台 + 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填（本卡**没碰 `backend/**`**，后端与 main 逐字节相同） |
