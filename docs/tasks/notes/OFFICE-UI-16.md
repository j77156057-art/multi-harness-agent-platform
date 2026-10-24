# OFFICE-UI-16

### 统一任务卡

任务编号：OFFICE-UI-16
负责 Harness：Mavis
开始时间：2026-10-24 18:55 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（左边改成会话列表）
真相源引用：`backend/app/pool/routing.py`（平台怎么安排这一轮走哪个模型）、
`backend/app/pool/service.py`（池子里有哪些模型、谁的额度、哪一档）、
`backend/app/adapter/models.py`（窗口自带的模型是 CLI 自己配置里的事实）、
`backend/app/adapter/registry.py`（窗口登记）；R10 §8（canonical 数据模型）、
§37.6（表与必要字段）、§38（R10-fix 流程）；`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-032

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-032` |
| 允许修改 | `backend/app/adapter/model_choice.py`、`backend/app/adapter/registry.py`、`backend/app/api/routes.py`、`backend/app/api/schemas.py`、`backend/app/pool/routing.py`、`backend/app/pool/routes.py`、`backend/migrations/017_harness_model_selection.sql`、`backend/tests/test_harness_model_selection.py`、`office/src/flat-office.js`、`office/src/flat-office.css`、`office/scripts/verify-office-*.mjs`、`office/server/formal-dispatch-gateway.mjs`、`docs/claims/mavis--OFFICE-UI-16.md`、`docs/tasks/notes/OFFICE-UI-16.md`、`docs/traceability.md`、`docs/spec/0.3-R10.md`（§38.14 R10-fix-19 + §37.6 表行）、`TASKS.md` |
| 禁止修改 | `backend/app/tasks/**`、`backend/app/admin/worker.py`、`docs/dev-convention.md`、`tools/**`、其他 Harness 的工作区与分支 |

## 做什么

窗口页给每个窗口一个「**它用哪个模型**」的选择器，**选了平台照办**：

- **自带模型**：用这个窗口自己的 CLI 配置里那个模型（现状）。
- **额度池的模型**：这一轮**直接**走池子里指定的模型。界面上按档摆清楚：
  - **免费额度**：不额外花钱，用的是各家 CLI 自带的免费额度 —— 写明是哪家的什么模型
    （例：`qoder 的 Qwen3.8-Flash`、`codebuddy 的 hy3`）；
  - **付费额度**：按量计费 —— 写明是哪家的什么模型（例：`codex 的 gpt-5.6`）；
  - **兜底额度**：订阅里的额度，平台不自己花；
  - **你自己的 key**：平台不自己花，要你先点头。

平台这边要真的照办：`plan_attempt` 在安排这一轮时，**先看人给这个窗口指定的选择**。

## 为什么

仓库主人：「添加模型选择，可以选择自带的模型，也可以选择使用额度池的模型，要写清楚是
哪个池子的什么模型，额度池子分为免费和付费两种池子，免费池子标清楚是哪家harness
内部的什么模型，付费池子也得标清楚是哪家harness的什么模型」。主人选的是**真能选**
（问卷里明确选了「真能选」而不是「只列出来」）。

## 怎么做到不出错

1. **人要点头才花自己的钱**：池子「你自己的 key」那一档仍然要人点头；没点头就问，
   这一轮照旧走自带。
2. **不静默换模型**：指定的模型此刻不在池子里 → 回落自带 + 写明原因。
3. **刚挂掉的模型不用第二次**：人指定的模型正是上一轮失败的那个 → 按现状自动挑。
4. **谁的额度写清楚**：每个模型都写「哪家的什么模型」，不给人一个光秃秃的模型名。
5. **改 schema 要过 §38**：迁移 + R10-fix-19 条目 + §37.6 表行，**同一次提交**；
   不新增事件名/状态/迁移边 → 事件 `schema_version` 不动。
6. **不新增权限键**：复用 `harness:register`（人可用）。

## 完成标准

1. 迁移 `017_harness_model_selection.sql` 落地，`harnesses` 多两个字段。
2. `POST /adapter/model-selection` 能存下「自带 / 池子里的哪个模型」，`GET
   /adapter/availability` 读得回。
3. `plan_attempt` 认这个选择：指定池子模型时这一轮直接走它（并写明为什么）。
4. 窗口页有选择器：按免费/付费/兜底/自己的 key 分档，每条写明「哪家的什么模型」，
   保存后读回平台的现状。
5. `backend/tests/test_harness_model_selection.py`（新增）与办公室冒烟通过；全部冒烟
   无回归；构建通过；`./scripts.sh check` 全绿。
6. `docs/traceability.md` 登记 `MVP-UI-032`；R10 §38.14 R10-fix-19 与实现同一次提交。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_harness_model_selection.py`（新增） | 待回填 |
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 真跑（8849 真平台 + 预览） | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
