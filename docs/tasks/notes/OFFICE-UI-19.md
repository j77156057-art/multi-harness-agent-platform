# OFFICE-UI-19

### 统一任务卡

任务编号：OFFICE-UI-19
负责 Harness：Mavis
开始时间：2026-10-24 20:10 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-16（窗口页的模型选择）
真相源引用：`backend/app/pool/service.py`（池子的发现与缓存、`invalidate_discovery`）、
`backend/app/pool/sources.py`（额度自己声明的 `tier`）、`backend/app/pool/routing.py`
（平台照办前怎么核对）、`backend/app/adapter/model_choice.py`（选择的语义）；
R10 §8（canonical 数据模型）、§37.6（表与必要字段）、§38（R10-fix 流程）；
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-034

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-16 |
| test_id | `MVP-UI-034` |
| 允许修改 | `backend/app/adapter/model_choice.py`、`backend/app/api/routes.py`、`backend/app/api/schemas.py`、`backend/app/pool/routing.py`、`backend/app/pool/routes.py`、`backend/migrations/018_harness_model_tier.sql`、`backend/tests/test_harness_model_selection.py`、`office/src/flat-office.js`、`office/src/flat-office.css`、`office/src/main.js`、`office/scripts/verify-office-conversations.mjs`、`office/server/formal-dispatch-gateway.mjs`、`docs/claims/mavis--OFFICE-UI-19.md`、`docs/tasks/notes/OFFICE-UI-19.md`、`docs/traceability.md`、`docs/spec/0.3-R10.md`（§38.15 R10-fix-20 + §37.6 表行）、`TASKS.md` |
| 禁止修改 | `backend/app/tasks/**`、`backend/app/admin/worker.py`、`docs/dev-convention.md`、`tools/**`、其他 Harness 的工作区与分支 |

## 做什么

把「它用哪个模型」收口成三件事：

| 要什么 | 怎么做 |
|---|---|
| 收费额度也要能选，但要点头 | 按量计费（`metered`）的模型必须人明确同意（`paid_consent`），平台才肯花；拒绝时把原因说清 |
| 清单折叠起来，别弄太长 | 二十几个模型收进一个可展开的清单（`<details>`），标题写明每档几个 |
| 加个探针，点刷新时重探 | `POST /pool/probe` 清掉发现缓存重探一遍；办公室把新清单与上次那份比：**新增的免费额度**、**从免费变成收费的额度**都要说出来 |

平台这一侧还要补上关键的一环：**人点头时那一档**要记下来（`harnesses.model_tier`）。
人当初点头时它是免费、现在它变成收费了 —— 平台**不再花它**，回落到自带并写明
「要你重新点头」。没有这一栏，光在界面上提醒是没用的：人没打开页面时，平台照样在花。

## 为什么

仓库主人：「那么收费额度呢，另外折叠起来，别弄太长，另外加个探针，点击刷新的时候可以
重探模型，防止之前的免费模型重新收费，或者加入新的免费模型却不知道」。

## 怎么做到不出错

1. **收费一定要点头**（服务端闸门，不靠界面自觉）：没点头就 409 + 说明。
2. **免费变收费就不再花**：照办前核「点头时那一档」与「现在这一档」；对不上就回落
   自带 + 写明原因。
3. **新额度要看得见**：探针结果与上次那份比（上次那份记在 localStorage），多的、变档
   的都摆出来。
4. **探针不常跑**：只有人点了才重探；平时自动刷新读缓存。
5. **改 schema 要过 §38**：迁移 018 + R10-fix-20 + §37.6 表行，**同一次提交**；不新增
   事件名/状态/迁移边 → 事件 `schema_version` 不动。

## 完成标准

1. 迁移 `018_harness_model_tier.sql` 落地，`harnesses` 多一列 `model_tier`。
2. 选按量计费的模型：没点头 → 平台拒（409）并说清；点头 → 存下选择与那一档。
3. 照办时发现「点头时是免费、现在收费」→ 不花它，回落自带并写明要重新点头。
4. `POST /pool/probe` 重探并回一份新清单；办公室的「刷新」与「重新探一遍」都走它。
5. 界面：清单默认折叠、收费模型有标记、收费模型要确认、探针后报出新增与变档。
6. `backend/tests/test_harness_model_selection.py`（加断言）与办公室冒烟通过；全部冒烟
   无回归；构建通过；`./scripts.sh check` 全绿。
7. `docs/traceability.md` 登记 `MVP-UI-034`；R10 §38.15 R10-fix-20 与实现同一次提交。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_harness_model_selection.py` | 待回填 |
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 真跑（8849 真平台 + 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
