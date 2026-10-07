### jsonl-export：深度集成面——SQLite→JSONL 异步导出 + 恢复对账（✅ done）

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
当前状态：done（作者完成，待合入；外部验收待认）
完成时间：2026-10-08 01:01
commit：`cead61a`（实现提交；合并提交见 git log main）
验证结果：
  docstate    : 90 项检查，0 项失败
  discipline  : 44 项检查，0 项失败
  ruff        : All checks passed（102 files already formatted）
  pytest      : 390 passed（本任务净增 8 项：test_jsonl_export.py），exit 0
  关键断言     : ①顺序导出与水位推进（records_read/exported_seq/lag、行序、payload
                对象、file_hash=sha256(文件字节)）；②二次运行零增量幂等；③崩溃
                窗口 A（替换后、水位未推进）：注入 _advance 失败 → 水位不动、
                status=failed/attempts=1、文件已含残尾；重跑按水位修剪后去重
                （`len(set(seqs))==3`）且归 ok/attempts=0；④崩溃窗口 B（替换前）：
                注入 os.replace 失败 → 旧文件字节不动、无 .tmp 残留、水位不动；
                重跑自动补导；⑤文件与库分歧（删中间行）触发"重建"路径；⑥reconcile
                检出 lag/篡改（追加 junk）/尾部无换行/哈希不符；⑦CLI：缺库 exit 2；
                `--once`/`--check` 正常 0、库侧漂移后 `--check` exit 1、`--once`
                补导后恢复 0（子进程真跑，含裸 SQL 注入的 9999 号事件）。
  dogfood      : 对前端实测的演示库（fe-demo，真事件 4 条）实跑：旧库先给出
                「schema is behind；先跑 python -m app.db」exit 2 → 迁移 →
                `--once` 导出 4 条（plan.approve×3 + claim）→ `--check` ok、
                hash_matches true。导出文件样例（gitignored）：
                `backend/data/fe-demo/exports/task_events.jsonl`。
  ADR          : `docs/adr/ADR-DEV-004-deep-integration-export.md`（闸门后第一份
                裁决：集成面=只读导出；Nostr 运行时/外部调度器互联/传输层替换
                仍各自另立 ADR；双向同步 = 停，回退条件明写）。
已知问题：
  - **无外部告警通道**（记录）：告警面 = jsonl_exports 的 status/attempts/
    last_error + `--check` 退出码/stderr；推送通道若需要属另一份 ADR。
  - **全量重写式导出**：文件每次以"修剪后的既有内容 + 增量"重建后原子替换——
    正确性优先；大事件量下的增量追加/轮转被显式推迟（ADR-DEV-004 决策 4）。
  - **仅 `task_events` 一个 stream**（表结构按 stream 泛化）；`usage_records`
    等其余 stream 未做；调度器自动串跑未接（运维 cron `--once` 即可）。
  - schema 预检是本任务 dogfood 后补的（旧库给出明确提示而非 traceback）；
    `_record_failure` 为尽力而为——连记账行都写不进去时不把报告变成回溯。
~~~
