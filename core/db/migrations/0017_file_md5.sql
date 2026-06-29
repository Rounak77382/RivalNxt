-- Migration 0017: Add file_md5 column to local_downloads
-- Stores the MD5 hash of non-conforming archive files so they can be looked
-- up against the Nexus API without re-hashing on every run.
ALTER TABLE local_downloads ADD COLUMN file_md5 TEXT;
CREATE INDEX IF NOT EXISTS idx_local_downloads_file_md5 ON local_downloads(file_md5);
