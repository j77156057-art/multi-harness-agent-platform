# ADR-DEV-004：深度集成面——事件导出与外部消费边界

状态：**已接受**（MVP 后阶段；八步计划第 8 步）
日期：2026-10-08
关联任务：jsonl-export（协调者「继续完成 8」指令）
影响范围：`backend/app/exporter/**`、`backend/migrations/**`（`jsonl_exports`）、
          未来的外部消费方（备份/分析/看板/另有 Buzz 式前端）

---

## 背景

ADR-DEV-003 给"深度集成"设了闸：引入外部运行时或协议必须另立 ADR 并经协调者
裁决；同时它把"统一事件模型"登记为已有资产（`task_events` append-only + 88 项
白名单），把真缺口标注为"消费侧"。协调者指令「继续完成 8」——本 ADR 就是那道
闸门后的第一份裁决，回答一个问题：**外部系统与平台深度集成的第一个（也应是
主要的）接触面是什么？**

R10 其实已经给了答案，只是一直没实现：§15 / ADR-055 / FIN-09 / 阶段 1 清单
「JSONL 导出水位和恢复对账」——**SQLite 是唯一事实源，JSONL 是它的异步导出**：
轮转备份、灾备、以及外部只读消费的底座。它是"深度集成"与"单一真相源"两件事
的公共解：外部拿到的是**权威事件流的副本**，而不是第二套事实。

## 决策

1. **首个集成面 = 事件导出（只读）**。外部消费方（备份系统、分析、Buzz 式前端、
   任何第三方）以 `task_events.jsonl` 为输入；导出器只从 SQLite 读、只写文件，
   永不回写；仓内一切写路径不变。
2. **落地点**：`jsonl_exports` 表（迁移 008）+ `app/exporter`（`export_once` /
   `reconcile`）+ 模块入口 `python -m app.exporter`。契约按 §15 原文：从
   `exported_seq+1` 顺序读、临时文件 fsync、原子 replace、水位条件 UPDATE
   推进、失败进重试队列、恢复按 seq 去重（全局 seq 与其他 stream 共享，**洞是
   正常的，重复与缺失不是**）。
3. **告警面**（如实记录）：本仓无 pager/webhook；告警 = `jsonl_exports` 的
   status/attempts/last_error + `--check` 的退出码与 stderr。外部监控若需要
   推送通道，属另一份 ADR。
4. **仍留在闸外**（本 ADR 不授权，需各自另立 ADR）：
   - Nostr relay 或任何外部协议运行时；
   - 外部调度器本体互联（Paperclip 式"两台调度中心互认"）；
   - 替换既有事件传输层（WebSocket/轮询策略）；
   - 除 `task_events` 外的 stream（`usage_records` 等）与文件轮转策略。
5. **单向性不许破坏**：任何未来的集成实现都不得让外部系统成为可写的第二事实
   源；新增事件类型仍只经 R10 §37.7 白名单变更流程落地。

## 后果

### 正面

- "深度集成"有了一个不牺牲单一真相源的具体起点，且它同时兑现了 R10 阶段 1
  的既有承诺（轮转备份/灾备/导出对账）；
- 导出是**可对账**的（file_hash + 水位 + reconcile），不是"拷一份文件了事"——
  备份可验证、恢复可判定；
- 外部消费方与平台之间没有私有协议，只有 NDJSON + 事件白名单。

### 负面 / 成本

- 全量重写式导出在事件量线性增长时成本上升（轮转与增量追加策略被显式推迟，
  见决策 4——先要正确，再要规模）；
- `reconcile` 是只读诊断，不自动修复（修复 = 再跑 `export_once`，自动补导的
  路径单一，避免两个"修理工"）。

## 回退条件

1. 若外部消费需要**双向**同步（外部也要往平台写事件）→ 停：那是第二事实源的
   苗头，必须另立 ADR 并重评 ADR-DEV-003 的边界；
2. 若事件量级使全量重写不可接受（按 §29 的规模化触发线评审）→ 轮转/增量方案
   另立 ADR；
3. 若 `jsonl_exports` 的语义需要变更（如多消费者各自水位）→ 需求归外部消费
   方，方案另立 ADR。

## 兼容性保证

本 ADR 不改变 R10 任何语义；实现不触碰任何写路径（`app/exporter` 只依赖
`Database.read` / `EventStore.iter_since`；`jsonl_exports` 表不被其他模块写）。
`task_events` 的 append-only、88 项白名单、seq 唯一性均不变。

## 关联

- R10 §15（异步 exporter 契约原文）、§8（`jsonl_exports` 列）、ADR-055、FIN-09
- `docs/adr/ADR-DEV-003-paperclip-buzz-adoption.md`（本 ADR 的闸门与前作）
- `backend/app/exporter/service.py`（实现与其中逐条引用的契约）
- `TASKS.md` 任务卡 `jsonl-export`
