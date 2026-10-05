-- MVP-10a：补 R10 §8 列出、而 001_initial.sql 没有的两张 Adapter 表。
--
-- 背景：§7 的前两个动词是 probe() 与 capabilities()，§6 规定「注册信息变更必须保留历史。
-- 任务 Claim 时固定注册版本」。harnesses 表只存当前值，没有历史；capability_probes 在
-- §8 的 canonical 清单里，但两张迁移都没建。于是 probe 结果无处落、注册版本无法追溯
-- —— 而 registration_version 是要在 Claim 时固定下来的东西，没有历史就没法核对
-- 「当时固定的那一版是什么」。
--
-- 与本任务的关系：Fake Harness 的能力探测要真的写进去，才算完成标准里的
-- 「能力探测与注册版本」；只把 probe 做成一个返回常量、不做持久化的端点是假实现。
--
-- 口径：照 001 既有风格，列表型字段用 *_json 文本列。harnesses 表不动（它继续存当前值，
-- 注册历史的每一次变更在 harness_registrations 里留一行）。

CREATE TABLE IF NOT EXISTS harness_registrations (
    registration_id TEXT PRIMARY KEY,
    harness_id TEXT NOT NULL REFERENCES harnesses(harness_id),
    registration_version TEXT NOT NULL,
    provider TEXT NOT NULL,
    adapter TEXT NOT NULL,
    adapter_version TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    max_parallel_tasks INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'online',
    superseded_by TEXT,
    created_seq INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE (harness_id, registration_version)
);

-- probe 的每次结果都留一行：R10 §6 说「运行时自报只用于告警或降级，不能单独用于高风险
-- 任务 Claim」，所以历史必须可查，否则无从判断某次 Claim 依据的是哪次探测。
CREATE TABLE IF NOT EXISTS capability_probes (
    probe_id TEXT PRIMARY KEY,
    harness_id TEXT NOT NULL REFERENCES harnesses(harness_id),
    registration_version TEXT,
    reported_capabilities_json TEXT NOT NULL DEFAULT '[]',
    effective_capabilities_json TEXT NOT NULL DEFAULT '[]',
    host_mode TEXT,
    degraded INTEGER NOT NULL DEFAULT 0 CHECK (degraded IN (0, 1)),
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_seq INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capability_probes_harness
    ON capability_probes (harness_id, created_seq);
