-- 巡检器家族（sweeper-runner，设计 docs/proposals/sweeper-family.md §2.5）：
-- 「连续 N 次巡检无进展」的指纹载体。
--
-- 为什么是独立表而不是 tasks 列：计数是巡检器自己的状态（每轮更新），
-- 写完即弃；放进 tasks 会让每次巡检都触碰热行并写一条本不属于状态机的字段。
-- tasks 里已有的 last_progress_seq/progress_hash 是「进度本身」的模式定义
-- （R10 §8），指纹是它的巡检读数，两者职责不同。
--
-- 本任务只建表；写入者在 sweep-aging-stuck（SCHED-SWEEP-003）。

CREATE TABLE IF NOT EXISTS sweep_progress (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    fingerprint TEXT NOT NULL,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    last_checked_at TEXT NOT NULL
);
