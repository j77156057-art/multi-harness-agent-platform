-- 全局 seq：让 task_events 与 task_messages 共享同一个序号空间。
--
-- 背景：R10 §15 规定每个事实事件带全局 seq 且禁止空洞；§19 的 read_context
-- 用一个 since_seq 混合排序消息与事件。MVP-08 的实现里 task_messages.created_seq
-- 是普通 INTEGER，按 group 各做 MAX+1，消息又不写 task_events，于是两个流
-- 各自从 1 开始，§19 的混合游标无法实现。MVP-08 prep（openapi.yaml）原本就
-- 要求消息 seq 取自与 task_events 相同的全局空间，本迁移让实现回归契约。
--
-- 方案（独立序列表，不改 R10、不让消息进事件流）：
--   1. 建单行表 global_seq 作为唯一发号器，初值取两流历史最大值；
--   2. task_events 去掉 AUTOINCREMENT，seq 改由写事务显式写入。
--      SQLite 不支持 ALTER COLUMN 改列约束，只能建表 + 拷数据 + 换名
--      （与 002 相同的先例），并重建索引与两个 append-only 触发器。

PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS global_seq (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL DEFAULT 0
);

-- 存量库：从历史最大号继续；全新库两表皆空，初值 0。
INSERT INTO global_seq(id, seq)
SELECT 1, COALESCE(MAX(seq), 0)
FROM (
    SELECT seq FROM task_events
    UNION ALL
    SELECT created_seq AS seq FROM task_messages WHERE created_seq IS NOT NULL
);

DROP TRIGGER IF EXISTS task_events_no_update;
DROP TRIGGER IF EXISTS task_events_no_delete;

CREATE TABLE IF NOT EXISTS task_events_new (
    seq INTEGER PRIMARY KEY,
    event_name TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    created_at TEXT NOT NULL
);

INSERT INTO task_events_new (
    seq, event_name, aggregate_type, aggregate_id, actor_type, actor_id,
    payload_json, payload_hash, schema_version, created_at
)
SELECT
    seq, event_name, aggregate_type, aggregate_id, actor_type, actor_id,
    payload_json, payload_hash, schema_version, created_at
FROM task_events
ORDER BY seq ASC;

DROP TABLE task_events;
ALTER TABLE task_events_new RENAME TO task_events;

CREATE INDEX IF NOT EXISTS idx_task_events_aggregate
    ON task_events (aggregate_type, aggregate_id, seq);

CREATE TRIGGER IF NOT EXISTS task_events_no_update
BEFORE UPDATE ON task_events
BEGIN
    SELECT RAISE(ABORT, 'task_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS task_events_no_delete
BEFORE DELETE ON task_events
BEGIN
    SELECT RAISE(ABORT, 'task_events is append-only');
END;

-- 发号已移交 global_seq，清掉旧 AUTOINCREMENT 记账，避免双计数器漂移。
DELETE FROM sqlite_sequence WHERE name = 'task_events';

PRAGMA foreign_keys=ON;
