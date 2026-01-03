ALTER TABLE printers ADD COLUMN external_id TEXT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_printers_external_id
    ON printers(external_id);
