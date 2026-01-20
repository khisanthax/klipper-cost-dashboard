CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    severity TEXT NULL,
    meta_json TEXT NULL
);

CREATE INDEX IF NOT EXISTS idx_system_events_ts ON system_events(ts);
