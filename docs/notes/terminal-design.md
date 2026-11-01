# 内置终端（Agent Office）：设计稿

状态：设计已定，按 M1 实现中。作者：助理 agent。日期：2026-11-01。

## 1. 为什么要它

新机器上从「装好 CLI」到「能派活」之间卡着两步，平台现在都推给人去开终端：

1. **装**：`GET /harnesses/installers` 只把命令交给人复制（README 明写「平台不下载、不执行安装脚本」）；
2. **登**：登录几乎全是交互式的 —— 上架清单里 `login_note` 写得很直白：
   「运行 codex 并选择 Sign in with ChatGPT」「运行 claude 按浏览器提示登录」
   「首次运行会提示用 Google 账号登录」。非交互的命令面板**只能装、不能登**。

`start-fresh.ps1` 解决了「窗口自动登记 + 自动起 worker」，但没有解决这两步。
终端就是补这一段：人在界面里点开一个真终端，装完、登完，那个窗口立刻能干活。

## 2. 本版做什么 / 不做什么

**做（M1）**

- 一个网页面板，里面是**本机 PowerShell 的真 PTY**（`node-pty` + `@xterm/xterm`）；
- 面板顶部三个按钮，全部由平台的 CLI 清单生成，不手写命令：
  - **安装**：跑清单里该 CLI 在当前系统上的 `install` 命令；
  - **重探**：跑它的 `version_args`（与 `installers.probe()` 同一条命令）；
  - **登录**：跑**该 CLI 自己**（`codex` / `claude` / `opencode` …），跟着它自己的提示走。
    清单里没有登录子命令，所以**不猜**子命令，只运行 CLI 本身；
- 允许人在面板里自由键入（登录时要在 CLI 的界面里选账号、粘贴 key，按钮替代不了）；
- 会话留痕：输入输出脱敏后落到 `data/terminals/<id>.log`，界面明写这件事；
- 关面板/关页面即结束会话；空闲超时兜底；同时最多 2 个会话。

**不做**

- **不给 harness / 管理员窗口 / 平台助手开**（人点才算）。让模型拿到本机 shell 与
  「谁能写入必须可检查」直接冲突，这条是本设计的硬边界；
- 不代跑清单里的命令：**没有任何自动安装/自动登录路径**，一切以人点按钮或人键入发起；
- 不做多标签/分屏/会话恢复；关掉就没了（日志还在）；
- 不动 `AP_*` 环境变量、不把令牌注入终端环境。

## 3. 环境事实（实测，不是推断）

| 事实 | 证据 |
|---|---|
| `node-pty` 在 Node v24.15.0 + Windows 上装得上、能起 shell | 探针拿到 `pty-ok` / `ConsoleHost` |
| `term.kill()` 会触发 node-pty 的 helper 子进程崩 `AttachConsole failed` | 探针输出；父进程存活但 teardown 不可靠 |
| `taskkill /PID <pid> /T /F` 能干净杀掉 PTY 里的 shell | 杀后 pid 确实不存在 |
| `useConpty: false`（winpty）也能起，且无该报错 | 探针 variant=winpty |
| 前端现在没有任何终端依赖、也没有 websocket 通道 | `office/package.json` 只有 vite + playwright |
| 平台**已经**在本机跑命令：`--version` 探测、起 worker、`where` | `installers.probe()` / `window-provisioner` / `harness-gateway` |

**结论**：本版在 Windows 上用 **ConPTY**，teardown **一律走 `taskkill`**，
不调用 `term.kill()`。

## 4. 分层与职责

```
浏览器  ── ws /api/formal/terminal?ticket=…  ──┐
        └─ POST /api/formal/terminal/ticket ──┤   （复用现有同源写校验）
                                              ▼
office/server/terminal-session.mjs   会话管理：票据、PTY、空闲回收、脱敏日志
office/server/formal-dispatch-gateway.mjs  路由与三部门禁（loopback / Origin / X-Office-Formal）
office/src/terminal-panel.js         xterm 面板 + 三类按钮（读 /api/formal/installers）
```

票据的意义：浏览器不能给 websocket 加自定义头，所以先用一次正常的同源 POST 换一张
**一次性票据**（60 秒有效），ws 只凭票据建连 —— 写操作的同源校验仍在同一条路上。

## 5. 接口契约

**POST /api/formal/terminal/ticket**（需 `X-Office-Formal: 1` 与同源 Origin）

