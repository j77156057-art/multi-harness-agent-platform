# pool-generic-adapter

### 统一任务卡

任务编号：pool-generic-adapter
负责 Harness：Codex
开始时间：2026-10-23 10:43 +08:00
工作区：.worktrees/codex/
依赖任务：FORMAL-MULTI-HARNESS-01（正式 harness 接替/领任务主链）
真相源引用：R10 §7（Adapter 的十六动词与收据）、§19.2（额度池语义沿 `docs/claims/codex--FORMAL-MULTI-HARNESS-01.md` 与 `pool-*` 任务卡落档）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：done（实现与定点验收完成；实现经 `2c02969` 合入 main，2026-10-26 由 Mavis 逐条核实并收尾）
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | FORMAL-MULTI-HARNESS-01 |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/pool/**`, `backend/app/adapter/**`, `backend/app/admin/service.py`, `backend/tests/**`, `TASKS.md`, `docs/claims/codex--pool-generic-adapter.md`, `docs/tasks/notes/pool-generic-adapter.md` |
| 禁止修改 | `docs/spec/0.3-R10.md`（真相源只读）、`backend/migrations/**`、生产项目素材与外部 Harness 工作区 |

## 做什么

额度池的"消费侧"通用化：一个新 CLI 不写 Python 适配器类也能当正式成员。

## 为什么

池子的"供给侧"已修到任意 provider，但 `formal_worker` 的适配器表仍写死五家，
新 harness 身份（`pi-member-1` 等）进不了执行槽，池子借不到也无从谈起。

## 修法

- 池子四小坑：自环防护按上游归属约定通用化；内置槽显式 `tier`；声明槽缺
  `tier`/含未知 `store` 拒绝；声明与内置合并且可整体关闭；池子配置迁移到
  `<AP_DATA_DIR>/pool/config.json` 优先；
- 通用适配器：`<AP_DATA_DIR>/adapters/cli.json` 声明式注册 +
  `GenericCliAdapter(CliHarness)`；argv 模板支持 `{workdir}`/`{model}`、
  stdin、双协议 route_environment；
- 接线：`base_harness_id` / `_INSTANCE_RE` / `build_adapter` / `known_bases`；
  管理员细化与身份校验同表；内置五家永不覆盖。

## 完成标准

见认领文件。全部为机械可判（pytest 模块清单 + ruff + docstate +
discipline + mock 二进制冒烟）。

## 状态

实现与定点测试已就位。分支 `agent/local/codex/OFFICE-UI-01/a1`，
commit：`c4c16a1`（feat(pool-generic-adapter)，含池子四小坑、通用适配器、
接线与登记；实现与定点测试同一次提交）。

### 合并状态（2026-10-26 更正）

原写的「该 commit 尚未合入 `main`」**已经过时**：`c4c16a1` 那条单独提交确实不在
任何分支上，但它的内容后来经 **`2c02969`**（`Merge branch
'agent/local/codex/OFFICE-UI-01/a1'`）**合入了 `main`** —— 通用适配器
（`backend/app/adapter/harnesses/generic.py`）、池子侧四小坑与接线都在 main 上。

2026-10-26 由 Mavis（OFFICE-UI-37）**逐条核实**（不是「代码在就算验收」）：

| 它自己写的修法 | 核实结果 |
|---|---|
| 内置槽显式 `tier`（缺 tier 不当免费） | ✅ `CLI_TIERS=('free','reserve','metered')`，`DEFAULT_CLI_SLOTS` 每条都带 tier |
| 声明槽含未知 `store` 拒绝提供 | ✅ `KNOWN_CLI_STORES=('opencode',)` |
| 声明与内置合并、`enabled:false` 可整体关闭 | ✅ `_merge_slots()` 合并；给 `opencode` 声明 `enabled:false` 后其槽位剩 0 条 |
| 池子配置优先 `<AP_DATA_DIR>/pool/config.json` | ✅ `pool_config_path()` = `data/pool/config.json` |
| 自环防护按上游归属 | ✅ `routing.py` 的 `_base_of()` + `_owned_by_harness()` |
| 通用适配器 `GenericCliAdapter`（`adapters/cli.json` 声明式注册） | ✅ `generic.py` 有 `GenericCliAdapter` / `load_declarations` / `base_template` / `base_gaps` |
| 接线 `base_harness_id` / `_INSTANCE_RE` / `build_adapter` / `known_bases` | ✅ `formal_worker.py` 四个都在 |
| 登记（任务行 / 卡片 / 认领） | ✅ TASKS.md 三处都在 |

**真跑**（8849 真平台 + 真实声明文件）：
`GET /harnesses/bases` 给出声明基座 `gemini`（`gemini-print`）与两条缺口
（「没有 review_args：这个 CLI 不能做只读审阅」「池子侧没有这个 CLI 的槽位：它现在
不能给别的窗口供模型」），内置基座 `claude-code, codebuddy, codex, opencode, pi,
qoder` 都在，`declaration_error` 为 null；`GET /pool/status` 给出 21 个上游。

→ 这张卡**可以结**。实现没有被改动（代码在 main 上且全量 pytest 绿），这一张只做
核实与收尾。

### 验证结果

（2026-10-26 由 Mavis 补记，OFFICE-UI-37）

| 门禁 | 结果 |
|---|---|
| 逐条核实（对照本卡「修法」逐条） | **9/9 通过**：内置槽显式 tier（`CLI_TIERS=('free','reserve','metered')`，`DEFAULT_CLI_SLOTS` 每条都带 tier）、未知 store 拒绝提供（`KNOWN_CLI_STORES=('opencode',)`）、声明与内置合并（`_merge_slots()`）且 `enabled:false` 可整体关闭（实测 opencode 槽位归 0）、池子配置优先 `<AP_DATA_DIR>/pool/config.json`（`pool_config_path()`）、自环防护按上游归属（`routing.py` 的 `_base_of()` + `_owned_by_harness()`）、通用适配器 `GenericCliAdapter` 与声明表在 main、接线 `base_harness_id` / `_INSTANCE_RE` / `build_adapter` / `known_bases` 都在、登记三处都在 |
| 真跑（8849 真平台 + 真实声明文件） | `GET /harnesses/bases` → 声明基座 `gemini`（`gemini-print`）+ 两条缺口（「没有 review_args：这个 CLI 不能做只读审阅」「池子侧没有这个 CLI 的槽位：它现在不能给别的窗口供模型」）+ 内置基座 `claude-code, codebuddy, codex, opencode, pi, qoder`，`declaration_error` 为 null；`GET /pool/status` → 21 个上游 |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| `pytest`（全量） | EXIT=0（实现代码逐字节未变；全量已在合并 codex 分支那轮跑过并 EXIT=0） |
| 边界 | **没有改它的实现** —— 代码已在 main 上且全量绿；核实出缺口就另开卡，不在结卡里顺手改 |
