# 认领：OFFICE-UI-16

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-16` |
| Harness | Mavis |
| 认领时间 | 2026-10-24 18:55 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-16/a1` |
| 状态 | doing |

目标：**窗口页给每个窗口一个「它用哪个模型」的选择器，选了平台照办** —— 用这个窗口
自带的模型，或者用额度池里的某个模型（写清楚是哪一档额度、哪家 harness 的什么模型）。

为什么：仓库主人：「添加模型选择，可以选择自带的模型，也可以选择使用额度池的模型，
要写清楚是哪个池子的什么模型，额度池子分为免费和付费两种池子，免费池子标清楚是哪家
harness 内部的什么模型，付费池子也得标清楚是哪家harness的什么模型」。主人选的是
**真能选**（不是只列出来给人看）—— 所以要有数据、要有迁移、平台要照办。

现在平台的事实（查过代码才敢这么说）：

1. **模型是 CLI 自己配置里的事实，平台只读**（`backend/app/adapter/models.py:55`
   的 `configured_model`）。平台没有「给某个窗口指定用哪个模型」这件事。
2. **额度池已经在替人挑模型，但那是平台自己挑的**：`backend/app/pool/routing.py:59`
   的 `plan_attempt` 只在**上一轮是 provider 失败**时才从池子里挑一个（免费 →
   计量 → 人的 key，需点头）。人**没有开口子**指定过。
3. **池子里的模型是有主的**：每条都带 `owned_by`（额度名，形如 `qoder:Qwen3.8-Flash`）
   与 `tier`（`free` / `reserve` / `metered` / `user`）。所以「哪家的什么模型」是
   **平台已有的事实**，只是没摆到人面前。

范围：

- `backend/migrations/017_harness_model_selection.sql`：`harnesses` 加
  `model_mode` / `model_id` / `model_updated_at`（**新增 schema → 必须有迁移**）。
- `backend/app/adapter/model_choice.py`（新）：选择的读写与语义（`cli` / `pool`）。
- `backend/app/adapter/registry.py`：`listing()` 带上这两个字段（办公室要显示现状）。
- `backend/app/api/routes.py`：
  - `GET /adapter/availability` 每个窗口多两个字段（当前选择）；
  - `POST /adapter/model-selection`（**复用 `harness:register` 权限，不新增权限键**）。
- `backend/app/pool/routing.py`：`plan_attempt` 认人的指定 —— 这一轮**直接**走那个
  池子模型（不再只在失败后才换池子）。
- `backend/app/pool/routes.py`：`/pool/plan` 把这个窗口的选择读出来交给路由。
- `office/src/flat-office.js` + `office/src/flat-office.css`：窗口页的「它用哪个模型」。
- `office/server/formal-dispatch-gateway.mjs`：转发保存动作。
- `backend/tests/test_harness_model_selection.py`（新）：断言。
- `docs/spec/0.3-R10.md`：§38.14 R10-fix-19 + §37.6 `harnesses` 行补列
  （**schema 改了，正文必须同一次提交一起改**）。

**不碰** R10 §10 状态机、§37.7 事件 allowlist（不新增事件名/状态/迁移边 →
事件 `schema_version` 不动）、其他 Harness 的工作区与分支。

## 怎么做到不出错

1. **人不点头就不花他自己的钱**：池子里「你自己的 key」那一档仍然要人点头；
   没点头就**问**，这一轮照旧走自带（与现状一致）。
2. **不静默换一个模型**：指定的模型此刻不在池子里 → 回落到自带，并写明为什么；
   绝不悄悄换一个看起来差不多的。
3. **刚挂掉的模型不用第二次**：人指定的模型如果就是上一轮失败的那个，这一轮按现状
   自动挑（自动路径本来就会跳过失败的那个）。
4. **「谁的额度」写清楚**：界面上每个模型都写「哪家的什么模型」，不给人一个光秃秃的
   模型名。
5. **数不到就说数不到**：界面上的当前选择是平台登记里的事实，平台没登记就写
   「未登记」，不猜。
