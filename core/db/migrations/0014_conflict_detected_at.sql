-- Migration 0014: Add detected_at timestamp to materialized conflict tables
-- Preserves when a conflict was first detected, enabling chronological sorting.

ALTER TABLE asset_conflicts ADD COLUMN detected_at TEXT DEFAULT '';
ALTER TABLE asset_conflicts_active ADD COLUMN detected_at TEXT DEFAULT '';

-- Backfill existing rows from generated_at
UPDATE asset_conflicts SET detected_at = COALESCE(generated_at, datetime('now'));
UPDATE asset_conflicts_active SET detected_at = COALESCE(generated_at, datetime('now'));

INSERT OR IGNORE INTO schema_migrations(version) VALUES ('0014_conflict_detected_at');
