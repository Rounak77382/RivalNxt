"""
Service module for Marvel Rivals character and skin extraction.

Provides functions to:
- Extract character and skin data from PAK files
- Ingest extracted data into the database
- Check and run extraction on first build
"""

import sys
import json
import re
from pathlib import Path
from collections import defaultdict
from typing import Dict, Set, Any

# Add project root to path if needed
if '.' not in sys.path:
    sys.path.insert(0, '.')

from core.config.settings import SETTINGS
from rust_ue_tools import PyUnpacker
from pylocres import LocresFile
from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder
from core.db.db import (
    get_connection,
    has_character_data,
    clear_character_data,
    insert_characters,
    insert_skins,
    get_all_characters
)
from typing import Dict, Set, Any, List, Optional


def extract_character_and_skin_data() -> Dict[str, Any]:
    """
    Extract all character and skin data from PAK files.
    
    Returns:
        dict: Structure: {
            "character_id": {
                "name": "character name",
                "skins": {
                    "variant": "skin name",
                    ...
                }
            },
            ...
        }
    """
    from core.config.settings import load_settings
    
    # Reload settings to get the latest marvel_rivals_root
    current_settings = load_settings()
    
    if not current_settings.marvel_rivals_root:
        raise ValueError("marvel_rivals_root is not configured in settings")
    
    from core.config.settings import get_paks_dir
    paks_dir = get_paks_dir(current_settings.marvel_rivals_root)
    
    # Step 1: Extract character names
    print("[1/4] Extracting character names from locres...")
    character_names = _extract_character_names(paks_dir)
    print(f"Extracted {len(character_names)} character names")
    
    # Step 2: Extract skin IDs
    print("[2/4] Extracting skin IDs from pakchunkCharacter...")
    character_skins = _extract_skin_ids(paks_dir)
    print(f"Extracted {sum(len(s) for s in character_skins.values())} skin IDs")
    
    # Step 3: Extract skin names
    print("[3/4] Extracting skin names from pakchunkLocres...")
    skin_names = _extract_skin_names(paks_dir)
    print(f"Extracted {len(skin_names)} skin names")
    
    # Step 4: Combine data
    print("[4/4] Building final database...")
    final_data = _combine_data(character_names, character_skins, skin_names)
    
    total_skins = sum(len(char['skins']) for char in final_data.values())
    print(f"SUCCESS! Extracted {len(final_data)} characters with {total_skins} skins")
    
    return final_data


def _extract_character_names(paks_dir: Path) -> Dict[str, str]:
    """Extract character names from locres files."""
    from core.extraction.marvel_rivals_ids import extract_character_names_from_locres
    return extract_character_names_from_locres(paks_dir)


def _extract_skin_ids(paks_dir: Path) -> Dict[str, Set[str]]:
    """Extract skin IDs from character pak files."""
    from core.extraction.marvel_rivals_ids import extract_skin_ids_from_pak
    return extract_skin_ids_from_pak(paks_dir)


def _extract_skin_names(paks_dir: Path) -> Dict[str, str]:
    """Extract skin names from locres files."""
    from core.extraction.marvel_rivals_ids import extract_skin_names_from_locres
    return extract_skin_names_from_locres(paks_dir)


def _combine_data(character_names: Dict[str, str], 
                  character_skins: Dict[str, Set[str]], 
                  skin_names: Dict[str, str]) -> Dict[str, Any]:
    """Combine extracted data into final structure."""
    from core.extraction.marvel_rivals_ids import combine_extraction_data
    return combine_extraction_data(character_names, character_skins, skin_names)


def ingest_into_database(data: Dict[str, Any]) -> Dict[str, List[Any]]:
    """
    Ingest extracted character and skin data into the database.
    
    Args:
        data: Extracted data from extract_character_and_skin_data()
        
    Returns:
        Dict containing lists of 'new_characters' and 'new_skins' (name strings)
    """
    conn = get_connection()
    
    changes = {
        "new_characters": [],
        "new_skins": []
    }
    
    try:
        # Before clearing, get current state to find what's new
        existing_chars_data = get_all_characters(conn)
        existing_char_ids = {c['character_id'] for c in existing_chars_data}
        existing_skin_ids = set()
        for c in existing_chars_data:
            char_id = c['character_id']
            for s in c['skins']:
                existing_skin_ids.add(f"{char_id}{s['variant']}")

        # Clear existing data
        clear_character_data(conn)
        
        # Prepare character and skin data
        characters = []
        skins = []
        
        for char_id, char_data in data.items():
            characters.append((char_id, char_data['name']))
            
            # Check if this character is new
            if char_id not in existing_char_ids:
                changes["new_characters"].append(char_data['name'])
            
            for variant, skin_name in char_data['skins'].items():
                skin_id = f"{char_id}{variant}"
                skins.append((skin_id, char_id, variant, skin_name))
                
                # Check if this skin is new
                if skin_id not in existing_skin_ids:
                    # Format as "Character Name - Skin Name"
                    changes["new_skins"].append(f"{char_data['name']} - {skin_name}")
        
        # Insert characters and skins
        if characters:
            insert_characters(conn, characters)
        
        if skins:
            insert_skins(conn, skins)
        
        print(f"Ingested {len(characters)} characters and {len(skins)} skins into database")
        if changes["new_characters"]:
            print(f"New characters: {', '.join(changes['new_characters'])}")
        if changes["new_skins"]:
            print(f"New skins: {len(changes['new_skins'])} found")
            
        return changes
        
    except Exception as e:
        raise Exception(f"Failed to ingest data: {e}")
    finally:
        conn.close()


def extract_and_ingest() -> Dict[str, List[Any]]:
    """
    Extract character and skin data from PAK files and ingest into database.
    This is the main entry point for rebuilding the database.
    """
    print("="*80)
    print("MARVEL RIVALS CHARACTER & SKIN DATA EXTRACTION")
    print("="*80)
    
    try:
        # Extract data
        data = extract_character_and_skin_data()
        
        # Ingest into database
        changes = ingest_into_database(data)
        
        print("="*80)
        print("EXTRACTION AND INGESTION COMPLETE!")
        print("="*80)
        
        return changes
        
    except Exception as e:
        print(f"ERROR: Extraction failed - {e}")
        raise


def run_extraction_if_needed() -> bool:
    """
    Check if database has character data, and run extraction if empty.
    This should be called on application startup.
    
    Returns:
        bool: True if extraction was run, False if data already existed
    """
    conn = get_connection()
    try:
        if not has_character_data(conn):
            print("No character data found in database. Running initial extraction...")
            conn.close()  # Close before extraction
            extract_and_ingest()
            return True
        else:
            print("Character data already present in database.")
            return False
    finally:
        try:
            conn.close()
        except:
            pass

