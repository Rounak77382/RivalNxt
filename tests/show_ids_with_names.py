"""
Debug script - Show all characters and skins with IDs and Names
"""

import sys
import io
from pathlib import Path

# Set UTF-8 encoding for stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.extraction.marvel_rivals_ids import (
    extract_skin_ids_from_pak,
    extract_character_names_from_locres,
    extract_skin_names_from_locres,
    combine_extraction_data
)
from core.config.settings import SETTINGS

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"

print("="*80)
print("COMPLETE CHARACTER AND SKIN LIST WITH NAMES")
print("="*80)

print("\n[1/3] Extracting character names from locres...")
char_names = extract_character_names_from_locres(paks_dir)
print(f"✓ Found {len(char_names)} character names")

print("\n[2/3] Extracting skin IDs from all PAKs (excluding mods)...")
character_skins = extract_skin_ids_from_pak(paks_dir)
total_skins = sum(len(skins) for skins in character_skins.values())
print(f"✓ Found {total_skins} skin IDs")

print("\n[3/3] Extracting skin names from locres...")
skin_names = extract_skin_names_from_locres(paks_dir)
print(f"✓ Found {len(skin_names)} skin names")

print("\n[4/4] Combining data...")
final_data = combine_extraction_data(char_names, character_skins, skin_names)

print("\n" + "="*80)
print("COMPLETE LIST - CHARACTERS AND SKINS")
print("="*80)

sorted_char_ids = sorted(final_data.keys())

for char_id in sorted_char_ids:
    char_data = final_data[char_id]
    char_name = char_data['name']
    
    print(f"\n{'='*80}")
    print(f"CHARACTER: {char_id} - {char_name.upper()}")
    print(f"{'='*80}")
    
    skins = char_data['skins']
    print(f"Total Skins: {len(skins)}\n")
    
    if not skins:
        print("  (No skins found)")
        continue
    
    # Group by variant prefix
    by_tier = {}
    for variant, skin_name in skins.items():
        tier = variant[0] if variant else '0'
        if tier not in by_tier:
            by_tier[tier] = []
        by_tier[tier].append((variant, skin_name))
    
    tier_names = {
        '0': 'Base/Default',
        '1': 'Tier 1',
        '2': 'Tier 2',
        '3': 'Tier 3',
        '4': 'Tier 4',
        '5': 'Tier 5',
        '6': 'Tier 6',
        '7': 'Tier 7',
        '8': 'Tier 8',
        '9': 'Tier 9',
    }
    
    for tier in sorted(by_tier.keys()):
        tier_skins = sorted(by_tier[tier], key=lambda x: x[0])
        tier_label = tier_names.get(tier, f'Tier {tier}')
        print(f"\n  {tier_label} ({len(tier_skins)} skins):")
        
        for variant, skin_name in tier_skins:
            skin_id = f"{char_id}{variant}"
            # Format: ID -> name
            print(f"    {skin_id} -> {skin_name}")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"Total Characters: {len(final_data)}")

total_final_skins = sum(len(char_data['skins']) for char_data in final_data.values())
print(f"Total Skins: {total_final_skins}")

# Count named vs fallback
named_count = 0
for char_data in final_data.values():
    for skin_name in char_data['skins'].values():
        if not skin_name.startswith('variant '):
            named_count += 1

print(f"  - With names from locres: {named_count}")
print(f"  - With fallback names: {total_final_skins - named_count}")

print(f"\nCharacter ID Range: {min(sorted_char_ids)} - {max(sorted_char_ids)}")

# Tier distribution
tier_distribution = {}
for char_data in final_data.values():
    for variant in char_data['skins'].keys():
        tier = variant[0] if variant else '0'
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

print("\nSkin Distribution by Tier:")
for tier in sorted(tier_distribution.keys()):
    tier_label = tier_names.get(tier, f'Tier {tier}')
    count = tier_distribution[tier]
    print(f"  {tier_label}: {count} skins")

print("\n" + "="*80)
