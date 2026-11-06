CREATE TABLE experience_confirmations (
    confirmation_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    project TEXT NOT NULL,
    task_category TEXT NOT NULL,
    scope TEXT NOT NULL CHECK(scope IN ('project', 'cross_project')),
    status TEXT NOT NULL CHECK(status IN ('confirmed', 'ignored', 'revoked')),
    decided_by TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    revoked_by TEXT,
    revoked_at TEXT,
    UNIQUE(experience_id, task_id)
);

CREATE INDEX idx_experience_confirmations_dispatch
    ON experience_confirmations(group_id, project, status);

CREATE TABLE experience_injections (
    injection_id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    confirmation_id TEXT NOT NULL,
    confirmed_by TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    injected_at TEXT NOT NULL,
    layer TEXT NOT NULL CHECK(layer = 'admin_dispatch'),
    UNIQUE(task_id, experience_id, confirmation_id)
);

CREATE INDEX idx_experience_injections_task
    ON experience_injections(task_id, injected_at);

CREATE TABLE experience_injection_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
