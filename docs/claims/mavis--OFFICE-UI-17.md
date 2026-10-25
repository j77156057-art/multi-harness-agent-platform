# 认领：OFFICE-UI-17

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-17` |
| Harness | Mavis |
| 认领时间 | 2026-10-24 17:50 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-17/a1` |
| 状态 | doing |

目标：**窗口页给每个窗口四个 token 度量** —— 每秒生成多少、消耗总共多少、最近一小时多少、
缓存命中率。

为什么：仓库主人：「添加每秒生成token数量计时，消耗总token数量，消耗每小时token数量，
缓存命中率」。

两个**数据上的洞**（查过代码才敢这么说）：

1. **平台以前不记缓存 token** —— provider 把缓存命中的 token 放在三个不同的地方
   （OpenAI chat 的 `prompt_tokens_details.cached_tokens`、Responses 的
   `input_tokens_details.cached_tokens`、Anthropic 的 `cache_read_input_tokens`），
   池子以前一个都不取。所以「缓存命中率」**根本算不出来**。
2. **汇总层只给请求数，不给 token** —— 用量账里每条记录都有 `consumer`（`harness/<id>`）、
   `duration_ms`、token 数，但 `usage_summary()` 给每个 consumer 的只有
   `{requests, upstreams, models}`。于是「这个窗口烧了多少」问不出来。

范围：

- `backend/app/pool/service.py`：
  - 记 `cached_tokens`（三种协议位置都认；只认**读**缓存，写缓存不算命中）；
  - `usage_summary()` 给每个 consumer：输入/输出/缓存 token、**每秒生成 token**
    （生成 token ÷ 生成耗时）、**缓存命中率**（命中 ÷ 输入）；
  - `usage_timeline()` 的小时桶给每个 consumer 的 token（回答「每小时多少」）；
  - 写进用量日志的行里（`cached_tokens`）。
- `backend/tests/test_pool_token_metrics.py`：新增断言（4 条）。
- `office/src/flat-office.js`：窗口页加「它的用量」块。

**不碰** schema（用量账是 JSONL 日志，不是表）→ 不需要迁移、不需要动 R10 正文 ✓。

不碰 `docs/spec/**`、其他 Harness 的工作区与分支。

## 怎么做到不出错

1. **数不到就是数不到**：分母是 0 时给 `None`，界面上写「没数到」，**不给 0** ——
   0 会被读成「它一次都没生成」。
2. **命中率的分母是输入 token**：命中的是输入的一部分（`cached ≤ prompt`）。
3. **速率的分母是生成耗时**（`duration_ms`），不是墙钟时间 —— 一个请求生成了 200 token
   用了 4 秒，就是 50 token/秒。
4. **只认读缓存**：`cache_creation_input_tokens`（写缓存）不是命中。
5. **后端与界面都断一次**：后端断言账上的数，界面断言窗口页写出来的数。
