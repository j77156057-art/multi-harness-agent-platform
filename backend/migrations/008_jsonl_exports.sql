-- JSONL 异步导出（jsonl-export；R10 §15 / ADR-055 / FIN-09）。
--
-- 为什么单独一张表而不是复用别的载体：水位（exported_seq）是导出器自己的
-- 游标状态——「SQLite 是唯一事实源、JSONL 是它的异步导出」这一句（§15）
-- 要可对账，就必须有一个地方记录"导到哪里了、上次成功还是失败、失败几次"。
-- 列名与 §15 原文一致：stream、exported_seq、file_hash、status、attempts、
-- last_error、updated_at。
--
-- status 取值约定（本切片）：idle（未跑过）/ ok（上次成功）/ failed（上次失败，
-- 下次运行补导）。attempts 是连续失败计数，成功清零。
--

CREATE TABLE IF NOT EXISTS jsonl_exports (
    stream TEXT PRIMARY KEY,
    exported_seq INTEGER NOT NULL DEFAULT 0,
    file_hash TEXT,
    status TEXT NOT NULL DEFAULT 'idle',
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TEXT NOT NULL
);
