# 认领：pool-generic-adapter

| 字段 | 值 |
|---|---|
| task_id | `pool-generic-adapter` |
| Harness | Codex |
| 认领时间 | 2026-10-23 10:43 +08:00 |
| 分支 | `agent/local/codex/OFFICE-UI-01/a1` |
| 状态 | doing（池子通用性修复 + 通用适配器实现与定点测试就位；待仓库主人复核与合并） |

## 做什么
让额度池的"消费侧"也通用起来：一个新的 CLI 不必写一个 Python 适配器类，
就能作为正式成员借池子的额度。

## 为什么
上一轮把池子修到"任意 provider 规模"后，暴露出另一半：

- 池子修好了（`_base_of` 通用化、显式 `tier`、坏 store 拒绝、声明槽合并内置），
  但 worker 侧 `ADAPTER_TYPES` 仍是写死五家的名字；
- `pi-member-1` 这类新 harness 身份会被 `base_harness_id` 直接判"unsupported"，
  池子再通用也借不到——执行槽进不来；
- 给第六个 CLI 写第六个类意味着每个新 CLI 都动平台代码，与"额度池可扩展到
  任意新 harness"的目标矛盾。

## 修法

- **池子小坑（先做）**：未知 harness 也按归属排除，不再自环；内置 5 槽显式
  `tier: free`；声明槽缺 `tier` / 含未知 `store` 拒绝提供并在 `/pool/status`
  显示 note；声明与内置**合并**（同名同 model 覆盖，`{"enabled": false}` 可关
  整个 CLI）；池子配置优先 `<AP_DATA_DIR>/pool/config.json`；
- **通用适配器**：声明式注册表 `<AP_DATA_DIR>/adapters/cli.json`，
  `GenericCliAdapter(CliHarness)` 按声明构造（binary / argv 模板 / stdin /
  pool 协议 / 只读 review argv / 默认 model）；不覆盖内置五家，重名即拒绝；
- **接线**：`base_harness_id` 现读声明表；`_INSTANCE_RE` 放开成通用 base 形状
  （base 仍必须内置或声明，未知照旧拒绝）；`build_adapter` 统一构造入口；
  管理员细化 `_model_channel` 与 `_known_harness_id` 走同一张表；
- **不猜**：review 必须显式声明只读 argv，否则拒绝（平台不会替 CLI 编一个
  "只读方式"）；回执 `registration.adapter` 用声明名（默认 `<base>-cli`），
  绝不冒充内置；坏声明整个拒绝并报出文件与条目号，不做半个注册表。

## 边界

- 声明文件是**操作者所有**的可信输入：它指名一个可执行文件与它的 argv，
  平台只读不写；合法形状（base/协议/占位符）由加载时校验兜底；
- 新增配置文件**不是**新增事件名 / 状态 / schema / 迁移边——与
  `<AP_DATA_DIR>/pool/config.json` 同一层级，`schema_version` 不动；
- 显式钉管理员通道（`AP_ADMIN_MODEL_CHANNEL`）仍只收内置五家（config 启动期
  校验不做动态加载）；声明身份当管理员走 `auto` 已可用。

## 完成标准（机械可判）

1. `backend/tests/test_generic_adapter.py` 20 条绿：声明加载与路径、base 解析、
   argv/stdin/{model}、review 拒绝、route_environment（双协议）、坏声明拒绝
   （重名/缺 binary/坏协议/{model} 无默认/重复）、内置名单与 `ADAPTER_TYPES`
   一致、无常量兜底的未知仍拒；
2. `backend/tests/test_pool*.py` 五模块全绿（含本轮新增 6 条池子测试）；
3. 既有行为钉死不回归：`test_formal_multi_harness.py`、`test_admin_harness.py`、
   `test_admin_worker.py`、`test_cli_output.py` 全绿；
4. mock 二进制冒烟：声明式 CLI 以真实子进程跑完 command → 输出解析 →
   stdin 通道，argv/输入/解析全部按声明发生（smoke 脚本留档工作区外）；
5. `tools/docstate`、`tools/discipline`、`ruff`（check + format，197 文件）全绿。
