CREATE TABLE IF NOT EXISTS printers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    moonraker_url TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filament_profiles (
    id INTEGER PRIMARY KEY,
    profile_uid TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE,
    material TEXT NULL,
    filament_mode TEXT NULL,
    filament_rate REAL NULL,
    cost_per_kg REAL NULL,
    grams_per_meter REAL NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hourly_rate_profiles (
    id INTEGER PRIMARY KEY,
    profile_uid TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE,
    description TEXT NULL,
    rate_per_hour REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    project_uid TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE,
    notes TEXT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    hourly_rate_override REAL NULL,
    filament_cost_per_kg_override REAL NULL,
    labor_only INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    job_uid TEXT NOT NULL UNIQUE,
    printer_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NULL,
    ended_at TEXT NULL,
    duration_seconds INTEGER NULL,
    paused_seconds_total REAL NULL,
    pause_count INTEGER NULL,
    runout_count INTEGER NULL,
    filament_mm REAL NULL,
    duration_hours REAL NULL,
    filament_meters REAL NULL,
    rate_per_hour REAL NULL,
    filament_mode TEXT NULL,
    filament_rate REAL NULL,
    grams_per_meter REAL NULL,
    time_cost REAL NULL,
    material_cost REAL NULL,
    total_cost REAL NULL,
    filament_profile_id TEXT NULL,
    filament_material TEXT NULL,
    failure_reason TEXT NULL,
    import_source TEXT NULL,
    import_id TEXT NULL,
    job_outcome TEXT NULL,
    duration_seconds_raw REAL NULL,
    duration_seconds_est REAL NULL,
    duration_seconds_effective REAL NULL,
    filament_mm_raw REAL NULL,
    filament_mm_est REAL NULL,
    filament_mm_effective REAL NULL,
    thumbnail TEXT NULL,
    override_rate_per_hour REAL NULL,
    override_material_cost REAL NULL,
    override_total_cost REAL NULL,
    hourly_rate_profile_id TEXT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (printer_id) REFERENCES printers(id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_printer_time ON jobs(printer_id, ended_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_filename ON jobs(filename);

CREATE TABLE IF NOT EXISTS project_assignments (
    project_id INTEGER NOT NULL,
    job_uid TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, job_uid),
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (job_uid) REFERENCES jobs(job_uid)
);

CREATE TABLE IF NOT EXISTS user_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    type TEXT NOT NULL,
    printer_id INTEGER NULL,
    job_uid TEXT NULL,
    project_id INTEGER NULL,
    payload_json TEXT NULL,
    FOREIGN KEY (printer_id) REFERENCES printers(id),
    FOREIGN KEY (project_id) REFERENCES projects(id)
);
