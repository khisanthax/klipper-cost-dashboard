ALTER TABLE filament_profiles ADD COLUMN price_input_mode TEXT;
ALTER TABLE filament_profiles ADD COLUMN conversion_basis TEXT;
ALTER TABLE filament_profiles ADD COLUMN spool_price REAL;
ALTER TABLE filament_profiles ADD COLUMN net_spool_grams REAL;
ALTER TABLE filament_profiles ADD COLUMN density_g_cm3 REAL;
ALTER TABLE filament_profiles ADD COLUMN diameter_mm REAL;
ALTER TABLE jobs ADD COLUMN filament_grams REAL;
ALTER TABLE jobs ADD COLUMN filament_weight_kind TEXT;
ALTER TABLE jobs ADD COLUMN filament_density REAL;
ALTER TABLE jobs ADD COLUMN filament_diameter REAL;
ALTER TABLE jobs ADD COLUMN billable_hours REAL;
ALTER TABLE projects ADD COLUMN filament_price_json TEXT;
CREATE TABLE IF NOT EXISTS job_cost_originals (
    job_uid TEXT PRIMARY KEY REFERENCES jobs(job_uid) ON DELETE CASCADE,
    original_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_job_pricing (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    job_uid TEXT NOT NULL REFERENCES jobs(job_uid) ON DELETE CASCADE,
    calculation_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(project_id,job_uid)
);
