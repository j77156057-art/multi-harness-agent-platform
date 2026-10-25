# pool-generic-adapter

### 统一任务卡

任务编号：pool-generic-adapter
负责 Harness：Codex
开始时间：2026-10-23 10:43 +08:00
工作区：.worktrees/codex/
依赖任务：FORMAL-MULTI-HARNESS-01（正式 harness 接替/领任务主链）
真相源引用：R10 §7（Adapter 的十六动词与收据）、§19.2（额度池语义沿 `docs/claims/codex--FORMAL-MULTI-HARNESS-01.md` 与 `pool-*` 任务卡落档）；AGENTS.md §1/§2/§4/§5/§5b
当前状态：waiting（实现与验收完成；提交未合入 main，待仓库主人复核移植/合并方式）
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

### 合并状态

该 commit 尚未合入 `main`。当前 `main` 已有后续 pool/adapter 文件结构改动，
直接 cherry-pick 会造成大量冲突/删除；待仓库主人决定是否从当前 `main`
移植通用适配器功能，不把“实现完成”误记为“已合入”。
