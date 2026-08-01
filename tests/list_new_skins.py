"""
List all new skin IDs and character IDs from patch PAKs
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
print("Extracting All New Skin IDs from Patch PAKs")
print("="*60)

# Get all PAK files
pak_map = extract_pak_asset_map_from_folder(str(paks_dir), aes_key=SETTINGS.aes_key_hex)

# Focus on official patch PAKs only (not mod PAKs)
patch_paks = {name: assets for name, assets in pak_map.items() 
              if name.startswith('Patch_-Windows_')}

print(f"\nScanning {len(patch_paks)} official patch PAK(s)...\n")

all_skins = defaultdict(set)  # character_id -> set of variants
pattern = re.compile(r'/Characters/(\d{4})/(\d{4})(\d{2,3})(/|_)')

for pak_name, assets in patch_paks.items():
    char_assets = [a for a in assets if "/Characters/" in a]
    
    for path in char_assets:
        match = pattern.search(path)
        if match:
            char_id = match.group(1)
            skin_char_id = match.group(2)
            variant = match.group(3)
            
            if char_id == skin_char_id:
                skin_id = f"{char_id}{variant}"
                if len(skin_id) == 7:
                    all_skins[char_id].add(variant)

# Sort and display
print("="*60)
print("New Skins Found in Patches (by Character)")
print("="*60)

sorted_chars = sorted(all_skins.keys())

print(f"\nTotal Characters with new skins: {len(sorted_chars)}\n")

for char_id in sorted_chars:
    variants = sorted(all_skins[char_id])
    print(f"\nCharacter {char_id}:")
    print(f"  Total variants: {len(variants)}")
    print(f"  Skin IDs: {', '.join([char_id + v for v in variants])}")

# Summary by variant prefix
print("\n" + "="*60)
print("Summary by Variant Type")
print("="*60)

variant_counts = defaultdict(int)
for char_id, variants in all_skins.items():
    for variant in variants:
        prefix = variant[0]  # First digit (1xx, 2xx, 3xx, etc.)
        variant_counts[prefix] += 1

print("\nSkin distribution:")
variant_labels = {
    '0': '0xx (Base/Default)',
    '1': '1xx (Tier 1)',
    '2': '2xx (Tier 2)', 
    '3': '3xx (Tier 3)',
    '4': '4xx (Tier 4)',
    '5': '5xx (Tier 5)',
    '6': '6xx (Tier 6)',
    '7': '7xx (Tier 7)',
    '8': '8xx (Tier 8)',
    '9': '9xx (Tier 9)',
}

for prefix in sorted(variant_counts.keys()):
    label = variant_labels.get(prefix, f'{prefix}xx')
    count = variant_counts[prefix]
    print(f"  {label}: {count} skins")

# Total count
total_skins = sum(len(variants) for variants in all_skins.values())
print(f"\n🎉 Grand Total: {total_skins} new skins from patches!")

# Check if 1029303 is there
if '1029' in all_skins and '303' in all_skins['1029']:
    print(f"\n✅ Confirmed: Skin 1029303 (Magik variant 303) is present!")
