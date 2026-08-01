"""
Test the updated extraction to verify patch PAKs are included
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.extraction.marvel_rivals_ids import (
    extract_skin_ids_from_pak,
    extract_character_names_from_locres,
    extract_skin_names_from_locres
)
from core.config.settings import SETTINGS

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"

print("="*60)
print("Testing Updated Extraction with Patch PAKs")
print("="*60)

print("\n[1/3] Extracting character names...")
char_names = extract_character_names_from_locres(paks_dir)
print(f"✓ Found {len(char_names)} character names")

if '1029' in char_names:
    print(f"  Character 1029: {char_names['1029']}")

print("\n[2/3] Extracting skin IDs from ALL PAKs...")
character_skins = extract_skin_ids_from_pak(paks_dir)
total_skins = sum(len(skins) for skins in character_skins.values())
print(f"✓ Found {total_skins} total skin IDs")

# Check for Magik and 1029303
if '1029' in character_skins:
    magik_skins = sorted(character_skins['1029'])
    print(f"\n  Magik (1029) skins: {len(magik_skins)}")
    
    # Show first few and check for 303
    print(f"  Sample variants: {', '.join(magik_skins[:10])}")
    
    if any(v == '303' for v in [s[4:] for s in magik_skins]):
        print(f"  ✅ Variant 303 found!")
    else:
        print(f"  ❌ Variant 303 NOT found")

print("\n[3/3] Extracting skin names from locres...")
skin_names = extract_skin_names_from_locres(paks_dir)
print(f"✓ Found {len(skin_names)} skin names")

# Check if 1029303 has a name
if '1029303' in skin_names:
    print(f"\n  ✅ Skin 1029303 name: '{skin_names['1029303']}'")
else:
    print(f"\n  ⚠ Skin 1029303 name not found in locres (will use fallback)")

print("\n" + "="*60)
print("Summary")
print("="*60)
print(f"Characters: {len(char_names)}")
print(f"Skin IDs: {total_skins}")
print(f"Skin names: {len(skin_names)}")

# Expected with patches: ~450+ skins (base ~366 + patches ~391)
expected_min = 700
if total_skins >= expected_min:
    print(f"\n✅ Extraction working correctly! ({total_skins} >= {expected_min} expected)")
else:
    print(f"\n⚠ May be missing patches ({total_skins} < {expected_min} expected)")
