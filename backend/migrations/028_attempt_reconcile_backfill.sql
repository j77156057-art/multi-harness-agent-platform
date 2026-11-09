-- 一次性收口历史漏账：任务已经进入终态/停泊态，attempt 却还开着。
--
-- 这些行是旧版本里"绕过状态机收口点"留下的（例如任务已 cancelled/merged，
-- attempt 还停在 claimed/in_progress/verifying）。新代码由
-- sweep_attempt_reconcile 持续收口；这里只补历史。
--
-- 状态记 reconciled，而不是 merged/abandoned：前者会冒充正常完成，后者会
-- 冒充 owner 主动放弃。原因里带上任务当时的状态，事后可以分清每一类的来源。
-- 幂等：closed_at IS NULL 只命中还没结束的行。
UPDATE task_attempts
SET status = 'reconciled',
    closed_at = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
    close_reason = 'reconcile: task ' || (
        SELECT t.status FROM tasks t WHERE t.task_id = task_attempts.task_id
    )
WHERE closed_at IS NULL
  AND status IN ('claimed', 'in_progress', 'verifying')
  AND EXISTS (
      SELECT 1 FROM tasks t
      WHERE t.task_id = task_attempts.task_id
        AND t.status NOT IN (
            'claimed',
            'in_progress',
            'needs_help',
            'blocked',
            'verifying',
            'awaiting_human',
            'plan_drift',
            'impl_drift'
        )
  );
