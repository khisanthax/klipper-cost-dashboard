CREATE TABLE IF NOT EXISTS manual_jobs (
    id INTEGER PRIMARY KEY,
    manual_job_id TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    hours REAL NOT NULL,
    filament_g REAL NULL,
    cost_override REAL NULL,
    notes TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_manual_jobs_project ON manual_jobs(project_id);

CREATE TABLE IF NOT EXISTS planned_items (
    id INTEGER PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    project_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    est_time_s INTEGER NOT NULL,
    est_filament_g REAL NULL,
    est_cost REAL NULL,
    est_cost_is_override INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NULL,
    notes TEXT NULL,
    converted_to_manual_job_id TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_planned_items_project ON planned_items(project_id);
CREATE INDEX IF NOT EXISTS idx_planned_items_status ON planned_items(status);
