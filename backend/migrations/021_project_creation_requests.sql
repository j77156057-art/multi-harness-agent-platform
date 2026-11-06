CREATE TABLE project_creation_requests (
    request_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    project_name TEXT NOT NULL,
    group_id TEXT NOT NULL,
    group_name TEXT NOT NULL,
    source_group_id TEXT NOT NULL,
    purpose TEXT NOT NULL,
    scope TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    requested_by_type TEXT NOT NULL,
    requested_by_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    decided_by_type TEXT,
    decided_by_id TEXT,
    decided_at TEXT,
    decision_reason TEXT
);

CREATE INDEX idx_project_creation_requests_status_created
    ON project_creation_requests(status, created_at DESC);
CREATE INDEX idx_project_creation_requests_project
    ON project_creation_requests(project_id);
CREATE INDEX idx_project_creation_requests_group
    ON project_creation_requests(group_id);
