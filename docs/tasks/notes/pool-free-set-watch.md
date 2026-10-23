# pool-free-set-watch

### 统一任务卡

任务编号：pool-free-set-watch
负责 Harness：Codex
开始时间：未开工（提案登记 2026-10-23）
工作区：.worktrees/codex/
依赖任务：assistant-react-loop-p2（通道已按"目录驱动"实现，本卡是平台侧抽样哨）
真相源引用：MVP-ADAPTER-003（模型目录/池子面同族）；新检查 ID（如
`CORE-POOLWATCH-001`）待规范复审后回填，不在本卡自造
当前状态：待仓库主人复核（提案 docs/proposals/pool-free-set-watch.md）
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | 池子目录动态发现（现成）；alerts 入队/投递（现成） |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/pool/free_set_watch.py`（新增）、调度调用点（共享文件届时单开合并）、`docs/**` 登记 |
| 禁止修改 | `docs/spec/0.3-R10.md`、`docs/traceability.md`（无新 ID 校验前）、表结构/事件/路由（一律不加） |

## 做什么
免费档目录快照 diff（内容 hash 去重键），变更走 alerts 现成面告警；不主动
探测烧 CLI 槽，活性靠消费端冷却喂探针。

## 为什么
免费额度会失效、免费模型会下架、新的会出现——目录会变，人需要被喊；
目录本身已是动态的，缺的是"变了有人说"。

## 修法
见提案：`snapshot_free_ids` + `check(pool, alerts)`，快照落在
`<data>/pool/free-set.json`。

## 完成标准
scripted：同目录不告警；增/删/换各出一条告警（payload 带旧/新名单）；
同 hash 重复不重复投。

## 状态
待复核。未写代码。
