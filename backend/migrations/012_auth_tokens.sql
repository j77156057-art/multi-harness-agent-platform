-- auth-layer：主体令牌表（R10 §17 API 层权限矩阵的执行基础）。
--
-- 设计选择（一次记录，避免后人重猜）：
--   * **只存 SHA-256 哈希**——明文只在签发的那一刻由 CLI 打印一次；库被
--     拷走不发凭据。校验=按哈希查唯一索引 + 吊销检查，不引入 HMAC/非对称
--     密钥管理（v1 规模不值得）。
--   * subject_type 只有两类：harness / human。R10 §17 的七角色里，
--     Planner 并入 human；Verify Worker / Merge Worker 是进程面（不经
--     HTTP token，报告 §4.5 建议的"调度中心自身主体"）；协作者是打
--     help 面的 harness。角色到主体的归并在 policy.py 表内逐行记录。
--   * 一个主体可以有多把有效 token（轮换用）；吊销=置 revoked_at，
--     不删行（留痕）。重放已吊销 token 与未知 token 都拒绝，但审计
--     reason 可区分（token_revoked / token_invalid）。

CREATE TABLE IF NOT EXISTS auth_tokens (
    token_hash TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('harness', 'human')),
    subject_id TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_auth_tokens_subject
    ON auth_tokens(subject_type, subject_id);
