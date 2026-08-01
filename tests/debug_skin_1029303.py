"""
Debug script to check if specific skin ID exists and trace extraction
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

def check_skin_in_database():
    """Check if skin 1029303 exists in the database."""
    print("="*60)
    print("Checking Skin ID 1029303 in Database")
    print("="*60)
    
    from core.db.db import get_connection, get_all_characters
    
    target_skin = "1029303"
    target_char = target_skin[:4]  # 1029
    
    conn = get_connection()
    try:
        characters = get_all_characters(conn)
        
        # Find character 1029
        char_1029 = next((c for c in characters if c['character_id'] == target_char), None)
        
        if char_1029:
            print(f"\n✓ Character {target_char} found: {char_1029['name']}")
            print(f"  Skins: {len(char_1029['skins'])}")
            
            # Check if 1029303 exists
            skin_303 = next((s for s in char_1029['skins'] if s['variant'] == '303'), None)
            
            if skin_303:
                print(f"\n✅ Skin 1029303 EXISTS in database!")
                print(f"  Name: {skin_303['name']}")
            else:
                print(f"\n❌ Skin 1029303 NOT FOUND in database")
                print(f"\n  Available variants for {char_1029['name']}:")
                for skin in sorted(char_1029['skins'], key=lambda s: s['variant']):
                    print(f"    {target_char}{skin['variant']} -> {skin['name']}")
        else:
            print(f"\n❌ Character {target_char} not found in database")
            
    finally:
        conn.close()

def check_skin_in_extraction():
    """Check if skin 1029303 is extracted from PAK files."""
    print("\n" + "="*60)
    print("Checking Skin ID 1029303 in PAK Extraction")
    print("="*60)
    
    from core.extraction.marvel_rivals_ids import (
        extract_skin_ids_from_pak,
        extract_skin_names_from_locres,
        extract_character_names_from_locres,
        combine_extraction_data
    )
    from core.config.settings import SETTINGS
    from pathlib import Path
    
    paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Content" / "Paks"
    
    print(f"\nPAK directory: {paks_dir}")
    print(f"Exists: {paks_dir.exists()}\n")
    
    print("[1/4] Extracting character names...")
    char_names = extract_character_names_from_locres(paks_dir)
    print(f"  Found {len(char_names)} characters")
    
    char_1029 = char_names.get('1029')
    if char_1029:
        print(f"  Character 1029: {char_1029}")
    
    print("\n[2/4] Extracting skin IDs from PAK...")
    skin_ids = extract_skin_ids_from_pak(paks_dir)
    print(f"  Total skin IDs found: {len(skin_ids)}")
    
    if '1029303' in skin_ids:
        print(f"  ✅ Skin ID 1029303 found in PAK extraction")
    else:
        print(f"  ❌ Skin ID 1029303 NOT found in PAK extraction")
        # Check what 1029xxx skins exist
        skin_1029 = [sid for sid in skin_ids if sid.startswith('1029')]
        print(f"\n  Found {len(skin_1029)} skins for character 1029:")
        for sid in sorted(skin_1029):
            print(f"    {sid}")
    
    print("\n[3/4] Extracting skin names from locres...")
    skin_names = extract_skin_names_from_locres(paks_dir)
    print(f"  Total skin names found: {len(skin_names)}")
    
    if '1029303' in skin_names:
        print(f"  ✅ Skin 1029303 has name: '{skin_names['1029303']}'")
    else:
        print(f"  ⚠ Skin 1029303 has no name in locres (will use fallback)")
    
    print("\n[4/4] Combining data...")
    combined = combine_extraction_data(char_names, skin_ids, skin_names)
    
    # Check character 1029
    char_1029_data = next((c for c in combined if c['character_id'] == '1029'), None)
    
    if char_1029_data:
        print(f"\n✓ Character 1029: {char_1029_data['name']}")
        print(f"  Total skins extracted: {len(char_1029_data['skins'])}")
        
        skin_303 = next((s for s in char_1029_data['skins'] if s['variant'] == '303'), None)
        
        if skin_303:
            print(f"\n✅ Skin 303 found in extraction!")
            print(f"  Name: {skin_303['name']}")
        else:
            print(f"\n❌ Skin 303 NOT in final extraction data")
            print(f"\n  Available variants:")
            for skin in sorted(char_1029_data['skins'], key=lambda s: s['variant']):
                print(f"    {skin['variant']} -> {skin['name']}")

if __name__ == "__main__":
    check_skin_in_database()
    check_skin_in_extraction()
