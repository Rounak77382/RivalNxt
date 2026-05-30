import sqlite3, json, os

db_path = os.path.expandvars(r'%APPDATA%\com.rivalnxt.modmanager\mods.db')
print(f"Connecting to {db_path}...")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Truncate local_downloads contents
cur.execute('SELECT id, contents FROM local_downloads')
rows = cur.fetchall()
count = 0
for rid, cjson in rows:
    if cjson:
        try:
            arr = json.loads(cjson)
            if isinstance(arr, list) and len(arr) > 100:
                paks = [f for f in arr if f.lower().endswith('.pak')]
                others = [f for f in arr if not f.lower().endswith('.pak')]
                sample = others[:100 - len(paks)] if len(paks) < 100 else []
                new_arr = paks + sample
                new_arr.append(f"...and {len(arr) - len(paks) - len(sample)} more files")
                cur.execute('UPDATE local_downloads SET contents = ? WHERE id = ?', (json.dumps(new_arr), rid))
                count += 1
        except Exception as e:
            pass
conn.commit()
print(f'Truncated {count} rows in local_downloads')

# Truncate pak_assets_json
cur.execute('SELECT download_id, pak_name, assets_json FROM pak_assets_json')
rows = cur.fetchall()
count_assets = 0
for did, pname, ajson in rows:
    if ajson:
        try:
            arr = json.loads(ajson)
            if isinstance(arr, list) and len(arr) > 100:
                new_arr = arr[:100] + [f"...and {len(arr) - 100} more assets"]
                cur.execute('UPDATE pak_assets_json SET assets_json = ? WHERE download_id = ? AND pak_name = ?', (json.dumps(new_arr), did, pname))
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
