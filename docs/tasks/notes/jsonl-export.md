### jsonl-export：深度集成面——SQLite→JSONL 异步导出 + 恢复对账（🚧 doing）

~~~text
任务编号：jsonl-export
负责人 Harness：Qoder
开始时间：2026-10-08 00:42
依赖任务：paperclip-buzz-adr（✅ fb30ae9 合入链上的 ADR-DEV-003——第 8 步的
          闸门 ADR）；frontend-thread（✅ fb30ae9，八步计划第 6 步收口）
工作区：.worktrees/qoder/
真相源引用：R10 §15（「JSONL 由 SQLite 提交成功后的异步 exporter 生成，不参与
            事务双写；jsonl_exports 记录 stream/exported_seq/file_hash/status/
            attempts/last_error/updated_at；从 exported_seq+1 顺序读取，写临时
            文件后 fsync、原子 rename，再推进；失败重试；重启和定时巡检比较
            max(seq) 与 exported_seq 自动补导并告警；恢复以 seq 去重，禁止空洞
            或重复」）；ADR-055；FIN-09；阶段 1 清单「JSONL 导出水位和恢复对账」；
            ADR-DEV-003（深度集成须另立 ADR——本任务含 ADR-DEV-004）
范围声明：
  - **本切片**（第 8 步的"集成面"落地）：
    ①ADR-DEV-004：定义深度集成的唯一首选面 = **只读事件导出**（外部消费方
      ingest JSONL，SQLite 保持唯一事实源；新增事件类型仍只经 §37.7 白名单
      流程）；Nostr relay 运行时、外部调度器本体互联、替换事件传输层仍留在
      「另立 ADR」闸外（本切片不碰）。
    ②迁移 008：`jsonl_exports` 表（§8 清单已有该表，迁移从未建）。
    ③`app/exporter`：`export_once()`（水位读取→delta 顺序读→按水位修剪既有
      文件防崩溃残尾→临时文件 fsync→`os.replace` 原子替换→条件 UPDATE 推进
      水位，rowcount 断言[并发第二写者幂等]）；失败 → status=failed +
      attempts+1 + last_error（模板化，不含 payload 材料），下次运行补导；
    ④`reconcile()`：纯读对账（DB 连续性与 max(seq)、文件行数/末 seq/连续性/
      file_hash、lag），问题列表可机读；
    ⑤`python -m app.exporter [--once|--check|--loop] [--interval N]` 模块入口
      （同 app.scheduler 形态；--once 失败 exit 1、--check 漂移 exit 1 —— 这就是
      "自动补导并告警"在本仓可用告警面上的落点）。
  - **显式出范围**（记录不静默）：**无外部告警通道**（无 pager/webhook——告警面 =
    表内 status/attempts/last_error + stderr + 退出码，如实记录）；文件**轮转**
    （§15 提"轮转备份"——本切片单文件全量重写，轮转另案）；`usage_records`
    等其他 stream（本切片只导 `task_events`，表结构按 stream 泛化）；调度器
    自动串跑（运维可 cron `--once`，是否挂进 sweeper 循环另案）。
  - **迁移 008** 为纯加表（无回填、无破坏）。
允许修改目录：backend/app/exporter/**（新增）、backend/app/config.py（export_dir）、
            backend/migrations/008_jsonl_exports.sql、backend/tests/**、
            docs/adr/ADR-DEV-004*.md（新增）、docs/traceability.md、TASKS.md、
            docs/tasks/notes/jsonl-export.md、docs/claims/qoder--jsonl-export.md
禁止修改目录：backend/app/state/**、backend/app/tasks/**、docs/spec/**、tools/**、
            scripts.sh、.github/**
交付分支：agent/local/qoder/jsonl-export/a1
完成标准：
  1. 全量导出：事件按 seq 升序逐行落 `task_events.jsonl`（含 payload 对象），
     水位推进到 max(seq)；重复运行零增量幂等
  2. 崩溃窗口：替换前失败 → 文件与水位原样、无 .tmp 残留（原子性）；替换后
     未推进水位 → 重跑按水位修剪残尾后补导，最终文件 seq 连续无重复无空洞
  3. 对账：`reconcile()` 检出 lag（水位落后）、哈希不符、文件空洞/残尾、
     DB 侧 max(seq) 一致性；ok 路径返回 problems 空
  4. CLI：`--once` 输出一行 JSON 报告（exit 0；失败 exit 1）；`--check` 漂移
     exit 1；`--loop` 可跑；无 DB 时 exit 2（同 scheduler 约定）
  5. `PYTHONUTF8=1 ./scripts.sh check` 全绿；traceability 新增
     `CORE-EXPORT-001` 行（✅ 有断言）；ADR-DEV-004 落成
test_id：CORE-EXPORT-001（新登记）
测试命令：PYTHONUTF8=1 ./scripts.sh check
当前状态：doing
~~~
