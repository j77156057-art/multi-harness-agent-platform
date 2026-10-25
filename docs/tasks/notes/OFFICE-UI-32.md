# OFFICE-UI-32

### 统一任务卡

任务编号：OFFICE-UI-32
负责 Harness：Mavis
开始时间：2026-10-25 17:20 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）
真相源引用：`office/src/opening-host.js`（向导宿主）、
`office/contributions/office-opening-phone/office-opening-phone.js`（锁定的向导组件，只读）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`IMPLEMENT_COMMIT_PLACEHOLDER`（向导的「任务目标」输入框不再落在折叠线以下）
验证结果：见文末「验证结果」表。
test_id：MVP-UI-047

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-UI-047` |
| 允许修改 | `office/src/opening-host.js`、`office/src/*.css`、`office/scripts/**`、`docs/claims/mavis--OFFICE-UI-32.md`、`docs/tasks/notes/OFFICE-UI-32.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `office/contributions/**`（锁定的贡献）、`docs/spec/**`、`backend/app/api/routes.py`（codex 在途）、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 「任务目标」输入框排在表单最后，落在滚动区下方（实测 `top=912` vs 可视区 `bottom=798`），要滚才看得到 | 目标输入框在表单最前面，第一屏就看得见（组件自己的那个输入框，状态与校验照旧） |

## 为什么

仓库主人圈了「单开一卡」：向导的「任务目标」输入框落在折叠线以下。
第一次用的人第一眼看不到自己要写什么。

## 怎么做到不出错

1. 搬组件自己的输入框，不新增第二个（避免两处不同步）。
2. 组件每次重渲染都重建表单 → 每次重渲染后都搬（MutationObserver，与既有 patch 同一手法）。
3. 不改锁定贡献文件，不动组件的取值与校验。

## 完成标准

1. 向导 config 阶段打开时，「任务目标」输入框在第一屏可见。
2. 组件行为不变（摘要、校验、派单照旧）。
3. 办公室冒烟与既有向导走查全绿。
4. `docs/traceability.md` 登记 `MVP-UI-047`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 真跑（8849 真平台 + 4174 预览） | 打开向导的 config 阶段，量目标输入框的几何：**改前** `top=912`（滚动区可视区只到 `bottom=798` → 落在折叠线以下，第一眼看不到）；**改后** `top=251`（可视区 208~798 → 第一屏可见），且它是表单的**第一项**。画面确认：目标框现在是向导里的第一项 |
| 搬动没有把它弄坏 | 新增 `office/scripts/verify-opening-goal.mjs`（进冒烟清单）：填目标 → 选管理员与成员 → 「查看配置摘要」→ 摘要里**照旧**出现目标原文（组件的状态、校验、摘要都没坏；没有新增第二个输入框） |
| 办公室全部冒烟 | 31/34（3 条是与本卡无关的环境依赖：verify-anchors、verify-harness-connections、verify-project-workspace，与 OFFICE-UI-28~31 同） |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| 代码 | 本卡**只动前端**（`office/src/opening-host.js` + 一个走查脚本），后端与 OFFICE-UI-31 逐字节相同（`pytest` 全量已在那一轮跑过并 EXIT=0） |
