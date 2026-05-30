import sqlite3, os

db_path = os.path.expandvars(r'%APPDATA%\com.rivalnxt.modmanager\mods.db')
print(f"Connecting to {db_path}...")
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Truncate all custom images larger than 1MB to prevent OOM
cur.execute('SELECT count(*) FROM mod_custom_images WHERE length(image_data) > 1000000')
count = cur.fetchone()[0]

if count > 0:
    print(f"Found {count} massive images. Truncating to empty string to prevent OOM...")
    cur.execute("UPDATE mod_custom_images SET image_data = '' WHERE length(image_data) > 1000000")
    conn.commit()
    print("Vacuuming database to reclaim disk space...")
    conn.execute("VACUUM")
else:
    print("No massive images found.")

conn.close()
print("Done!")
