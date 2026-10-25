# 认领：OFFICE-UI-28

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-28` |
| Harness | Mavis |
| 认领时间 | 2026-10-25 12:45 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-28/a1` |
| 状态 | doing |

目标：**办公室本地接上声明表** —— 声明出来的新 harness，界面上能登记，不用改代码。

为什么：仓库主人问「加入新的 harness 以后可以通用目前的接口吗」，问完选了「办公室本地接声明表」。

现在的缺口（查代码查出来的）：

- 平台侧 `backend/app/adapter/formal_worker.py:known_bases()` **已经**会读
  `adapters/cli.json` 的声明 —— 声明出来的 base 在平台那边是一等公民。
- 但**办公室本地网关**（`office/server/formal-dispatch-gateway.mjs`）的
  `BASE_ADAPTERS` 写死了 5 家（codex / claude-code / opencode / qoder / codebuddy）：
  - `/status` 的 `base_adapters` 只从这张表出 → 声明出来的 base **露不出来**；
  - `POST /base-harnesses` 拿模板只查这张表 → 登记声明出来的 base 会报
    `unknown_base_harness`；
  - `POST /harnesses`（给基座注册窗口）用 `BASE_HARNESSES` 卡基座 id → 同样漏。

范围：

- `backend/app/adapter/harnesses/generic.py`：把「一个声明长什么样」的模板抽成一个
  函数（`GenericCliAdapter` 造注册表的那份与它同源，别两处各写一遍）。
- `backend/app/api/routes.py`：`GET /harnesses` 的响应里多带一段 `base_adapters`
  （声明出来的基座模板）。**不新增端点、不新增权限行** —— 这个路由本来就是
  人用的（`harness:register`），办公室本地网关本来就在调它。
- `office/server/formal-dispatch-gateway.mjs`：基座列表、基座注册、窗口注册的
  基座校验，都接上平台给的声明模板。

**不碰** `docs/spec/**`、不新增 HTTP 端点、不改权限矩阵、不动其他 Harness 的
工作区与分支。

## 怎么做到不出错

1. **模板同源**：声明的模板由平台给（`GenericCliAdapter` 造注册表用的就是它），
   办公室本地不自己拼 provider/adapter —— 两处各写一遍就会漂。
2. **声明仍是受信输入**：这一卡**不写**声明文件（那由人写、平台不写），
   只是让界面认得人写好的声明。
3. **不新增端点与权限行**：`GET /harnesses` 已在权限矩阵里（`harness:register`，
   人），只扩响应体。
4. **登记路径要测**：网关测试（`node --test server/formal-gateway-admin.test.mjs`）
   加一条：平台给了声明模板 → 基座出现在列表里 → 登记用的是那份模板。
