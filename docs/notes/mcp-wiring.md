# 平台工具的接线（MCP）：四家能接到什么程度

状态：M1 已实现 codex / codebuddy / qoder，pi 不接。作者：助理 agent。日期：2026-11-02。

这篇回答一个问题：**「这个 CLI 也能用平台工具了」这句话，我们凭什么说**。答案分三档，
混在一起说就是骗人：

| 档 | 说的是什么 | 怎么才算 |
|---|---|---|
| A 配置解析 | 这份配置文件/这串 argv 它认，认出来的端点、传输、认证方式都对 | 它自己的命令把配置**回读**出来，字段对得上 |
| B 会话内行为 | 它真的去连了，并把服务端的工具注册进自己那套 | 一次真跑里平台侧收到 MCP 的 `initialize`，且只断连没报错 |
| C 端到端 | 模型真的调了平台的工具，平台真的收到了 | 一次真跑里出现工具调用，且产物经过工具写进工作树 |

**本版只做到 A**（外加单测钉住命令行形状）。B 与 C 要一次真跑，见 §7 ——
没跑之前不要在任何地方写"已验证"。

## 1. 四家各怎么给（全部实测，不是记忆）

| CLI | 通道 | 令牌怎么给 | 证据 | 本版 |
|---|---|---|---|---|
| **codex** | `-c mcp_servers.platform.url="…"` + `-c mcp_servers.platform.bearer_token_env_var="AP_PLATFORM_MCP_TOKEN"` | 命令行里只有**变量名**；令牌进子进程环境 | A（`mcp list --json` 回读：`type: streamable_http`、`bearer_token_env_var`、`auth_status: bearer_token`） | 接 |
| **codebuddy** | `--mcp-config <临时文件>`，文件是 `{"mcpServers": {…}}` | 令牌写进 0600 临时文件；命令行只有路径 | A（`mcp add -t http` 写出的形状与我们的文件同形；`--mcp-config` 见 help 与源码） | 接 |
| **qoder** | 同 codebuddy（`--mcp-config <路径>`） | 同上 | A（`mcp add -t http -H` 写出的形状；schema 枚举 `stdio/sse/http/ws/streamable-http`） | 接 |
| **pi** | 只有 `~/.pi/agent/mcp.json`（靠 `PI_CODING_AGENT_DIR` 隔离），**没有** `--mcp-config` | — | A | **不接**（见 §5） |

三家都要的字段只有三个：`type: http`、`url`、`headers.Authorization`。
`type` 对 codex 是隐式的（有 `url` 就是 streamable HTTP），对另外两家写出来更清楚。

## 2. 为什么不依赖 `${VAR}`

codebuddy 与 qoder 都支持在配置里写 `${VAR}`（codebuddy 的正则只收大写变量名；
qoder 还支持 `${VAR:-默认}`）。**但不采用**，理由是同一条：codebuddy 研究侧明确标了
未知 —— `--mcp-config` 那条（DYNAMIC 作用域）是否也走同一套展开，没有验过。万一不走，
请求头会原样是 `Bearer ${AP_PLATFORM_MCP_TOKEN}`，平台回 401，看起来像"模型不会用工具"。

所以跟 claude 一样：**令牌写进那份临时文件**（`write_json` 会 `chmod 0600`），
命令行只出现路径。秘密落在磁盘上但只落在一个 0600 的临时目录里，跑完就删 ——
比"赌一个没验过的展开"划算。

## 3. 令牌不进 argv（三条，各有各的做法）

- codex：`-c` 里只有环境变量**名**（`bearer_token_env_var=…`），值由 `process_environment()`
  放进子进程环境；
- codebuddy / qoder：argv 里只有临时文件路径，令牌在文件里；
- 三家都不把配置文件写进工作树：工作树里多一个平台自己写的文件，会让这次交付被判成
  "留了脏东西"（claude/opencode 都踩过）。codex 根本不写文件。

## 4. 代价：接上之后"谁提交"变了

`formal_worker` 判定"平台替它提交"的条件里有一条是 `adapter.mcp_binding() is not None`。
这不是副作用，是必须的：工具那一侧**没有** git 写（提交要过范围判定），拿工具的窗口
自己提不了。opencode 拿 `run_command` 去 `git commit` 被门槛拒掉、于是没人提交、整单
被判"没有提交"退回 —— 那一次就是这么踩的。所以接 MCP 与"平台提交"是同一件事。

## 5. 本版不做

- **pi 不接**：它没有 `--mcp-config`，只能往隔离的 agent 目录写 `mcp.json`；而它的工具
  是 codemode / `tool_search` 那套体系，服务端的工具能不能被模型直接调到，一点证据都没有
  （它还拒绝 legacy SSE，只收 streamable HTTP）。三样都不确定的情况下接上去，最可能的
  结果是"看起来有工具、实际调不到"，比不接更坏。等有人愿意跑一次 pi 的端到端再说。
- **不传 `--strict-mcp-config`**：那会把这个人自己配的 MCP 服务器全部忽略掉。宁可合并 ——
  只有他自己也起了一个叫 `platform` 的服务器才会撞名。
- **不写用户级配置目录**（`~/.codex`、`~/.codebuddy`、`~/.qoder`、`~/.pi` 一律不碰）：
  那是人的东西。codex 用 `-c` 只影响这一次进程；另两家用临时文件。
- **不替人做 MCP OAuth 登录**；**不做 stdio 桥**（我们给的是 HTTP 端点，不是本地进程）。

