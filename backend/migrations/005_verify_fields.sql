-- MVP-06：补齐 R10 §16 要求、而 001_initial.sql 没有的 receipt / verify_job 列。
--
-- 背景：§16 明确列出 receipt「至少记录」的字段（baseline_exit_code、head_exit_code、
-- new_test_files、diff hash、verify_env_hash、工具链版本、failure_reason、
-- failed_test_cases、flaky_registry_ids、diff_intersection 等约 19 项），也列出了
-- verify_jobs 的字段（fencing_epoch、verify_profile、failure_reason、两个退出码、
-- receipt_hash、receipt_id）。001_initial.sql 的 verification_receipts 只有 11 列、
-- verify_jobs 缺 7 列，MVP-06 无法在不丢字段的前提下落库。
--
-- 为什么不是「把 receipt 塞进 artifacts」：§16 要求「receipt 权威副本只能在调度器
-- 数据库中」，而 artifacts 只存 uri + hash，内容是磁盘上的文件 —— 那样仓库侧的
-- verification/receipt.json 与它地位相同，权威副本就不存在了。
--
-- 为什么用 ADD COLUMN 而不是重建表：这里只加列、不改约束，SQLite 的
-- ALTER TABLE ADD COLUMN 足够；002 重建表是因为要改 NOT NULL 约束。
--
-- 口径：new_test_files / toolchain / decisions / secrets 等列表型字段跟随
-- 001 既有风格用 *_json 文本列（如 assigned_harness_ids_json），不新增表。

ALTER TABLE verification_receipts ADD COLUMN baseline_exit_code INTEGER;
ALTER TABLE verification_receipts ADD COLUMN head_exit_code INTEGER;
ALTER TABLE verification_receipts ADD COLUMN failure_reason TEXT;
ALTER TABLE verification_receipts ADD COLUMN log_hash TEXT;
-- 完整的 §16 receipt。列出的关键列是为了让 Merge Gate 不必解析 JSON。
ALTER TABLE verification_receipts ADD COLUMN receipt_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE verify_jobs ADD COLUMN fencing_epoch INTEGER;
ALTER TABLE verify_jobs ADD COLUMN verify_profile TEXT;
ALTER TABLE verify_jobs ADD COLUMN failure_reason TEXT;
ALTER TABLE verify_jobs ADD COLUMN baseline_exit_code INTEGER;
ALTER TABLE verify_jobs ADD COLUMN head_exit_code INTEGER;
ALTER TABLE verify_jobs ADD COLUMN receipt_hash TEXT;
ALTER TABLE verify_jobs ADD COLUMN receipt_id TEXT;
