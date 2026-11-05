CREATE TABLE IF NOT EXISTS filament_spool_archives (
    spool_uid TEXT PRIMARY KEY REFERENCES filament_spools(spool_uid) ON DELETE RESTRICT,
    archived_at TEXT,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filament_consumption_corrections (
    event_uid TEXT PRIMARY KEY REFERENCES filament_stock_events(event_uid) ON DELETE RESTRICT,
    original_event_uid TEXT NOT NULL REFERENCES filament_stock_events(event_uid) ON DELETE RESTRICT,
    previous_measured_mg INTEGER NOT NULL CHECK (previous_measured_mg >= 0),
    measured_mg INTEGER NOT NULL CHECK (measured_mg >= 0),
    CHECK (previous_measured_mg != measured_mg)
);
CREATE INDEX IF NOT EXISTS idx_filament_corrections_original ON filament_consumption_corrections(original_event_uid);

CREATE TABLE IF NOT EXISTS filament_consumption_estimates (
    operation_uid TEXT PRIMARY KEY,
    spool_uid TEXT NOT NULL REFERENCES filament_spools(spool_uid) ON DELETE RESTRICT,
    job_uid TEXT NOT NULL,
    job_filename TEXT NOT NULL,
    estimated_mg INTEGER NOT NULL CHECK (estimated_mg >= 0),
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_filament_estimates_spool_job ON filament_consumption_estimates(spool_uid, job_uid, recorded_at);

CREATE TABLE IF NOT EXISTS filament_spool_lifecycle_events (
    operation_uid TEXT PRIMARY KEY,
    spool_uid TEXT NOT NULL REFERENCES filament_spools(spool_uid) ON DELETE RESTRICT,
    archived INTEGER NOT NULL CHECK (archived IN (0, 1)),
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
