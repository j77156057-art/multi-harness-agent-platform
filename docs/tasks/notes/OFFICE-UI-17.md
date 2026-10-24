# OFFICE-UI-17

### 统一任务卡

任务编号：OFFICE-UI-17
负责 Harness：Mavis
开始时间：2026-10-24 17:50 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-12（左边改成会话列表）
真相源引用：`backend/app/pool/service.py`（用量账与汇总）、`backend/app/pool/routes.py`（`/pool/status`）、
R10 §19（群聊与定向消息）；`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-UI-031

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-12 |
| test_id | `MVP-UI-031` |
| 允许修改 | `backend/app/pool/service.py`、`backend/tests/test_pool_token_metrics.py`、`office/src/flat-office.js`、`office/scripts/verify-office-*.mjs`、`docs/claims/mavis--OFFICE-UI-17.md`、`docs/tasks/notes/OFFICE-UI-17.md`、`docs/traceability.md`、`TASKS.md` |
| 禁止修改 | `backend/migrations/**`、`backend/app/api/routes.py`、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

窗口页给每个窗口四个 token 度量：

| 度量 | 口径 |
|---|---|
| 每秒生成 token | 生成 token ÷ 生成耗时（`duration_ms`），单位 token/秒 |
| 消耗总 token | 输入 + 输出（命中缓存的另列） |
| 消耗每小时 token | 最近一个有记录的小时（时间线的小时桶） |
| 缓存命中率 | 命中缓存的 token ÷ 输入 token |

后端两个洞一起补上：**平台以前不记缓存 token**（三种协议位置都没取），**汇总层只给请求数
不给 token**。

## 为什么

仓库主人：「添加每秒生成token数量计时，消耗总token数量，消耗每小时token数量，缓存命中率」。

## 怎么做到不出错

1. **数不到就是数不到**：分母是 0 时给 `None`，界面写「没数到」，**不给 0**。
2. **命中率的分母是输入 token**。
3. **速率的分母是生成耗时**，不是墙钟时间。
4. **只认读缓存**（`cache_creation_input_tokens` 是写缓存，不算命中）。
5. **后端与界面各断一次**：后端断言账上的数，界面断言窗口页写出来的数。
6. **不碰 schema**：用量账是 JSONL 日志，不是表 → 不需要迁移、不用动 R10 正文。

## 完成标准

1. 平台记下缓存 token（三种协议位置都认）；用量日志的行里多这个字段。
2. `usage_summary()` 给每个 consumer：输入/输出/缓存 token、每秒生成 token、缓存命中率。
3. `usage_timeline()` 的小时桶给每个 consumer 的 token。
4. 窗口页显示这四个数，数不到写「没数到」。
5. `backend/tests/test_pool_token_metrics.py`（4 条）与办公室冒烟通过；全部冒烟无回归；
   构建通过；`./scripts.sh check` 全绿。
6. `docs/traceability.md` 登记 `MVP-UI-031`。

## 验证结果

| 门禁 | 结果 |
|---|---|
| `backend/tests/test_pool_token_metrics.py`（新增） | ok（4 条） |
| `office/scripts/verify-office-conversations.mjs` | 待回填 |
| 办公室全部冒烟 | 待回填 |
| `office/` 构建 | 待回填 |
| `tools/docstate` / `tools/discipline` / `ruff` | 待回填 |
| `pytest`（全量） | 待回填 |
