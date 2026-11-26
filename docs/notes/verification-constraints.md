# 验证与 CI 的实证约束（2026-11-17 一轮连红修复后固化）

本文件只收**踩过并实证过**的约束：每条写清触发条件与根因，改对应代码前先读。
证据口径：本地全套件 1720/1720、真栈冒烟 62/62（完全隔离条件）、WSL Ubuntu 子集、
CI 运行 43964742020（双 OS 首次全链路绿）。

## 1. 测试封闭性：结果不得取决于宿主机

- **并行跑**：后端全套件用 `pytest -n 4 --dist loadfile`（串行 25m42s → 并行 9m58s，
  2.6×）。`--dist loadfile` 按**文件**分片：模块级状态不跨文件互相污染（默认
  `load` 会把同一文件里的用例打散，容易暴露顺序依赖）。怀疑顺序依赖时
  `AP_TEST_WORKERS=1` 退回串行。Makefile / `scripts.sh test` / CI gate.yml 三处
  必须同一条命令。

- **代理环境变量**：`tests/conftest.py` 会话级 autouse fixture 清空
  `HTTP(S)_PROXY/ALL_PROXY/NO_PROXY` 及小写变体。根因：Qoder 等宿主向子进程注入的
  `NO_PROXY` 含 `[::1]`，httpx 0.28.1 构造 Client 即抛 `InvalidURL`（裸 `::1` 正常）。
  生产侧对应约束见 §5。
- **CLI 配置依赖**：凡读 `Path.home()` 下 CLI 配置的测试必须注入 tmp home 并 seed
  配置（范例 `tests/test_pool_cli_enumerate.py`）。CI runner 没装任何 CLI，
  不注入就是「本地绿、CI 红」。
- **平台注入测试**：适配器接受 `platform=` 注入时，解析注入的 Windows 路径必须用
  `ntpath`（`Path()` 在 POSIX 宿主上不认反斜杠，整串会变成文件名）。
  真跑 `cmd.exe` 的用例加 `skipif(os.name != "nt")`，argv/bundle 断言保留全平台。
- **行尾差异**：静态扫描类门禁不得用「±N 字符窗口」取上下文——LF checkout 下窗口
  覆盖更多行，会跨调用借词拼出假违规（review-gate 曾因此在 ubuntu 红）。
  取上下文按语法单元（如 fetch 调用括号配平切片）。

## 2. ci-stack 真栈隔离范围（缺一项就是假等价）

`office/scripts/ci-stack.mjs` 起真后端 + vite + 冒烟。隔离必须覆盖：

| 变量 | 指向 | 不隔离的后果 |
|---|---|---|
| `PATH` 头部 | 临时 bin（CLI stub） | 开发机真 CLI 被枚举，CI 无 CLI 时额度槽不成立 |
| `HOME` / `USERPROFILE` | 临时 modelHome | 上游发现读 `Path.home()` 下 opencode/codex 配置，真 home 掩盖 CI 缺口 |
| `XDG_DATA_HOME` | 临时 xdg | opencode store 路径落回真实 profile |
| `PLAYWRIGHT_BROWSERS_PATH` | 安装时真实缓存 | POSIX 默认缓存随 HOME 走，隔离后浏览器落空 |

历史教训：只隔离 `AP_MODEL_HOME` 时本地冒烟曾「62/62」而 CI 红——真 home 的
opencode 配置提供了 CI 不存在的「opencode 组」。等价性声明必须以全隔离轮为准。

## 3. CLI stub 与 seed（内置额度槽的成立条件）

内置槽（`DEFAULT_CLI_SLOTS`）要求**二进制可执行 + store 可读**，二者缺一槽不被提供：

- ci-stack 写 `opencode` / `codebuddy` / `qoderclicn` stub（win `.cmd` / posix sh+exec 位），
  名册输出须含 `opencode/beam-free`——`verify-office-model-retier.mjs` 的默认期望
  （`OFFICE_RETIER_MODEL` 可覆盖）。
- **8402 的模型 stub 必须说助手循环的协议**，不是人话：每轮回一个 JSON 对象
  （取证用 `tool` / 作答用 `final{text,cites}`），引证要解析得到（`[cN]` 指上下文行、
  `[sN]` 指带观察的步），终局还要过自检轮（`{"verdict":"pass"}`）。回普通句子会被判
  「没有可引证的结论」，助手只能回**诚实 IOU**（"没能查出可回查的结论"）——那验的是
  「没编答案」，不是「答对了」。阶段靠 prompt 里的「终局自检」区分
  （`app/assistant/loop.py::_transcript`）。collab-talk 在 `TALK_EXPECT_STUB=1` 时
  断言回话**含 stub 标记且不是 IOU**（内容正确，不只是"有回话"）。
