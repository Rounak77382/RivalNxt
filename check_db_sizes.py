import sqlite3, os

db_path = os.path.expandvars(r'%APPDATA%\com.rivalnxt.modmanager\mods.db')
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()

sizes = []
for t in tables:
    table_name = t[0]
    cur.execute(f"PRAGMA table_info({table_name})")
    cols = cur.fetchall()
    col_names = [c[1] for c in cols]
    
    # Calculate approx size by summing length of all columns
    if not col_names:
        continue
    
    sum_expr = " + ".join([f"length(CAST({c} AS TEXT))" for c in col_names])
    try:
        cur.execute(f"SELECT sum({sum_expr}) FROM {table_name}")
        size = cur.fetchone()[0] or 0
        sizes.append((size, table_name))
    except Exception as e:
        print(f"Error on {table_name}: {e}")

sizes.sort(reverse=True)
for size, name in sizes:
    print(f"{name}: {size / (1024*1024):.2f} MB")
