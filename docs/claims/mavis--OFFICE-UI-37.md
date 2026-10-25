# 认领：OFFICE-UI-37

| 字段 | 值 |
|---|---|
| task_id | `OFFICE-UI-37` |
| Harness | Mavis |
| 认领时间 | 2026-10-26 05:00 +08:00 |
| 分支 | `agent/local/mavis/OFFICE-UI-37/a1` |
| 状态 | doing |

目标：**接手 codex 的在途**，先把 `pool-generic-adapter` 这张卡收尾。

## 接手时的实况（查过的）

- codex 的助手那条线（`assistant-react-loop` / `-p2` / `-p3` / `assistant-diagnose-pack` /
  `review-contract-gate`）**已由他自己关成 done**，实现都在 main 上；他分支上剩下的 4 个
  提交（评审契约守门 `10a50bb` 为主）已通过 mergeguard 合入 main（`e9b58f2`）。
- `pool-generic-adapter` 仍写着 **waiting**：「实现提交 `c4c16a1` 尚未合入 main，main 上
  pool/adapter 已有后续结构变更，待仓库主人复核移植/合并方式」。
- 核实下来**这条描述已经过时**：`c4c16a1` 那条单独提交确实不在任何分支上，但它的内容
  后来经 `2c02969`（`Merge branch 'agent/local/codex/OFFICE-UI-01/a1'`）**已经进了 main**：
  - `backend/app/adapter/harnesses/generic.py`（`GenericCliAdapter` + 声明表）在 main 上 ✅
  - 池子四小坑在 main 上：`CLI_TIERS` 无默认（缺 tier 不当免费）、`KNOWN_CLI_STORES`
    （未知 store 拒绝提供）、`_merge_slots(declared)`（声明与内置合并、`enabled:false`
    可整体关闭）、`pool_config_path()`（`<AP_DATA_DIR>/pool/config.json` 优先）✅
  - 接线在 main 上：`base_harness_id` / `_INSTANCE_RE` / `build_adapter` / `known_bases` ✅

所以这张卡**该结** —— 但它不是「我实现的」，我不能凭代码在就写「已验收」。这一张做的是
**逐条核实 + 真跑**，把核实证据写进它的卡片，状态改成 done。

## 范围

- `docs/tasks/notes/pool-generic-adapter.md`（更新它的状态与「合并状态」段：写清实际
  是经 `2c02969` 合入的，不是 `c4c16a1`）
- `TASKS.md`（那张卡的状态行）
- `docs/claims/codex--pool-generic-adapter.md`（补记核实结论）
- 必要时 `backend/tests/**`（若核实发现缺口才补测试）

## 怎么做到不出错

1. **不碰它的实现**：代码已在 main 上且全绿，这一张只做核实与收尾；若核实发现缺口，
   另开卡，不在「结卡」里顺手改。
2. 核实要**逐条对得上它自己写的完成标准**，不能只说「代码在」。
3. 真跑一遍声明式适配器（声明 → 读面 → 缺口清单），用真实数据。

## 完成标准

1. `pool-generic-adapter` 的完成标准逐条核实完，证据写进它的卡片。
2. 状态改成 done，并写清实际合入路径。
3. 真跑一遍声明式适配器。
4. 门禁全绿；`docs/traceability.md` 登记 `MVP-ADAPTER-004`。
