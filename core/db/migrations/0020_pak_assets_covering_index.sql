-- Migration 0020: covering index for the conflict-detection aggregate.
--
-- pak_assets has PRIMARY KEY(pak_name, asset_path) plus a single-column index on
-- asset_path. The conflict rebuild's base CTE groups by asset_path and counts
-- DISTINCT pak_name:
--
--   FROM pak_assets pa JOIN mod_paks mp ON mp.pak_name = pa.pak_name
--   GROUP BY pa.asset_path
--
-- With only idx_pak_assets_asset_path, SQLite scans that index and then fetches
-- pak_name from the table for every row:
--     SCAN pa USING INDEX idx_pak_assets_asset_path
-- With (asset_path, pak_name) both columns come from the index alone:
--     SCAN pa USING COVERING INDEX idx_pak_assets_asset_pak
CREATE INDEX IF NOT EXISTS idx_pak_assets_asset_pak
    ON pak_assets(asset_path, pak_name);

-- Now redundant: asset_path is the leading column of the new index, so
-- "WHERE asset_path = ?" lookups (get_asset_conflict_detail, the participant
-- queries) are still satisfied by an indexed seek -- a covering one, in fact.
-- Keeping both would just add write amplification on every pak_assets insert.
DROP INDEX IF EXISTS idx_pak_assets_asset_path;

-- Refresh sqlite_stat1 so the planner costs the new index correctly.
ANALYZE;
