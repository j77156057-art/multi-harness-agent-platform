# OFFICE-UI-39

### 统一任务卡

任务编号：OFFICE-UI-39
负责 Harness：Mavis
开始时间：2026-10-26 06:25 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~38 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/pool/free_set_watch.py`（哨的模块与入口）、
`backend/tests/test_pool_free_set_watch.py`（7 条 scripted）、
`docs/tasks/notes/pool-free-set-watch.md`（被收尾的那张卡）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：done
commit：`IMPLEMENT_COMMIT_PLACEHOLDER`（接手 codex 的在途：收尾 pool-free-set-watch）
验证结果：见文末「验证结果」表。
test_id：MVP-ADAPTER-003

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-ADAPTER-003` |
| 允许修改 | `docs/tasks/notes/pool-free-set-watch.md`、`docs/claims/codex--pool-free-set-watch.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-39.md`、`docs/tasks/notes/OFFICE-UI-39.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| 免费档目录变更哨：实现与 7 条 scripted 测试已在 main 上（`9d73000`），但卡停在「部署时机待仓库主人定」 | 核实 + 真跑两遍 `--once`，把**部署的三个选项、各自的影响、以及不部署会怎样**写清楚，等主人一句话定 |

## 为什么

仓库主人圈了「你接手 codex 的活」，顺序是「先做 1，接着 2、3」。这是 2。

## 怎么做到不出错

1. **不擅自常驻**：哨会发告警（outbound 性质），挂不挂、挂哪儿是运维决定。
2. 不碰实现。
3. 真跑只用 `--once`，不留常驻进程。

## 完成标准

1. 7 条 scripted 绿 + 入口可用 + 真跑两遍的记录。
2. 部署选项摆在主人面前。
3. 门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 逐条核实 | 模块与入口在 main 上（`9d73000`）；7 条 scripted 测试在 main 上且**全绿** |
| 真跑（两遍 --once） | ① 首跑 → `first_snapshot: true`、`changed: false`、`alerts_enqueued: 0`（建基线不喊人），快照落盘 `data/pool/free-set.json`，free 名单 14 个模型，指纹 `a9b68f20…`；② 第二遍 → `first_snapshot: false`、`changed: false`、`alerts_enqueued: 0`、指纹不变（同名单静默、账本不重写） |
| 部署选项 | 写进被收尾那张卡的「部署（待仓库主人定）」段：**A 常驻 `--loop`**（不能挂 sweeper —— 平台有「sweeps 永不做 outbound」的不变量）／**B 定时打 `--once`**／**C 暂不部署**（免费档变了没人被喊）。投递沿 alerts 现成面，本卡不新增表结构、事件或路由 |
| `tools/docstate` / `tools/discipline` | 98/98、44/44 |
| `pytest`（全量） | EXIT=0（本卡**只改文档**；后端代码逐字节未变） |
| 边界 | **没有改它的实现**；也**没有擅自部署** —— 哨会发告警（outbound），挂不挂是运维决定 |
