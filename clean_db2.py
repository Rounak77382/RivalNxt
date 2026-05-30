import sqlite3, json, os

db_path = os.path.expandvars(r'%APPDATA%\com.rivalnxt.modmanager\mods.db')
print(f"Connecting to {db_path}...")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Truncate pak_assets_json
cur.execute('SELECT mod_id, pak_name, assets_json FROM pak_assets_json')
rows = cur.fetchall()
count_assets = 0
for mid, pname, ajson in rows:
    if ajson:
        try:
            arr = json.loads(ajson)
            if isinstance(arr, list) and len(arr) > 100:
                new_arr = arr[:100] + [f"...and {len(arr) - 100} more assets"]
                cur.execute('UPDATE pak_assets_json SET assets_json = ? WHERE mod_id = ? AND pak_name = ?', (json.dumps(new_arr), mid, pname))
                count_assets += 1
        except Exception:
            pass
conn.commit()
print(f'Truncated {count_assets} rows in pak_assets_json')

# Vacuum the database to reclaim the 2GB of space!
print("Vacuuming database to reclaim disk space...")
conn.execute("VACUUM")
conn.close()
print("Done!")
