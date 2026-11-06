CREATE TABLE project_creation_request_revisions (
    revision_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    original_project_id TEXT NOT NULL,
    original_project_name TEXT NOT NULL,
    original_group_id TEXT NOT NULL,
    original_group_name TEXT NOT NULL,
    final_project_id TEXT NOT NULL,
    final_project_name TEXT NOT NULL,
    final_group_id TEXT NOT NULL,
    final_group_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    revised_by_type TEXT NOT NULL,
    revised_by_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_project_creation_request_revisions_request
    ON project_creation_request_revisions(request_id);
