# 认领：GATE-MERGE-CHECK-01

<!-- TRAE 独占。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `GATE-MERGE-CHECK-01` |
| Harness | TRAE |
| 认领时间 | 2026-10-05 20:18 |
| 分支 | `agent/local/trae/gate-merge-check-01/a1` |
| 状态 | ✅ done |
| 登记序号 | 28 |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `GATE-MERGE-CHECK-01`。

## 变更记录

- 2026-10-05 20:18：认领。给「先合 main 后验收/缺卡」三次违规建机械闸：
  discipline merge 组从 git 合并历史反向核对登记/状态/卡证据，fail-closed。
- 2026-10-05 21:50：完成。实施期间 main 由 claims-split 系列把认领表拆为
  docs/claims/ 独占文件并独立落地了大部分 32 项检查；合 main 时以 main 版
  检查器为底，移植本任务独有的 merge 组与认领序号连续断言，
  tools/discipline 测试与 gate.yml 一并保留。
