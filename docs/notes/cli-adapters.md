# 市面 CLI 的适配器契约（十条）

写这份是为了回答一个问题：**这些 CLI 到底怎么在无人值守下跑起来**。每条都给了
证据等级 —— `measured` 是这台机器上真的跑过 `--help`（或跑过"不给凭据"的拒绝
路径，不花额度），`documented` 是厂商文档/包元数据里的原话。没有一条来自记忆。

代码在 `backend/app/adapter/harnesses/market_cli.py`，一份规格表 + 十个只指规格
的子类。改契约只改那一处。

## 一、三条硬条件

一个 CLI 要能接活，必须同时满足：

1. **不会停下来问人** —— 有自动批准（`--yolo` / `--force` / `--yes-always` /
   `--allow-all-tools` / `--auto-approve`）。没有它，进程会一直等一个不会出现的
   TTY 输入，代价是整个尝试超时报废。
2. **会自己结束** —— 一次性模式（`-p` / `-x` / `--message` / `run`），不是会话。
3. **不需要交互式配置** —— 这条最容易被忽略：Amp 缺 key 会转去设备登录（**实测
   挂起 >75 秒**）；Crush 没配 provider 会让人跑交互命令去配。

第 3 条在适配器里做成**启动前拒绝**（`requires` + `CliUnavailable`），而不是让它
挂着 —— 半小时后才知道"它其实在等登录"是最坏的失败形态。

## 二、十条契约

| CLI | 二进制 | 无交互 argv | 自动批准 | 输出 | 只读审阅 | 接池子 |
|---|---|---|---|---|---|---|
| Gemini CLI | `gemini` | `--approval-mode yolo --skip-trust -p <prompt>` | 同上 | `-o json` → `.response` | `--approval-mode plan` | ✗（要 HTTPS） |
| Qwen Code | `qwen` | `--approval-mode yolo -o json <prompt>` | 同上 | `-o json` 数组 → `[-1].result` | `--approval-mode plan` | ✓ `OPENAI_BASE_URL/API_KEY/MODEL` |
| Cursor CLI | `agent`（旧名 `cursor-agent`） | `-p --force --output-format json <prompt>` | `--force` | `.result` | `--mode=ask` | ✗ |
| Continue CLI | `cn` | `-p --auto --format json <prompt>` | `--auto` | 模型自写 JSON（无固定字段）→ 按纯文本 | `--readonly` | ✗（只能改 config.yaml） |
| Aider | `aider` | `--message <prompt> --yes-always --no-stream` | `--yes-always` | 纯文本 | `--dry-run` | ✓ `OPENAI_API_BASE/API_KEY` |
| GitHub Copilot CLI | `copilot` | `-p <prompt> --allow-all-tools --output-format json` | `--allow-all-tools` | JSONL，字段**未核实** | ✗ | ✗（BYOK 未端到端验证） |
| Mistral Vibe | `vibe` | `--prompt <prompt> --auto-approve --output json` | `--auto-approve` | JSON，字段**未核实** | ✗（`--agent ask` 要审批＝会挂） | ✗ |
| Amp | `amp` | `-x <prompt> --stream-json` | **无 flag**，只有设置项 | `{"type":"result"}.result` | ✗ | ✗（`AMP_URL` 不是模型端点） |
| Crush | `crush` | `run --quiet <prompt>` | **无 flag**，只能写配置 | 纯文本 | ✗ | ✗ |
| iFlow CLI | `iflow` | `-p <prompt> -y` | `-y` | 纯文本 | ✗（`--plan` 未声明是写保护） | ✓ `baseUrl/apiKey/modelName` |

## 三、几处必须知道的坑

**免费档接不了任何编码 CLI（这是设计，不是 bug）**。实测走过一遍完整的路：把
Qwen 窗口的模型选成池子里的免费档（`qoder/Qwen3.8-Flash`，tier=free），worker 领活、
建工作树、按我们生成的 argv 把 qwen 起来 —— qwen 的 init 事件里看得到
`model: qoder/Qwen3.8-Flash`、`permission_mode: yolo`、cwd 是工作树，说明
argv 与池子环境变量都对上了。然后它报 `UND_ERR_SOCKET: other side closed`。

原因是额度池里那些免费档都是 **`protocol: cli` 槽位**（背后是另一个 CLI），而
`_cli_chat` 明确**拒绝**带工具定义的请求：这一跳只能回文字、回不了 `tool_calls`，
它的提示词里还写着「不许用工具」。任何编码 CLI 都会发工具定义，所以必然撞上。

