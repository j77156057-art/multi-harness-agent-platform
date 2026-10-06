### message-refs-persist：消息 refs 字段落库与回读（✅ done，关闭 MVP-08 验收遗留 3b）

~~~text
任务编号：message-refs-persist
负责人 Harness：TRAE
开始时间：2026-10-05 22:21
依赖任务：MVP-08（message-global-seq 之后，同面契约）
工作区：.worktrees/trae/
真相源引用：docs/prep/MVP-08/openapi.yaml（post_message 请求体 refs、
            Message schema refs: string[]）、R10 §19（read_context 增量读消息）、
            backend/migrations/001_initial.sql（task_messages 现状无 refs 列）
允许修改目录：backend/migrations/** backend/app/tasks/** backend/app/api/**
            backend/tests/** docs/** TASKS.md
禁止修改目录：docs/spec/** tools/** docs/prep/**
交付分支：agent/local/trae/message-refs-persist/a1
背景：MVP-08 验收遗留 3b。POST /groups/{id}/messages 接收 refs 且在响应里
      回显，但 task_messages 表没有该列，service 的 INSERT 不带 refs，
      GET 走 SELECT * 永远读不回——引用信息静默丢失。与 3a 同属
      「实现未回归 openapi 契约」，openapi 已声明故 R10/prep 无需改。
完成内容：
  1. 迁移 004_message_refs.sql：ALTER TABLE task_messages ADD COLUMN
     refs TEXT NOT NULL DEFAULT '[]'（JSON TEXT，沿用 payload_json 惯例；
     仅加带默认值列，SQLite 无需重建表）；存量行由默认值回填 []；
  2. service.post_message：新增 _normalise_refs（None→[]；非 list 或
     含非 str/空白元素→InvalidInput；保序不去重），在写事务内随消息
     同 INSERT 落库（json.dumps ensure_ascii=False）；
  3. service.list_messages：SELECT * 后将 refs JSON 文本解码为数组，
     缺省/存量行得到 [] 而非 null；POST 响应 refs 与落库值一致；
  4. 测试：
     - test_api_events_messages.py：refs POST→GET 原样读回（3 条引用，
       含斜杠/冒号的值）、不带 refs 读回 []；元素非字符串→边缘 400
       request.invalid，空白元素→服务层 422 request.invalid；
     - test_database_events.py：新增旧库（仅 001~003）升级专项——
       004 应用计数、DDL 含新列、存量行 refs=='[]'、升级后新消息
       多 refs 往返、服务层直接调用三种非法形状抛 InvalidInput；
       既有全局 seq 迁移用例的 applied 期望 1→2 并追加存量 refs 断言。
test_id：MVP-API-001
测试命令：cd backend; ..\.venv\Scripts\python.exe -m pytest -q
当前状态：✅ done
完成时间：2026-10-05 22:30
commit：cf2b46c（实现与证据提交；本行为随附回填提交）
验证结果：
  - backend pytest：116 项退出码 0（较基线 113 新增 3：2 API + 1 迁移）；
  - ruff check app tests：All checks passed；ruff format --check：41 files；
  - docstate：90 项 0 失败；discipline：39 项 0 失败；
    tools pytest：14 passed；
  - MVP-08 prep：check_contract FAILURES 0；mutation_test caught 10/10；
  - 范围说明：R10/prep/openapi 均未改（refs 本就是 openapi 契约字段，
    本次是实现回归契约）；docs/traceability.md 仅追加 3b 关闭记录
    （跨任务登记，同 3a 先例）；MVP-08 验收卡与 message-global-seq 卡的
    「3b 仍开放」标注同步更新。openapi 中 plan_id/review_id/
    in_reply_to_seq/body_withheld/trusted 等未实现字段不在本任务范围。
  合并后复验（main 已含 Qoder MVP-05，无代码冲突；claims 撞号顺延 30）：
  backend pytest 135 项退出 0（116 + MVP-05 的 19）、ruff 49 files、
  docstate 90/0、discipline 39/0、tools 14 passed、prep FAILURES 0、
  mutation 10/10。
~~~
