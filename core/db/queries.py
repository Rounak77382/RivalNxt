from typing import Any, Dict, List, Optional, Tuple
import sqlite3

def _row_to_dict(cur: sqlite3.Cursor, row: Tuple[Any, ...]) -> Dict[str, Any]:
    return {d[0]: v for d, v in zip(cur.description, row)}

def get_mod(conn: sqlite3.Connection, mod_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM mods WHERE mod_id = ?;",
        (mod_id,),
    )
    r = cur.fetchone()
    return _row_to_dict(cur, r) if r else None

def get_latest_file(conn: sqlite3.Connection, mod_id: int) -> Optional[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM v_latest_file_per_mod WHERE mod_id = ?;",
        (mod_id,),
    )
    r = cur.fetchone()
    return _row_to_dict(cur, r) if r else None

def get_latest_file_by_version(conn: sqlite3.Connection, mod_id: int) -> Optional[dict]:
    cur = conn.execute(
        """
        SELECT latest_file_id, file_name, file_version, file_category, file_size_in_bytes, latest_is_primary, latest_uploaded_at, latest_version_key
        FROM v_mods_with_latest_by_version
        WHERE mod_id = ?;
        """,
        (mod_id,),
    )
    row = cur.fetchone()
    if row:
        return {
            "file_id": row[0],
            "file_name": row[1],
            "file_version": row[2],
            "file_category": row[3],
            "file_size_in_bytes": row[4],
            "is_primary": row[5],
            "uploaded_at": row[6],
            "version_key": row[7],
        }
    return None

def get_file_by_id(conn: sqlite3.Connection, mod_id: int, file_id: int) -> Optional[dict]:
    cur = conn.execute(
        """
        SELECT file_id, name, version, category, size_in_bytes, is_primary, uploaded_at, version_key
        FROM mod_files
        WHERE mod_id = ? AND file_id = ?;
        """,
        (mod_id, file_id),
    )
    row = cur.fetchone()
    if row:
        return {
            "file_id": row[0],
            "file_name": row[1],
            "file_version": row[2],
            "file_category": row[3],
            "file_size_in_bytes": row[4],
            "is_primary": row[5],
            "uploaded_at": row[6],
            "version_key": row[7],
        }
    return None

def list_mod_files(conn: sqlite3.Connection, mod_id: int, order_by: str = "version") -> List[dict]:
    if order_by == "version":
        order_clause = "ORDER BY COALESCE(version_key, '' ) DESC, uploaded_at DESC"
    elif order_by == "uploaded":
        order_clause = "ORDER BY uploaded_at DESC"
    elif order_by == "name":
        order_clause = "ORDER BY name COLLATE NOCASE ASC"
    else:
        order_clause = "ORDER BY uploaded_at DESC"
    cur = conn.execute(
        f"""
        SELECT file_id, name, version, category, size_in_bytes, is_primary, uploaded_at
        FROM mod_files
        WHERE mod_id = ?
        {order_clause};
        """,
        (mod_id,),
    )
    out: List[dict] = []
    for row in cur.fetchall():
        out.append(
            {
                "file_id": row[0],
                "name": row[1],
                "version": row[2],
                "category": row[3],
                "size_in_bytes": row[4],
                "is_primary": row[5],
                "uploaded_at": row[6],
            }
        )
    return out

def get_changelogs(conn: sqlite3.Connection, mod_id: int) -> List[dict]:
    cur = conn.execute(
        """
        SELECT version, changelog, uploaded_at
        FROM mod_changelogs
        WHERE mod_id = ?
        ORDER BY uploaded_at DESC;
        """,
        (mod_id,),
    )
    return [
        {"version": row[0], "changelog": row[1], "uploaded_at": row[2]}
        for row in cur.fetchall()
    ]

def list_local_without_remote(conn: sqlite3.Connection, limit: int = 50) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM v_local_without_remote ORDER BY local_count DESC, mod_id LIMIT ?;",
        (limit,),
    )
    return [_row_to_dict(cur, r) for r in cur.fetchall()]

def search_mods(conn: sqlite3.Connection, q: str, limit: int = 50) -> List[Dict[str, Any]]:
    like = f"%{q}%"
    cur = conn.execute(
        """
        SELECT mod_id, name, author, summary
        FROM mods
        WHERE name LIKE ? OR author LIKE ? OR summary LIKE ?
        ORDER BY name COLLATE NOCASE
        LIMIT ?;
        """,
        (like, like, like, limit),
    )
    return [_row_to_dict(cur, r) for r in cur.fetchall()]

def mod_with_local_and_latest(conn: sqlite3.Connection, mod_id: int) -> Dict[str, Any]:
    m = get_mod(conn, mod_id)
    lf = get_latest_file(conn, mod_id)
    cur = conn.execute(
        "SELECT COUNT(*) FROM local_downloads WHERE mod_id = ?;",
        (mod_id,),
    )
    local_count = cur.fetchone()[0]
    return {"mod": m, "latest_file": lf, "local_count": local_count}

__all__ = [
    "get_mod",
    "get_latest_file",
    "get_latest_file_by_version",
    "list_local_without_remote",
    "search_mods",
    "mod_with_local_and_latest",
    "list_mod_files",
    "get_changelogs",
]
