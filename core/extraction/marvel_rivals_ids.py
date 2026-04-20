"""
MARVEL RIVALS IDS EXTRACTION - 100% PAK SOURCED
================================================================================

Extracts character IDs, character names, skin IDs, and skin names
directly from Marvel Rivals game pak files.

100% PAK-SOURCED - NO EXTERNAL DATA NEEDED!

Can be used as:
1. Standalone script: python marvel_rivals_ids.py
2. Imported module: from core.extraction.marvel_rivals_ids import extract_*
"""

import sys
import json
import re
from pathlib import Path
from collections import defaultdict

if '.' not in sys.path:
    sys.path.insert(0, '.')

from core.config.settings import SETTINGS
from rust_ue_tools import PyUnpacker
from pylocres import LocresFile


def is_internal_name(name: str) -> bool:
    """Check if a skin/character name is an internal or placeholder name."""
    n = name.strip().lower()
    # Check for "[sg]", "[tbd]" or other bracketed tags at the start of names
    if n.startswith('['):
        return True
    # "esu-recolor" and similar variations
    if 'recolor' in n:
        return True
    # Placeholders
    if n.startswith('placeholder') or n.endswith('**'):
        return True
    return False


def extract_character_names_from_locres(paks_dir):
    """Extract character names from pakchunkLocres using discovered patterns."""
    output_dir_locres = Path("temp_locres_chars")
    output_dir_locres.mkdir(exist_ok=True)
    
    try:
        unpacker = PyUnpacker()
        unpacker.unpack_pak(
            str(paks_dir / "pakchunkLocres-Windows.pak"),
            str(output_dir_locres),
            aes_key=SETTINGS.aes_key_hex,
            force=True,
            quiet=True
        )
        
        locres_files = list(output_dir_locres.rglob("*.locres"))
        en_files = [lf for lf in locres_files if 'en' in lf.parent.name.lower()]
        en_file = en_files[0] if en_files else locres_files[0]
        
        lf = LocresFile()
        lf.read(str(en_file))
        
        all_strings = {}
        for ns_name, namespace in lf.namespaces.items():
            all_strings[ns_name] = {}
            for entry_key, entry in namespace.entrys.items():
                all_strings[ns_name][entry_key] = entry.translation
        
        character_names = {}
        
        # Pattern 1: MarvelItemTable_{CHAR_ID}_ItemName
        for ns_name, entries in all_strings.items():
            if not ns_name.startswith("123_Customize_"):
                continue
            
            char_id_match = re.search(r'123_Customize_(\d{4})_ST', ns_name)
            if not char_id_match:
                continue
            
            char_id = char_id_match.group(1)
            key_to_find = f'MarvelItemTable_{char_id}_ItemName'
            if key_to_find in entries:
                value = entries[key_to_find].strip().lower()
                if len(value) < 30:
                    character_names[char_id] = value
        
        # Pattern 2: UIHeroTable_{CHAR_ID}0_HeroBasic_TName (fallback for NPCs)
        for ns_name, entries in all_strings.items():
            for key, value in entries.items():
                hero_match = re.search(r'UIHeroTable_(\d{4})0_HeroBasic_TName', key)
                if hero_match:
                    char_id = hero_match.group(1)
                    if char_id not in character_names:
                        clean_name = value.strip().replace('Lobby NPC - ', '').lower()
                        if len(clean_name) < 30:
                            character_names[char_id] = clean_name
        
        # Filter out garbage/internal names
        filtered_character_names = {k: v for k, v in character_names.items() if not is_internal_name(v)}
        return filtered_character_names
        
    finally:
        import shutil
        shutil.rmtree(output_dir_locres, ignore_errors=True)


