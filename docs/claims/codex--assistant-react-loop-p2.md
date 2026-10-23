# 认领：assistant-react-loop-p2

| 字段 | 值 |
|---|---|
| task_id | `assistant-react-loop-p2` |
| Harness | Codex |
| 认领时间 | 2026-10-23 16:40 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（P1 已交付：`aaa8da5`；P2：模型通道＋assistant worker＋权限面核实） |

## 做什么（P2）
把 P1 的引擎接到**真模型**和**真消息**上：

1. **池子直连模型通道** `backend/app/assistant/model.py`：每步一次
   `POST /pool/v1/chat/completions`（OpenAI 兼容，服务端 `_cli_chat` 已能从
   CLI 槽出货）；模型链**钉死免费档 5 家**（qoder/Qwen3.8-Flash、
   qoder/DeepSeek-Flash、codebuddy/hy3、codebuddy/minimax-m3.1-flash、
   opencode-cli/minimax-m3.1-flash-free），依次回退；上游收据（usage）带回
   引擎 trace——**不本地估 token**，账记 consumer=harness/office-assistant。
   metered 档一分不碰。
2. **assistant worker** `backend/app/assistant/worker.py`：从 admin/worker
   削出的薄壳——复用 `PlatformClient`（import 复用，**零修改**共享文件）、
   私聊轮询（首跑锚定最新 seq 不重放历史）、发给 office-assistant 的
   direct 消息 → 吃进引擎（正文只作为上下文条 [c1] 进围栏，不是指令）→
   终局私聊回给提问者；stopped/异常按管理员先例私聊老实说明原因；状态
   文件只存游标。
3. **权限面**：核实结论——读面全部是公共读（host 头守卫），池子键
   `harness:pool` 本就对全体 harness 放行（policy.py L42），**权限矩阵零行
   新增**；部署时按既有 register 流程给 office-assistant 发 harness token。

## 边界
- 不改 `backend/app/admin/**`（只 import）、不新增事件/状态/schema；
- trace 目录不变（`<data>/assistant/trace/`）；引擎状态不进平台库；
- 模型通道失败（全免费档不可用）→ 老实报"通道不可用"，不烧 metered。

## 完成标准（机械可判，全不烧真额度）
1. `tests/test_assistant_channel.py`：免费链首个可用模型出货且回执入账；
   回退次序正确；全链不可用诚实抛错；空回复合不计步。
2. `tests/test_assistant_worker.py`：首跑锚定不重放；私聊提问→引擎→私聊
   回答（direct/mentions/正文断言）；成员文本只以 [cN] 围栏进 prompt；
   stopped 私聊诚实欠条；非私聊 @ 不触发；dry-run 不发消息；游标跨重启。
3. `tests/test_assistant_loop.py` 增补：模型带收据时 trace 记真实 token，
   不带仍写"未记上"。
4. ruff / docstate / discipline / pytest 定点+全量绿。
