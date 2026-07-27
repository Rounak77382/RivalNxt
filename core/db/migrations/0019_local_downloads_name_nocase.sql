-- Migration 0019: case-insensitive index on local_downloads.name
--
-- _find_duplicate_download (core/api/server.py) matched with
--   WHERE LOWER(name) = LOWER(?)
-- Wrapping the column in LOWER() makes the predicate non-sargable, so
-- idx_local_downloads_name could not be used and every duplicate check
-- degraded to a full scan of local_downloads. The query now uses
--   WHERE name = ? COLLATE NOCASE
-- which requires a matching NOCASE index to be satisfied by a seek.
CREATE INDEX IF NOT EXISTS idx_local_downloads_name_nocase
    ON local_downloads(name COLLATE NOCASE);

-- Present since migration 0017, restated here so a DB that somehow skipped
-- it still gets the index that backs MD5-based duplicate detection.
CREATE INDEX IF NOT EXISTS idx_local_downloads_file_md5
    ON local_downloads(file_md5);

-- Give the query planner real statistics; without sqlite_stat1 SQLite falls
-- back to heuristics and can still prefer a scan on small-but-growing tables.
ANALYZE;
