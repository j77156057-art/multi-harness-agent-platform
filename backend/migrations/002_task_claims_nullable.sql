-- R10-fix-11：让 task_claims.attempt_id / owner 可空。
--
-- 背景（R10 §8 + §11 + §37.6 三处规范此前无法同时满足）：
--   §11:511  `ready / claim_deferred_resource` 预占资源失败时「只进入 queued，
--                不写 owner、attempt、Lease」；
--   §11:617  批量 Claim 的「每个任务项（含失败项）都必须留一行 task_claims」；
--   §8       task_claims 的 attempt_id / owner 此前是 NOT NULL。
--
-- 三者叠加的结果：延迟资源时既要留行、又不能有 owner/attempt，而列不允许为空。
-- MVP-04 用空串哨兵（NO_ATTEMPT）绕过，但空串与「真实但异常的值」不可区分，
-- 属于设计债。本迁移改为可空，让「未领取」与「已领取」在数据层就��区分开。
--
-- 为什么重建表：SQLite 不支持 ALTER COLUMN 改约束，只能建新表 + 拷数据 + 换名。
-- 换名后重建 UNIQUE(claim_request_id, task_id) 与外键。
--
-- 兼容性：重放逻辑用 LEFT JOIN task_attempts，本就容忍 attempt_id 为 NULL；
--claimed_at 保持 NOT NULL（留行必有创建时间），released_at / release_reason 不变。

PRAGMA foreign_keys=OFF;

CREATE TABLE IF NOT EXISTS task_claims_new (
    claim_id TEXT PRIMARY KEY,
    claim_request_id TEXT REFERENCES claim_requests(claim_request_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT,
    owner TEXT,
    claimed_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT,
    UNIQUE (claim_request_id, task_id)
);

INSERT INTO task_claims_new (
    claim_id, claim_request_id, task_id, attempt_id, owner,
    claimed_at, released_at, release_reason
)
SELECT
    claim_id, claim_request_id, task_id,
    -- 空串哨兵回填为 NULL：MVP-04 写入的 NO_ATTEMPT 就是空串
    NULLIF(attempt_id, ''),
    NULLIF(owner, ''),
    claimed_at, released_at, release_reason
FROM task_claims;

DROP TABLE task_claims;
ALTER TABLE task_claims_new RENAME TO task_claims;

PRAGMA foreign_keys=ON;
