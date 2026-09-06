import json
from core.db.db import get_connection
from core.api.server import list_downloads, check_mod_update

def main():
    dls = list_downloads()
    needs_up = [d for d in dls if d.get("needs_update")]
    print(f"Total downloads: {len(dls)}")
    print(f"Downloads with needs_update=True: {len(needs_up)}")
    by_mod = {}
    for d in needs_up:
        m_id = d.get("mod_id")
        by_mod.setdefault(m_id, []).append(d)
    print(f"Unique mod_ids with needs_update=True: {len(by_mod)}")
    
    for m_id, items in by_mod.items():
        print(f"\n--- Mod ID: {m_id} ---")
        for it in items:
            print(f"  DL {it.get('id')}: name={it.get('name')}, ver={it.get('version')}, latest_ver={it.get('latest_version')}, loc_k={it.get('local_version_key')}, lat_k={it.get('latest_version_key')}")
        if m_id:
            try:
                chk = check_mod_update(m_id)
                print(f"  check_mod_update({m_id}) -> needs_update: {chk.get('needs_update')}, pending: {len(chk.get('pending', []))}")
                if chk.get('pending'):
                    print(f"    pending items: {chk.get('pending')}")
            except Exception as e:
                print(f"  check_mod_update error: {e}")

if __name__ == "__main__":
    main()