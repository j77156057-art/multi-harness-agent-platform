# pool-free-set-watch

### 统一任务卡

任务编号：pool-free-set-watch
负责 Harness：Codex
开始时间：2026-10-23 18:5x +08:00（仓库主人指示"继续完成其他的"后开工）
工作区：.worktrees/codex/
依赖任务：assistant-react-loop-p2（通道已按"目录驱动"实现，本卡是平台侧抽样哨）
真相源引用：MVP-ADAPTER-003（模型目录/池子面同族）；新检查 ID（如
`CORE-POOLWATCH-001`）待规范复审后回填，不在本卡自造
当前状态：doing（实现＋scripted 测试已就位并在 main 上，运行入口备好并真跑过；**只剩部署时机待仓库主人定** —— 2026-10-26 由 Mavis 核实并真跑，见「验证结果」）
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | 池子目录动态发现（现成）；alerts 入队/投递（现成） |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `backend/app/pool/free_set_watch.py`（新增）、`backend/tests/test_pool_free_set_watch.py`（新增）、本卡、认领、`docs/proposals/pool-free-set-watch.md`、`TASKS.md` |
| 禁止修改 | `docs/spec/0.3-R10.md`、`docs/traceability.md`（无新 ID 校验前）、表结构/事件/路由（一律不加）、`backend/app/scheduler/**`、`backend/app/alerts/**` |

## 做什么

免费档目录快照 diff（内容指纹做去重键），变更走 alerts 现成面告警；
不主动探测烧 CLI 槽（一次探测就是一次真实调用），活性由消费端冷却喂探针。

## 为什么

免费额度会失效、免费模型会下架、新的会出现——目录会变，人需要被喊；
目录本身已是动态的（PoolService.models() 带 tier 标签），缺的是"变了有人说"。

## 修法（含实施中发现的两条平台事实）

- 模块：`backend/app/pool/free_set_watch.py`——`free_set(pool)`（滤
  `tier=="free"`）、`check(pool, enqueuer)`（快照
  `<data>/pool/free-set.json`、diff、入队 `pool.free_set_changed`，
  source_key=当轮名单指纹）、`WatchReport.as_json()`；
- **运行入口**：`python -m app.pool.free_set_watch [--once|--loop]`，
  同款 budget runner 形状（--check/--once/--loop、exit 0/1/2）；
- **平台事实 1**：sweeper 家族有不变量"sweeps 永不做 outbound（连告警
  排队都不做）"（sweeps.py sweep_budget 注释），所以哨**不挂**
  run_all_sweeps，独立模块入口才合平台规矩；
- **平台事实 2**：投递不入本卡——入队后由 `python -m app.alerts` 投
  （收件人沿 alerts 现有 webhook，budget 同款的分工）。

## 实施采用的现行默认（三条裁决未决，可推翻）

1. 收件人：沿 alerts webhook（未加新通道）；
2. 阈值 N=1：任一名单变化即投一条；
3. 新检查 ID 未自造，truth source 待复核。

## 完成标准（scripted 7 条，全不烧真额度）

首跑建基线不喊人；同名单静默且账本不重写；删/添各投一条（payload 带
旧/新名单与 added/removed）；同名单重查不重投、回滚快照重演已投变迁
不重投（去重键=内容指纹）；坏快照算基线丢失不误报；点名刷新可透传。

## 状态

实现与测试已就位（本轮提交）。部署：运维侧需要哪个进程跑
`--loop`（或 cron 打 `--once`）待仓库主人定；未部署前哨不上工。

### 验证结果

（2026-10-26 由 Mavis 补记，OFFICE-UI-39）

| 门禁 | 结果 |
|---|---|
| 逐条核实 | ✅ 模块与入口在 main 上（`9d73000`）；7 条 scripted 测试在 main 上且**全绿**（`tests/test_pool_free_set_watch.py`：首跑建基线不喊人 / 同名单静默且账本不重写 / 删添各投一条 / 同名单重查不重投 / 回滚快照重演已投变迁不重投 / 坏快照算基线丢失不误报 / 点名刷新可透传） |
| **真跑**（8849 真实池子） | ① 首跑 `python -m app.pool.free_set_watch --once` → `first_snapshot: true`、`changed: false`、`alerts_enqueued: 0`（**建基线，不喊人**），快照落盘 `data/pool/free-set.json`，当前 free 名单 14 个模型（codebuddy×3、opencode-cli×8、qoder×2…），指纹 `a9b68f20…`；② 第二遍 → `first_snapshot: false`、`changed: false`、`alerts_enqueued: 0`、指纹不变（**同名单静默，账本不重写**） |
| 入口 | `python -m app.pool.free_set_watch [--once｜--loop] [--interval N]`，`--once` 是默认；`--help` 可读 |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| `pytest`（全量） | EXIT=0（实现代码逐字节未变） |
| 边界 | **没有改它的实现**；也没有擅自部署 —— 哨会发告警（outbound），挂不挂是运维决定 |

### 部署（2026-10-26 定案，OFFICE-UI-42）

**两个独立进程，与平台进程并列**：

```
python -m app.pool.free_set_watch --loop --interval 900   # 哨：入队
python -m app.alerts --loop --interval 60                  # 投递：送出去
```

**为什么不是计划任务/cron**：那是某台机器的事，不是仓库的事；仓库只给「跑什么、跑多久、
怎么停」。

**为什么不能挂进平台进程**：平台有「sweeps 永不做 outbound（连告警排队都不做）」的
不变量，哨会发告警，所以它是**独立入口**才合规矩。

**安装门槛**：投递端要 `AP_ALERT_WEBHOOK_URL`（HTTPS）+ `AP_ALERT_WEBHOOK_SECRET`；
**没有收件人就别装** —— 装了也只是「变了记录在本地账里、没人知道」。本仓库目前也没有
告警读面（`routes.py` 里没有 alerts 的读端点），队列里的告警只能直接查
`alert_deliveries` 表。

完整的起停、排错、账怎么查、以及一次整链验证（哨认出变化 → 账里多一条 `pending` →
投递端因没配收件人而**明说**跳过）见 **`docs/deploy-free-set-watch.md`**。

无论怎么部署，投递都沿 **alerts 现有面** —— 本卡没有新增任何表结构、事件或路由。
