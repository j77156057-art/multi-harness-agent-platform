# 接一个新 harness：成员窗口与额度来源

适用：在一台**新机器**上把一个新 CLI 接成正式成员（或让它给别的窗口供模型）。
真相源：R10 §7（Adapter 的动词与回执）、§8（预算与豁免）、§17（权限面与主体）
状态：现行（2026-10-25；声明式适配器与办公室接线已合入 main）

---

## 0. 先读这个：两种「加入」不是一回事

| | 当**成员窗口** | 当**额度来源** |
|---|---|---|
| 谁调这个 CLI | 成员机器上的 worker | **平台**自己 spawn |
| CLI 装在哪台机器 | **成员机器** | **平台机器** |
| 怎么声明 | `<AP_DATA_DIR>/adapters/cli.json` | `<AP_DATA_DIR>/pool/config.json` 的 `cli_slots` |
| 平台从哪看到 token | 成员自报（完成回执里带 usage） | 池子记账（带缓存命中） |
| 少了会怎样 | 这个窗口领不了活 | 别人借不到它的额度 |

两件事可以都做，也可以只做一件。**先走通「当成员」这条** —— 它不依赖额度池。

---

## 1. 五步

### 1.1 装 CLI，让它自己登录

CLI 装在**要干活的那台机器**上；账号由各 CLI 自己管（`gemini` / `claude` / `codex` … 各自的
登录命令），平台管不了这层。

办公室大厅的「连接工作窗口」会在**本机**扫描（`where.exe` / `which`），扫到的列在界面上。

### 1.2 声明这个 CLI 怎么被调起来（新 CLI 才需要）

平台自带 6 家适配器（`codex` / `claude-code` / `opencode` / `pi` / `qoder` / `codebuddy`），
它们不用写声明。**新的** CLI 写一份声明，放在**平台那台机器**的数据目录：

```
<AP_DATA_DIR>/adapters/cli.json
```

```json
{
  "adapters": [
    {
      "base": "gemini",
      "binary": "gemini",
      "args": ["-p", "--yolo", "--model", "{model}"],
      "pool_protocol": "openai",
      "model": "gemini-2.5-pro",
      "display_name": "Gemini CLI",
      "adapter": "gemini-print",
      "capabilities": ["coding"]
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `base` | 小写连字符（`^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`）。**不能与内置同名** —— 声明不许偷偷覆盖内置那 6 家 |
| `binary` | 可执行文件名。**由成员机器上的 worker 去找**（`which`），所以平台机器上不必装它 |
| `args` | argv 模板。`{workdir}` / `{model}` 会被替换；**提问自动追加在末尾** |
| `prompt_via_stdin` | true = 提问走 stdin（长提示不丢） |
| `pool_protocol` | `openai` / `anthropic` —— CLI 指着池子说话时用哪种协议，默认 `openai` |
| `review_args` | 只读审阅的 argv。**留空 = 这个 CLI 不能做审阅**（能改文件的审阅不算审阅） |
| `model` | `args` 里有 `{model}` 就必填 |
| `display_name` | 界面上显示的名字 |
| `adapter` | 注册表里的适配器名，默认 `<base>-cli` |
| `capabilities` | 默认 `["coding"]` |

声明**写坏了会被拒绝并说明原因**（不猜）：base 重名或重声明、缺 `binary`、args 里有
`{model}` 却没有 `model`、`pool_protocol` 不认识、args 不是字符串列表。

**声明是受信输入**：它点名了要执行的二进制与 argv → 由人写、平台不写。

### 1.3 在办公室登记基座、注册窗口

办公室大厅 →「连接工作窗口」：

1. **登记基座 Harness** → 选你刚声明的那个 → 登记（写平台注册表）。
2. **注册同一 Harness 的新窗口** → 窗口实例 ID 必须形如 `<base>-member-1` /
   `<base>-window-1`（规则 `^(base)-(member|window)-[A-Za-z0-9][A-Za-z0-9_-]{0,31}$`），
   显示名随意。

注册只是**复制基座已登记的能力与适配器** —— 不复制凭据，也不在浏览器里存 token。
登记是**人**的动作（`harness:register` 权限只给人）。

### 1.4 给窗口发平台令牌，起 worker

每个窗口要**自己**的令牌（平台按未吊销的 harness 令牌数算「这个窗口有没有凭据」）：

```bash
python -m app.auth issue --type harness --id <窗口实例 id> --label "说明"
```

明文只打印一次 → 写进成员机器上的一个 key 文件。然后在**成员机器**起 worker：

```bash
python -m app.adapter.formal_worker \
  --url http://<平台>:8849 \
  --token-file data/<窗口实例 id>.key \
  --harness-id <窗口实例 id> \
  --group <群组 id> \
  --canonical-repo <平台那份仓库> \
  --worktree-root <本机工作区根>
