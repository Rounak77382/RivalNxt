"""
Search for skin 1029303 in all official PAK files (excluding ~mods)
"""

import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS
from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"

print("="*80)
print("SEARCHING FOR SKIN 1029303 IN OFFICIAL PAK FILES")
print("="*80)

pak_map = extract_pak_asset_map_from_folder(str(paks_dir), aes_key=SETTINGS.aes_key_hex)

# Exclude mod PAKs
official_paks = {}
mod_paks = {}

for pak_name, assets in pak_map.items():
    if '~mods' in pak_name.lower() or '9999999' in pak_name or '9999998' in pak_name:
        mod_paks[pak_name] = assets
    else:
        official_paks[pak_name] = assets

print(f"\nTotal PAKs: {len(pak_map)}")
print(f"Official PAKs: {len(official_paks)}")
print(f"Mod PAKs (excluded): {len(mod_paks)}")

print(f"\n{'='*80}")
print("Searching all official PAKs for /1029303/ or /1029/1029303/")
print(f"{'='*80}\n")

found_in = []
pattern = re.compile(r'/1029303/')

for pak_name, assets in official_paks.items():
    # Look for assets containing 1029303
    matching_assets = [a for a in assets if '/1029303/' in a or '/1029/1029303' in a]
    
    if matching_assets:
        found_in.append({
            'pak': pak_name,
            'assets': matching_assets
        })
        print(f"✅ FOUND in: {pak_name}")
        print(f"   Matching assets: {len(matching_assets)}")
        for asset in matching_assets[:5]:
            print(f"   - {asset}")
        if len(matching_assets) > 5:
            print(f"   ... and {len(matching_assets) - 5} more")
        print()

# Summary
print(f"{'='*80}")
print("SUMMARY")
print(f"{'='*80}\n")

if found_in:
    print(f"✅ Skin 1029303 FOUND in {len(found_in)} official PAK file(s):")
    for item in found_in:
        print(f"  • {item['pak']} ({len(item['assets'])} assets)")
else:
    print("❌ Skin 1029303 NOT FOUND in any official PAK file")
    print("\n🔍 This means skin 1029303 only exists in user mods!")
    
    # Check in mods too
    print(f"\n{'='*80}")
    print("Checking in MOD PAKs (for reference):")
    print(f"{'='*80}\n")
    
    found_in_mods = []
    for pak_name, assets in mod_paks.items():
        matching_assets = [a for a in assets if '/1029303/' in a or '/1029/1029303' in a]
        if matching_assets:
            found_in_mods.append(pak_name)
            print(f"  Found in mod: {pak_name}")
    
    if found_in_mods:
        print(f"\n⚠️ Skin 1029303 exists ONLY in {len(found_in_mods)} user mod(s)")
        print("  This is NOT part of the official game!")
