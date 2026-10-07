-- 告警去重键改造（liveness-watch）。
--
-- 背景：①的告警以事件游标为源，去重键是 `source_seq`（事件 seq，整数）；
-- ②的「零 commit 告警」是**状态型**告警——同一 attempt 只告一次，键是语义键
-- （attempt_id 字符串）。009 的表级 `UNIQUE(kind, source_seq)` 会让所有 key 型
-- 行（source_seq 恒 0）互相撞掉，必须拆成两个**部分唯一索引**：
--   source_seq 族：UNIQUE(kind, source_seq) WHERE source_key IS NULL
--   source_key 族：UNIQUE(kind, source_key) WHERE source_key IS NOT NULL
-- SQLite 不支持删除表级约束，故用标准重建法（建新表→拷贝→换名）。
--

CREATE TABLE alert_deliveries_new (
    delivery_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    task_id TEXT,
    source_seq INTEGER NOT NULL DEFAULT 0,
    source_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

INSERT INTO alert_deliveries_new(
    delivery_id, kind, task_id, source_seq, source_key, payload_json, status, attempts,
    last_error, created_at, delivered_at
)
SELECT
    delivery_id, kind, task_id, source_seq, NULL, payload_json, status, attempts,
    last_error, created_at, delivered_at
FROM alert_deliveries;

DROP TABLE alert_deliveries;

ALTER TABLE alert_deliveries_new RENAME TO alert_deliveries;

CREATE UNIQUE INDEX alert_deliveries_kind_source_seq
    ON alert_deliveries(kind, source_seq)
    WHERE source_key IS NULL;

CREATE UNIQUE INDEX alert_deliveries_kind_source_key
    ON alert_deliveries(kind, source_key)
    WHERE source_key IS NOT NULL;