def extract_skin_ids_from_pak(paks_dir):
    """Extract skin IDs from ALL PAK files containing character assets.
    
    This scans all PAK files (base, patches, DLC) to find character skins,
    ensuring we catch new skins added in updates.
    """
    # Scan valid PAK files individually to ensure strict exclusion and avoid verbose logging
    valid_paks = []
    # Search recursively for .pak files
    for pak_path in paks_dir.rglob("*.pak"):
        pak_name = pak_path.name.lower()
        
        # STRICT ~mods EXCLUSION
        if "~mods" in str(pak_path).lower():
            continue
            
        # Also exclude temporary or mod-like files if any remain
        if '9999999' in pak_name or '9999998' in pak_name:
             continue
        # Check if this PAK contains character data (Base, Patch, or Character specific)
        is_relevant = False
        if "marvel" in pak_name and "character" in pak_name: # e.g. pakchunkMarvel-Characters-...
            is_relevant = True
        elif any(keyword in pak_name for keyword in ['patch', 'dlc', 'season', '_p']):
            is_relevant = True
        elif "pakchunklocres" in pak_name:
            continue # Skip locres paks for asset scanning
        
        # If broadly relevant, we'll scan it. Converting logic from previous robust version.
        # Actually, since we want to be thorough but safe, let's scan all official PAKs 
        # that aren't obviously unrelated (like pure audio/locres if named so).
        # The previous logic checked for "Character" in asset paths, so effectively it scanned everything.
        # We will scan all valid official PAKs.
        valid_paks.append(pak_path)

    print(f"Found {len(valid_paks)} valid PAKs to scan.")
    
    character_skins = defaultdict(set)
    # Updated pattern to allow . as terminator (for direct file matches)
    pattern = re.compile(r'/Characters/(\d{4})/(\d{4})(\d{2,3})(/|_|\.)')
    
    unpacker = PyUnpacker()
    
    for pak_path in valid_paks:
        try:
            assets = []
            # Check for IoStore (.utoc) companion file
            utoc_path = pak_path.with_suffix('.utoc')
            
            if utoc_path.exists():
                # Use list_utoc for IoStore containers
                assets = unpacker.list_utoc(str(utoc_path), aes_key=SETTINGS.aes_key_hex, json_format=False)
            else:
                # Use get_pak_file_list for legacy/standalone PAKs
                assets = unpacker.get_pak_file_list(str(pak_path), aes_key=SETTINGS.aes_key_hex)
            
            # print(f"DEBUG: {pak_path.name} -> {len(assets)} assets")
            
            for asset in assets:
                # asset is a PyAssetPath, convert to string
                # Normalize slashes to ensure regex matches
                asset_path = str(asset).replace('\\', '/')
                
                if "/Characters/" in asset_path:
                    # print(f"DEBUG: Candidate path: {asset_path}") # Commented to avoid spam unless needed
                    match = pattern.search(asset_path)
                    if match:
                        char_id = match.group(1)
                        skin_char_id = match.group(2)
                        variant = match.group(3)
                        
                        if char_id == skin_char_id:
                            skin_id = f"{char_id}{variant}"
                            if len(skin_id) == 7:
                                character_skins[char_id].add(skin_id)

        except Exception as e:
            # Silently continue on individual pak error to be robust
            print(f"Error reading {pak_path.name}: {e}")
            continue

    return character_skins


def extract_skin_names_from_locres(paks_dir):
    """Extract skin names from pakchunkLocres and Patch PAKs using all discovered patterns."""
    output_dir = Path("temp_locres")
    output_dir.mkdir(exist_ok=True)
    
    try:
        # Find all relevant PAKs: Base locres + Patches
        # Note: We prioritize patches by loading them later (overwriting base values)
        paks_to_process = []
        
        # 1. Base Locres
        base_locres = paks_dir / "pakchunkLocres-Windows.pak"
        if base_locres.exists():
            paks_to_process.append(base_locres)
            
        # 2. Patch PAKs (sorted to ensure correct overwrite order)
        # Explicitly exclude anything that might be in a ~mods subdirectory
        all_patch_paks = sorted(paks_dir.glob("Patch*Windows*.pak"))
        patch_paks = [p for p in all_patch_paks if "~mods" not in str(p).lower()]
        paks_to_process.extend(patch_paks)
            
        all_strings = {} # Shared dictionary for all translations (merged)

        # Iterate through all PAKs
        for pak_path in paks_to_process:
            # Create a unique sub-temp dir for this pak to avoid conflicts
            pak_temp_dir = output_dir / pak_path.stem
            pak_temp_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                unpacker = PyUnpacker()
                unpacker.unpack_pak(
                    str(pak_path),
                    str(pak_temp_dir),
                    aes_key=SETTINGS.aes_key_hex,
                    force=True,
                    quiet=True
                )
                
                # Find locres files in this unpacked PAK
                locres_files = list(pak_temp_dir.rglob("*.locres"))
                
                # Filter for English or Game.locres
                # In patches, it's often just 'Content/Localization/Game/en/Game.locres'
                target_files = []
                for lf in locres_files:
                    # Prefer english, but grab everything if we want to be safe. 
                    # Usually we just want the 'en' folder one.
                    if 'en' in lf.parent.name.lower():
                        target_files.append(lf)
                
                # If no specific 'en' folder found, fallback to all locres (unlikely but safe)
                if not target_files:
                    target_files = locres_files
                
                for locres_file in target_files:
                    lf = LocresFile()
                    lf.read(str(locres_file))
                    
                    # Merge into master dict
                    for ns_name, namespace in lf.namespaces.items():
                        if ns_name not in all_strings:
                            all_strings[ns_name] = {}
                        
                        for entry_key, entry in namespace.entrys.items():
                            all_strings[ns_name][entry_key] = entry.translation
                            
            except Exception as e:
                print(f"Error processing {pak_path.name}: {e}")
            finally:
                # Clean up individual pak extraction to save space/time
                import shutil
                shutil.rmtree(pak_temp_dir, ignore_errors=True)
        
        # --- Extraction Logic (Standard Patterns) ---
        
        skin_names = {}
        
        # Pattern 1: HeroUIAsset namespaces
        for ns_name, entries in all_strings.items():
            if not ns_name.startswith("601_HeroUIAsset_"):
                continue
            for key, value in entries.items():
                match = re.search(r'(UISkinTable|HeroUIAssetBPTable)_(\d{7,8})0?_\w+_SkinName', key)
                if match:
                    full_id = match.group(2)
                    skin_id = full_id if len(full_id) == 7 else full_id[:-1]
                    skin_names[skin_id] = value.strip().lower()
        
        # Pattern 2: UISkinTable in Customize namespaces
        for ns_name, entries in all_strings.items():
            if not ns_name.startswith("123_Customize_"):
                continue
            for key, value in entries.items():
                match = re.search(r'UISkinTable_(\d{7})0_SkinBasic_SkinName', key)
                if match:
                    skin_id = match.group(1)
                    if skin_id not in skin_names:
                        skin_names[skin_id] = value.strip().lower()
        
        # Pattern 3: MarvelItemTable in Customize namespaces
        for ns_name, entries in all_strings.items():
            if not ns_name.startswith("123_Customize_"):
                continue
            for key, value in entries.items():
                match = re.search(r'MarvelItemTable_(\d{7})_ItemName', key)
                if match:
                    skin_id = match.group(1)
                    if skin_id not in skin_names:
                        skin_names[skin_id] = value.strip().lower()
        
        # Pattern 4: Color variants with ps prefix
        for ns_name, entries in all_strings.items():
            if ns_name != "123_Customize_ST":
                continue
            for key, value in entries.items():
                match = re.search(r'MarvelItemTable_ps(\d{7})_ItemName', key)
                if match:
                    skin_id = match.group(1)
                    if skin_id not in skin_names:
                        skin_names[skin_id] = value.strip().lower()
                        
        # Filter out garbage/internal names
        filtered_skin_names = {k: v for k, v in skin_names.items() if not is_internal_name(v)}
        return filtered_skin_names
        
    finally:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)


