-- attempt 收口的「何时」与「为什么」。
--
-- task_attempts 原来只有 status：事后只能知道这次 attempt 关了，却答不出
-- 「什么时候关的」「为什么关的」。R10 §37.7 的事件白名单不动，所以把这两个
-- 事实落在 attempt 行自身（DDL 是自由文本，不受事件 allowlist 管辖）。
--
-- 可空、不回填：旧行两列为 NULL，不猜历史。幂等由迁移器保证
-- （schema_migrations 记录 version + checksum，重复 apply 直接跳过）。
ALTER TABLE task_attempts ADD COLUMN closed_at TEXT;
ALTER TABLE task_attempts ADD COLUMN close_reason TEXT;
