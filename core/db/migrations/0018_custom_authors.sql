-- Migration: Custom authors table and per-mod metadata overrides
-- custom_authors holds shared author records (Nexus or user-created).
-- local_mod_metadata links a mod (by mod_key) to an author and stores
-- an extra_json blob for future extensible fields (title, description, etc.)

CREATE TABLE IF NOT EXISTS custom_authors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name_normalized TEXT NOT NULL UNIQUE,   -- lower-trimmed display name (lookup key)
    display_name    TEXT NOT NULL,          -- original casing shown in UI
    author_type     TEXT NOT NULL DEFAULT 'custom',  -- 'nexus' | 'custom'
    nexus_member_id INTEGER,                -- populated when author_type = 'nexus'
    avatar_base64   TEXT,                  -- base64-encoded image data URI (may be NULL)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_custom_authors_name ON custom_authors(name_normalized);

-- local_mod_metadata: one row per logical mod group.
-- mod_key format: "mod:<nexus_mod_id>" | "local:<local_download_id>"
CREATE TABLE IF NOT EXISTS local_mod_metadata (
    mod_key          TEXT PRIMARY KEY,
    custom_author_id INTEGER REFERENCES custom_authors(id) ON DELETE SET NULL,
    extra_json       TEXT,   -- JSON blob for future per-mod overrides (title, url, etc.)
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_local_mod_metadata_author ON local_mod_metadata(custom_author_id);
