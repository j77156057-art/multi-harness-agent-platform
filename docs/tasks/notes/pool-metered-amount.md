# pool-metered-amount

### 统一任务卡

任务编号：pool-metered-amount
负责人 Harness：Codex
开始时间：2026-10-22 12:20 +08:00
工作区：.worktrees/codex/
依赖任务：FORMAL-MULTI-HARNESS-01
真相源引用：R10 §16（完成证据）、§37.7（schema_version 提升只针对新增事件）；AGENTS.md §2/§3/§5b
当前状态：doing（实现与定点测试已就位；待 Merge Gate）
test_id：DOC-SCHEMA-001 DOC-STATE-001 DOC-TRACE-001

| 项 | 内容 |
|---|---|
| 依赖 | FORMAL-MULTI-HARNESS-01 |
| test_id | `DOC-SCHEMA-001` `DOC-STATE-001` `DOC-TRACE-001` |
| 允许修改 | `backend/app/pool/**`, `backend/tests/**`, `docs/prep/AGENT-OFFICE-UNIFICATION/**`, `docs/claims/codex--pool-metered-amount.md`, `docs/tasks/notes/pool-metered-amount.md`, `TASKS.md` |
| 禁止修改 | `docs/spec/**`, `backend/migrations/**`, 生产项目素材与外部 Harness 工作树 |

## 完成标准

1. **流式请求的 token 被记上**：一个真的本地 HTTP 服务回一个带 usage 的
   Responses 流，池子把它记进 `usage.jsonl`（prompt / completion 都要对），
   且 `usage_summary` 里能看见。
2. **三种协议的 usage 都能收到**：Responses（`response.usage`）、chat
   （顶层 `usage`）、Anthropic（`message.usage` + `message_delta`）。
3. **余额是 provider 自己报的数**：读 provider 的只读余额接口，答案带来源与
   查询时间；读不到时给出**原因**（没设置 / 无凭据 / 拒绝 / 超时），不编 0。
4. **单价不被猜**：界面与接口都只报 token 与余额，并说明单价是 provider 自己的
   口径（这些模型名是中转站改过的）。
5. **补历史不编数字**：没数过的记录在汇总里是“未记上”（`tokens_unknown`），
   而不是 0；日志本身不被改写；补历史由可重跑的脚本产生。
6. 定点测试绿；`tools/discipline`、`tools/docstate`、`ruff` 绿；全量 pytest 绿。

### 验证结果

（待本分支收口时补：定点测试与门禁输出。）
