from __future__ import annotations
import sqlite3
from typing import List, Dict, Any, Optional

__all__ = [
    "list_asset_conflicts",
    "get_asset_conflict_detail",
]

def list_asset_conflicts(conn: sqlite3.Connection, *, active_only: bool = False, limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Return rows of asset conflicts.

    active_only: when True uses v_asset_conflicts_active else v_asset_conflicts_all.
    limit: optional max rows.
    """
    view = "v_asset_conflicts_active" if active_only else "v_asset_conflicts_all"
    sql = f"SELECT asset_path, mod_count, pak_count FROM {view} ORDER BY mod_count DESC, pak_count DESC, asset_path"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    cur = conn.execute(sql)
    cols = [c[0] for c in cur.description]
    out = []
    for r in cur.fetchall():
        out.append({cols[i]: r[i] for i in range(len(cols))})
    return out

def get_asset_conflict_detail(conn: sqlite3.Connection, asset_path: str) -> Dict[str, Any]:
    """Return detailed breakdown for a conflicting asset_path.

    Includes the mods & paks providing it. If not conflicting returns empty dict.
    """
    # Confirm it is a conflict
    cur = conn.execute(
        """
        SELECT asset_path, mod_count, pak_count FROM v_asset_conflicts_all WHERE asset_path = ?
        """,
        (asset_path,),
    )
    cols = [c[0] for c in cur.description]
    row = cur.fetchone()
    if not row:
        return {}
    # List contributing paks -> mod info
    cur2 = conn.execute(
        """
        SELECT pa.pak_name, mp.mod_id, m.name AS mod_name, mp.source_zip
        FROM pak_assets pa
        JOIN mod_paks mp ON mp.pak_name = pa.pak_name
        LEFT JOIN mods m ON m.mod_id = mp.mod_id
        WHERE pa.asset_path = ? AND mp.mod_id IS NOT NULL
        ORDER BY mp.mod_id, pa.pak_name
        """,
        (asset_path,),
    )
    cols2 = [c[0] for c in cur2.description]
    providers = []
    for r in cur2.fetchall():
        providers.append({cols2[i]: r[i] for i in range(len(cols2))})
    return {
        "asset_path": row[cols.index("asset_path")],
        "mod_count": row[cols.index("mod_count")],
        "pak_count": row[cols.index("pak_count")],
        "providers": providers,
    }
