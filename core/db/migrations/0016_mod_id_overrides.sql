CREATE TABLE IF NOT EXISTS mod_id_overrides (
    local_path   TEXT PRIMARY KEY,
    nexus_mod_id INTEGER NOT NULL,
    confirmed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mod_id_overrides_nexus ON mod_id_overrides(nexus_mod_id);

ALTER TABLE local_downloads ADD COLUMN needs_manual_mod_id INTEGER NOT NULL DEFAULT 0;
ALTER TABLE local_downloads ADD COLUMN rename_status TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE local_downloads ADD COLUMN rename_error TEXT;
