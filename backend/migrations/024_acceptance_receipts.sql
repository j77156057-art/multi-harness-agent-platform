CREATE TABLE acceptance_receipts (
    receipt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    head_commit TEXT NOT NULL,
    profile TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    retries INTEGER NOT NULL,
    attempt_state TEXT NOT NULL,
    retry_state TEXT,
    gaps_json TEXT NOT NULL,
    rounds_json TEXT NOT NULL,
    frozen_json TEXT NOT NULL,
    finish_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, attempt_id, head_commit)
);

CREATE INDEX idx_acceptance_receipts_task
    ON acceptance_receipts(task_id, created_at);

ALTER TABLE tasks ADD COLUMN acceptance_state TEXT;
ALTER TABLE tasks ADD COLUMN acceptance_retries INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN acceptance_receipt_id TEXT;
