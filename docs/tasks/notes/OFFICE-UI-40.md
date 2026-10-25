# OFFICE-UI-40

### 统一任务卡

任务编号：OFFICE-UI-40
负责 Harness：Mavis
开始时间：2026-10-26 06:45 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~39 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/pool/service.py`（usage 解析与记账）、
`backend/app/pool/balance.py`（provider 自己报的余额）、
`backend/tests/test_pool_metered.py`、`docs/tasks/notes/pool-metered-amount.md`（被收尾的
那张卡）、`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`IMPLEMENT_COMMIT_PLACEHOLDER`（接手 codex 的在途：结掉 pool-metered-amount）
验证结果：见文末「验证结果」表。
test_id：DOC-SCHEMA-001

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `DOC-SCHEMA-001` |
| 允许修改 | `docs/tasks/notes/pool-metered-amount.md`、`docs/claims/codex--pool-metered-amount.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-40.md`、`docs/tasks/notes/OFFICE-UI-40.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| `pool-metered-amount` 写着 doing（实现与定点测试已就位，待 Merge Gate）；那张卡还是 GBK 编码，在 UTF-8 仓库里显示为乱码 | 6 条完成标准逐条核实 + 定点测试真跑；卡转 UTF-8；状态改成 done |

## 为什么

仓库主人圈了顺序「先做 1，接着 2、3」。这是 3。

## 怎么做到不出错

1. 不碰实现。
2. 转码先按 GBK 读原文、再按 UTF-8 写回（不能直接改字节）。
3. 逐条核实它自己写的完成标准。

## 完成标准

1. 6 条逐条核实 + 定点测试真跑。
2. 卡转 UTF-8，状态 done。
3. 门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 逐条核实（6 条） | **6/6 通过**：① 流式 token 被记账（真跑，usage.jsonl 有 `prompt=120 completion=45`，**不是 0**）② `_usage_blobs()` 一个地方收三种协议（Responses `response.usage` / chat 顶层 / Anthropic `message.usage`+`message_delta`）③ 余额读 provider 自己的只读接口（`/user/balance`、`/v1/usage`），读不到给原因不编 0 ④ 单价不猜（只报 token 与余额）⑤ 没被计过的记「未计上」（`tokens_unknown`）不是 0 ⑥ 定点测试与门禁全绿 |
| 真跑（手动，不是单元级） | 真起一个本地 HTTP 服务回带 usage 的 Responses 流 → 池子走 `responses_stream` 真发一次 → 3 个流式块回来，`usage.jsonl` 记 1 条（`note="streamed (responses)"`），`usage_summary` 里能看到 consumer `harness/manual-run` |
| 定点测试真跑 | `tests/test_pool_metered.py` + `test_pool_stream_usage.py` + `test_pool_token_metrics.py` 全绿 |
| 「转 GBK」这件事 | **不需要转** —— 那张卡本来就是 UTF-8（U+FFFD 0 个），在 Windows 控制台里显示乱码是**终端按 GBK 读 UTF-8 文件**的缘故。已在卡片里写清，免得下一个人再去「修」一个没坏的东西 |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| `pytest`（全量） | EXIT=0（本卡**只改文档**；后端代码逐字节未变） |
| 边界 | **没有改它的实现**；核实出缺口就另开卡 |
