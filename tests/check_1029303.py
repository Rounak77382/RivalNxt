"""
Simple diagnostic for skin 1029303
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db.db import get_connection, get_all_characters

print("="*60)
print("Checking Database for Skin 1029303")
print("="*60)

conn = get_connection()
try:
    characters = get_all_characters(conn)
    
    # Find character 1029
    char_1029 = next((c for c in characters if c['character_id'] == '1029'), None)
    
    if char_1029:
        print(f"\n✓ Character 1029: {char_1029['name']}")
        print(f"  Total skins: {len(char_1029['skins'])}\n")
        
        # Show all variants
        print("All variants:")
        for skin in sorted(char_1029['skins'], key=lambda s: s['variant']):
            skin_id = f"1029{skin['variant']}"
            print(f"  {skin_id} -> {skin['name']}")
        
        # Check for 303
        has_303 = any(s['variant'] == '303' for s in char_1029['skins'])
        
        if has_303:
            print(f"\n✅ Variant 303 exists!")
        else:
            print(f"\n❌ Variant 303 NOT FOUND")
            print(f"\nVariants with 3xx:")
            for skin in char_1029['skins']:
                if skin['variant'].startswith('3'):
                    print(f"  {skin['variant']} -> {skin['name']}")
    else:
        print(f"\n❌ Character 1029 not found in database")
        print("\nAvailable characters:")
        for char in sorted(characters, key=lambda c: c['character_id'])[:10]:
            print(f"  {char['character_id']} -> {char['name']}")
        
finally:
    conn.close()
