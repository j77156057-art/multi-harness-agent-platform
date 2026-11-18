-- verification_receipts 不再用外键绑住 tasks / task_attempts。

-- 为什么：这张表是 **append-only 的证据**（表上有 BEFORE UPDATE / BEFORE DELETE
-- 触发器，删改都不许），而它的 task_id / attempt_id 又指着投影表。于是回收站
-- 到期清扫时两条路都堵着：删回执被触发器 ABORT，留回执又删不掉任务与尝试
-- （2026-11-19 真机复核：活库那张表 120 行，回收站到期清扫**一次都没成功过**，
-- 而清扫的异常被 scheduler 吞成报告字段 —— 界面毫无异样，看着像清过了）。
--
-- 口径：**证据留底，投影可以没有** —— 与 task_events 同一条规矩。回执里该有的
-- 事实（commit、hash、receipt_json 全文）都在自己身上，不再需要靠外键证明它
-- 指向谁；读它的那些接口（verify/worker、verify/receipts、api 的 task_verification）
-- 都是按 (task_id, attempt_id) 直查，本来就不 join tasks。
--
-- 重建表：SQLite 不能直接删外键，只能按新形状建一张、把数据搬过去、把
-- append-only 的两个触发器重新挂上。行数与内容不变（120 行 → 120 行）。

CREATE TABLE verification_receipts_new (
    receipt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL,
    "commit" TEXT NOT NULL,
    base_commit TEXT,
    fencing_epoch INTEGER NOT NULL,
    receipt_hash TEXT NOT NULL,
    differential_mode TEXT NOT NULL,
    verify_profile TEXT NOT NULL,
    created_seq INTEGER, baseline_exit_code INTEGER, head_exit_code INTEGER, failure_reason TEXT, log_hash TEXT, receipt_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE (task_id, attempt_id, "commit", verify_profile)
);

INSERT INTO verification_receipts_new (
    receipt_id, task_id, attempt_id, "commit", base_commit, fencing_epoch,
    receipt_hash, differential_mode, verify_profile, created_seq,
    baseline_exit_code, head_exit_code, failure_reason, log_hash, receipt_json
)
SELECT receipt_id, task_id, attempt_id, "commit", base_commit, fencing_epoch,
    receipt_hash, differential_mode, verify_profile, created_seq,
    baseline_exit_code, head_exit_code, failure_reason, log_hash, receipt_json
FROM verification_receipts;

DROP TABLE verification_receipts;

ALTER TABLE verification_receipts_new RENAME TO verification_receipts;

CREATE TRIGGER receipt_no_update BEFORE UPDATE ON verification_receipts BEGIN
    SELECT RAISE(ABORT, 'verification_receipts is append-only');
END;

CREATE TRIGGER receipt_no_delete BEFORE DELETE ON verification_receipts BEGIN
    SELECT RAISE(ABORT, 'verification_receipts is append-only');
END;
