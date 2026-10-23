-- R10 §39 分配与传阅（R10-fix-13 登记，见规范 §38.11）。
--
-- 这 8 张表是「方案的传阅」和「任务的分配」两件事的落点。为什么不在 `tasks` 上加列、
-- 也不往 `tasks.status` 里加"竞争中"：§117 明确分配状态必须与任务执行状态分开 ——
-- 分配只决定"谁有权 Claim"，任务本身仍走 §10/§11 的老流程。因此这里的一切事件都是
-- §37.7 的 A 集合（分配事件），**不得**改写 `tasks.status`（封闭规则 6）。
--
-- 两条不变量由索引而不是靠应用逻辑钉住：
--   1. `allocation_decisions` 的 unique(round_id, task_id)：一轮里同一个任务最多一条
--      裁决。两个管理员同时裁决时，第二个人的条件更新会因为这一行存在而失败（ALLOC-004）。
--   2. `allocations` 的部分唯一索引 ON task_id WHERE state='accepted'：一个任务在任一
--      时刻最多有一份"已确认、可用于 Claim"的授权。撤销与 Claim 并发时只有一方成功
--      （ALLOC-014）。
--
-- `revision` 列是乐观锁版本，给 §39 硬规则 4 的 `expected_revision` 用；`material`
-- 与 `compared_json` 只增不改，保证"评审意见与裁决依据"可回放（§115）。

CREATE TABLE IF NOT EXISTS circulation_rounds (
    circulation_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups(group_id),
    plan_id TEXT NOT NULL,
    plan_snapshot_id TEXT NOT NULL,
    round_no INTEGER NOT NULL,
    reviewer_harness_ids_json TEXT NOT NULL DEFAULT '[]',
    opened_by TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    deadline_at TEXT,
    closed_at TEXT,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1,
    UNIQUE (plan_snapshot_id, round_no)
);

CREATE INDEX IF NOT EXISTS idx_circulation_rounds_group
    ON circulation_rounds(group_id, plan_snapshot_id, state);

CREATE TABLE IF NOT EXISTS circulation_reviews (
    review_id TEXT PRIMARY KEY,
    circulation_id TEXT NOT NULL REFERENCES circulation_rounds(circulation_id),
    -- 意见属于**哪一轮**，不是只属于哪个传阅记录。§39 的 `revising → circulating`
    -- 复用同一条 circulation_rounds 行并把 round_no 往前推，所以唯一键必须带上
    -- round_no —— 否则第一轮评审过的人在第二轮再也提交不了意见（这个 bug 由
    -- test_the_two_round_path_reaches_completed 抓到）。
    round_no INTEGER NOT NULL,
    harness_id TEXT NOT NULL,
    instance_id TEXT,
    body TEXT NOT NULL DEFAULT '',
    material_hash TEXT,
    submitted_at TEXT NOT NULL,
    -- 一个成员在同一轮里只有一份意见；重开是下一轮，不是改旧轮
    UNIQUE (circulation_id, round_no, harness_id)
);

CREATE TABLE IF NOT EXISTS allocation_rounds (
    round_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups(group_id),
    plan_snapshot_id TEXT NOT NULL,
    plan_version TEXT,
    -- §39 硬规则 1：开分配轮必须先有至少一轮完成的传阅。这里记下当时满足条件的传阅轮，
    -- 让"开轮时方案确实传阅过"这件事可回放，而不是只看当下的 state 值。
    circulation_id TEXT REFERENCES circulation_rounds(circulation_id),
    opened_by TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    deadline_at TEXT,
    closed_at TEXT,
    state TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_allocation_rounds_group
    ON allocation_rounds(group_id, plan_snapshot_id, state);

CREATE TABLE IF NOT EXISTS applications (
    application_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES allocation_rounds(round_id),
    group_id TEXT NOT NULL REFERENCES groups(group_id),
    harness_id TEXT NOT NULL,
    instance_id TEXT,
    state TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    submitted_at TEXT NOT NULL,
    updated_at TEXT,
    withdrawn_at TEXT,
    -- 一个 harness 在一轮里只能有一份申请；改的是同一份（revision 递增），
    -- 不是提交第二份来绕过"志愿服务顺序"的唯一性
    UNIQUE (round_id, harness_id)
);

CREATE INDEX IF NOT EXISTS idx_applications_round
    ON applications(round_id, state);

CREATE TABLE IF NOT EXISTS application_tasks (
    application_id TEXT NOT NULL REFERENCES applications(application_id),
    task_id TEXT NOT NULL,
    PRIMARY KEY (application_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_application_tasks_task
    ON application_tasks(task_id);

CREATE TABLE IF NOT EXISTS application_materials (
    material_id TEXT PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(application_id),
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    -- 材料只增不改：补材料是新版本，旧版本必须留着，否则"管理员当时看到的是哪一版"
    -- 就查不出来了（任务书 §2 要求精确记录）
    UNIQUE (application_id, version)
);

CREATE TABLE IF NOT EXISTS allocation_decisions (
    decision_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES allocation_rounds(round_id),
    task_id TEXT NOT NULL,
    chosen_application_id TEXT REFERENCES applications(application_id),
    compared_json TEXT NOT NULL DEFAULT '[]',
    decider_harness_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL DEFAULT 1,
    decided_at TEXT NOT NULL,
    -- 一轮一任务一条裁决：这条唯一键就是"两个管理员同时裁决只有一个成功"的落点
    UNIQUE (round_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_allocation_decisions_decider
    ON allocation_decisions(decider_harness_id, decided_at);

CREATE TABLE IF NOT EXISTS allocations (
    allocation_id TEXT PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES allocation_rounds(round_id),
    task_id TEXT NOT NULL,
    harness_id TEXT NOT NULL,
    decision_id TEXT REFERENCES allocation_decisions(decision_id),
    state TEXT NOT NULL,
    accepted_at TEXT,
    declined_at TEXT,
    revoked_at TEXT,
    -- 这份授权有没有被真的用掉：§39 只允许撤销"尚未 Claim 的分配"，而
    -- "accepted"本身只说明被选中者答应了，不说明他已经拿着它开了工。没有这两列就
    -- 分不出"撤销一份没人用的授权"和"撤掉一个正在跑的 Lease"，前者合法、后者会留下
    -- 孤儿工作区。`claim_id` 同值重复 Claim 视为幂等（见 mark_claimed）。
    claimed_at TEXT,
    claim_id TEXT,
    revision INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_allocations_round
    ON allocations(round_id, state);

CREATE INDEX IF NOT EXISTS idx_allocations_harness
    ON allocations(harness_id, state);

-- 一个任务任一时刻最多一份有效的、可用于 Claim 的授权。部分唯一索引把这条不变量
-- 钉在数据库里：并发撤销 / Claim 时，写入方要靠它来判定谁赢（ALLOC-014）。
CREATE UNIQUE INDEX IF NOT EXISTS idx_allocations_one_accepted_per_task
    ON allocations(task_id) WHERE state = 'accepted';
