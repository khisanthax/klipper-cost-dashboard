CREATE TABLE IF NOT EXISTS filament_spools (
    spool_uid TEXT PRIMARY KEY,
    manufacturer TEXT NOT NULL,
    material TEXT NOT NULL,
    color TEXT NOT NULL,
    net_mg INTEGER NOT NULL CHECK (net_mg > 0),
    remaining_mg INTEGER NOT NULL CHECK (remaining_mg BETWEEN 0 AND net_mg),
    low_stock_mg INTEGER NOT NULL CHECK (low_stock_mg BETWEEN 0 AND net_mg),
    purchase_cents INTEGER CHECK (purchase_cents >= 0),
    purchased_on TEXT,
    grams_per_meter REAL CHECK (grams_per_meter > 0),
    printer_id INTEGER REFERENCES printers(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS filament_stock_events (
    event_uid TEXT PRIMARY KEY,
    spool_uid TEXT NOT NULL REFERENCES filament_spools(spool_uid) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('opening', 'adjustment', 'verified_consumption')),
    delta_mg INTEGER NOT NULL,
    job_uid TEXT,
    job_filename TEXT,
    reason TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    CHECK ((kind = 'verified_consumption' AND job_uid IS NOT NULL AND delta_mg < 0)
        OR (kind != 'verified_consumption' AND job_uid IS NULL)),
    UNIQUE (spool_uid, job_uid)
);

CREATE INDEX IF NOT EXISTS idx_filament_stock_events_spool
    ON filament_stock_events(spool_uid, recorded_at);
