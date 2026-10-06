# 认领：sweeper-family

<!-- 本文件由 Qoder 创建。其他 AI 请勿修改本文件；改你自己的即可，永不冲突。 -->

| 字段 | 值 |
|---|---|
| task_id | `sweeper-family` |
| Harness | Qoder |
| 认领时间 | 2026-10-06 11:05 |
| 分支 | `agent/local/qoder/sweeper-family/a1` |
| 状态 | ✅ done（设计已交付；采纳与否待裁决） |

任务表对应行：`TASKS.md` → 「任务表」章节，搜 `sweeper-family`。

## 范围

**交付物是本设计，不是实现**：`docs/proposals/sweeper-family.md`。它把 R10 里所有
"时间/资源条件满足即自动转移"的出口统一成一族巡检器，逐条给出：触发谓词、动作
（事件+转移+副作用）、参数、幂等与并发要求、测试注入点、与
`test_state_coverage.py` 豁免表的对接（实现后哪些豁免被强制点亮）。

背景：MVP-11 终审报告 §6 记录 7 个显式豁免态与散落的超时出口；现状只有 lease
巡检器一个原型（`app/lease/service.py::expire_stale_leases`）。

## 变更记录

- 2026-10-06 11:05：认领。前置已核对：MVP-01~11 全部合入 main（`0991ed5`）。
  设计编号承接终审报告 §6-P3-1 与 §3.2 的两类豁免清单。
- 2026-10-06 11:40：设计交付 `docs/proposals/sweeper-family.md`。要点：10 组巡检规格
  （全部从 R10 §10 守卫列逐条引原文）、单循环运行器架构、参数清单（新增 10 项）、
  覆盖对接（实现后豁免 7→3）、实现拆分 5+1 个任务、开放问题 O1/O2/O3 待裁决。
  门禁：docstate 90/0、discipline 44/0。
