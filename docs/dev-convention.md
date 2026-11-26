# 开发约定（门禁 / 合并 / 提交 / 验证分层）

Makefile 与 `scripts.sh` 的注释引用本文件。口径以代码为准（`tools/mergeguard/cli.py`、
`tools/docstate/docstate.py`、`tools/discipline/discipline.py`），本文只做人读入口。

## 门禁：两个入口必须等价

- `make check` 与 `./scripts.sh check` 都是 **docstate + discipline + lint + test**，
  顺序有意为之（规范最快最可能红，先跑）。改一处必须改另一处——历史上 discipline
  曾在 Makefile 缺席，使 `make check` 比 `scripts.sh check` 弱一档。
- **提交前必须全绿**。门禁类命令必须可在无 GUI、无网络下运行（Windows 用 Git Bash/WSL）。
- docstate 的机械断言共 98 项（`tools/docstate/docstate.py`，口径以代码为准）；
  README 与本文件都要写明这个数，改断言数时两处一起改（`tools/discipline` 的计数检查会核对）。
- 后端测试**并行跑**：`pytest -n 4 --dist loadfile`（约 10 分钟；串行 25 分钟）。
  `--dist loadfile` 按文件分片，模块级状态不跨文件互相污染；怀疑顺序依赖时
  `AP_TEST_WORKERS=1` 退回串行。`make test`、`./scripts.sh test`、CI 的 pytest
  步骤是同一条命令，改一处就要改三处。
- 真栈冒烟**不进 check**：`make smoke`（= `office/scripts/ci-stack.mjs`）要真起后端 +
  vite + playwright，属在线层；CI 里串行跑在 pytest 之后。
- 测试封闭性、CI 隔离范围、stub 机制、跳过语义、级联约束等实证约束见
  `docs/notes/verification-constraints.md`——改对应代码前先读那份。

## 受控合并：共享 main 必须走入口

- `make merge BR=agent/local/x/T-1/a1`（可加 `TASK=T-1`），等价
  `python tools/mergeguard/cli.py merge <branch> [--task-id X]`。**不要裸跑 `git merge`**。
- 互斥锁在 **git common dir**，跨 worktree 生效；`make lock` / `cli.py status` 看持有者。
- 入口预检：**脏工作区直接拒绝**（裸 merge 会把别人未提交改动卷进 merge commit）；
  见别人的 `MERGE_HEAD` 一律 exit 7，绝不接管。
- 冲突**不自动 abort**：另一个人可能正在同一工作区解冲突；输出里写明不要 abort。
- **门禁红则不提交 merge commit**（`--no-commit`）：仓库停在「已合并、未提交」，
  并写 `merge-pending.json` 记录本次预期 MERGE_HEAD。恢复语义按**内容**判：
  与记录相符 → 本工具自己的待提交合并，续跑门禁并提交（门禁红不再需要人肉介入）；
  不符或无记录 → 别人的合并，exit 7。
- 工作区布局：根工作区持 main；各 Harness 在 `.worktrees/<name>` 持
  `agent/local/...` 分支。不在别人的 worktree 里动 main。

## 提交与推送

- 提交信息：中文 conventional subject（`fix(scope): …` / `feat(scope): …` /
  `chore(scope): …` / `test(ci): …` / `docs: …`），一个主题一个提交；body 写根因与证据口径。
- 推送前门禁绿。github.com 被出口代理拦时走 **Git Data API 路径**（逐对象 SHA 比对、
  只快进不强推），推后核 `git rev-list --left-right --count origin/main...HEAD` 为 `0 0`，
  并按实测 SHA 重写 remote-tracking loose ref（本环境 `git fetch`/`update-ref` 可能静默失效）。
- 公开仓库：推送前扫路径/凭据/元数据/内部文档（通用系统路径与示例域不算敏感）。

## 三条容易再犯的坑（2026-11-19 复核出来的）

- **登记是全局的，项目是局部的。** 平台注册表每个项目共用同一批窗口。凡是把
  登记表当"这个项目的人"的读面（左栏、成员选择器、右栏"可执行成员"、改派候选），
  都会让界面凭空多出十个人。口径只有三层：全局注册表 ⊃ 群账本 ⊃ 项目成员；
  越靠近"项目"的那层，越要用**平台口径或人自己勾的**那份，别退到全机名册。
  确实该是全局的只有三处：机器安装清单、转接收件人、本机在线窗口数。
- **读坏即空、随后写回覆盖。** 任何"读—改—写"的小文件/小表，解析失败必须
  **报错**，不能当成"没有"：写回去一次就把别人的记录抹平，而且不留痕。
  口径只有文件不在才算空；写走 tmp+replace 并留 .bak；读—改—写加进程内锁；
  浏览器侧没有锁可加时，至少先把原文挪到 .bak 并在界面上说"读不出来，没被覆盖"。
  （已同构发生过四处：user_keys 索引、池子声明文件、项目历史、巡检记录。）
- **append-only 的证据表不该用外键绑住投影表。** 外键会让"删投影"永远失败，
  而失败往往被吞成日志里的一行（回收站到期清扫就是这么一次都没成功过）。
  证据表留底、投影表可删，两者只能选一个方向 —— 要留证据就别加外键。

## 真栈走查（CI_KEEP）

- `CI_KEEP=1 node office/scripts/ci-stack.mjs` 起栈后常驻（不跑 verify、不清理），
  供浏览器逐功能走查与 API 探针；Ctrl+C 照常杀子进程删临时目录。
- **常驻栈会占 8848/4174**：跑 `make smoke` 前先停掉它。ci-stack 有端口预检，
  被占时直接报端口与换端口变量（`CI_BACKEND_PORT` / `CI_OFFICE_PORT`），不再起栈。
- 冒烟与后端全套件**不要并发跑**：曾出现并发下 vite dev 静默死亡（同代码单跑绿）。