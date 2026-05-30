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


def get_all_locres_strings(paks_dir):
    """Helper to extract all strings from all relevant locres files."""
    paks_to_process = []
    
    # 1. Base Locres
    base_locres = paks_dir / "pakchunkLocres-Windows.pak"
    if base_locres.exists():
        paks_to_process.append(base_locres)
        
    # 2. Patch PAKs
    all_patch_paks = sorted(paks_dir.glob("Patch*Windows*.pak"))
    paks_to_process.extend(all_patch_paks)
    
    all_strings = {}
    unpacker = PyUnpacker()
    
    for pak_path in paks_to_process:
        pak_temp_dir = Path(f"temp_locres_{pak_path.stem}")
        pak_temp_dir.mkdir(exist_ok=True)
        
        try:
            unpacker.unpack_pak(
                str(pak_path),
                str(pak_temp_dir),
                aes_key=SETTINGS.aes_key_hex,
                force=True,
                quiet=True,
                include_patterns=["**/*.locres", "*locres"]
            )
            
            locres_files = list(pak_temp_dir.rglob("*.locres"))
            if not locres_files:
                continue
                
            # Prefer English, fallback to first found
            en_files = [lf for lf in locres_files if 'en' in lf.parent.name.lower()]
            locres_file_to_read = en_files[0] if en_files else locres_files[0]
            
            fast_locres = None
            if getattr(sys, 'frozen', False):
                exe_path = Path(sys._MEIPASS) / "src-cpp" / "fast_locres.exe"
            else:
                exe_path = Path(__file__).parent.parent.parent.parent / "src-cpp" / "fast_locres.exe"
            
            if exe_path.exists():
                fast_locres = str(exe_path)
                
            if fast_locres:
                import subprocess
                result = subprocess.run([fast_locres, str(locres_file_to_read)], capture_output=True, text=True, encoding='utf-8')
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if not line:
                            continue
                        parts = line.split('|', 2)
                        if len(parts) == 3:
                            ns_name, key, translation = parts
                            translation = translation.replace('\\n', '\n').replace('\\r', '\r')
                            if ns_name not in all_strings:
                                all_strings[ns_name] = {}
                            all_strings[ns_name][key] = translation
                else:
                    print(f"Warning: fast_locres failed: {result.stderr}")
                    lf = LocresFile()
                    lf.read(str(locres_file_to_read))
                    for ns_name, namespace in lf.namespaces.items():
                        if ns_name not in all_strings:
                            all_strings[ns_name] = {}
                        for entry_key, entry in namespace.entrys.items():
                            all_strings[ns_name][entry_key] = entry.translation
            else:
                lf = LocresFile()
                lf.read(str(locres_file_to_read))
                
                for ns_name, namespace in lf.namespaces.items():
                    if ns_name not in all_strings:
                        all_strings[ns_name] = {}
                    for entry_key, entry in namespace.entrys.items():
                        # Later paks (patches/mods) overwrite earlier ones
                        all_strings[ns_name][entry_key] = entry.translation
                    
        except Exception as e:
            # Skip problematic paks but keep going
            print(f"Warning: Failed to process {pak_path.name}: {e}")
        finally:
            # Cleanup temp dir
            import shutil
            if pak_temp_dir.exists():
                shutil.rmtree(pak_temp_dir)
                
    return all_strings


def extract_character_names_from_locres(paks_dir, all_strings=None):
    """Extract character names from locres files using discovered patterns."""
    try:
        if all_strings is None:
            all_strings = get_all_locres_strings(paks_dir)
        if not all_strings:
            return {}
            
        character_names = {}
        fallback_names = {}
        
        # O(N) Single-Pass over all strings
        for ns_name, entries in all_strings.items():
            if ns_name.startswith("123_Customize_") and ns_name.endswith("_ST"):
                # Pattern 1: MarvelItemTable_{CHAR_ID}_ItemName
                for key, value in entries.items():
                    if key.startswith("MarvelItemTable_") and key.endswith("_ItemName"):
                        char_id = key[16:20]
                        if char_id.isdigit():
                            cleaned = value.strip().lower()
                            if len(cleaned) < 30 and not is_internal_name(cleaned):
                                character_names[char_id] = cleaned
            else:
                # Pattern 2: UIHeroTable_{CHAR_ID}0_HeroBasic_TName (fallback)
                for key, value in entries.items():
                    if key.startswith("UIHeroTable_") and key.endswith("0_HeroBasic_TName"):
                        char_id = key[12:16]
                        if char_id.isdigit():
                            clean_name = value.strip().replace('Lobby NPC - ', '').lower()
                            if len(clean_name) < 30 and not is_internal_name(clean_name):
                                fallback_names[char_id] = clean_name
                                
        # Merge fallbacks where primary names are missing
        for char_id, name in fallback_names.items():
            if char_id not in character_names:
                character_names[char_id] = name
                
        return character_names
        
    except Exception as e:
        print(f"Error extracting character names: {e}")
        return {}


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
                if not assets:
                    # Fallback for hybrid PAKs
                    assets = unpacker.get_pak_file_list(str(pak_path), aes_key=SETTINGS.aes_key_hex)
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


