from __future__ import annotations

import sqlite3, json, re, glob, os, sys
from importlib import resources
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union
from pathlib import Path

from core.utils.download_paths import normalize_download_path
from core.utils.pak_files import collapse_pak_bundle

DB_FILENAME = "mods.db"
_DB_PATH_LOGGED = False

def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]

def _data_root() -> Path:
    """Get the data directory, always reading fresh from settings.
    
    This ensures we pick up any runtime configuration changes made via configure().
    """
    from core.config.settings import SETTINGS
    
    candidate = SETTINGS.data_dir
    if candidate:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return candidate
    if getattr(sys, "frozen", False):
        exe_path = Path(sys.executable).resolve()
        data_dir = exe_path.parent
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return data_dir
    return _project_root()

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    if not db_path:
        db_path = str(_data_root() / DB_FILENAME)
    global _DB_PATH_LOGGED
    if not _DB_PATH_LOGGED:
        print(f"Connecting to SQLite at {db_path}")
        _DB_PATH_LOGGED = True
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA busy_timeout = 5000;")
    # Pragmas tuned for local app usage
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_schema(conn: sqlite3.Connection) -> None:
    """Create tables and views if they do not already exist."""
    cur = conn.cursor()
    # Core tables
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS local_downloads (
            path TEXT PRIMARY KEY,
            id INTEGER NOT NULL,
            name TEXT NOT NULL,
            mod_id INTEGER,
            version TEXT,
            contents TEXT,
            active_paks TEXT,
            last_activated_at TEXT,
            last_deactivated_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_local_downloads_id_unique ON local_downloads(id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_local_downloads_mod_id ON local_downloads(mod_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_local_downloads_name ON local_downloads(name);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mods (
            mod_id INTEGER PRIMARY KEY,
            game TEXT NOT NULL,
            name TEXT,
            summary TEXT,
            description_bbcode TEXT,
            description_html TEXT,
            author TEXT,
            author_profile_url TEXT,
            author_member_id INTEGER,
            version TEXT,
            updated_at TEXT,
            created_time TEXT,
            created_timestamp INTEGER,
            updated_timestamp INTEGER,
            picture_url TEXT,
            contains_adult_content INTEGER,
            status TEXT,
            available INTEGER,
            category_id INTEGER,
            mod_downloads INTEGER,
            mod_unique_downloads INTEGER,
            endorsement_count INTEGER
        );
        """
    )
    # Removed mod_descriptions table as mod descriptions are no longer used
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_files (
            mod_id INTEGER NOT NULL,
            file_id INTEGER NOT NULL,
            name TEXT,
            version TEXT,
            category TEXT,
            size_in_bytes INTEGER,
            is_primary INTEGER,
            uploaded_at TEXT,
            -- description TEXT,  # removed, not used
            version_key TEXT,
            v_maj INTEGER,
            v_min INTEGER,
            v_patch INTEGER,
            v_build INTEGER,
            PRIMARY KEY (mod_id, file_id),
            FOREIGN KEY(mod_id) REFERENCES mods(mod_id) ON DELETE CASCADE
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mod_files_mod_id ON mod_files(mod_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mod_files_version ON mod_files(version);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mod_files_version_key ON mod_files(version_key);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_changelogs (
            mod_id INTEGER NOT NULL,
            version TEXT NOT NULL,
            changelog TEXT,
            uploaded_at TEXT,
            PRIMARY KEY (mod_id, version),
            FOREIGN KEY(mod_id) REFERENCES mods(mod_id) ON DELETE CASCADE
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mod_changelogs_mod_id ON mod_changelogs(mod_id);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_api_cache (
            mod_id INTEGER PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_asset_paths (
            source_zip TEXT PRIMARY KEY,
            mod_id INTEGER,
            assets_json TEXT NOT NULL,
            io_store INTEGER,
            FOREIGN KEY(mod_id) REFERENCES mods(mod_id) ON DELETE CASCADE
        );
        """
    )
    # New per-pak schema (non-destructive addition). Each pak holds its own assets.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mod_paks (
            pak_name TEXT PRIMARY KEY,
            mod_id INTEGER,
            source_zip TEXT,
            local_download_id INTEGER,
            io_store INTEGER,
            first_seen_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY(mod_id) REFERENCES mods(mod_id) ON DELETE SET NULL,
            FOREIGN KEY(local_download_id) REFERENCES local_downloads(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mod_paks_mod_id ON mod_paks(mod_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_mod_paks_local_download ON mod_paks(local_download_id);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pak_assets (
            pak_name TEXT NOT NULL,
            asset_path TEXT NOT NULL,
            PRIMARY KEY(pak_name, asset_path),
            FOREIGN KEY(pak_name) REFERENCES mod_paks(pak_name) ON DELETE CASCADE
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_pak_assets_asset_path ON pak_assets(asset_path);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pak_assets_json (
            pak_name TEXT PRIMARY KEY,
            mod_id INTEGER,
            assets_json TEXT NOT NULL,
            FOREIGN KEY(pak_name) REFERENCES mod_paks(pak_name) ON DELETE CASCADE,
            FOREIGN KEY(mod_id) REFERENCES mods(mod_id) ON DELETE SET NULL
        );
        """
    )
    # Materialized conflict tables (used by Settings maintenance and conflict views)
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_conflicts (
            asset_path TEXT PRIMARY KEY,
            distinct_mods INTEGER NOT NULL,
            distinct_paks INTEGER NOT NULL,
            generated_at TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_conflicts_mods ON asset_conflicts(distinct_mods DESC);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_conflict_participants (
            asset_path TEXT NOT NULL,
            pak_name TEXT NOT NULL,
            mod_id INTEGER,
            source_zip TEXT,
            PRIMARY KEY(asset_path, pak_name),
            FOREIGN KEY(pak_name) REFERENCES mod_paks(pak_name) ON DELETE CASCADE
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_participants_asset_path ON asset_conflict_participants(asset_path);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_participants_mod_id ON asset_conflict_participants(mod_id);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_conflicts_active (
            asset_path TEXT PRIMARY KEY,
            distinct_mods INTEGER NOT NULL,
            distinct_paks INTEGER NOT NULL,
            generated_at TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_conflicts_active_mods ON asset_conflicts_active(distinct_mods DESC);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_conflict_participants_active (
            asset_path TEXT NOT NULL,
            pak_name TEXT NOT NULL,
            mod_id INTEGER,
            source_zip TEXT,
            PRIMARY KEY(asset_path, pak_name)
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_participants_active_asset_path ON asset_conflict_participants_active(asset_path);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_participants_active_mod_id ON asset_conflict_participants_active(mod_id);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS asset_tags (
            asset_path TEXT PRIMARY KEY,
            entity     TEXT,
            category   TEXT NOT NULL,
            tag        TEXT NOT NULL
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_tags_category ON asset_tags(category);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_asset_tags_entity ON asset_tags(entity);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pak_tags_json (
            pak_name  TEXT PRIMARY KEY,
            mod_id    INTEGER,
            tags_json TEXT NOT NULL,
            FOREIGN KEY(pak_name) REFERENCES mod_paks(pak_name) ON DELETE CASCADE,
            FOREIGN KEY(mod_id) REFERENCES mods(mod_id) ON DELETE SET NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    # ── Collections ─────────────────────────────────────────────────────────
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS collections (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            slug          TEXT NOT NULL,
            nexus_id      INTEGER,
            revision_id   INTEGER,
            revision_num  INTEGER,
            game          TEXT NOT NULL DEFAULT 'marvelrivals',
            name          TEXT,
            summary       TEXT,
            picture_url   TEXT,
            author        TEXT,
            total_mods    INTEGER,
            total_size    INTEGER,
            status        TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            fetched_at    TEXT NOT NULL DEFAULT (datetime('now')),
            raw_json      TEXT
        );
        """
    )
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_collections_slug_rev ON collections(slug, COALESCE(revision_num, -1));")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_mod_files (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id   INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
            entry_id        TEXT,
            file_id         INTEGER NOT NULL,
            mod_id          INTEGER,
            optional        INTEGER NOT NULL DEFAULT 0,
            version         TEXT,
            file_name       TEXT,
            file_uri        TEXT,
            size_in_bytes   INTEGER,
            mod_name        TEXT,
            picture_url     TEXT,
            download_state  TEXT NOT NULL DEFAULT 'pending'
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cmf_collection ON collection_mod_files(collection_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cmf_file_id    ON collection_mod_files(file_id);")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_cmf_mod_id     ON collection_mod_files(mod_id);")
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS handoff_failures (
            file_id         TEXT PRIMARY KEY,
            mod_id          INTEGER,
            error_message   TEXT,
            retry_count     INTEGER DEFAULT 0,
            last_attempt_at TEXT,
            handoff_id      TEXT
        );
        """
    )
    cur.execute("CREATE INDEX IF NOT EXISTS idx_handoff_failures_handoff_id ON handoff_failures(handoff_id);")
    conn.commit()
    run_migrations(conn)
    _init_views(conn)



def _normalize_datetime_hint(value: Any) -> Optional[str]:
    """Convert disparate timestamp representations into UTC ISO-8601 strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat()
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        try:
            return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Numeric string timestamp
        if s.isdigit():
            try:
                return datetime.fromtimestamp(int(s), timezone.utc).isoformat()
            except Exception:
                return None
        # Common ISO variants (support trailing 'Z')
        iso_candidate = s.replace("Z", "+00:00") if "Z" in s and "+" not in s else s
        try:
            dt = datetime.fromisoformat(iso_candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat()
        except ValueError:
            pass
        # Legacy Nexus style "YYYY-MM-DD HH:MM:SS"
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
            try:
                dt = datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue
    return None


def _path_mtime_iso(candidate: Optional[Union[str, Path]]) -> Optional[str]:
    if not candidate:
        return None
    try:
        p = Path(candidate)
        if not p.exists():
            return None
        mtime = p.stat().st_mtime
        return datetime.fromtimestamp(mtime, timezone.utc).isoformat()
    except Exception:
        return None


def resolve_created_at(
    *,
    path: Optional[Union[str, Path]] = None,
    hints: Iterable[Any] = (),
) -> str:
    """Resolve a created_at timestamp preferring supplied hints over filesystem mtimes."""
    for hint in hints:
        iso = _normalize_datetime_hint(hint)
        if iso:
            return iso
    path_iso = _path_mtime_iso(path)
    if path_iso:
        return path_iso
    return datetime.now(timezone.utc).isoformat()

_MEMBER_ID_RE = re.compile(r"(\d+)(?:\D*$)")


def _extract_member_id(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            num = int(value)
        except (TypeError, ValueError):
            return None
        return num if num >= 0 else None
    s = str(value).strip()
    if not s:
        return None
    match = _MEMBER_ID_RE.search(s)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None

def _safe_url(href: str) -> str:
    href = (href or "").strip()
    if href.lower().startswith(("http://", "https://")):
        return href
    return "#"

from core.utils.bbcode_wrapper import bbcode_to_html

def sanitize_html(html: str) -> str:
    """Very basic server-side sanitizer: strip scripts/styles and event handlers,
    and neutralize javascript: URLs. The frontend also uses DOMPurify for robust sanitation.
    """
    if not html:
        return html
    # Remove script and style blocks
    cleaned = re.sub(r"<\s*(script|style)[^>]*>[\s\S]*?<\s*/\s*\1\s*>", "", html, flags=re.IGNORECASE)
    # Remove on*="..." event handlers
    cleaned = re.sub(r"\son[a-zA-Z]+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", cleaned, flags=re.IGNORECASE)
    # Neutralize javascript: in href/src
    def _rewrite_attr(m: re.Match) -> str:
        attr = m.group(1)
        val = m.group(2)
        q = ''
        v = val
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            q = val[0]
            v = val[1:-1]
        if v.strip().lower().startswith("javascript:"):
            v = "#"
        return f" {attr}={q}{v}{q}"
    cleaned = re.sub(r"\s(href|src)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", _rewrite_attr, cleaned, flags=re.IGNORECASE)
    return cleaned

def run_migrations(conn: sqlite3.Connection) -> None:
    """Apply .sql migration files in chronological order if not yet applied.

    Migration files live in core/db/migrations and should begin with a zero-padded
    ordinal (e.g. 0001_description.sql). We store applied versions in schema_migrations.
    """
    candidates: List[Path] = []
    root = _project_root() / 'core' / 'db' / 'migrations'
    candidates.append(root)
    if getattr(sys, 'frozen', False):
        meipass_raw = getattr(sys, '_MEIPASS', None)
        if meipass_raw:
            candidates.append(Path(meipass_raw) / 'core' / 'db' / 'migrations')
    entries: List[Tuple[str, str]] = []
    for candidate in candidates:
        if not candidate.exists():
            continue
        file_paths = sorted(glob.glob(str(candidate / '*.sql')))
        if not file_paths:
            continue
        for path in file_paths:
            version = Path(path).name.split('.')[0]
            sql_text = Path(path).read_text(encoding='utf-8')
            entries.append((version, sql_text))
        break
    if not entries:
        try:
            package_root = resources.files('core.db.migrations')
            for item in sorted(package_root.iterdir(), key=lambda p: p.name):
                if not item.name.endswith('.sql'):
                    continue
                version = item.name.split('.')[0]
                sql_text = item.read_text(encoding='utf-8')
                entries.append((version, sql_text))
        except (FileNotFoundError, ModuleNotFoundError, AttributeError):
            pass
    if not entries:
        return
    cur = conn.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    applied = {row[0] for row in cur.execute("SELECT version FROM schema_migrations").fetchall()}
    for version, sql_text in entries:
        if version in applied:
            continue
        try:
            cur.executescript(sql_text)
            # record applied version
            cur.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (version,))
            print(f"Applied migration {version}")
        except Exception as e:
            # If the migration attempted to add columns that already exist, mark as applied
            msg = str(e).lower()
            if "duplicate column name" in msg:
                try:
                    from re import finditer

                    add_column_statements = list(finditer(r"alter\s+table\s+(\w+)\s+add\s+column\s+(\w+)", sql_text, re.IGNORECASE))
                    if not add_column_statements and ("description_bbcode" in sql_text or "description_html" in sql_text):
                        add_column_statements = [
                            ("mods", "description_bbcode"),
                            ("mods", "description_html"),
                        ]
                    all_columns_exist = True
                    for match in add_column_statements:
                        table = match[0] if isinstance(match, tuple) else match.group(1)
                        column = match[1] if isinstance(match, tuple) else match.group(2)
                        existing = [r[1].lower() for r in cur.execute(f"PRAGMA table_info({table})").fetchall()]
                        if column.lower() not in existing:
                            all_columns_exist = False
                            break
                    if all_columns_exist:
                        cur.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (version,))
                        print(f"Marked migration {version} as applied (duplicate columns)")
                        conn.commit()
                        continue
                except Exception:
                    pass
            print(f"Failed migration {version}: {e}")
            conn.rollback()
            raise
    conn.commit()

def _init_views(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    # Latest file by uploaded timestamp (simple latest)
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_latest_file_per_mod AS
        SELECT mf.mod_id,
               mf.file_id,
               mf.name AS file_name,
               mf.version AS file_version,
               mf.category,
               mf.size_in_bytes,
               mf.is_primary,
               mf.uploaded_at
        FROM mod_files mf
        JOIN (
            SELECT mod_id, MAX(uploaded_at) AS max_uploaded_at
            FROM mod_files
            GROUP BY mod_id
        ) latest ON latest.mod_id = mf.mod_id AND latest.max_uploaded_at = mf.uploaded_at;
        """
    )
    # Latest file by semantic-ish version ordering (version_key) + tie break uploaded_at
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_mods_with_latest_by_version AS
        SELECT m.mod_id,
               ranked.file_id  AS latest_file_id,
               ranked.name     AS file_name,
               ranked.version  AS file_version,
               ranked.category AS file_category,
               ranked.size_in_bytes AS file_size_in_bytes,
               ranked.is_primary    AS latest_is_primary,
               ranked.uploaded_at   AS latest_uploaded_at,
               ranked.version_key   AS latest_version_key
        FROM mods m
        LEFT JOIN (
            SELECT file_id, mod_id, name, version, category, size_in_bytes, is_primary, uploaded_at, version_key
            FROM (
                SELECT mf.*, ROW_NUMBER() OVER (
                    PARTITION BY mod_id
                    ORDER BY COALESCE(version_key,'') DESC, uploaded_at DESC
                ) as rn
                FROM mod_files mf
            ) sub WHERE sub.rn = 1
        ) ranked ON ranked.mod_id = m.mod_id;
        """
    )
    # Backwards compatibility view similar to earlier naming (latest file row itself)
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_latest_file_by_version_per_mod AS
        SELECT mod_id, latest_file_id AS file_id, file_name, file_version, file_category AS category,
               file_size_in_bytes AS size_in_bytes, latest_is_primary AS is_primary,
               latest_uploaded_at AS uploaded_at, latest_version_key AS version_key
        FROM v_mods_with_latest_by_version;
        """
    )
    # Aggregate changelogs per-mod into a JSON array for easy consumption by
    # clients. Uses SQLite JSON1 functions (json_object, json_group_array).
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_mod_changelogs_json AS
        SELECT
            m.mod_id AS mod_id,
            (
                SELECT json_group_array(
                    json_object('version', version, 'changelog', changelog, 'uploaded_at', uploaded_at)
                )
                FROM (
                    SELECT version, changelog, uploaded_at
                    FROM mod_changelogs mc
                    WHERE mc.mod_id = m.mod_id
                    ORDER BY uploaded_at DESC, version DESC
                ) sub
            ) AS changelogs
        FROM mods m;
        """
    )
    # Local downloads without remote mod row
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_local_without_remote AS
        SELECT l.mod_id, COUNT(*) AS local_count
        FROM local_downloads l
        LEFT JOIN mods m ON m.mod_id = l.mod_id
        WHERE l.mod_id IS NOT NULL AND m.mod_id IS NULL
        GROUP BY l.mod_id;
        """
    )
    # Conflicts across all paks with JSON listing of involved providers (handles local-only mods)
    cur.execute("DROP VIEW IF EXISTS v_asset_conflicts_all;")
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_asset_conflicts_all AS
        WITH base AS (
            SELECT
                pa.asset_path,
                pa.pak_name,
                mp.mod_id,
                mp.source_zip,
                mp.local_download_id,
                CASE
                    WHEN mp.mod_id IS NOT NULL THEN CAST(mp.mod_id AS TEXT)
                    WHEN mp.local_download_id IS NOT NULL THEN 'local:' || CAST(mp.local_download_id AS TEXT)
                    WHEN mp.source_zip IS NOT NULL THEN 'zip:' || LOWER(mp.source_zip)
                    ELSE 'pak:' || LOWER(pa.pak_name)
                END AS provider_key
            FROM pak_assets pa
            JOIN mod_paks mp ON mp.pak_name = pa.pak_name
        ),
        agg AS (
            SELECT
                asset_path,
                COUNT(DISTINCT provider_key) AS mod_count,
                COUNT(DISTINCT pak_name) AS pak_count,
                json_group_array(
                    DISTINCT json_object(
                        'pak_name', pak_name,
                        'source_zip', source_zip,
                        'mod_id', mod_id,
                        'local_download_id', local_download_id
                    )
                ) AS conflict_paks_json
            FROM base
            GROUP BY asset_path
            HAVING mod_count > 1
        )
        SELECT
            agg.asset_path,
            agg.mod_count,
            agg.pak_count,
            agg.conflict_paks_json,
            ac.detected_at
        FROM agg
        LEFT JOIN asset_conflicts ac ON ac.asset_path = agg.asset_path;
        """
    )
    # Active conflicts view (join active paks) with JSON listing
    cur.execute("DROP VIEW IF EXISTS v_asset_conflicts_active;")
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_asset_conflicts_active AS
        -- Fast read from pre-built tables (populated by rebuild_conflicts).
        -- Avoids recomputing the full active-pak analysis on every request.
        SELECT
            ac.asset_path,
            ac.distinct_mods  AS mod_count,
            ac.distinct_paks  AS pak_count,
            (
                SELECT json_group_array(json_object(
                    'pak_name',        acp.pak_name,
                    'source_zip',      acp.source_zip,
                    'mod_id',          acp.mod_id,
                    'local_download_id', mp.local_download_id
                ))
                FROM   asset_conflict_participants_active acp
                LEFT JOIN mod_paks mp ON mp.pak_name = acp.pak_name
                WHERE  acp.asset_path = ac.asset_path
            ) AS conflict_paks_json,
            ac.detected_at
        FROM asset_conflicts_active ac;
        """
    )
    # Local downloads with consolidated tags from pak_tags_json (NULL when none)
    cur.execute("DROP VIEW IF EXISTS v_local_downloads_with_tags;")
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_local_downloads_with_tags AS
        WITH candidates AS (
            SELECT l.id AS download_id, LOWER(TRIM(value)) AS pak_name
            FROM local_downloads l, json_each(l.contents)
            UNION
            SELECT mp.local_download_id AS download_id, LOWER(mp.pak_name) AS pak_name
            FROM mod_paks mp
            WHERE mp.local_download_id IS NOT NULL
        ), tags_raw AS (
            -- Exact match on pak_name
            SELECT c.download_id, pt.tags_json
            FROM candidates c
            JOIN pak_tags_json pt ON LOWER(pt.pak_name) = c.pak_name
            UNION
            -- Alternate extension: .pak -> .utoc
            SELECT c.download_id, pt.tags_json
            FROM candidates c
            JOIN pak_tags_json pt
              ON LOWER(pt.pak_name) = (SUBSTR(c.pak_name, 1, LENGTH(c.pak_name) - 4) || '.utoc')
            WHERE c.pak_name LIKE '%.pak'
            UNION
            -- Alternate extension: .utoc -> .pak
            SELECT c.download_id, pt.tags_json
            FROM candidates c
            JOIN pak_tags_json pt
              ON LOWER(pt.pak_name) = (SUBSTR(c.pak_name, 1, LENGTH(c.pak_name) - 5) || '.pak')
            WHERE c.pak_name LIKE '%.utoc'
        ), expanded AS (
            SELECT download_id, value AS tag_str
            FROM tags_raw, json_each(tags_raw.tags_json)
        )
        SELECT
            l.id   AS download_id,
            l.name AS download_name,
            l.path AS local_path,
            (
                SELECT CASE WHEN EXISTS (SELECT 1 FROM expanded e WHERE e.download_id = l.id)
                            THEN (SELECT json_group_array(tag_str)
                                  FROM expanded e2 WHERE e2.download_id = l.id)
                            ELSE NULL END
            ) AS tags_json
        FROM local_downloads l;
        """
    )
    cur.execute(
        """
        CREATE VIEW IF NOT EXISTS v_mod_pak_version_status AS
        WITH latest_mod_files AS (
            SELECT
                mod_id,
                REPLACE(REPLACE(REPLACE(LOWER(name), ' ', ''), '-', ''), '_', '') AS key_name,
                file_id,
                version,
                uploaded_at,
                ROW_NUMBER() OVER (
                    PARTITION BY mod_id, REPLACE(REPLACE(REPLACE(LOWER(name), ' ', ''), '-', ''), '_', '')
                    ORDER BY uploaded_at DESC, file_id DESC
                ) AS rn
            FROM mod_files
        )
        SELECT
            mp.pak_name,
            mp.mod_id,
            mp.source_zip,
            mp.local_download_id,
            ld.path AS local_path,
            ld.name AS local_name,
            ld.version AS local_version,
            mf.file_id AS reference_file_id,
            mf.version AS reference_version,
            CASE
                WHEN ld.version IS NULL OR ld.version = '' THEN 'missing_local_version'
                WHEN mf.version IS NULL OR mf.version = '' THEN 'missing_remote_version'
                WHEN LOWER(ld.version) = LOWER(mf.version) THEN 'match'
                ELSE 'mismatch'
            END AS version_status,
            CASE
                WHEN ld.version IS NULL OR ld.version = '' THEN 1
                WHEN mf.version IS NULL OR mf.version = '' THEN 0
                WHEN LOWER(ld.version) = LOWER(mf.version) THEN 0
                ELSE 1
            END AS needs_update
        FROM mod_paks mp
        LEFT JOIN local_downloads ld ON ld.id = mp.local_download_id
        LEFT JOIN latest_mod_files mf
            ON mf.mod_id = mp.mod_id
            AND mf.rn = 1
            AND (
                mf.key_name = REPLACE(REPLACE(REPLACE(LOWER(mp.source_zip), ' ', ''), '-', ''), '_', '')
                OR (ld.name IS NOT NULL AND mf.key_name = REPLACE(REPLACE(REPLACE(LOWER(ld.name), ' ', ''), '-', ''), '_', ''))
            )
        LEFT JOIN v_mods_with_latest_by_version latest ON latest.mod_id = mp.mod_id;
        """
    )
    conn.commit()

def make_version_key(version: Optional[str]) -> Tuple[Optional[str], Optional[int], Optional[int], Optional[int], Optional[int]]:
    if not version or not isinstance(version, str):
        return None, None, None, None, None

    parts = [p for p in re.split(r"[^0-9]+", version) if p]
    if not parts:
        return None, None, None, None, None

    nums: List[int] = []
    for p in parts[:4]:
        try:
            nums.append(int(p))
        except Exception:
            nums.append(0)
    while len(nums) < 4:
        nums.append(0)

    vmaj, vmin, vpatch, vbuild = nums[:4]

    # Some local archive versions encode a Unix timestamp in the third segment
    # (e.g. "2.0.1743611945"). When we detect a very large third segment and
    # no explicit fourth component, treat it as the build number to keep the
    # semantic major/minor/patch comparison aligned with Nexus values such as
    # "2.0" or "3.5".
    if len(parts) == 3 and len(parts[2]) >= 7:
        if vbuild == 0:
            vbuild = vpatch
        vpatch = 0

    key = f"{vmaj:010d}.{vmin:010d}.{vpatch:010d}.{vbuild:010d}"
    return key, vmaj, vmin, vpatch, vbuild


def versions_equivalent(local: Optional[str], reference: Optional[str]) -> bool:
    if not local or not reference:
        return False

    def _trim(value: str) -> str:
        return value.strip().lower().lstrip("v")

    local_trimmed = _trim(str(local))
    reference_trimmed = _trim(str(reference))
    if not local_trimmed or not reference_trimmed:
        return False

    if local_trimmed == reference_trimmed:
        return True

    # New logic: compare components up to the minimum number of parts present in either.
    # This ensures that "2" and "2.177.1" match, and "1.2.123" and "1.2" match,
    # while "1.2" and "1.3" remain different.
    def _get_parts_count(v: str) -> int:
        return len([p for p in re.split(r"[^0-9]+", v) if p])

    l_count = _get_parts_count(local_trimmed)
    r_count = _get_parts_count(reference_trimmed)

    # Use existing make_version_key to get normalized numeric components (handles timestamp shifting)
    _, l_maj, l_min, l_patch, l_build = make_version_key(local_trimmed)
    _, r_maj, r_min, r_patch, r_build = make_version_key(reference_trimmed)

    l_nums = [l_maj, l_min, l_patch, l_build]
    r_nums = [r_maj, r_min, r_patch, r_build]

    # Compare up to the length of the version string that has fewer parts (capped at 4)
    num_to_compare = min(l_count, r_count, 4)
    if num_to_compare == 0:
        return False

    for i in range(num_to_compare):
        if l_nums[i] != r_nums[i]:
            return False

    return True

def replace_local_downloads(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    cur = conn.cursor()
    existing: Dict[str, int] = {}
    for path, ident in cur.execute("SELECT path, id FROM local_downloads;"):
        if isinstance(path, str) and ident is not None:
            existing[path] = int(ident)
    inserted = 0
    seen_paths: Set[str] = set()
    max_id_row = cur.execute("SELECT COALESCE(MAX(id), 0) FROM local_downloads;").fetchone()
    max_id = int(max_id_row[0]) if max_id_row and max_id_row[0] else 0
    for row in rows:
        name = row.get("name")
        mod_id_val = row.get("modID") or row.get("mod_id") or None
        try:
            mod_id_int: Optional[int] = int(mod_id_val) if mod_id_val else None
        except (TypeError, ValueError):
            mod_id_int = None
        version = row.get("version") or None
        raw_path = row.get("path") or ""
        path = normalize_download_path(raw_path)
        if not path:
            continue
        seen_paths.add(path)
        contents = row.get("contents")
        if contents is None:
            contents_json = None
        else:
            parsed_contents: List[str] = []
            if isinstance(contents, str):
                try:
                    maybe_list = json.loads(contents)
                except Exception:
                    maybe_list = contents
                if isinstance(maybe_list, list):
                    parsed_contents = [str(item) for item in maybe_list if isinstance(item, str)]
                elif isinstance(maybe_list, str):
                    parsed_contents = [maybe_list]
            elif isinstance(contents, list):
                parsed_contents = [str(item) for item in contents if isinstance(item, str)]
            else:
                try:
                    parsed_contents = [str(item) for item in contents if isinstance(item, str)]  # type: ignore[arg-type]
                except Exception:
                    parsed_contents = []
            collapsed_contents = collapse_pak_bundle(parsed_contents)
            try:
                contents_json = json.dumps(collapsed_contents, ensure_ascii=False)
            except Exception:
                contents_json = "[]"
        active_paks = row.get("active_paks")
        if active_paks is None:
            active_paks_json = "[]"
        else:
            if isinstance(active_paks, str):
                active_paks_json = active_paks
            else:
                try:
                    active_paks_json = json.dumps(active_paks, ensure_ascii=False)
                except Exception:
                    active_paks_json = "[]"
        raw_id = row.get("id")
        if raw_id is not None:
            try:
                assigned_id = int(raw_id)
            except (TypeError, ValueError):
                assigned_id = None
        else:
            assigned_id = None
        if assigned_id is None:
            assigned_id = existing.get(path)
        if assigned_id is None:
            max_id += 1
            assigned_id = max_id
        # Resolve a filesystem candidate so we can inspect modification times when available
        fs_path: Optional[Path] = None
        abs_path_val = row.get("absolute_path")
        if isinstance(abs_path_val, str) and abs_path_val.strip():
            try:
                candidate_path = Path(abs_path_val.strip())
                if candidate_path.exists():
                    fs_path = candidate_path
            except Exception:
                fs_path = None
        try:
            p = Path(path)
            candidates: List[Path] = [p]
            # If the normalized path is not an absolute path on disk, try known
            # download roots (configured or guessed) so we can locate the real
            # archive/folder and use its modification time.
            if not p.exists():
                try:
                    from core.utils.download_paths import known_download_roots

                    for root in known_download_roots():
                        candidates.append(Path(root) / path)
                except Exception:
                    # If anything goes wrong importing or iterating roots,
                    # fall back to the single candidate above.
                    pass

            for candidate in candidates:
                try:
                    if candidate.exists():
                        fs_path = candidate
                        break
                except Exception:
                    continue
        except Exception:
            fs_path = None

        created_at_hints = [
            row.get("created_at"),
            row.get("createdAt"),
            row.get("uploaded_at"),
            row.get("uploadedAt"),
            row.get("uploaded_time"),
            row.get("uploadedTime"),
            row.get("uploaded_timestamp"),
            row.get("uploadedTimestamp"),
        ]
        created_at_iso = resolve_created_at(path=fs_path, hints=created_at_hints)

        cur.execute(
            """
            INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                id=excluded.id,
                name=excluded.name,
                mod_id=excluded.mod_id,
                version=excluded.version,
                contents=excluded.contents,
                active_paks=excluded.active_paks,
                created_at=COALESCE(excluded.created_at, local_downloads.created_at)
            ;
            """,
            (path, assigned_id, name, mod_id_int, version, contents_json, active_paks_json, created_at_iso),
        )
        existing[path] = assigned_id
        inserted += 1
    stale_paths = [p for p in existing.keys() if p not in seen_paths]
    if stale_paths:
        placeholders = ",".join("?" for _ in stale_paths)
        cur.execute(f"DELETE FROM local_downloads WHERE path IN ({placeholders});", tuple(stale_paths))
    conn.commit()
    return inserted

def fetch_pak_version_status(
    conn: sqlite3.Connection,
    *,
    only_needs_update: bool = False,
    mod_id: Optional[int] = None,
    download_ids: Optional[Sequence[int]] = None,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if only_needs_update:
        clauses.append("needs_update = 1")
    if mod_id is not None:
        try:
            clauses.append("mod_id = ?")
            params.append(int(mod_id))
        except (TypeError, ValueError):
            pass
    clean_ids: List[int] = []
    if download_ids:
        seen: Set[int] = set()
        for raw in download_ids:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value < 0 or value in seen:
                continue
            seen.add(value)
            clean_ids.append(value)
    if clean_ids:
        placeholders = ",".join("?" for _ in clean_ids)
        clauses.append(f"local_download_id IN ({placeholders})")
        params.extend(clean_ids)
    sql = (
        "SELECT pak_name, mod_id, source_zip, local_download_id, local_path, local_name, local_version, "
        "reference_file_id, reference_version, version_status, needs_update FROM v_mod_pak_version_status"
    )
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY pak_name;"
    cur = conn.cursor()
    rows = cur.execute(sql, tuple(params)).fetchall()
    columns = [desc[0] for desc in cur.description]
    results: List[Dict[str, Any]] = []
    for row in rows:
        entry = {col: row[idx] for idx, col in enumerate(columns)}
        local_version = entry.get("local_version")
        reference_version = entry.get("reference_version")
        entry["display_version"] = local_version
        entry["needs_update"] = bool(entry.get("needs_update"))
        # If either version is unknown, we cannot reliably compare — don't flag an update.
        if not local_version or not reference_version:
            entry["needs_update"] = False
        if versions_equivalent(local_version, reference_version):
            entry["version_status"] = "match"
            entry["needs_update"] = False
            entry["display_version"] = reference_version or local_version
        elif entry["needs_update"]:
            # Only flag an update when the remote version is strictly greater.
            # A version downgrade on Nexus should not trigger an update prompt.
            local_key = make_version_key(local_version)[0]
            remote_key = make_version_key(reference_version)[0]
            if local_key and remote_key and local_key >= remote_key:
                entry["needs_update"] = False
                entry["version_status"] = "local_newer_or_equal"
        results.append(entry)
    return results

def _get_mods_folder_for_deletion() -> Path:
    """Get the mods folder path for file deletion operations."""
    from core.config.settings import SETTINGS
    return SETTINGS.marvel_rivals_root / "MarvelGame" / "~mods"


def _remove_in_mods_by_names(mods_dir: Path, names: List[str]) -> List[str]:
    """Remove any files in mods_dir (recursively) whose basename is in names (case-insensitive)."""
    removed: List[str] = []
    if not mods_dir.exists():
        return removed
    
    names_lower = [n.lower() for n in names]
    try:
        for file_path in mods_dir.rglob("*"):
            if file_path.is_file():
                file_name = file_path.name.lower()
                if file_name in names_lower:
                    try:
                        file_path.unlink()
                        removed.append(str(file_path))
                    except Exception:
                        pass
    except Exception:
        pass
    return removed


def _remove_in_mods_by_stems(mods_dir: Path, stems: List[str]) -> List[str]:
    """Remove any files in mods_dir (recursively) with basename matching stem + (.pak|.utoc|.ucas)."""
    removed: List[str] = []
    if not mods_dir.exists():
        return removed
    
    stems_lower = [s.lower() for s in stems]
    extensions = ['.pak', '.utoc', '.ucas', '.sig']
    try:
        for file_path in mods_dir.rglob("*"):
            if file_path.is_file():
                stem = file_path.stem.lower()
                if stem in stems_lower and file_path.suffix.lower() in extensions:
                    try:
                        file_path.unlink()
                        removed.append(str(file_path))
                    except Exception:
                        pass
    except Exception:
        pass
    return removed


def delete_local_downloads(
    conn: sqlite3.Connection,
    download_ids: Sequence[int],
) -> Tuple[int, List[int], List[str]]:
    """Delete local download rows and cascade related pak records.

    Returns a tuple of (deleted_count, removed_mod_ids, source_paths).
    ``removed_mod_ids`` contains Nexus mod IDs that no longer have any local
    downloads after the deletion and were therefore purged from the metadata
    tables as well.
    ``source_paths`` holds the normalized path value that each deleted row
    referenced, enabling callers to remove the corresponding archive from disk.
    
    IMPORTANT: If any downloads being deleted have active paks, they will be
    deactivated first before deletion to ensure clean removal.
    """
    if not download_ids:
        return 0, [], []

    seen: Set[int] = set()
    for raw in download_ids:
        try:
            value = int(raw)  # handles str/int/float inputs
        except (TypeError, ValueError):
            continue
        if value < 0:
            continue
        seen.add(value)
    unique_ids = sorted(seen)
    if not unique_ids:
        return 0, [], []

    cur = conn.cursor()
    placeholders = ",".join("?" for _ in unique_ids)
    rows = cur.execute(
        f"SELECT id, mod_id, path FROM local_downloads WHERE id IN ({placeholders});",
        tuple(unique_ids),
    ).fetchall()
    if not rows:
        return 0, [], []

    # First, deactivate any active downloads and remove files from ~mods folder
    active_rows = cur.execute(
        f"SELECT id, active_paks, contents, name FROM local_downloads WHERE id IN ({placeholders});",
        tuple(unique_ids),
    ).fetchall()
    
    for download_id, active_paks_json, contents_json, download_name in active_rows:
        if active_paks_json:
            try:
                active_paks = json.loads(active_paks_json)
                if isinstance(active_paks, list) and active_paks:
                    # Remove files from ~mods folder - use active_paks to know what's actually active
                    mods_dir = _get_mods_folder_for_deletion()
                    
                    # Use the ACTIVE pak names (what's actually in ~mods), not contents
                    # active_paks contains the basenames of files that are currently active
                    pak_names = [os.path.basename(p) for p in active_paks if isinstance(p, str)]
                    
                    # Remove files by stems (handles .pak/.utoc/.ucas)
                    removed_files = []
                    if pak_names:
                        stems = [os.path.splitext(p)[0] for p in pak_names]
                        removed_files.extend(_remove_in_mods_by_stems(mods_dir, stems))
                        # Also attempt direct/name-based removal as a safety
                        removed_files.extend(_remove_in_mods_by_names(mods_dir, pak_names))
                    
                    # Update database to mark as inactive
                    update_local_download_active_paks(conn, download_id, [])
                    print(f"[delete_local_downloads] Deactivated download_id={download_id}, removed {len(removed_files)} files from ~mods")
            except Exception as e:
                # If we can't parse the active paks, try to deactivate anyway
                try:
                    update_local_download_active_paks(conn, download_id, [])
                    print(f"[delete_local_downloads] Deactivated download_id={download_id} before deletion (best effort)")
                except Exception as e2:
                    print(f"[delete_local_downloads] Warning: Failed to deactivate download_id={download_id}: {e2}")

    mods_to_check: Set[int] = set()
    source_zips: Set[str] = set()
    source_paths: List[str] = []
    for row_id, mod_id, path in rows:
        if mod_id is not None:
            try:
                mods_to_check.add(int(mod_id))
            except Exception:
                pass
        if path:
            try:
                source_path = str(path)
                source_paths.append(source_path)
                source_zips.add(Path(source_path).name)
            except Exception:
                continue

    cur.execute(
        f"DELETE FROM local_downloads WHERE id IN ({placeholders});",
        tuple(unique_ids),
    )

    if source_zips:
        cur.executemany(
            "DELETE FROM mod_asset_paths WHERE source_zip = ?;",
            ((zip_name,) for zip_name in source_zips),
        )

    removed_mod_ids: List[int] = []
    for mod_id in mods_to_check:
        row = cur.execute(
            "SELECT 1 FROM local_downloads WHERE mod_id = ? LIMIT 1;",
            (mod_id,),
        ).fetchone()
        if row:
            continue
        cur.execute("DELETE FROM mods WHERE mod_id = ?;", (mod_id,))
        cur.execute("DELETE FROM mod_api_cache WHERE mod_id = ?;", (mod_id,))
        cur.execute("DELETE FROM mod_asset_paths WHERE mod_id = ?;", (mod_id,))
        cur.execute("DELETE FROM mod_files WHERE mod_id = ?;", (mod_id,))
        cur.execute("DELETE FROM mod_changelogs WHERE mod_id = ?;", (mod_id,))
        removed_mod_ids.append(mod_id)

    conn.commit()
    deleted_count = len(rows)
    return deleted_count, removed_mod_ids, source_paths

def update_local_download_active_paks(
    conn: sqlite3.Connection,
    download_id: int,
    new_active_paks: List[str],
    *,
    now_iso: Optional[str] = None,
) -> None:
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT active_paks, last_activated_at, last_deactivated_at FROM local_downloads WHERE id = ?;",
        (download_id,),
    ).fetchone()
    if row is None:
        return
    prev_active_json, prev_last_activated, prev_last_deactivated = row
    prev_list: List[str] = []
    if prev_active_json:
        try:
            prev_list = json.loads(prev_active_json)
            if not isinstance(prev_list, list):
                prev_list = []
        except Exception:
            prev_list = []
    became_active = (not prev_list) and new_active_paks
    became_inactive = prev_list and (not new_active_paks)
    last_activated_at = prev_last_activated
    last_deactivated_at = prev_last_deactivated
    if became_active:
        last_activated_at = now_iso
    if became_inactive:
        last_deactivated_at = now_iso
    cur.execute(
        """
        UPDATE local_downloads
        SET active_paks = ?, last_activated_at = ?, last_deactivated_at = ?
        WHERE id = ?;
        """,
        (json.dumps(new_active_paks, ensure_ascii=False), last_activated_at, last_deactivated_at, download_id),
    )
    conn.commit()

def upsert_mod_info(
    conn: sqlite3.Connection,
    game: str,
    mod_id: int,
    mod_info_status: int,  # kept for signature compatibility even if unused now
    mod_info: Any,
) -> None:
    name = summary = author = version = updated_at = None
    created_time = created_timestamp = updated_timestamp = picture_url = None
    contains_adult_content = status = available = category_id = None
    mod_downloads = mod_unique_downloads = endorsement_count = None
    description_bbcode = description_html = None
    author_profile_url = None
    author_member_id: Optional[int] = None
    if isinstance(mod_info, dict):
        name = mod_info.get("name")
        summary = mod_info.get("summary")
        raw_description = mod_info.get("description")
        author = (
            mod_info.get("user", {}).get("name")
            if isinstance(mod_info.get("user"), dict)
            else mod_info.get("author")
        )
        version = mod_info.get("version") or mod_info.get("latest_file_version")
        updated_at = mod_info.get("updated_time") or mod_info.get("updated_at")
        created_time = mod_info.get("created_time")
        created_timestamp = mod_info.get("created_timestamp")
        updated_timestamp = mod_info.get("updated_timestamp")
        picture_url = mod_info.get("picture_url")
        contains_adult_content = (
            1 if mod_info.get("contains_adult_content") else 0
            if mod_info.get("contains_adult_content") is not None else None
        )
        status = mod_info.get("status")
        available = (
            1 if mod_info.get("available") else 0
            if mod_info.get("available") is not None else None
        )
        category_id = mod_info.get("category_id")
        mod_downloads = mod_info.get("mod_downloads")
        mod_unique_downloads = mod_info.get("mod_unique_downloads")
        endorsement_count = mod_info.get("endorsement_count")
        author_profile_url = mod_info.get("uploaded_users_profile_url")
        user_info = mod_info.get("user")
        if isinstance(user_info, dict):
            member_candidate = user_info.get("member_id")
            if author_member_id is None:
                author_member_id = _extract_member_id(member_candidate)
            if not author_profile_url:
                author_profile_url = user_info.get("profile_url")
        if author_member_id is None and author_profile_url:
            author_member_id = _extract_member_id(author_profile_url)
        # Merge summary + description (BBCode) and convert to HTML for storage
        merged_bbcode_parts: List[str] = []
        if isinstance(summary, str) and summary.strip():
            merged_bbcode_parts.append(summary.strip())
        if isinstance(raw_description, str) and raw_description.strip():
            merged_bbcode_parts.append(raw_description)
        if merged_bbcode_parts:
            description_bbcode = "\n\n".join(merged_bbcode_parts)
            try:
                tmp_html = bbcode_to_html(description_bbcode)
            except Exception:
                tmp_html = description_bbcode
            # Server-side sanitize
            description_html = sanitize_html(tmp_html)
    conn.execute(
        """
        INSERT INTO mods(
            mod_id, game, name, summary, description_bbcode, description_html, author, version, updated_at,
            created_time, created_timestamp, updated_timestamp,
            picture_url, contains_adult_content, status, available, category_id,
            mod_downloads, mod_unique_downloads, endorsement_count,
            author_profile_url, author_member_id
        )
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(mod_id) DO UPDATE SET
            game=excluded.game,
            name=excluded.name,
            summary=excluded.summary,
            description_bbcode=excluded.description_bbcode,
            description_html=excluded.description_html,
            author=excluded.author,
            version=excluded.version,
            updated_at=excluded.updated_at,
            created_time=excluded.created_time,
            created_timestamp=excluded.created_timestamp,
            updated_timestamp=excluded.updated_timestamp,
            picture_url=excluded.picture_url,
            contains_adult_content=excluded.contains_adult_content,
            status=excluded.status,
            available=excluded.available,
            category_id=excluded.category_id,
            mod_downloads=excluded.mod_downloads,
            mod_unique_downloads=excluded.mod_unique_downloads,
            endorsement_count=excluded.endorsement_count,
            author_profile_url=excluded.author_profile_url,
            author_member_id=excluded.author_member_id;
        """,
        (
            mod_id,
            game,
            name,
            summary,
            description_bbcode,
            description_html,
            author,
            version,
            updated_at,
            created_time,
            created_timestamp,
            updated_timestamp,
            picture_url,
            contains_adult_content,
            status,
            available,
            category_id,
            mod_downloads,
            mod_unique_downloads,
            endorsement_count,
            author_profile_url,
            author_member_id,
        ),
    )
    conn.commit()

def replace_mod_files(
    conn: sqlite3.Connection,
    mod_id: int,
    files_payload: Any,
) -> None:
    cur = conn.cursor()
    if isinstance(files_payload, dict):
        file_list = files_payload.get("files") or []
    elif isinstance(files_payload, list):
        file_list = files_payload
    else:
        file_list = []
    cur.execute("DELETE FROM mod_files WHERE mod_id = ?;", (mod_id,))
    for f in file_list:
        if not isinstance(f, dict):
            continue
        file_id = f.get("file_id") or f.get("id")
        if file_id is None:
            continue
        name = f.get("name")
        version = f.get("version") or f.get("file_version")
        category = f.get("category_name") or f.get("category")
        size_in_bytes = f.get("size_in_bytes")
        is_primary = 1 if f.get("is_primary") else 0
        uploaded_at = f.get("uploaded_time") or f.get("uploaded_at")
    # description = f.get("description")  # removed, not used
        vkey, vmaj, vmin, vpatch, vbuild = make_version_key(version)
        cur.execute(
            """
            INSERT INTO mod_files(
                mod_id, file_id, name, version, category, size_in_bytes, is_primary, uploaded_at,
                version_key, v_maj, v_min, v_patch, v_build
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mod_id, file_id) DO UPDATE SET
                name=excluded.name,
                version=excluded.version,
                category=excluded.category,
                size_in_bytes=excluded.size_in_bytes,
                is_primary=excluded.is_primary,
                uploaded_at=excluded.uploaded_at,
                -- description=excluded.description,  # removed, not used
                version_key=excluded.version_key,
                v_maj=excluded.v_maj,
                v_min=excluded.v_min,
                v_patch=excluded.v_patch,
                v_build=excluded.v_build;
            """,
            (
                mod_id,
                file_id,
                name,
                version,
                category,
                size_in_bytes,
                is_primary,
                uploaded_at,

                vkey,
                vmaj,
                vmin,
                vpatch,
                vbuild,
            ),
        )
    conn.commit()

def replace_mod_changelogs(
    conn: sqlite3.Connection,
    mod_id: int,
    changelogs_payload: Any,
) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM mod_changelogs WHERE mod_id = ?;", (mod_id,))
    items: List[Tuple[str, str, Optional[str]]] = []
    if isinstance(changelogs_payload, dict):
        if "changelogs" in changelogs_payload and isinstance(changelogs_payload["changelogs"], list):
            source_iter = changelogs_payload["changelogs"]
        else:
            source_iter = []
    elif isinstance(changelogs_payload, list):
        source_iter = changelogs_payload
    else:
        source_iter = []
    for ch in source_iter:
        if not isinstance(ch, dict):
            continue
        version = str(ch.get("version") or ch.get("mod_version") or "")
        text = ch.get("changelog") or ch.get("content") or ""
        uploaded_at = ch.get("uploaded_time") or ch.get("uploaded_at")
        if version:
            items.append((version, text, uploaded_at))
    for version, text, uploaded_at in items:
        cur.execute(
            """
            INSERT INTO mod_changelogs(mod_id, version, changelog, uploaded_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(mod_id, version) DO UPDATE SET
                changelog=excluded.changelog,
                uploaded_at=excluded.uploaded_at;
            """,
            (mod_id, version, text, uploaded_at),
        )
    conn.commit()

def upsert_api_cache(conn: sqlite3.Connection, mod_id: int, payload: Any) -> None:
    conn.execute(
        """
        INSERT INTO mod_api_cache(mod_id, fetched_at, payload)
        VALUES(?, ?, ?)
        ON CONFLICT(mod_id) DO UPDATE SET
            fetched_at=excluded.fetched_at,
            payload=excluded.payload;
        """,
        (mod_id, datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()

__all__ = [
    "get_connection",
    "init_schema",
    "run_migrations",
    "make_version_key",
    "next_local_download_id",
    "replace_local_downloads",
    "fetch_pak_version_status",
    "update_local_download_active_paks",
    "upsert_mod_info",

    "replace_mod_files",
    "replace_mod_changelogs",
    "upsert_api_cache",
    # New helpers (defined below)
    "upsert_mod_pak",
    "bulk_upsert_pak_assets",
    "upsert_pak_assets_json",
    # Conflict materialization
    "rebuild_conflicts",
]

def next_local_download_id(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM local_downloads;")
    value = cur.fetchone()
    if not value:
        return 1
    try:
        return int(value[0]) if value[0] else 1
    except (TypeError, ValueError):
        return 1

# New helper functions for per-pak ingest

def upsert_mod_pak(
    conn: sqlite3.Connection,
    *,
    pak_name: str,
    mod_id: Optional[int] = None,
    source_zip: Optional[str] = None,
    local_download_id: Optional[int] = None,
    io_store: Optional[bool] = None,
) -> None:
    io_val = None if io_store is None else (1 if io_store else 0)
    conn.execute(
        """
        INSERT INTO mod_paks(pak_name, mod_id, source_zip, local_download_id, io_store)
        VALUES(?, ?, ?, ?, ?)
        ON CONFLICT(pak_name) DO UPDATE SET
            mod_id=COALESCE(excluded.mod_id, mod_paks.mod_id),
            source_zip=COALESCE(excluded.source_zip, mod_paks.source_zip),
            local_download_id=COALESCE(excluded.local_download_id, mod_paks.local_download_id),
            io_store=COALESCE(excluded.io_store, mod_paks.io_store);
        """,
        (pak_name, mod_id, source_zip, local_download_id, io_val),
    )
    conn.commit()

def bulk_upsert_pak_assets(
    conn: sqlite3.Connection,
    pak_name: str,
    asset_paths: Iterable[str],
    *,
    replace: bool = True,
) -> int:
    cur = conn.cursor()
    norm = []
    for p in asset_paths:
        if not p:
            continue
        np = p.replace("\\", "/").lower()
        norm.append(np)
    if replace:
        cur.execute("DELETE FROM pak_assets WHERE pak_name = ?;", (pak_name,))
    inserted = 0
    for ap in dict.fromkeys(norm):  # dedupe while preserving order
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO pak_assets(pak_name, asset_path)
                VALUES(?, ?);
                """,
                (pak_name, ap),
            )
            inserted += 1
        except Exception:
            continue
    conn.commit()
    return inserted

def upsert_pak_assets_json(
    conn: sqlite3.Connection,
    pak_name: str,
    assets: Iterable[str],
    *,
    mod_id: Optional[int] = None,
) -> None:
    assets_list = [a.replace("\\", "/").lower() for a in assets if a]
    # Validate mod_id exists; if not, store NULL to avoid FK violation
    if mod_id is not None:
        exists = conn.execute("SELECT 1 FROM mods WHERE mod_id = ?", (mod_id,)).fetchone()
        if not exists:
            mod_id = None
    conn.execute(
        """
        INSERT INTO pak_assets_json(pak_name, mod_id, assets_json)
        VALUES(?, ?, ?)
        ON CONFLICT(pak_name) DO UPDATE SET
            mod_id=COALESCE(excluded.mod_id, pak_assets_json.mod_id),
            assets_json=excluded.assets_json;
        """,
        (pak_name, mod_id, json.dumps(assets_list, ensure_ascii=False)),
    )
    conn.commit()

def rebuild_conflicts(conn: sqlite3.Connection, *, active_only: bool | None = None) -> dict:
    """Rebuild materialized conflict tables.

    ``active_only`` controls which snapshot(s) to rebuild:
    - ``None`` (default) refreshes both the "all" and "active" tables.
    - truthy values refresh only the active snapshot.
    - falsy values refresh only the all snapshot.
    Returns a mapping of table name to row counts.
    """

    cur = conn.cursor()
    results: dict[str, int] = {}

    def _rebuild(*, active: bool) -> None:
        suffix = "_active" if active else ""
        conflicts_tbl = f"asset_conflicts{suffix}"
        participants_tbl = f"asset_conflict_participants{suffix}"
        active_cte = ""
        active_join = ""
        if active:
            active_cte = """
            raw_active AS (
                SELECT DISTINCT
                    lower(trim(json_each.value)) AS pak_name
                FROM local_downloads,
                     json_each(COALESCE(local_downloads.active_paks, '[]'))
                WHERE json_each.value IS NOT NULL
            ),
            -- Recursively strip directory prefixes to extract bare filenames.
            -- e.g. 'subdir/xl/thicc_luna.pak' -> 'thicc_luna.pak'
            -- This avoids a leading-wildcard LIKE scan on pak_assets (unindexable).
            strip_paths(remaining) AS (
                SELECT pak_name FROM raw_active
                UNION ALL
                SELECT substr(remaining, instr(remaining, '/') + 1)
                FROM   strip_paths
                WHERE  instr(remaining, '/') > 0
            ),
            active_basenames AS (
                SELECT DISTINCT remaining AS pak_name
                FROM   strip_paths
                WHERE  instr(remaining, '/') = 0 AND remaining != ''
            ),
            active_stems AS (
                SELECT DISTINCT
                    CASE
                        WHEN pak_name LIKE '%.pak'  THEN substr(pak_name, 1, length(pak_name) - 4)
                        WHEN pak_name LIKE '%.utoc' THEN substr(pak_name, 1, length(pak_name) - 5)
                        WHEN pak_name LIKE '%.ucas' THEN substr(pak_name, 1, length(pak_name) - 5)
                        ELSE pak_name
                    END AS stem
                FROM active_basenames
                WHERE pak_name != '' AND pak_name IS NOT NULL
            ),
            active_paks AS (
                SELECT pak_name FROM active_basenames
                UNION
                SELECT stem || '.pak'  FROM active_stems WHERE stem != '' AND stem IS NOT NULL
                UNION
                SELECT stem || '.utoc' FROM active_stems WHERE stem != '' AND stem IS NOT NULL
                UNION
                SELECT stem || '.ucas' FROM active_stems WHERE stem != '' AND stem IS NOT NULL
            ),
            """
            active_join = "JOIN active_paks ap ON ap.pak_name = lower(pa.pak_name)"

        # Snapshot existing detected_at timestamps so returning conflicts keep
        # their original first-detected time after the rebuild.
        prev_detected: dict[str, str] = {}
        try:
            for row in cur.execute(f"SELECT asset_path, detected_at FROM {conflicts_tbl} WHERE detected_at IS NOT NULL").fetchall():
                prev_detected[row[0]] = row[1]
        except Exception:
            # Column may not exist yet (pre-migration); ignore.
            pass

        cur.execute(f"DELETE FROM {conflicts_tbl};")
        cur.execute(f"DELETE FROM {participants_tbl};")

        agg_sql = f"""
            WITH {active_cte}
            base AS (
                SELECT
                    pa.asset_path,
                    pa.pak_name,
                    mp.mod_id,
                    mp.source_zip,
                    mp.local_download_id,
                    CASE
                        WHEN mp.mod_id IS NOT NULL THEN CAST(mp.mod_id AS TEXT)
                        WHEN mp.local_download_id IS NOT NULL THEN 'local:' || CAST(mp.local_download_id AS TEXT)
                        WHEN mp.source_zip IS NOT NULL THEN 'zip:' || LOWER(mp.source_zip)
                        ELSE 'pak:' || LOWER(pa.pak_name)
                    END AS provider_key
                FROM pak_assets pa
                JOIN mod_paks mp ON mp.pak_name = pa.pak_name
                {active_join}
            ),
            grouped AS (
                SELECT asset_path,
                       COUNT(DISTINCT provider_key) AS mod_count,
                       COUNT(DISTINCT pak_name) AS pak_count
                FROM base
                GROUP BY asset_path
                HAVING mod_count > 1
            )
            INSERT INTO {conflicts_tbl}(asset_path, distinct_mods, distinct_paks, generated_at, detected_at)
            SELECT asset_path, mod_count, pak_count, datetime('now'), datetime('now') FROM grouped;
        """
        cur.executescript(agg_sql)

        # Restore previously-known detected_at for returning conflicts so they
        # retain their original detection time instead of being reset to now.
        if prev_detected:
            for asset_path, detected_at in prev_detected.items():
                cur.execute(
                    f"UPDATE {conflicts_tbl} SET detected_at = ? WHERE asset_path = ?",
                    (detected_at, asset_path),
                )

        part_sql = f"""
            WITH {active_cte}
            base AS (
                SELECT
                    pa.asset_path,
                    pa.pak_name,
                    mp.mod_id,
                    mp.source_zip,
                    mp.local_download_id,
                    CASE
                        WHEN mp.mod_id IS NOT NULL THEN CAST(mp.mod_id AS TEXT)
                        WHEN mp.local_download_id IS NOT NULL THEN 'local:' || CAST(mp.local_download_id AS TEXT)
                        WHEN mp.source_zip IS NOT NULL THEN 'zip:' || LOWER(mp.source_zip)
                        ELSE 'pak:' || LOWER(pa.pak_name)
                    END AS provider_key
                FROM pak_assets pa
                JOIN mod_paks mp ON mp.pak_name = pa.pak_name
                {active_join}
            )
            INSERT INTO {participants_tbl}(asset_path, pak_name, mod_id, source_zip)
            SELECT g.asset_path, b.pak_name, b.mod_id, b.source_zip
            FROM {conflicts_tbl} g
            JOIN base b ON b.asset_path = g.asset_path;
        """
        cur.executescript(part_sql)

        count_conflicts = cur.execute(f"SELECT COUNT(*) FROM {conflicts_tbl}").fetchone()[0]
        results[conflicts_tbl] = count_conflicts

    if active_only is None:
        _rebuild(active=False)
        _rebuild(active=True)
    elif bool(active_only):
        _rebuild(active=True)
    else:
        _rebuild(active=False)

    conn.commit()
    return results


# ============================================================================
# Character and Skin Data Functions
# ============================================================================

def has_character_data(conn: sqlite3.Connection) -> bool:
    """Check if the database has character data."""
    cur = conn.cursor()
    result = cur.execute("SELECT COUNT(*) as count FROM characters").fetchone()
    return result[0] > 0 if result else False


def get_all_characters(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Get all characters with their skins.
    
    Returns:
        List of dicts with structure: {
            "character_id": "1034",
            "name": "iron man",
            "skins": [
                {"variant": "001", "name": "default"},
                {"variant": "100", "name": "armor model 42"},
                ...
            ]
        }
    """
    cur = conn.cursor()
    
    # Get all characters
    chars = cur.execute("SELECT character_id, name FROM characters ORDER BY character_id").fetchall()
    
    result = []
    for char_row in chars:
        char_id, char_name = char_row
        
        # Get skins for this character
        skins = cur.execute(
            "SELECT variant, name FROM skins WHERE character_id = ? ORDER BY variant",
            (char_id,)
        ).fetchall()
        
        result.append({
            "character_id": char_id,
            "name": char_name,
            "skins": [{"variant": v, "name": n} for v, n in skins]
        })
    
    return result


def get_character_skins(conn: sqlite3.Connection, character_id: str) -> List[Dict[str, str]]:
    """
    Get all skins for a specific character.
    
    Returns:
        List of dicts: [{"variant": "001", "name": "default"}, ...]
    """
    cur = conn.cursor()
    skins = cur.execute(
        "SELECT variant, name FROM skins WHERE character_id = ? ORDER BY variant",
        (character_id,)
    ).fetchall()
    
    return [{"variant": v, "name": n} for v, n in skins]


def get_character_names(conn: sqlite3.Connection) -> List[str]:
    """
    Get all character names (lowercase) for filtering/tagging.
    Used as replacement for character_ids.json loading.
    
    Returns:
        List of lowercase character names
    """
    cur = conn.cursor()
    rows = cur.execute("SELECT name FROM characters ORDER BY name").fetchall()
    return [row[0] for row in rows]


def clear_character_data(conn: sqlite3.Connection) -> None:
    """Clear all character and skin data (cascades to skins via FK)."""
    cur = conn.cursor()
    cur.execute("DELETE FROM skins")
    cur.execute("DELETE FROM characters")
    conn.commit()


def insert_characters(conn: sqlite3.Connection, characters: List[Tuple[str, str]]) -> None:
    """
    Batch insert characters.
    
    Args:
        characters: List of (character_id, name) tuples
    """
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO characters (character_id, name) VALUES (?, ?)",
        characters
    )
    conn.commit()


def insert_skins(conn: sqlite3.Connection, skins: List[Tuple[str, str, str, str]]) -> None:
    """
    Batch insert skins.

    Args:
        skins: List of (skin_id, character_id, variant, name) tuples
    """
    cur = conn.cursor()
    cur.executemany(
        "INSERT OR REPLACE INTO skins (skin_id, character_id, variant, name) VALUES (?, ?, ?, ?)",
        skins
    )
    conn.commit()


# ── Custom Author Metadata ──────────────────────────────────────────────────────────────────────────────

def upsert_custom_author(
    conn,
    *,
    display_name: str,
    avatar_base64=None,
    author_type: str = "custom",
    nexus_member_id=None,
) -> int:
    """Insert or update a custom author by normalised name.  Returns the row id."""
    name_normalized = display_name.strip().lower()
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).isoformat()
    conn.execute(
        """
        INSERT INTO custom_authors
            (name_normalized, display_name, author_type, nexus_member_id, avatar_base64, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name_normalized) DO UPDATE SET
            display_name    = excluded.display_name,
            author_type     = excluded.author_type,
            nexus_member_id = COALESCE(excluded.nexus_member_id, custom_authors.nexus_member_id),
            avatar_base64   = COALESCE(excluded.avatar_base64, custom_authors.avatar_base64),
            updated_at      = excluded.updated_at;
        """,
        (name_normalized, display_name, author_type, nexus_member_id, avatar_base64, now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM custom_authors WHERE name_normalized = ?;", (name_normalized,)
    ).fetchone()
    return int(row[0])


def update_custom_author(
    conn,
    author_id: int,
    *,
    display_name=None,
    avatar_base64=None,
    clear_avatar: bool = False,
) -> None:
    """Partially update an existing custom author row."""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).isoformat()
    if display_name is not None:
        name_normalized = display_name.strip().lower()
        conn.execute(
            "UPDATE custom_authors SET display_name=?, name_normalized=?, updated_at=? WHERE id=?;",
            (display_name, name_normalized, now, author_id),
        )
    if clear_avatar:
        conn.execute(
            "UPDATE custom_authors SET avatar_base64=NULL, updated_at=? WHERE id=?;",
            (now, author_id),
        )
    elif avatar_base64 is not None:
        conn.execute(
            "UPDATE custom_authors SET avatar_base64=?, updated_at=? WHERE id=?;",
            (avatar_base64, now, author_id),
        )
    conn.commit()


def get_custom_author(conn, author_id: int):
    """Fetch a single custom author by id."""
    cur = conn.execute(
        "SELECT id, display_name, author_type, nexus_member_id, avatar_base64 FROM custom_authors WHERE id = ?;",
        (author_id,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "display_name": row[1],
        "author_type": row[2],
        "nexus_member_id": row[3],
        "avatar_base64": row[4],
    }


def search_custom_authors(conn, query: str, limit: int = 100):
    """Search custom_authors + mods table by partial name match.

    Returns a unified list: persisted custom authors first, then Nexus authors
    from the local mods table, deduplicated by normalised name.
    """
    like = "%" + query.strip().lower() + "%"
    rows = conn.execute(
        """
        SELECT id, display_name, author_type, nexus_member_id, avatar_base64
        FROM custom_authors
        WHERE name_normalized LIKE ?
        ORDER BY display_name COLLATE NOCASE
        LIMIT ?;
        """,
        (like, limit),
    ).fetchall()
    results = [
        {
            "id": r[0],
            "display_name": r[1],
            "author_type": r[2],
            "nexus_member_id": r[3],
            "avatar_base64": r[4],
        }
        for r in rows
    ]
    seen_names = {r["display_name"].strip().lower() for r in results}
    # Supplement with Nexus authors from the mods table (not yet in custom_authors)
    if len(results) < limit:
        nexus_rows = conn.execute(
            """
            SELECT DISTINCT author, author_member_id
            FROM mods
            WHERE LOWER(author) LIKE ? AND author IS NOT NULL AND author != ''
            ORDER BY author COLLATE NOCASE
            LIMIT ?;
            """,
            (like, limit - len(results)),
        ).fetchall()
        for nr in nexus_rows:
            name = (nr[0] or "").strip()
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())
            results.append({
                "id": None,
                "display_name": name,
                "author_type": "nexus",
                "nexus_member_id": nr[1],
                "avatar_base64": None,
            })
    return results


def set_mod_author(conn, mod_key: str, author_id: int) -> None:
    """Assign a custom author to a mod (upsert)."""
    from datetime import datetime, timezone as _tz
    now = datetime.now(_tz.utc).isoformat()
    conn.execute(
        """
        INSERT INTO local_mod_metadata (mod_key, custom_author_id, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(mod_key) DO UPDATE SET
            custom_author_id = excluded.custom_author_id,
            updated_at       = excluded.updated_at;
        """,
        (mod_key, author_id, now, now),
    )
    conn.commit()


def clear_mod_author(conn, mod_key: str) -> None:
    """Remove the custom author assignment for a mod."""
    from datetime import datetime, timezone as _tz
    conn.execute(
        "UPDATE local_mod_metadata SET custom_author_id=NULL, updated_at=? WHERE mod_key=?;",
        (datetime.now(_tz.utc).isoformat(), mod_key),
    )
    conn.commit()


def get_mod_metadata(conn, mod_key: str):
    """Fetch local_mod_metadata + joined custom_author for a single mod_key."""
    cur = conn.execute(
        """
        SELECT lmm.mod_key,
               lmm.custom_author_id,
               lmm.extra_json,
               ca.display_name,
               ca.author_type,
               ca.nexus_member_id,
               ca.avatar_base64
        FROM local_mod_metadata lmm
        LEFT JOIN custom_authors ca ON ca.id = lmm.custom_author_id
        WHERE lmm.mod_key = ?;
        """,
        (mod_key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "mod_key": row[0],
        "custom_author_id": row[1],
        "extra_json": row[2],
        "custom_author_name": row[3],
        "author_type": row[4],
        "nexus_member_id": row[5],
        "avatar_base64": row[6],
    }

