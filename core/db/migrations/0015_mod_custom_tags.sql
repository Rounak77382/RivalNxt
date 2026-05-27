-- Migration 0015: add mod_custom_tags table for user-defined tags per mod
CREATE TABLE IF NOT EXISTS mod_custom_tags (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    mod_id   INTEGER NOT NULL,
    tag      TEXT    NOT NULL COLLATE NOCASE,
    added_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(mod_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_mod_custom_tags_mod_id ON mod_custom_tags(mod_id);