- **档位声明要在起后端之前写**：`data/pool/config.json` 的 `upstream_tiers`。
  池子发现上游后有 TTL 缓存，seed 在后端起来之后再写，界面仍显示代码默认档
  （opencode:1 会显示「按量计费」而不是免费，助手的免费档实况目录也就少了它）。
  ci-stack 在起后端前写这份文件；seed 里的同名写入只是幂等兜底。
- **撤档测试要恢复现场**：`verify-office-upstream-tier.mjs` 的「撤档」是真删声明
  （那是它的契约），所以脚本必须在跑完把**测前**那条声明写回去 —— 否则它会把
  操作员在真平台上写的覆盖悄悄删掉（CI 里表现为 opencode:1 从免费退回按量计费）。
- `app/tools/seed_ci.py` 播种：空但可读的 opencode store（schema 镜像 CLI 自有表
  `session_message`）+ opencode provider 配置（baseURL 指链端口，探测失败优雅降级）。
- 改 stub 名册或 seed schema 时，同步核对 `app/pool/sources.py` 的
  enumerate/roster 正则与 `app/pool/cli_usage.py` 的查询列。

## 4. 冒烟脚本约定

- **跳过语义**：退出码 2 + 输出 `<脚本>: 跳过 —— <原因>`；只有确实依赖外部不可得物
  （真实模型、本树不存在的产物）才允许跳过，跳过数进 summary 与 step summary。
  已退休：`verify-anchors.mjs`（2026-11-17 删除）——它验的 Godot 场景预览产物
  （docs/prototypes/office-anim 导出静态页）不属于本树，永久跳过等于门禁噪音；
  办公室界面/组件由其余 verify-* 覆盖。collab-talk 已由 ci-stack 的助手 worker
  （office-assistant + codex 两进程）+ 8402 OpenAI 兼容 stub + seed 免费档声明变为
  可执行：回话链路真跑（模型真被调用）。stub 答复不满足助手循环的引证要求时，
  助手回**诚实 IOU**（"不给编的答案"），脚本记回话原文并计通过——验的是送信与
  回话链路，不是模型答案质量；IOU 原文留在输出里，不算假绿。
- **并发资源竞争**：冒烟与后端全套件并发跑时曾出现 vite dev 静默死亡（smoke9，
  同代码单跑 smoke10 复现绿）；CI 步骤串行不受影响，本地避免两者并发。
- **端口预检**：ci-stack 起栈前探测 8848/4174，被占直接报端口与换端口变量
  （`CI_BACKEND_PORT`/`CI_OFFICE_PORT`）并退出。根因：后端 bind 失败时 waitFor 会摸到
  占端口者（如 CI_KEEP 常驻走查栈）的响应，seed 再报 no database 这类二手症状。
- **单脚本熔断**：`verify.mjs` 对每个子进程 5 分钟无退出即 SIGKILL 记失败并点名。
  根因：一个挂死脚本曾把整个 job 拖到 45 分钟超时（windows 被砍、ubuntu 绿那轮）。
- **向导自动打开竞态**：创建项目走 `openAfterCreate` 会自动开开工向导；脚本点
  `#aoStartBtn` 前须容忍 dialog 已开（已开不再点；点击被拦截落 catch 等向导出现）。
  懒加载使自动打开不再等 mount，竞态窗口比静态 import 时代更宽。

## 5. 后端平台约束

- **回收只读树**：`app/git/worktree.py::_force_rmtree` 文件 chmod 0o600、目录 0o700。
  只清写位（`S_IWRITE`）在 POSIX 会剥掉目录读/执行位，rmtree 进不去且
  `ignore_errors=True` 静默留残骸；Windows 无此症状，故本地绿不能证明 POSIX 正确。
  overlay 复用同一函数，不得再抄副本。回归守卫：`test_force_rmtree_removes_a_readonly_tree`。
- **Windows 长路径**：临时 `git init` 的仓库（seed 临时库、overlay）必须写 per-repo
  `core.longpaths=true`；fetch 派生的 upload-pack 子进程不吃环境层 `GIT_CONFIG_*`。
  `run_git` 的环境层注入只是兜底。
- **启动中文输出**：`app/boot.py` 打印前 reconfigure stdout/stderr 为 utf-8
  （errors=replace）。CI Windows 管道编码 cp1252、stdout 默认 strict，中文直接
  `UnicodeEncodeError` 拖垮 lifespan；测试子进程须显式 `encoding="utf-8"` 解码。
- **出站 HTTP 统一入口**：app 内一律 `app.net.http_client/get/post`，不得裸用
  `httpx.Client/get/post`。宿主 `NO_PROXY` 的 `[::1]` 使 httpx 0.28.1 构造即崩，
  入口在 `InvalidURL` 时退回直连。新增便捷调用点也要走入口（漏过 5 处，真栈
  pool/status 500 的根因）。
