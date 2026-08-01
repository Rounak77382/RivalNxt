
"""
Search the patch locres dump using the 4 specific patterns from core/extraction/marvel_rivals_ids.py
"""

import re
from pathlib import Path

def search_patterns():
    dump_file = Path("patch_locres_dump.txt")
    if not dump_file.exists():
        print("Dump file not found!")
        return

    print(f"Searching {dump_file} with extraction patterns...")

    # Patterns derived from core/extraction/marvel_rivals_ids.py
    # We also include our "Description" discovery as a 5th bonus pattern since we know it exists
    patterns = {
        "1. HeroUIAsset": {
            "regex": re.compile(r'\[(601_HeroUIAsset_.*?)\] ((?:UISkinTable|HeroUIAssetBPTable)_(\d{7,8})0?_\w+_SkinName)\s*=\s*(.+)'),
            "desc": "Check namespaces starting with 601_HeroUIAsset_"
        },
        "2. UISkinTable": {
            "regex": re.compile(r'\[(123_Customize_.*?)\] (UISkinTable_(\d{7})0_SkinBasic_SkinName)\s*=\s*(.+)'),
            "desc": "Check namespaces starting with 123_Customize_"
        },
        "3. MarvelItemTable": {
            "regex": re.compile(r'\[(123_Customize_.*?)\] (MarvelItemTable_(\d{7})_ItemName)\s*=\s*(.+)'),
            "desc": "Check namespaces starting with 123_Customize_"
        },
        "4. Variation (ps)": {
            "regex": re.compile(r'\[(123_Customize_ST)\] (MarvelItemTable_ps(\d{7})_ItemName)\s*=\s*(.+)'),
            "desc": "Check 123_Customize_ST for ps prefix"
        },
        "5. Description (BONUS)": {
            "regex": re.compile(r'\[(.*?)\] (MarvelItemTable_(\d+)_ItemDescription_AppearanceItemIPSource)\s*=\s*(.+)'),
            "desc": "Fallback description check"
        }
    }

    found_counts = {k: 0 for k in patterns}
    found_1029303 = []

    with open(dump_file, "r", encoding="utf-8") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            
            for pid, pdata in patterns.items():
                match = pdata['regex'].search(line)
                if match:
                    found_counts[pid] += 1
                    
                    # Store found item details
                    # Group indices vary by pattern, but usually: 
                    # 1=Namespace, 2=Key, 3=ID, 4=Value (OR similar, finding ID is key)
                    
                    # Extract ID based on pattern structure
                    found_id = match.group(3)
                    found_key = match.group(2)
                    found_val = match.group(4)
                    
                    if "1029303" in found_id:
                        found_1029303.append({
                            "pattern": pid,
                            "line": line_num,
                            "ns": match.group(1),
                            "key": found_key,
                            "id": found_id,
                            "val": found_val
                        })

    print("\n" + "="*80)
    print("SEARCH RESULTS")
    print("="*80)
    
    for pid, count in found_counts.items():
        print(f"{pid}: {count} matches")

    print("\n" + "="*80)
    print("TARGET CHECK: 1029303")
    print("="*80)
    
    if found_1029303:
        print(f"✅ Found {len(found_1029303)} occurrences for 1029303:")
        for item in found_1029303:
            print(f"\nPattern: {item['pattern']}")
            print(f"Line {item['line']}")
            print(f"NS:   {item['ns']}")
            print(f"Key:  {item['key']}")
            print(f"ID:   {item['id']}")
            print(f"Val:  {item['val']}")
    else:
        print("❌ 1029303 NOT FOUND in any of the 4 standard name patterns.")
        print("(It might only exist in the Description pattern if that didn't trigger above)")

if __name__ == "__main__":
    search_patterns()
