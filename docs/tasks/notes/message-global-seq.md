### message-global-seq：消息与事件共享全局 seq（✅ done，关闭 MVP-08 验收遗留 3a）

~~~text
任务编号：message-global-seq
负责人 Harness：TRAE
开始时间：2026-10-05 21:18
依赖任务：MVP-08（已验收 087eb93；本任务关闭其验收遗留项 3a）
工作区：.worktrees/trae/
真相源引用：docs/spec/0.3-R10.md §15（事件带全局 seq、禁止空洞）、§19（read_context
            用 since_seq 混合排序消息与事件）；
            docs/prep/MVP-08/openapi.yaml L1215（消息 seq 取自与 task_events
            相同的全局空间——契约原本如此要求，是实现偏离）；
            MVP-08 验收遗留项 3a（TASKS.md）
背景：task_messages.created_seq 是普通 INTEGER，post_message 按 group 做
      MAX+1；消息不写 task_events，两流序号互不相干，§19 混合游标无法实现。
      借号路径被 append-only 触发器与 §37.7 事件封闭集合双重封死（R10 无消息
      事件名）。采用方案 A：独立全局序列表，事件与消息在各自写事务内同取一个号。
允许修改目录：backend/migrations/**、backend/app/db/**、backend/app/events/**、
            backend/app/tasks/**、backend/app/api/**、backend/tests/**、
            docs/traceability.md（跨任务登记，同 MVP-08 先例）、TASKS.md
禁止修改目录：docs/spec/0.3-R10.md、tools/**、docs/prep/MVP-08/**（契约本就
            要求全局 seq，无需改 prep）
交付分支：agent/local/trae/message-global-seq/a1
设计：
  1. 迁移 003：建单行表 global_seq(id=1, seq)，初值=MAX(task_events.seq,
     task_messages.created_seq)；重建 task_events 去掉 AUTOINCREMENT
     （SQLite 不能 ALTER 列约束，照 002 先例建新表+拷贝+换名，重建索引与
     两个 append-only 触发器，清 sqlite_sequence 旧条目）；
  2. app/db/sequence.py：next_global_seq(conn) 在调用方事务内
     UPDATE global_seq SET seq=seq+1 取号（BEGIN IMMEDIATE 串行，回滚不空洞）；
  3. EventStore.append 显式带 seq 插入（两条 connection 分支同事务取号）；
     post_message 用同一分配器，删除 per-group MAX+1；
  4. /events 与 /groups/{id}/messages 仍是各自子集，但号同源——交错写入产生
     的「缺口」是另一流的真实记录，非 §15 禁止的空洞。
完成标准：
  1. 交错写事件与消息，两流号严格递增、互不重复、并集在同一连续区间（有测试）；
  2. 旧库迁移：已有事件/消息号保留，新号从历史最大值 +1 继续（有测试）；
  3. 失败事务回滚不产生空洞（沿用 test_database_events 既有用例语义）；
  4. 全门禁：docstate 90/0、discipline、pytest 全量、ruff、check_contract、
     mutation_test；MVP-08 卡遗留项 3a 标记已关闭（refs 3b 不在本任务范围）。
test_id：MVP-API-001
测试命令：cd backend; ..\.venv\Scripts\python.exe -m pytest -q
当前状态：✅ done
完成时间：2026-10-05 21:30
commit：3b35e5f（实现与证据提交；本行为随附回填提交）
验证结果：
  方案 A 已落地：
  - backend/migrations/003_global_seq.sql：单行表 global_seq 发号器，
    初值取两流历史 MAX；task_events 去 AUTOINCREMENT 重建（照 002 先例，
    重建索引与两个 append-only 触发器，清 sqlite_sequence 旧条目）；
  - backend/app/db/sequence.py：next_global_seq(conn) 强制在写事务内
    UPDATE+SELECT 取号，无事务即 ValueError，回滚随事务不产生空洞；
  - app/events/store.py：两条 connection 分支同事务显式带 seq 插入，
    不再用 lastrowid；app/tasks/service.py post_message 删除 per-group
    MAX+1，改取全局号（BEGIN IMMEDIATE 串行，并发安全）。
  机械证据：
  - test_api_events_messages.py：消息号 = 当前事件最大号 +1/+2；交错写
    消息→claim 事件→消息，断言 m1 < event < m2 且 m2=m1+2；第二个群
    消息不重新从 1 开始；两流并集 == 1..N 连续区间且互不重号；
  - test_database_events.py：用 001+002 旧迁移造存量库（旧二进制式裸插：
    事件 1..5、两个群各有 created_seq=1），应用 003 后 global_seq=5、
    事件行全保留、DDL 无 AUTOINCREMENT，新事件取 6、新消息取 7；
  - 既有无空洞语义不变：失败事务连号回滚（test_failed_domain_transaction…）、
    12 线程并发取号 1..12 不重不漏，均仍通过。
  门禁实测（2026-10-05 21:30）：
  - docstate 90/0；pytest 全量 113 项退出码 0（本任务净增 2 项：
    test_api_events_messages 8→9、test_database_events 9→10）；
  - ruff check 全过；ruff format 41 files already formatted
    （新增 sequence.py 计入）；
  - check_contract.py FAILURES 0；mutation_test.py caught 10/10；
  - discipline：3 项失败全是 main e6b3026 既有红灯（verify-mvp-04/
    MVP-04-inject 命名与缺登记），由 GATE-MERGE-CHECK-01 分支清理，
    本任务零新增红灯；
  - 真实 app.main:app 冒烟（走 003 全新迁移）：批准事件 seq=1，随后
    消息 seq=2，同空间连续。
  范围说明：R10/prep 均未改（openapi.yaml L1215 本就要求全局空间，
  本次是实现回归契约）；docs/traceability.md 仅追加关闭记录（跨任务
  登记，同 MVP-08 先例）。遗留 3b（refs 字段接收即丢）已由
  message-refs-persist（迁移 004，2026-10-05）关闭。
~~~
