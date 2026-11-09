-- 存量 ready 任务的等待起点回填。
--
-- 2026-11-09 之前，"批准后直接进 ready" 把 waiting_since 写成了 NULL，
-- 于是 aging 与 ready_unclaimed 两条 sweep 的 `waiting_since IS NOT NULL`
-- 条件永远不成立：库里 31 条 ready 任务一条都没被看见过。
--
-- 起表时刻选**迁移执行时**而不是 updated_at：这些任务确实等了几天，但
-- 按真实时长起表会让升级后的第一轮 aging 一次性把它们全部升级，属于没有
-- 人按确认的状态变更。用 now 起表，提醒 10 分钟后照常出现，升级则按正常
-- 窗口走。历史时长仍可从 created_at/updated_at 读到。
UPDATE tasks
SET waiting_since = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
WHERE status = 'ready' AND waiting_since IS NULL;
