"""
Debug script - Show all character IDs and skin IDs from extraction
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.extraction.marvel_rivals_ids import (
    extract_skin_ids_from_pak,
    extract_character_names_from_locres
)
from core.config.settings import SETTINGS

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"

print("="*80)
print("ALL CHARACTER IDs AND SKIN IDs FROM EXTRACTION")
print("="*80)

# Get character names
print("\n[1/2] Loading character names...")
char_names = extract_character_names_from_locres(paks_dir)
print(f"Found {len(char_names)} characters\n")

# Get skin IDs
print("[2/2] Extracting skin IDs from all PAKs...")
character_skins = extract_skin_ids_from_pak(paks_dir)
print(f"Found {sum(len(skins) for skins in character_skins.values())} total skin IDs\n")

# Display all
print("="*80)
print("COMPLETE LIST")
print("="*80)

sorted_char_ids = sorted(character_skins.keys())

for char_id in sorted_char_ids:
    char_name = char_names.get(char_id, f"Unknown Character")
    
    print(f"\n{'='*80}")
    print(f"Character ID: {char_id} - {char_name.upper()}")
    print(f"{'='*80}")
    
    skins = sorted(character_skins[char_id])
    print(f"Total Skins: {len(skins)}\n")
    
    # Group by variant prefix (1xx, 3xx, 5xx, etc.)
    by_tier = {}
    for skin_id in skins:
        variant = skin_id[4:]  # Get the variant part (e.g., "001", "100", "303")
        tier = variant[0] if variant else '0'
        if tier not in by_tier:
            by_tier[tier] = []
        by_tier[tier].append(skin_id)
    
    # Display by tier
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
        tier_skins = by_tier[tier]
        tier_label = tier_names.get(tier, f'Tier {tier}')
        print(f"  {tier_label} ({len(tier_skins)} skins):")
        
        # Show in rows of 5
        for i in range(0, len(tier_skins), 5):
            row = tier_skins[i:i+5]
            print(f"    {', '.join(row)}")

# Summary
print("\n" + "="*80)
print("SUMMARY")
print("="*80)
print(f"Total Characters: {len(character_skins)}")
print(f"Total Skin IDs: {sum(len(skins) for skins in character_skins.values())}")
print(f"\nCharacter ID Range: {min(sorted_char_ids)} - {max(sorted_char_ids)}")

# Show distribution
tier_distribution = {}
for skins in character_skins.values():
    for skin_id in skins:
        variant = skin_id[4:]
        tier = variant[0] if variant else '0'
        tier_distribution[tier] = tier_distribution.get(tier, 0) + 1

print("\nSkin Distribution by Tier:")
for tier in sorted(tier_distribution.keys()):
    tier_label = tier_names.get(tier, f'Tier {tier}')
    count = tier_distribution[tier]
    print(f"  {tier_label}: {count} skins")

print("\n" + "="*80)