结论：**免费档只能回答"问一句话"，不能驱动编码 agent**。想让新 CLI 真干活，得让它
用自己的账号（Gemini 用 Google 账号、Qwen 用 Qwen OAuth），或者给它一个真 provider
的模型。这一条已同时记在 `app/pool/service.py::refuse_tools_for_cli` 的注释里。

（原先这条拒绝写在生成器里，调用方看到的是"连接被切断"而不是这句话；
2026-10-30 已改成**开流之前**就返回 502 + `relay_cli_cannot_use_tools`。）

**Qwen 的池子变量名有据、端到端未通**：`OPENAI_BASE_URL` / `OPENAI_API_KEY` /
`OPENAI_MODEL` 是它自己文档里的，但按上面那条，接到免费档上不会成功。别在别处写成
"已验证"。

**Gemini**：无 TTY 它会自动进 headless，但仍然要 `--skip-trust`，否则卡在"信任
这个目录吗"上；`GOOGLE_GEMINI_BASE_URL` 只收 HTTPS（localhost 除外），而平台池子
是 loopback 上的 http，所以它不接池子。

**Qwen**：`-p/--prompt` 在 0.26.4 已被标记弃用，提示词应该当**位置参数**传；两种
写法都能跑，但用它文档推荐的那种。

**Cursor**：不加 `--force` 时它只**提议**改动、不落盘 —— 一次"成功退出"的尝试会
交出空活。这是这套里最容易看错的一条。

**Continue**：`--format json` 不是包装层。它往提示词里塞一句"你的最终回复必须是
合法 JSON"，输出是模型自己写的东西，没有 schema。所以适配器把它当纯文本处理。

**Aider**：位置参数是**文件路径**不是提示词；问题只能走 `--message`。另外
`aider-chat 0.86.2` 要求 Python < 3.13，本机是 3.13，直接 `pip install` 会静默装成
0.16.0（flag 全不一样）。

**Copilot / Vibe**：最终消息的 JSON 字段名在可达文档里查不到。适配器的解析器先按
已知键（`result` / `response` / `text` / `content` / `messages[].content[]`）找，认
不出来就**留原文**，不返回空 —— 空会让上游把"它说了话"读成"它什么都没说"。

**Amp**：没有自动批准 flag（只有 `amp.dangerouslyAllowAll` 设置项或
`amp permissions add`），而且没 key 会卡在设备登录。适配器缺 `AMP_API_KEY` 时直接
拒绝启动。

**Crush**：`-y/--yolo` 只属于交互式根命令，`crush run -y` 会报
`Unknown shorthand flag: 'y'`（实测）。批准必须写进配置的
`permissions.allowed_tools`；没配 provider 时它立刻退出并让人去跑交互式命令。

**iFlow**：缺认证时它会**立刻退出并列出**可用的环境变量名
（`apiKey` / `baseUrl` / `modelName`）—— 这一点比 Amp 好，装完就能看出缺什么。

## 四、没有做成适配器的判断

- **只用「能改端点」的三条接额度池**（Qwen / Aider / iFlow）。其余七条各自用自己
  的账号，平台不假装能改 —— 假装的结果是"明明设了池子模型，账单还是走自己的卡"。
- **只给有硬只读保证的五条做审阅**。Copilot 的 `--mode plan` 只被描述成"从 plan
  模式开始"，iFlow 的 `--plan` 只写了"规划不执行"，两者都不是写保护。审阅者能写
  文件就不算审阅，所以这两条宁可拒绝。
- **不替人做登录**。十条里有账号体系的，一律由人自己登录；平台只负责在缺凭据时
  说清楚缺什么。

## 五、复现这些结论

```powershell
# 已装的那条，直接问它
gemini --help | Select-String 'approval-mode|output-format|skip-trust'

# 没装的，装到临时前缀（不动全局环境）
npm install --prefix D:\Temp\probe-qwen @qwen-code/qwen-code
D:\Temp\probe-qwen\node_modules\.bin\qwen.cmd --help

# Python 的：看包内源码比看 README 准
pip download --no-deps -d D:\Temp\whl aider-chat    # 注意解释器版本，见上
```

`D:\Temp\probe-*` 是临时目录，随时可删；没有做过任何全局安装。