```

前置：平台必须 `AP_AUTH_MODE=enforce`，否则 worker 拒绝启动。
worker 周期自报心跳与能力（`harness:probe`），并从 `ready` 的任务里领活。

### 1.5 想要它给别的窗口供模型 → 加一条池子槽位

**CLI 要装在平台机器上**（池子是平台亲自 spawn 它的）。在平台数据目录：

```
<AP_DATA_DIR>/pool/config.json
```

```json
{
  "cli_slots": [
    { "name": "mycli", "model": "my-model", "binary": "mycli",
      "args": ["run", "--file"], "file_arg": "--file", "tier": "free" }
  ]
}
```

- 声明与内置的**合并**（同名同 model 覆盖内置那一条）；`"enabled": false` 可以关掉某个 CLI。
- **`tier` 必须人声明**，取值 `free` / `reserve` / `metered` —— 平台**不猜**免费还是收费；
  缺 tier 的槽位**不提供**（额度不明的东西不能看着像免费的）。
- 可选：`store`（CLI 自己的用量库，平台目前只认 `opencode`）、`roster` / `enumerate`
  （CLI 自己列模型）、`requires`。

---

## 2. 哪些文件在哪台机器上

| 文件 | 放在哪 | 谁读 | 谁写 |
|---|---|---|---|
| `adapters/cli.json` | **平台**的数据目录 | 平台的适配器（成员 worker 按它拼 argv） | 人手写 |
| `pool/config.json` | **平台**的数据目录 | 平台的额度池 | 人手写 |
| 平台令牌（key 文件） | **成员机器** | 成员 worker | `app.auth issue` 打印一次，人保存 |
| `canonical.git` | **两边同一份**（见坑 1） | 平台合并时 fetch 它 | 平台与 worker 共用 |

---

## 3. 两个容易踩的坑

**坑 1：仓库要共享。** 平台合并时是 `git fetch` **它自己那台机器上**的 `canonical_repo`。
新机器的 worker 若 `--canonical-repo` 指向另一份克隆，它交上来的分支平台看不见 →
任务会卡在审核。跨机器就共享同一份（网络卷 / 同一挂载）。

**坑 2：档位要人声明。** 池子的 `tier` 平台不猜：免费额度也可能改成收费。平台只提供
**声明过**的那一档；免费变收费时，平台会停下并要人重新点头。

---

## 4. 什么时候才需要写代码

声明说不清这个 CLI 的行为时 —— 三种情况：

1. 输出形状特殊（不是通用 stream-json）；
2. 自己的用量库要读（像 opencode 那样，平台得认得哪种 store）；
3. 要接自己的协议。

这时照 `backend/app/adapter/harnesses/<name>.py` 写一个类，继承 `CliHarness`
（内置那 6 家就是这么来的），并把它的 base 名登记进 `generic.py` 的 `BUILTIN_BASES`。

**先问一句：能不能用声明说清？** 能就别写类。

---

## 5. 出问题先看哪里

界面上的「接不了活的原因」给的是**原因码**：

| 原因码 | 含义 | 怎么办 |
|---|---|---|
| `not_registered` | 不在平台注册表 | 先在办公室登记基座 |
| `offline` | 注册状态不是 online | 起 worker（它会自报心跳） |
| `no_credential` | 这个窗口没有平台令牌 | `python -m app.auth issue --type harness --id <窗口 id>` |
| `self_reported_degraded` | 窗口自报降级 | 看窗口自己的 CLI（登录 / 额度 / 网络） |

---

## 6. 真相源索引

| 步骤 | 实现 |
|---|---|
| 声明格式与拒绝规则 | `backend/app/adapter/harnesses/generic.py` |
| 声明 → 注册表模板 | `backend/app/adapter/harnesses/generic.py`（`base_template`） |
| base 名单（内置 + 声明） | `backend/app/adapter/formal_worker.py`（`known_bases`） |
| 窗口实例 id 规则 | `backend/app/adapter/formal_worker.py`（`_INSTANCE_RE`） |
| 办公室登记基座 / 注册窗口 | `office/server/formal-dispatch-gateway.mjs` |
| 本机 CLI 扫描 | `office/server/harness-gateway.mjs` |
| 令牌签发 / 吊销 | `backend/app/auth/__main__.py` |
| 凭据计数 | `backend/app/adapter/registry.py`（`listing`） |
| 池子槽位、档位与合并 | `backend/app/pool/sources.py` |
| 平台合并时的 fetch | `backend/app/merge/worker.py` |
