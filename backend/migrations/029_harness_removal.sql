-- 「自己加的窗口」可以被删掉：登记表加一列 removed_at。

-- 为什么不是 DELETE FROM harnesses：登记历史（harness_registrations）与探测历史
-- （capability_probes）都以外键指着这一行。硬删会把「这个窗口存在过、它领过什么
-- 活、当时的登记版本是什么」一起抹掉 —— 而这些是**过程证据**。
--
-- 软删除把两件事分开：
--   * 「不再用它」：removed_at 一填，它就不再出现在名册、派单候选与领活资格里；
--   * 「抹掉它存在过」：不做。行与历史都留着。
--
-- 口径：removed_at 为空 = 还在册；非空 = 已删除（记下什么时候删的）。

ALTER TABLE harnesses ADD COLUMN removed_at TEXT;