```json
{ "ticket": "t-<32 hex>", "expires_in": 60 }
```

**WS /api/formal/terminal?ticket=…&cols=100&rows=30**

客户端 → 服务端：
```json
{"type":"input","data":"ls\r"}
{"type":"resize","cols":120,"rows":40}
{"type":"close"}
```
服务端 → 客户端：
```json
{"type":"ready","session_id":"tm-<8hex>","shell":"powershell.exe","log":"data/terminals/tm-xxx.log"}
{"type":"data","data":"..."}
{"type":"exit","code":0,"reason":"closed|idle|cap|error","message":"..."}
```

失败一律回 `{"type":"exit","reason":"...","message":"..."}` 后关连接，
不同原因用不同 `reason`（见 §7）。

## 6. 会话模型

| 项 | 值 |
|---|---|
| 并发上限 | 2（超出回 `reason:"cap"`） |
| 空闲回收 | 15 分钟无输入即关（`reason:"idle"`） |
| 工作目录 | 仓库根 |
| 环境变量 | 继承服务端，但**剔除** `AP_*`、`OFFICE_FORMAL_*` |
| 日志 | `data/terminals/<session>.log`，逐段追加；`apt_[a-f0-9]{64}`、`sk-…`、PEM 头替换为 `‹已隐藏›` |

## 7. 失败分类与处置

| 情况 | 表现 | 处置 |
|---|---|---|
| `node-pty` 没装上/平台不支持 | 面板显示「这台机器上终端不可用」，附原因 | 降级到「复制命令」老路，不做半吊子模拟 |
| 票据过期/重用/伪造 | ws 握手 403 | 面板提示「重开终端」并自动重新取票一次 |
| 非 loopback 或跨站来源 | 403 `loopback_only` / `invalid_origin` | 与现有网关一致，不回细节 |
| 并发超限 | `reason:"cap"` | 面板提示已有会话，先关掉 |
| 会话被系统杀掉 | `reason:"exit"` | 面板显示退出码，允许重开 |
| 按钮命令为空（该 OS 厂商不发布） | 按钮禁用并写明 | 不猜替代命令 |

## 8. 测试与验收

**纯规则测试**（`office/scripts/verify-terminal-session.mjs`，不需要浏览器）

1. 票据一次性：第二次用同一张票 → 拒绝；
2. 票据过期（假时钟推 61 秒）→ 拒绝；
3. 脱敏：`apt_…` / `sk-…` 在日志里变成 `‹已隐藏›`；
4. 上限：第 3 个会话 → `cap`；
5. 空闲：假时钟推过 15 分钟 → 会话被关且 `reason:"idle"`。

**真跑测试**（同一个脚本，`pty` 可用时）

6. 真起一个 shell，写入 `echo pty-ok`，断言输出含 `pty-ok`；
7. 关闭走 `taskkill`：关闭后 pid 不存在（Windows）。

**浏览器验收**（真后端 + 真界面）

8. 点「终端」→ 面板出现，敲 `echo hi` 有回显；
9. 点「重探」→ 该 CLI 的版本行出现在终端里；
10. 关面板 → 会话结束（`data/terminals/*.log` 有内容、进程没了）；
11. 跨站 Origin 的 POST 被 403（沿用现有网关断言）。

## 9. 风险登记

| 风险 | 影响 | 缓解 |
|---|---|---|
| node-pty 在别的机器上编译失败 | 终端不可用 | 面板降级 + 明确写原因；不阻塞其它功能 |
| `term.kill()` 崩溃 | 可能带走 office 进程 | 已实测并规避：teardown 只走 `taskkill`，`close` 全程 try/catch |
| 终端成了「绕过平台写库」的后门 | 破坏「谁能写入可检查」 | 终端只跑 shell，不碰数据库；写只能在 git 里发生，仍走任务流程 |
| 人在终端里粘贴了真 key | 泄漏到日志 | 日志脱敏 + 界面写明「会留痕」 |
| 面板被 harness 调用 | 模型拿到本机 shell | 路由只认浏览器票据；不给 harness 令牌开（见 §2） |

## 10. 待确认（本期按默认走，可随时改）

- 空闲超时 15 分钟、并发 2 —— 都做成常量，改一行就变；
- 日志落盘位置 `data/terminals/`（`data/` 已在 .gitignore 里，不会误提交）；
- 「登录」按钮运行 CLI 本身而不是猜子命令 —— 清单里只有 `login_note`，没有登录命令原文。
