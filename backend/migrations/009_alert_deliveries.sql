-- 出站告警投递（alert-webhook）。
--
-- 为什么是两张表而不是复用别的：告警有两个独立状态——「读到哪了」（事件订阅
-- 游标）与「发到哪了」（投递队列）。游标放 alert_cursors（每 stream 一行），
-- 投递放 alert_deliveries（一条告警一行，含重试记账）。分离的好处：投递失败
-- 重试不阻断订阅推进，订阅推进也不因投递积压回退。
--
-- 语义约定（实现见 backend/app/alerts/service.py）：
--   status ∈ pending | delivered | failed（failed = 达尝试上限后的停靠态，
--   不再自动重试，操作面可见）；
--   UNIQUE(kind, source_seq) 是入队幂等键——同一条源事件重复扫描只会有一条
--   投递（与 jsonl_exports 的"恢复去重"同思路）。
--

CREATE TABLE IF NOT EXISTS alert_cursors (
    stream TEXT PRIMARY KEY,
    cursor_seq INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_deliveries (
    delivery_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    task_id TEXT,
    source_seq INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE (kind, source_seq)
);
