-- budget-guard：任务级 token 预算的运行期两张投影表（budget-guard 卡）。
--
-- 机制核读自 Paperclip `budget-reservations.ts` / `run-cost-accounting.ts` /
-- `budget_incidents` schema，代码为本仓原生重写。刻意**不新增事件名**
-- （R10 §37.7 白名单不动）：预留在场、结算、事故都是状态投影，事实日志
-- 仍是 task_events（usage 随 submit_result 的 completion 载荷落事件）。
--
-- budget_reservations：
--   * 一个 attempt 至多一行（UNIQUE）——零额行也拦重复派发（Paperclip 同款
--     语义：reservation row 本身就是派发阀门）；
--   * actual_tokens 可空：**未上报 ≠ 零**。结算时没有 harness 上报就留 NULL，
--     保守计入（policy 层按 amount_tokens 计）——不发明数字。
-- budget_incidents：
--   * 阈值事故（warning / exhausted），open 至多一条/任务/阈值（部分唯一索引），
--     解决走 status=resolved + 时间/人/注。解决动作不删行（留痕）。

CREATE TABLE IF NOT EXISTS budget_reservations (
    reservation_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL UNIQUE REFERENCES task_attempts(attempt_id),
    amount_tokens INTEGER NOT NULL CHECK (amount_tokens >= 0),
    state TEXT NOT NULL CHECK (state IN ('held', 'settled', 'released')),
    actual_tokens INTEGER CHECK (actual_tokens IS NULL OR actual_tokens >= 0),
    source TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL,
    settled_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_budget_reservations_task_state
    ON budget_reservations(task_id, state);

CREATE TABLE IF NOT EXISTS budget_incidents (
    incident_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    threshold TEXT NOT NULL CHECK (threshold IN ('warning', 'exhausted')),
    observed_tokens INTEGER NOT NULL,
    limit_tokens INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
    note TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    resolved_by TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_incidents_open
    ON budget_incidents(task_id, threshold) WHERE status = 'open';
