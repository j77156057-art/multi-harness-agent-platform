# 认领：pool-metered-amount

| 字段 | 值 |
|---|---|
| task_id | `pool-metered-amount` |
| Harness | Codex |
| 认领时间 | 2026-10-22 12:20 +08:00 |
| 分支 | `agent/local/codex/FORMAL-MULTI-HARNESS-01/a1` |
| 状态 | doing（实现与定点测试已就位；待 Merge Gate） |
| test_id | `DOC-SCHEMA-001` `DOC-STATE-001` `DOC-TRACE-001` |

## 范围

让计量档的"花了多少"**能被说出来**，并且只说三种事实里说得清的那些：

- **token**：平台自己的账。修一个真缺陷——**流式请求一律记 0 token**（上游把
  usage 放在最后一个事件里，池子只转发不看），于是计量档花的钱在账上完全
  看不见。修法是 `_UsageSniffer`：上游线与下游线同时经过它，Responses / chat /
  Anthropic 三种协议的 usage 都能收到。
- **余额**：provider 自己报的数。平台去问 provider 的只读余额接口（已验证
  DeepSeek `/user/balance` -> `CNY 1.04`），带来源与查询时间，缓存 5 分钟；
  读不到就说读不到的原因（没设置 / 无凭据 / 拒绝 / 超时）。
- **单价**：**不知道**。这些模型名（`gpt-6.1-sol`、`deepseek-v4-flash[1M]`）是
  中转站改过的，没有公开价格可套。平台**不猜单价**，只报 token 与余额。
- **补历史**：那两次历史请求的 token **恢复不了**（池子没记、代理只记元信息、
  CLI 侧只有会话总量、provider 侧只有账号汇总）。能做的只有把**假 0** 改成
  **"未记上"**：`usage-corrections.jsonl`（日志本身不动）+ 汇总里的
  `tokens_unknown`；补历史是**可重跑**的脚本，不是手改账。
- **两个 provider 都能报余额**：DeepSeek `/user/balance` 与 codex 链的
  `/v1/usage`（本地代理透传，报钱包余额与逐日用量）。

## 变更

- `backend/app/pool/service.py`：`_UsageSniffer` / `_usage_blobs`；chat 与
  responses 两条流式路径都接上。
- `backend/app/pool/balance.py`（新）：provider 的只读余额/用量读取与解析
  （DeepSeek `/user/balance`、codex 链的 `/v1/usage`）。
- `backend/app/pool/inventory.py`：计量档多出每条 provider 的余额与原因。
- `backend/app/pool/service.py`：汇总里的 `tokens_unknown`（没数过的记录不算 0）。
- `backend/scripts/backfill_usage_corrections.py`（新）：可重跑的补历史脚本。
- `backend/tests/test_pool_stream_usage.py`（新）：用**真的本地 HTTP 服务**走一遍
  流式路径，证明 token 被记上。
- `backend/tests/test_pool_metered.py`（新）：嗅探器、余额解析与补历史的定点测试。

## 边界

- **不改 R10**：不新增事件名、状态、状态迁移边、canonical 表与列。余额是
  provider 的**只读事实**，不是新的平台语言。
- 不改 `backend/migrations/**`；不改生产项目素材与外部 Harness 工作树。
- 余额只读 provider 的公开只读接口，**不下单、不改配置**；读失败一律降级为
  "问不到"，不影响池子状态路由。