- **opencode 用量口径**：input 一律含 `cache.read`（`app/pool/cli_usage.py` 与
  `app/usage/cli_reported.py` 同口径）。opencode 自有 store 的 `input` 不含缓存读，
  漏加就是两本账不一致。改任一侧须同步另一侧与断言（5600+7=5607 范例）。

## 6. 前端级联与懒加载（详见 office/README.md 同名章节）

flat-office.css 必须先于 app-shell.css 加载；懒加载 chunk 的 CSS 注入晚于主 CSS，
故 flat-office.css 静态 import 回 main.js 原槽位，JS 才懒加载。双 CSS 文件合并
两个方向都实证翻序，已决定不合并。

## 7. 派单前的工作树基线（2026-11-19 实证）

- **worker 的工作树基线来自 `canonical.git` 的 `integration` 分支**，不是当前工作目录。
  2026-11-19 实测：`canonical.git` 的 integration 指向平台自己建的**种子仓库**
  （README + apps/ + portfolio/ + verify/），里面**没有平台源码**（backend/ office/ tools/ 都不在）。
  成员在这种树上读不到 `app.*`，交出来的是**凭空捏造的接口**——而且审阅与合并都看不出破绽
  （文件确实写了、确实在契约路径内、提交确实存在）。
- **开工前先核对基线**：`git -C canonical.git ls-tree --name-only refs/heads/integration`
  里必须有任务要改的那棵子树（要改 `backend/**` 就得先看到 `backend/`）。看不到就别派单：
  先把 integration 指向正确的仓库
  （`git -C canonical.git fetch --force <repo> refs/heads/main:refs/heads/integration`），
  旧基线留一个 tag（历史是过程证据，不丢）。
- **症状与取证**：平台拒收时只说「no committed change against base」（成员没写文件）；
  真正的线索是**工作树里根本没有目标目录**。`data/archives/<task>.a1.patch` 是整棵工作树的快照，
  看它列了哪些文件就知道基线里有什么。
- **换基线后，在跑的条任务要撤掉重派**：任务的 base_commit 在领取时就锁定，
  换 integration 不会改写已在跑的 attempt；不撤就等于让成员继续在旧树上交付。
- **产品工作区的契约跟着基线走**：种子仓库自带 `verify-profiles.json`
  （integration_profile = artifact-check，要求交付物里有自包含 HTML 页面）。基线换成平台源码后，
  仓库侧 profile 变成平台自己的 `docs-check`（`backend/app/verify/docs_profile.py`：README.md 存在、
  所有 .md 合法 UTF-8 且非空）——合并闸门按**目标树**解析 profile，契约因此随基线变。

## 8. 平台交付 ≠ 仓库门禁（2026-11-19 实测）

- **平台的合并闸门只跑仓库声明的那一个 profile**：目标树里 `verify-profiles.json` 声明
  `integration_profile`，合并前就在**合并后的树**里跑它一次。本仓当前是 `docs-check`
  （README.md 存在、.md 合法 UTF-8 且非空），**不跑 ruff、不跑 pytest**。
  于是「平台合进来了」不等于「仓库门禁会绿」：2026-11-19 实测，平台交付的
  `backend/tests/test_doctor.py` 带着一处 `I001`（导入顺序）与 ruff format 差异，
  平台合并闸门全绿、`make check` 却红。**交付物合进来之后，仓库自己的门禁必须再跑一遍**
  （docstate + discipline + ruff check + ruff format --check + pytest），红了自己修（格式化类改动是机械的）。
- **门禁覆盖范围以 Makefile 为准**：`make lint` 只在 `backend/` 里跑 ruff；`tools/` 不在格式约定内
  （`tools/discipline/discipline.py`、`tools/mergeguard/cli.py` 同样过不了 `ruff format --check`）。
  别把 `tools/` 也拉进格式化，噪声大于收益。
- **交付物自身的验收要真跑**：交付的体检 CLI 要在真机真数据上跑一遍（人读报告、`--json`、
  读不到数据时的退出码），并确认**数据目录零写入**（含 `-wal`/`-shm` 边车文件）。
  单元测试全绿不等于这个产品能用。
- **一个 worker 一次只服务一个群**（`window-provisioner` 的纪律）：给新项目开成员窗口前，
  要先把旧群上同名窗口的 worker 停掉，否则起不来（409，理由写得很清楚）。
- **起 worker 的 argv 别让 shell 拆**：Windows 上 `Start-Process -ArgumentList @('a','b c')`
  会把带空格的路径拆成两段（进程秒退，报 `unrecognized arguments`）。用 Python 的
  `subprocess.Popen([...])` 传 argv 列表，或走办公室的 `POST /api/formal/harnesses/provision`
  （服务端自己拼 argv）。