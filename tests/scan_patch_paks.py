"""
Scan Patch PAKs for character/skin assets
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
print("Scanning Patch PAKs for Character Assets")
print("="*60)

# Get all PAK files
pak_map = extract_pak_asset_map_from_folder(str(paks_dir), aes_key=SETTINGS.aes_key_hex)

# Focus on patch PAKs
patch_paks = {name: assets for name, assets in pak_map.items() if 'Patch' in name or '_P' in name}

print(f"\nFound {len(patch_paks)} patch PAK(s)\n")

all_char_assets = []
skin_ids_by_pak = defaultdict(set)

for pak_name, assets in patch_paks.items():
    print(f"Scanning: {pak_name}")
    char_assets = [a for a in assets if "/Characters/" in a]
    print(f"  Character assets: {len(char_assets)}")
    
    all_char_assets.extend(char_assets)
    
    # Extract skin IDs from this PAK
    pattern = re.compile(r'/Characters/(\d{4})/(\d{4})(\d{2,3})(/|_)')
    
    for path in char_assets:
        match = pattern.search(path)
        if match:
            char_id = match.group(1)
            skin_char_id = match.group(2)
            variant = match.group(3)
            
            if char_id == skin_char_id:
                skin_id = f"{char_id}{variant}"
                if len(skin_id) == 7:
                    skin_ids_by_pak[pak_name].add(skin_id)
    
    if skin_ids_by_pak[pak_name]:
        print(f"  Unique skin IDs: {len(skin_ids_by_pak[pak_name])}")

print("\n" + "="*60)
print("Checking for Skin 1029303")
print("="*60)

found_in = []
for pak_name, skin_ids in skin_ids_by_pak.items():
    if '1029303' in skin_ids:
        found_in.append(pak_name)

if found_in:
    print(f"\n✅ Skin 1029303 FOUND in:")
    for pak in found_in:
        print(f"  • {pak}")
else:
    print("\n❌ Skin 1029303 NOT found in any patch PAK")
    
    # Show what Magik skins ARE in patches
    print("\nMagik skins (1029xxx) found in patches:")
    all_magik = set()
    for pak_name, skin_ids in skin_ids_by_pak.items():
        magik_skins = {sid for sid in skin_ids if sid.startswith('1029')}
        if magik_skins:
            all_magik.update(magik_skins)
            print(f"\n  {pak_name}:")
            for sid in sorted(magik_skins):
                print(f"    {sid}")
    
    if all_magik:
        print(f"\n  Total unique Magik skins in patches: {len(all_magik)}")
    else:
        print("\n  No Magik skins found in any patch PAK")

# Show total summary
print("\n" + "="*60)
print("Summary")
print("="*60)

all_skins = set()
for skin_ids in skin_ids_by_pak.values():
    all_skins.update(skin_ids)

print(f"Total unique skins in patch PAKs: {len(all_skins)}")
print(f"Total character assets in patches: {len(all_char_assets)}")
