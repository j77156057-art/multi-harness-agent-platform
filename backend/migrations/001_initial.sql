-- MVP-02 canonical SQLite schema.
-- State transitions are owned by API services; this migration only defines storage.

CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id),
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS harnesses (
    harness_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'offline',
    registration_version TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    plan_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups(group_id),
    status TEXT NOT NULL DEFAULT 'draft',
    plan_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES plans(plan_id),
    snapshot_hash TEXT NOT NULL UNIQUE,
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups(group_id),
    plan_snapshot_id TEXT REFERENCES plan_snapshots(snapshot_id),
    status TEXT NOT NULL DEFAULT 'draft',
    queue_reason TEXT,
    owner TEXT,
    assigned_to TEXT,
    assigned_harness_ids_json TEXT NOT NULL DEFAULT '[]',
    require_human_assign INTEGER NOT NULL DEFAULT 0 CHECK (require_human_assign IN (0, 1)),
    base_commit TEXT,
    base_locked_seq INTEGER,
    current_attempt_id TEXT,
    current_epoch INTEGER NOT NULL DEFAULT 0,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    hotfix INTEGER NOT NULL DEFAULT 0 CHECK (hotfix IN (0, 1)),
    review_round INTEGER NOT NULL DEFAULT 0,
    last_progress_seq INTEGER,
    progress_hash TEXT,
    state_changed_seq INTEGER,
    waiting_since TEXT,
    escalated_at TEXT,
    next_reminder_at TEXT,
    required_reviews INTEGER NOT NULL DEFAULT 1,
    review_policy TEXT NOT NULL DEFAULT 'cross_provider',
    registration_version TEXT,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    budget_override_by TEXT,
    last_review_id TEXT,
    last_receipt_id TEXT,
    upstream_invalidated_seq INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    parent_task_id TEXT NOT NULL REFERENCES tasks(task_id),
    child_task_id TEXT NOT NULL REFERENCES tasks(task_id),
    relation TEXT NOT NULL DEFAULT 'depends_on',
    output_name TEXT,
    PRIMARY KEY (parent_task_id, child_task_id)
);

CREATE TABLE IF NOT EXISTS claim_requests (
    claim_request_id TEXT PRIMARY KEY,
    task_ids_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    partial_allowed INTEGER NOT NULL DEFAULT 0 CHECK (partial_allowed IN (0, 1)),
    first_result_json TEXT,
    created_seq INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_claims (
    claim_id TEXT PRIMARY KEY,
    claim_request_id TEXT REFERENCES claim_requests(claim_request_id),
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    released_at TEXT,
    release_reason TEXT,
    UNIQUE (claim_request_id, task_id)
);

CREATE TABLE IF NOT EXISTS task_attempts (
    attempt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_no INTEGER NOT NULL,
    owner TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL,
    base_commit TEXT,
    base_locked_seq INTEGER,
    head_commit TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, attempt_no)
);

CREATE TABLE IF NOT EXISTS task_outputs (
    output_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    actual_hash TEXT,
    "commit" TEXT,
    merged_seq INTEGER,
    UNIQUE (task_id, attempt_id, name)
);

CREATE TABLE IF NOT EXISTS verify_jobs (
    job_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
    "commit" TEXT NOT NULL,
    base_commit TEXT,
    lease_until TEXT,
    worker_id TEXT,
    pid INTEGER,
    pgid INTEGER,
    boot_id TEXT,
    overlay_id TEXT,
    required_resources_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    queue_reason TEXT,
    created_seq INTEGER,
    updated_seq INTEGER
);

CREATE TABLE IF NOT EXISTS task_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_name TEXT NOT NULL,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK (schema_version > 0),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_aggregate
    ON task_events (aggregate_type, aggregate_id, seq);

