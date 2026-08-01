"""
Comprehensive search of locres file for skin 1029303
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS
from rust_ue_tools import PyUnpacker
from pylocres import LocresFile

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
output_dir = Path("temp_locres_search_1029303")
output_dir.mkdir(exist_ok=True)

print("="*80)
print("COMPREHENSIVE LOCRES SEARCH FOR 1029303")
print("="*80)

try:
    print("\nUnpacking pakchunkLocres-Windows.pak...")
    unpacker = PyUnpacker()
    unpacker.unpack_pak(
        str(paks_dir / "pakchunkLocres-Windows.pak"),
        str(output_dir),
        aes_key=SETTINGS.aes_key_hex,
        force=True,
        quiet=True
    )
    
    locres_files = list(output_dir.rglob("*.locres"))
    en_files = [lf for lf in locres_files if 'en' in lf.parent.name.lower()]
    en_file = en_files[0] if en_files else locres_files[0]
    
    print(f"Reading: {en_file.name}")
    
    lf = LocresFile()
    lf.read(str(en_file))
    
    print(f"\nSearching ALL namespaces and entries for '1029303'...\n")
    
    found_entries = []
    total_namespaces = 0
    total_entries = 0
    
    for ns_name, namespace in lf.namespaces.items():
        total_namespaces += 1
        for entry_key, entry in namespace.entrys.items():
            total_entries += 1
            
            # Search in both key and value
            if '1029303' in entry_key or '1029303' in entry.translation:
                found_entries.append({
                    'namespace': ns_name,
                    'key': entry_key,
                    'value': entry.translation
                })
    
    print(f"Searched:")
    print(f"  - {total_namespaces} namespaces")
    print(f"  - {total_entries:,} total entries")
    
    print(f"\n{'='*80}")
    print("RESULTS")
    print(f"{'='*80}\n")
    
    if found_entries:
        print(f"✅ FOUND {len(found_entries)} entries containing '1029303':\n")
        for i, entry in enumerate(found_entries, 1):
            print(f"{i}. Namespace: {entry['namespace']}")
            print(f"   Key: {entry['key']}")
            print(f"   Value: {entry['value']}")
            print()
    else:
        print("❌ NO entries found containing '1029303'")
        print("\nThis confirms that skin 1029303 has NO localization entry!")
        
        # Show what Magik entries DO exist
        print(f"\n{'='*80}")
        print("Magik Entries Found (for reference)")
        print(f"{'='*80}\n")
        
        magik_entries = []
        for ns_name, namespace in lf.namespaces.items():
            for entry_key, entry in namespace.entrys.items():
                if '1029' in entry_key and any(term in entry_key for term in ['Skin', 'Item', 'Table']):
                    magik_entries.append({
                        'key': entry_key,
                        'value': entry.translation
                    })
        
        # Show unique skin IDs
        unique_skin_ids = set()
        for entry in magik_entries:
            import re
            match = re.search(r'1029(\d{3})', entry['key'])
            if match:
                unique_skin_ids.add('1029' + match.group(1))
        
        print(f"Found {len(unique_skin_ids)} unique Magik skin IDs in locres:")
        for sid in sorted(unique_skin_ids):
            variant = sid[4:]
            print(f"  {sid} (variant {variant})")
        
        if '1029303' not in unique_skin_ids:
            print(f"\n⚠️ Confirmed: 1029303 is NOT in the locres file")
            print("   Skin exists in PAK assets but has no name entry")

finally:
    import shutil
    shutil.rmtree(output_dir, ignore_errors=True)
    print(f"\n{'='*80}")
    print("Search Complete")
    print(f"{'='*80}")