## 6. 验收

单测（`tests/test_agenttools_wiring.py`）钉住四件事：

1. 没有这次尝试（评审、一次性调用）就不给工具 —— `mcp_binding()` 是 `None`；
2. argv 里**没有令牌**（并且 codex 的 argv 里只有变量名）；
3. 令牌确实进了子进程环境 / 临时文件，且临时文件不在工作树里；
4. codex 的 `-c` 值带查询串（有 `&`）也算一个 argv —— 实测过 Python 走 cmd 壳子时
   不会把它拆开（`D:\Temp\argtest` 的一次性探针），这一条留作回归。

这些钉的是**配置形状**，不是「模型真的调得到」。那件事分三档写在 §7，读的时候别把两者混起来。

## 7. 这一版验到哪一档

先说结论，免得把"没验"读成"不能用"：

| 档 | 对谁成立 | 怎么来的 |
|---|---|---|
| A 配置解析 | codex / codebuddy / qoder | 它自己的命令回读配置（§1） |
| B 会话内真的连上 | **codex** | §7.1 的零成本实验：`initialize → notifications/initialized → tools/list`，每一条都带正确的 `Authorization` |
| C 模型真的调到平台工具 | **这是 MCP 这条路的适用条件，不是缺口**（§7.2） | 见下 |

`docs/notes/tool-calling-channels.md` 里写过：**MCP 是主路，`platform` 基座是保底**。
两件事必须分开，混在一起就会把"工具能不能用"和"MCP 接线通不通"说成一回事：

- **工具能不能用**：早就验过了，而且**不挑模型**——基座那条路请求里不带 `tools`，
  模型只回文本，平台执行。2026-10-31 实测 `platform-member-1` + `codebuddy/hy3`
  （免费档）跑完一条真任务，3 步 `list_files → create_file → finish`。
- **MCP 接线通不通**：验的是"外部 CLI 自己的循环能不能调到平台工具"。它多一层价值
  （少一次文本往返、模型原生看得到工具结果），但它**不是窗口拿到工具的唯一通道**。

## 7.1 codex 的 B 档证据（2026-11-02 实测，零成本）

做法值得记下来，因为它是唯一不花钱又能拿到证据的路子：

- 起一个只记账的本地 MCP 端点（`data/mcp-mock/server.mjs`，一行一个 JSON 记下谁来过、
  带了什么头、`rpc_method` 是什么）；
- 用适配器**同一套参数**跑一次 codex，只把模型指到死端口
  （`model_providers.deadpool.base_url=http://127.0.0.1:9`）—— 它必失败，但**连接 MCP
  发生在调模型之前**，证据照样拿到，且一个字节都不会外呼；
- 结果（`data/mcp-mock/log.jsonl` 三行）：

  | 顺序 | rpc_method | Authorization |
  |---|---|---|
  | 1 | `initialize` | `Bearer apt_selfcheck_token_123` |
  | 2 | `notifications/initialized` | 同上 |
  | 3 | `tools/list` | 同上 |

  令牌来自**环境变量**（`AP_PLATFORM_MCP_TOKEN`），命令行里只有变量名 —— 与设计一致。
  之后 codex 在死端口上连试 5 次报 `turn.failed`、退出码 1，这是预期的。

codebuddy / qoder 没有做这一档的实验：它们一旦真的跑起来就用**那个账号自己的额度**
（见 §7.3），要花谁的钱得先问。

## 7.2 C 档的适用条件：模型通道得支持 `tools`

这一档不是"还没做的工作"，而是这条路的**前提**，写清楚免得被当成阻塞：

- 池子里的免费档（`codebuddy/hy3`、`qoder/Qwen3.8-Flash`、`opencode-cli/*`）**全是
  `protocol: cli` 槽位** —— 编码 CLI 带着工具定义去接会被直接拒
  （`relay_cli_cannot_use_tools`）。所以 C 档要么用那个人自己的账号，要么用付费档。
- **窗口因此拿不到工具吗？不。** 换成 `platform` 基座就有工具，那条路不看模型能不能
  发 `tool_calls`，免费档也能跑（§7 的实测）。
- 所以取舍是：**想让窗口用免费额度干活 → 走基座；想让它用自己那套循环调工具 → 走
  MCP，那就要一条支持 tools 的模型通道。** 两条都在，不是二选一的技术债。

## 7.3 顺带一条不对称：能被指到额度池的只有 codex

`codebuddy` 与 `qoder` 的适配器**没有** `route_environment()`（基类返回空），平台没有
任何环境变量/参数能把它们指到额度池 —— 它们一跑就用那个账号自己的额度。codex 有
（`OPENAI_API_KEY` + `-c model_provider=pool`）。

这条**不阻塞任何事**：它只说明"这三家接了 MCP 之后，模型从哪来"不一样。要让它们也吃
池子，补一个 `route_environment()` 即可（§8 第 2 条），属于可选优化。

## 8. 待确认

1. codebuddy / qoder 的 B 档要不要也验一次：会花那个账号的额度（不免费），跑一次很便宜，
   但要不要花得由账号主人说。
2. 要不要给 codebuddy / qoder 补 `route_environment()`，让它们也能被指到额度池。
   不补不影响干活（基座那条路免费且通用），只是它们自己的循环用不上池子。
3. 要不要给 pi 也开一条（先得有人证明它那边工具真能调到）。