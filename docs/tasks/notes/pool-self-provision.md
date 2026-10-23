# pool-self-provision

### 统一任务卡

任务编号：pool-self-provision  
负责 Harness：Codex  
开始时间：2026-10-23  
工作区：`.worktrees/codex/`  
依赖任务：`pool-free-set-watch`  
真相源引用：MVP-ADAPTER-003；AGENTS.md §1/§2/§4/§5/§5b  
当前状态：done  
test_id：`MVP-ADAPTER-003`

## 做什么

复用 harness 注册表、`data/pool/config.json`、`data/adapters/cli.json` 和
`AlertNotifier`，发现没有池槽位覆盖的新 harness 与缺 tier 槽位，带来源证据
入告警。候选不入池，tier 不猜，未知 CLI 不执行。

## 完成标准

- 首次扫描只建立候选基线。
- 候选变化入 `pool.provision_candidates`，相同指纹不重复入队。
- 无 tier 不进入 free，未知 CLI 无 subprocess 路径。
- scripted 测试覆盖证据 payload、去重和人工批准边界。
- ruff、docstate、discipline 与定点 pytest 通过。

## 状态

完成时间：2026-10-23 22:45 +08:00  
commit：`09236a6`（只读候选发现、alerts 指纹去重与 scripted 验收）。

### 验收结果

- `tests/test_pool_provision.py` 与既有 `tests/test_pool_free_set_watch.py`：11 条通过。
- pool 相关全套测试：70 条通过；ruff、docstate、discipline 通过。
- 全量 pytest：`[100%]` 全部通过（无失败；总耗时约 193.7 秒）。
- 候选只读、首轮基线、指纹去重、缺 tier 不猜 free、未知 CLI 不执行均有 scripted 证据。
