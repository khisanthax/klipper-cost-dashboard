CREATE TABLE IF NOT EXISTS report_cache (
    key TEXT NOT NULL,
    range_key TEXT NOT NULL,
    backend_version INTEGER NOT NULL DEFAULT 1,
    jobs_fingerprint TEXT NOT NULL,
    generated_at INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (key, range_key, backend_version, jobs_fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_report_cache_lookup
    ON report_cache (key, range_key, backend_version, jobs_fingerprint);

CREATE INDEX IF NOT EXISTS idx_report_cache_generated
    ON report_cache (generated_at);

-- Reports query helpers
CREATE INDEX IF NOT EXISTS idx_jobs_ended_at
    ON jobs(ended_at);

CREATE INDEX IF NOT EXISTS idx_jobs_created_at
    ON jobs(created_at);

CREATE INDEX IF NOT EXISTS idx_jobs_printer_ended_at
    ON jobs(printer_id, ended_at);

CREATE INDEX IF NOT EXISTS idx_jobs_status_ended_at
    ON jobs(status, ended_at);
