import os
import sys
import tempfile
import zipfile
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder

def main():
    zip_path = r"C:\Users\rouna\OneDrive\Documents\Marvel_Rivals_Mods\downloads\Even_Horizon_-_Luna_Snow-7333-1-0-1774141136-16.zip"
    
    with tempfile.TemporaryDirectory() as tmpdir:
        print(f"Extracting {zip_path} to {tmpdir}...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(tmpdir)
            
        print("Running extract_pak_asset_map_from_folder...")
        # aes_key is not strictly needed just to parse the UTOC headers usually, but we'll see
        try:
            aes_key = "0" * 64
            result = extract_pak_asset_map_from_folder(tmpdir, aes_key=aes_key)
            print("Success:", result)
        except Exception as e:
            print("Failed:", e)

if __name__ == '__main__':
    main()
