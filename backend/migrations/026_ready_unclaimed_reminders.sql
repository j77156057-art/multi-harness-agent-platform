-- ready 任务无人领取时的提醒记录（去重 + 提醒间隔）。
--
-- 为什么单开一张表：task_decisions 没有 created_at，answ 之外的"上次提醒时间"
-- 无处可存；sweep_progress 的 fingerprint 属于 stuck 巡检器，混用会让它的
-- 「静默轮次」计数失真。这张表只回答一件事：这条 ready 任务上次因为什么、
-- 什么时候提醒过管理员，以及提醒了第几次。
CREATE TABLE IF NOT EXISTS ready_unclaimed_reminders (
    task_id TEXT PRIMARY KEY REFERENCES tasks(task_id),
    reason TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    notified_at TEXT NOT NULL,
    reminder_count INTEGER NOT NULL DEFAULT 1
);
