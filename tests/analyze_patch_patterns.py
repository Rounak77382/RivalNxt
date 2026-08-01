
"""
Analyze the patch locres dump to find naming patterns for skins and characters.
"""

import re
from pathlib import Path

def analyze_patterns():
    dump_file = Path("patch_locres_dump.txt")
    if not dump_file.exists():
        print("Dump file not found!")
        return

    print(f"Analyzing {dump_file}...")

    # Regex patterns to look for
    patterns = {
        "MarvelItemTable_ItemName": re.compile(r"MarvelItemTable_(\d+)_ItemName\s*=\s*(.+)"),
        "UISkinTable_SkinName": re.compile(r"UISkinTable_(\d+)_SkinBasic_SkinName\s*=\s*(.+)"),
        "HeroUIAsset_SkinName": re.compile(r"HeroUIAssetBPTable_(\d+)_SkinInfo_SkinName\s*=\s*(.+)"),
        "MarvelItemTable_Desc": re.compile(r"MarvelItemTable_(\d+)_ItemDescription_AppearanceItemIPSource\s*=\s*(.+)"),
    }

    found_data = {k: [] for k in patterns}

    with open(dump_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            for key, pattern in patterns.items():
                match = pattern.search(line)
                if match:
                    found_data[key].append((match.group(1), match.group(2)))

    print("\n--- Pattern Analysis Results ---")
    for key, items in found_data.items():
        print(f"\nPattern: {key} (Found {len(items)})")
        # Sort by ID
        sorted_items = sorted(items, key=lambda x: x[0])
        # Show first 5 and last 5
        for id_val, name in sorted_items[:5]:
            print(f"  {id_val} -> {name}")
        if len(sorted_items) > 10:
            print("  ...")
            for id_val, name in sorted_items[-5:]:
                print(f"  {id_val} -> {name}")
        elif len(sorted_items) > 5: # Show the rest if between 5 and 10
             for id_val, name in sorted_items[5:]:
                print(f"  {id_val} -> {name}")

    # Check specifically for our 1029303 friend in IDs
    print("\n--- Specific Check for 1029303 ---")
    found_specific = False
    for key, items in found_data.items():
        for id_val, name in items:
            if "1029303" in id_val:
                print(f"Found in {key}: {id_val} -> {name}")
                found_specific = True
    
    if not found_specific:
        print("1029303 ID not found in any standard name/desc keys.")

if __name__ == "__main__":
    analyze_patterns()