CREATE TRIGGER IF NOT EXISTS task_events_no_update
BEFORE UPDATE ON task_events
BEGIN
    SELECT RAISE(ABORT, 'task_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS task_events_no_delete
BEFORE DELETE ON task_events
BEGIN
    SELECT RAISE(ABORT, 'task_events is append-only');
END;

CREATE TABLE IF NOT EXISTS task_messages (
    message_id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES groups(group_id),
    task_id TEXT REFERENCES tasks(task_id),
    sender_type TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    body TEXT NOT NULL,
    created_seq INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_deliveries (
    delivery_id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES task_messages(message_id),
    recipient_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    delivered_at TEXT,
    UNIQUE (message_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS task_decisions (
    decision_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT,
    actor TEXT NOT NULL,
    question TEXT NOT NULL,
    options_json TEXT NOT NULL,
    answer TEXT,
    scope TEXT NOT NULL,
    expires_at TEXT,
    decided_seq INTEGER,
    superseded_by TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
    workspace_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
    branch TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
    kind TEXT NOT NULL,
    uri TEXT NOT NULL,
    hash TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    redaction_state TEXT NOT NULL DEFAULT 'clear',
    patch_base_commit TEXT,
    patch_applied_commit TEXT,
    UNIQUE (task_id, attempt_id, kind, hash)
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
    reviewer_harness_id TEXT NOT NULL,
    reviewer_provider TEXT NOT NULL,
    status TEXT NOT NULL,
    lease_until TEXT,
    fencing_epoch INTEGER,
    reviewed_commit TEXT,
    verdict TEXT,
    findings_hash TEXT,
    review_revision INTEGER NOT NULL DEFAULT 1,
    provider_confirmed_by TEXT,
    provider_override_decision_id TEXT,
    created_seq INTEGER,
    completed_seq INTEGER,
    UNIQUE (task_id, attempt_id, reviewer_harness_id, review_revision)
);

CREATE TABLE IF NOT EXISTS resource_leases (
    resource_lease_id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL,
    attempt_id TEXT,
    lease_until TEXT NOT NULL,
    epoch INTEGER NOT NULL,
    released_at TEXT,
    release_reason TEXT
);

CREATE TABLE IF NOT EXISTS side_effect_intents (
    intent_id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    task_id TEXT REFERENCES tasks(task_id),
    job_id TEXT,
    pid INTEGER,
    pgid INTEGER,
    boot_id TEXT,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    patch_hash TEXT,
    patch_uri TEXT,
    applied_by TEXT,
    generated_commit TEXT,
    fencing_epoch INTEGER,
    created_seq INTEGER,
    UNIQUE (type, idempotency_key)
);

CREATE TABLE IF NOT EXISTS command_idempotency (
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    first_result_json TEXT,
    created_seq INTEGER,
    PRIMARY KEY (operation, idempotency_key)
);

CREATE TABLE IF NOT EXISTS verification_receipts (
    receipt_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT NOT NULL REFERENCES task_attempts(attempt_id),
    "commit" TEXT NOT NULL,
    base_commit TEXT,
    fencing_epoch INTEGER NOT NULL,
    receipt_hash TEXT NOT NULL,
    differential_mode TEXT NOT NULL,
    verify_profile TEXT NOT NULL,
    created_seq INTEGER,
    UNIQUE (task_id, attempt_id, "commit", verify_profile)
);

CREATE TABLE IF NOT EXISTS integration_health (
    project_id TEXT PRIMARY KEY REFERENCES projects(project_id),
    status TEXT NOT NULL DEFAULT 'green',
    integration_commit TEXT,
    updated_seq INTEGER
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    attempt_id TEXT REFERENCES task_attempts(attempt_id),
    kind TEXT NOT NULL CHECK (kind IN ('development', 'review', 'verify', 'merge')),
    owner TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    fencing_epoch INTEGER NOT NULL,
    lease_until TEXT NOT NULL,
    grant_wall TEXT NOT NULL,
    grant_mono REAL NOT NULL,
    boot_id TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS verify_workers (
    worker_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    boot_id TEXT NOT NULL,
    last_heartbeat TEXT
);

CREATE TRIGGER IF NOT EXISTS receipt_no_update
BEFORE UPDATE ON verification_receipts
BEGIN
    SELECT RAISE(ABORT, 'verification_receipts is append-only');
END;

CREATE TRIGGER IF NOT EXISTS receipt_no_delete
BEFORE DELETE ON verification_receipts
BEGIN
    SELECT RAISE(ABORT, 'verification_receipts is append-only');
END;
