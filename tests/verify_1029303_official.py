"""
Check if skin 1029303 exists in official patch PAKs (not mods)
"""

import sys
import re
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS
from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"

print("="*60)
print("Checking for Skin 1029303 in Official Patch PAKs ONLY")
print("="*60)

pak_map = extract_pak_asset_map_from_folder(str(paks_dir), aes_key=SETTINGS.aes_key_hex)

# Only check official Patch PAKs
official_patches = {
    name: assets for name, assets in pak_map.items() 
    if name.startswith('Patch_-Windows_') and '_P' in name
}

print(f"\nFound {len(official_patches)} official patch PAKs:\n")
for name in official_patches.keys():
    print(f"  {name}")

print(f"\n{'='*60}")
print("Scanning for character 1029 skin assets...")
print(f"{'='*60}\n")

pattern = re.compile(r'/Characters/(\d{4})/(\d{4})(\d{2,3})(/|_)')

found_in_patches = {}

for pak_name, assets in official_patches.items():
    char_assets = [a for a in assets if "/Characters/1029/" in a]
    
    if char_assets:
        print(f"\n{pak_name}:")
        print(f"  Total 1029 assets: {len(char_assets)}")
        
        # Extract skin IDs
        skin_ids = set()
        for path in char_assets:
            match = pattern.search(path)
            if match:
                char_id = match.group(1)
                skin_char_id = match.group(2)
                variant = match.group(3)
                
                if char_id == skin_char_id == '1029':
                    skin_id = f"{char_id}{variant}"
                    if len(skin_id) == 7:
                        skin_ids.add(skin_id)
        
        if skin_ids:
            found_in_patches[pak_name] = sorted(skin_ids)
            print(f"  Skin IDs: {', '.join(sorted(skin_ids))}")
            
            if '1029303' in skin_ids:
                print(f"  ✅ CONTAINS 1029303!")

# Summary
print(f"\n{'='*60}")
print("Summary")
print(f"{'='*60}\n")

if any('1029303' in skins for skins in found_in_patches.values()):
    print("✅ Skin 1029303 FOUND in official patch PAKs!")
    for pak, skins in found_in_patches.items():
        if '1029303' in skins:
            print(f"  Located in: {pak}")
else:
    print("❌ Skin 1029303 NOT FOUND in any official patch PAK")
    print("\nThis means 1029303 only exists in user mods, not the base game!")
    
    all_magik_skins = set()
    for skins in found_in_patches.values():
        all_magik_skins.update(skins)
    
    if all_magik_skins:
        print(f"\nOfficial Magik skins from patches:")
        for sid in sorted(all_magik_skins):
            variant = sid[4:]
            print(f"  {sid} (variant {variant})")