def combine_extraction_data(character_names, character_skins, skin_names):
    """Combine extracted data into final structure."""
    final_data = {}
    all_char_ids = set(character_skins.keys()) | set(character_names.keys())
    
    for char_id in sorted(all_char_ids):
        char_name = character_names.get(char_id, f"Character {char_id}")
        
        final_data[char_id] = {
            "name": char_name,
            "skins": {}
        }
        
        if char_id in character_skins:
            for skin_id in sorted(character_skins[char_id]):
                variant = skin_id[len(char_id):]
                
                if variant == "000":
                    continue
                
                if variant == "001":
                    skin_name = "default"
                elif skin_id in skin_names:
                    skin_name = skin_names[skin_id]
                else:
                    # Use fallback name for skins without locres entry
                    skin_name = f"variant {variant}"
                
                final_data[char_id]["skins"][variant] = skin_name
    
    return final_data


def main():
    """Main entry point when script is run directly."""
    print("="*80)
    print("MARVEL RIVALS COMPLETE SKIN DATA EXTRACTION - 100% PAK SOURCED")
    print("="*80)
    
    paks_dir = SETTINGS.marvel_rivals_root / "MarvelGame" / "Marvel" / "Content" / "Paks"
    
    print("\n[1/4] Extracting character names from locres...")
    character_names = extract_character_names_from_locres(paks_dir)
    print(f"Extracted {len(character_names)} character names")
    
    print("\n[2/4] Extracting skin IDs from all PAK files (including patches)...")
    character_skins = extract_skin_ids_from_pak(paks_dir)
    print(f"Extracted {sum(len(s) for s in character_skins.values())} skin IDs")
    
    print("\n[3/4] Extracting skin names from pakchunkLocres...")
    skin_names = extract_skin_names_from_locres(paks_dir)
    print(f"Extracted {len(skin_names)} skin names")
    
    print("\n[4/4] Building final database...")
    final_data = combine_extraction_data(character_names, character_skins, skin_names)
    
    # Summary
    total_skins = sum(len(char['skins']) for char in final_data.values())
    named_skins = sum(1 for char in final_data.values() for name in char['skins'].values() if not name.startswith("variant"))
    
    print("\n" + "="*80)
    print("SUCCESS! 100% PAK-SOURCED EXTRACTION COMPLETE!")
    print("="*80)
    print(f"Total characters: {len(final_data)}")
    print(f"Total skins: {total_skins}")
    print(f"Skins with pak-sourced names: {named_skins}")
    print(f"Skins with fallback names: {total_skins - named_skins}")

    print("="*80)


if __name__ == "__main__":
    main()
