import os
import zipfile

zip_path = r"C:\Users\rouna\OneDrive\Documents\Marvel_Rivals_Mods\downloads\Even_Horizon_-_Luna_Snow-7333-1-0-1774141136-16.zip"
out_dir = r"C:\Users\rouna\Downloads\mod_7333_unpacked"
os.makedirs(out_dir, exist_ok=True)
with zipfile.ZipFile(zip_path, 'r') as zip_ref:
    zip_ref.extractall(out_dir)
print("Extracted to", out_dir)
