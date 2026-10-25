# OFFICE-UI-42

### 统一任务卡

任务编号：OFFICE-UI-42
负责 Harness：Mavis
开始时间：2026-10-26 07:30 +08:00
工作区：`.worktrees/mavis/`
依赖任务：OFFICE-UI-31（助手的深取证档）—— 与 UI-32~40 同一层：UI-33 已在链长 9 的
末端，再挂上去会超过清单 §0.2 声明的长度
真相源引用：`backend/app/pool/free_set_watch.py`（哨）、`backend/app/alerts/service.py`
（投递）、`backend/app/alerts/__main__.py`（投递入口）、
`docs/tasks/notes/pool-free-set-watch.md`（被收尾的那张卡）、
`docs/dev-convention.md` H-4 / H-7 / H-8
当前状态：doing
验证结果：见文末「验证结果」表。
test_id：MVP-ADAPTER-005

| 项 | 内容 |
|---|---|
| 依赖 | OFFICE-UI-31 |
| test_id | `MVP-ADAPTER-005` |
| 允许修改 | `docs/deploy-free-set-watch.md`（新增）、`docs/tasks/notes/pool-free-set-watch.md`、`docs/claims/codex--pool-free-set-watch.md`、`TASKS.md`、`docs/traceability.md`、`docs/claims/mavis--OFFICE-UI-42.md`、`docs/tasks/notes/OFFICE-UI-42.md` |
| 禁止修改 | `backend/app/**`（实现已在 main 上）、`docs/spec/**`、其他 Harness 的工作区与分支 |

## 做什么

| 原来 | 现在 |
|---|---|
| free-set-watch 的部署方式三个选项待主人定 | **定案**：哨与投递各是一个独立进程（与平台进程并列），命令与间隔写死；**安装门槛**（收件人）写清；落成 `docs/deploy-free-set-watch.md`；本机验证整条链（入队 → 账里有记录 → 投递端按规则处理） |

## 为什么

仓库主人圈了「先把 free-set-watch 的部署方式定了」。这是上一张卡留下的最后一个未决项。

## 怎么做到不出错

1. 不改实现。
2. 不擅自留常驻进程（本机只手动跑一次做验证）。
3. 不替主人选收件人 —— 缺它就说清楚，不假装装好了。

## 完成标准

1. 部署方式定案并写进仓（怎么起、怎么停、怎么排错、账在哪看）。
2. 本机验证整条链。
3. 门禁全绿。

## 验证结果

| 门禁 | 结果 |
|---|---|
| 部署方式定案 | 待回填 |
| 本机整链验证 | 待回填 |
| `tools/docstate` / `tools/discipline` | 待回填 |
| `pytest`（全量） | 待回填 |