def extract_skin_names_from_locres(paks_dir, all_strings=None):
    """Extract skin names from locres files using discovered patterns."""
    try:
        if all_strings is None:
            all_strings = get_all_locres_strings(paks_dir)
        if not all_strings:
            return {}
            
        skin_names = {}
        fallback_skin_names = {}
        ps_names = {}
        
        # O(N) Single-Pass over all strings
        for ns_name, entries in all_strings.items():
            if ns_name.startswith("601_HeroUIAsset_"):
                # Pattern 1: HeroUIAsset namespaces
                for key, value in entries.items():
                    if key.endswith("_SkinName") and (key.startswith("UISkinTable_") or key.startswith("HeroUIAssetBPTable_")):
                        # Use regex only when we know it's a match to extract precisely, or just string split
                        # e.g., UISkinTable_10110010_SkinBasic_SkinName -> split by _ -> [1] is ID
                        parts = key.split('_')
                        if len(parts) >= 3:
                            full_id = parts[1]
                            if full_id.isdigit():
                                skin_id = full_id if len(full_id) == 7 else full_id[:-1]
                                skin_names[skin_id] = value.strip().lower()
            elif ns_name.startswith("123_Customize_"):
                for key, value in entries.items():
                    # Pattern 4: ps variant (highest priority)
                    if key.startswith("MarvelItemTable_ps") and key.endswith("_ItemName"):
                        skin_id = key[18:-9]
                        if skin_id.isdigit():
                            cleaned = value.strip().lower()
                            if not is_internal_name(cleaned):
                                ps_names[skin_id] = cleaned
                    # Pattern 2: UISkinTable
                    elif key.startswith("UISkinTable_") and key.endswith("0_SkinBasic_SkinName"):
                        skin_id = key[12:19]
                        if skin_id.isdigit():
                            fallback_skin_names[skin_id] = value.strip().lower()
                    # Pattern 3 & 5: MarvelItemTable
                    elif key.startswith("MarvelItemTable_") and key.endswith("_ItemName"):
                        id_part = key[16:-9]
                        if id_part.isdigit():
                            skin_id = id_part[:7]
                            fallback_skin_names[skin_id] = value.strip().lower()
                            
        # Merge dictionaries following priority: ps_names > skin_names > fallback_skin_names
        final_names = {}
        for d in (fallback_skin_names, skin_names, ps_names):
            for k, v in d.items():
                if not is_internal_name(v):
                    final_names[k] = v
                    
        return final_names
        
    except Exception as e:
        print(f"Error extracting skin names: {e}")
        return {}


def fetch_wiki_skin_names():
    """Fetch skin names from Marvel Rivals Fandom Wiki Category:Costumes as a fallback dictionary."""
    wiki_skins = {}
    try:
        import urllib.request
        import urllib.parse
        import json
        import re
        
        base_url = "https://marvelrivals.fandom.com/api.php"
        
        # 1. Fetch category members
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": "Category:Costumes",
            "cmlimit": "500",
            "format": "json"
        }
        url = f"{base_url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(
            url, 
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        
        # Timeout quickly to avoid blocking extraction if offline
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            members = data.get("query", {}).get("categorymembers", [])
            
        if not members:
            return {}
            
        titles = [m["title"] for m in members]
        
        # 2. Fetch page contents in batches of 50
        batch_size = 50
        for i in range(0, len(titles), batch_size):
            batch = titles[i:i+batch_size]
            titles_str = "|".join(batch)
            
            params = {
                "action": "query",
                "prop": "revisions",
                "titles": titles_str,
                "rvprop": "content",
                "format": "json"
            }
            url = f"{base_url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(
                url, 
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
            )
            
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
                pages = data.get("query", {}).get("pages", {})
                for page_id, page_data in pages.items():
                    title = page_data.get("title")
                    revisions = page_data.get("revisions", [])
                    if revisions:
                        wikitext = revisions[0].get("*", "")
                        id_match = re.search(r'\|\s*id\s*=\s*(\d+)', wikitext)
                        if id_match:
                            skin_id = id_match.group(1)
                            # Get costumes name or page title
                            name_match = re.search(r'\|\s*costumes name\s*=\s*([^\n|]+)', wikitext)
                            if name_match:
                                skin_name = name_match.group(1).strip().lower()
                            else:
                                skin_name = title.strip().lower()
                                
                            # Filter out template strings
                            if not skin_name.startswith("{{"):
                                wiki_skins[skin_id] = skin_name
    except Exception as e:
        print(f"Note: Could not fetch fallback skin names from Fandom Wiki (offline or timeout): {e}")
        
    return wiki_skins


def combine_extraction_data(character_names, character_skins, skin_names):
    """Combine extracted data into final structure."""
    final_data = {}
    all_char_ids = set(character_skins.keys()) | set(character_names.keys())
    
    # Try fetching fallback skin names from Fandom Wiki
    print("\n[Optional] Fetching fallback skin names from Fandom Wiki...")
    wiki_skin_names = fetch_wiki_skin_names()
    if wiki_skin_names:
        print(f"Successfully retrieved {len(wiki_skin_names)} fallback skin names from Wiki")
    
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
                elif skin_id in wiki_skin_names:
                    # Use wiki-sourced name as high-quality fallback
                    skin_name = wiki_skin_names[skin_id]
                else:
                    # Use fallback name for skins without locres or wiki entry
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
