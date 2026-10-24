# cli-roster-enumerate

### 统一登记

任务编号：cli-roster-enumerate
执行 Harness：Codex
开始时间：2026-10-23 19:18 +08:00（仓库主人指示"探的不够啊，类似
opencode 明明还有更多免费模型的"后开工）
工作区：.worktrees/codex/
执行目录：.worktrees/codex/
依赖任务：pool-free-set-watch（免费档易变三件套之"发现面"扩展）
真相源引用：R10 §19.2（额度池语义）；AGENTS.md §1/§2/§4/§5/§5b；母卡
`docs/tasks/notes/pool-free-set-watch.md`（发现面/变更哨/活性三件套分工）
当前状态：done（实装＋7 条 scripted＋全量 pytest 绿＋真发现面 9 家实勘＋
哨上岗 1 条真告警）
commit：`0a7b637`（实装、测试、登记一轮提交）
test_id：`MVP-ADAPTER-003`

| 项 | 值 |
|---|---|
| 任务 | opencode 免费家族枚举：免费档 1→9 家，零冒充、零额度、零新增表 |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/pool/sources.py`（枚举声明＋扩展点）、`backend/tests/test_pool_cli_enumerate.py`（新增）、`docs/tasks/notes/cli-roster-enumerate.md`、`docs/claims/codex--cli-roster-enumerate.md`、`TASKS.md`、哨认领回填 |
| 禁止修改 | `docs/spec/0.3-R10.md`、`docs/traceability.md`（不新增 ID）、`backend/app/tasks/**`、`backend/app/scheduler/**`、`backend/app/alerts/**`、`backend/app/assistant/**` |

## 是什么

CLI 槽可声明一枚 `enumerate` 规则：CLI 自己能列清单（如
`opencode models`——列清单不烧额度），声明一个**命名规则**把清单里的
免费家族扩成同档位、同 argv 契约的槽。命名规则是人声明的，不是嗅探的：
"-free 后缀＝opencode 免费档"这是平台最初声明的那个槽就带着的事实。

## 为什么

发现面原来只能看见**手写声明的模型清单**：opencode 槽只声明 1 个
免费模型，而 opencode 花名册里 `opencode/*-free` 一族有 9 个。硬编码
9 条声明只会复制同一个过期问题——家族是易变的，目录驱动才是正路。

## 修法

- `DEFAULT_CLI_SLOTS` 的 opencode 条目加
  `"enumerate": {"args": ["models"], "pattern": r"^opencode/.+-free$"}`；
- `_run_listing`／`_enumerate_roster`（TTL 600s 缓存，按"解析出的
  binary＋清单 argv"共享，多个槽共用一次 spawn）；`ListingRunner`
  可注入，测试不 spawn 真 CLI；
- 扩展槽 = `dataclasses.replace(source, model=raw)`，pool_id 保持
  `{slot}/{尾巴}` 命名空间，与免费档模型链的调用名逐字对齐；
- 清单失败诚实跳过（只留声明槽），绝不编造家族。

## 完成标准（全 scripted 不烧真额度）

1. `tests/test_pool_cli_enumerate.py` 7 条：家族扩展＋档位同源；
   pool_id 命名空间不并槽；清单失败不扩；TTL 缓存（不重复 spawn、
   过期重列）；未声明规则的槽不动；散文行/他 provider 过滤；坏规则
   一步不扩；
2. 真发现面实勘：`discover_cli_slots()` 真跑出 9 家 `-free`
   （beam/deepseek-v4.1-flash/flint-40b/glm-5.3-flash/mimo-v2.6-flash/
   minimax-m3.1-flash/nemotron-3-ultra/nemotron-3.5-lightning/
   nemotron-3.5-super-vl），与 `opencode models` 花名册逐一对照；
3. 哨上岗证明：`--once` 检出免费档 5→13、入队 1 条真告警；
4. ruff（check+format）、`tools/docstate`（98/0）、`tools/discipline`
   （44/0＋1 根工作区占用告警）、pytest 定点＋全量绿。

### 验收结果

| 项 | 结果 |
|---|---|
| scripted 7 条 | ✅ `tests/test_pool_cli_enumerate.py` 全绿（pytest 定点） |
| ruff check＋format | ✅ 修复 2 处后全绿，重跑 pytest 确认未破 |
| tools/docstate | ✅ 98 项检查 0 失败 |
| tools/discipline | ✅ 44 项 0 失败＋1 条根工作区占用告警（不判红） |
| pytest 定点＋全量 | ✅ 定点绿；全量 `[100%]` 全点、退出码 0（留档 `D:\Temp\opencode\p3_full_pytest.txt`） |
| 真发现面实勘 | ✅ `discover_cli_slots()` 免费档 9 家与 `opencode models` 花名册逐一对照 |
| 哨上岗 | ✅ `--once`：5→13 家、alerts 入队恰 1 条、快照指纹 `e349cd68…`→`f844659e…` |
| commit | `0a7b637`（一轮提交）＋本轮登记 `见 git log` |

## 状态

实装＋全绿＋真凭据三项齐。哨下一轮的部署方式（--loop 挂哪个常驻
进程/cron）待仓库主人拍板，不在本卡范围。
