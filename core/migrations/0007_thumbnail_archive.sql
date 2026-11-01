CREATE TABLE IF NOT EXISTS thumbnail_assets (
    sha256 TEXT PRIMARY KEY,
    extension TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_thumbnails (
    job_uid TEXT PRIMARY KEY REFERENCES jobs(job_uid) ON DELETE CASCADE,
    asset_sha256 TEXT REFERENCES thumbnail_assets(sha256),
    status TEXT NOT NULL,
    source_url TEXT,
    source_filename TEXT,
    source_modified REAL,
    provenance TEXT,
    checked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS job_thumbnails_asset ON job_thumbnails(asset_sha256);
