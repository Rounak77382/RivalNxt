from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# ---------- Entity map loading ----------

def load_entity_map_from_db() -> Dict[str, str]:
    """Load character and skin names from database."""
    mapping: Dict[str, str] = {}
    try:
        # Add project root to path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        
        from core.db.db import get_connection, get_all_characters
        
        conn = get_connection()
        try:
            characters = get_all_characters(conn)
            
            # Add character names (character_id -> name)
            for char in characters:
                char_id = char['character_id']
                char_name = char['name']
                mapping[char_id] = char_name
                
                # Add skin names (skin ID -> just skin name, NOT "character - skin")
                for skin in char['skins']:
                    variant = skin['variant']
                    skin_name = skin['name']
                    
                    # Skip fallback variant names generated during extraction
                    if skin_name.startswith('variant '):
                        continue
                        
                    skin_id = f"{char_id}{variant}"
                    # Include ALL skins, even "default" (variant 001)
                    # Store just the skin name (we'll add character separately in tags)
                    mapping[skin_id] = skin_name
            
            return mapping
        finally:
            conn.close()
    except Exception as e:
        print(f"Warning: Failed to load from database: {e}", file=sys.stderr)
        return {}

def load_entity_map(path: Optional[str]) -> Dict[str, str]:
    """Load entity mapping from database (preferred) or fallback to JSON file."""
    # Try database first
    db_mapping = load_entity_map_from_db()
    if db_mapping:
        return db_mapping
    
    # Fallback to JSON file if database fails
    mapping: Dict[str, str] = {}
    if not path:
        # Try default in repo root
        default = Path(__file__).resolve().parents[1] / 'character_ids.json'
        
        # If running as PyInstaller bundle, check _MEIPASS
        if not default.exists() and hasattr(sys, '_MEIPASS'):
            default = Path(sys._MEIPASS) / 'character_ids.json'
        
        if default.exists():
            path = str(default)
        else:
            return mapping
    p = Path(path)
    if not p.exists():
        return mapping
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return mapping
    # Support either list of {id:name} or single dict
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                for k, v in item.items():
                    mapping[str(k)] = str(v)
    elif isinstance(data, dict):
        for k, v in data.items():
            mapping[str(k)] = str(v)
    return mapping

# ---------- Normalization helpers ----------

def split_camel(s: str) -> List[str]:
    # Split CamelCase to words: 'HeroKnight' -> ['Hero', 'Knight']
    return re.sub('([a-z0-9])([A-Z])', r"\1 \2", s).split()

def normalize_name(key: str) -> str:
    key = key.replace('_', ' ').replace('-', ' ').strip()
    parts: List[str] = []
    for token in key.split():
        parts.extend(split_camel(token))
    norm = ' '.join(parts) if parts else key
    # collapse spaces
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm.lower()

# ---------- Category detection ----------

CATEGORY_RULES = [
    ("audio", lambda p, fn: ("/wwiseaudio/" in p) or ("/audio/" in p) or ("/sound/" in p) or ("/sfx/" in p) or fn.endswith(('.bnk', '.wem', '.wav'))),
    ("ui", lambda p, fn: ("/ui/" in p) or ("/umg/" in p) or ("/slate/" in p)),
    ("vfx", lambda p, fn: ("/vfx/" in p) or ("/fx/" in p) or ("/niagara/" in p) or re.match(r"^(ns_|ps_|fx_)", fn)),
    ("animation", lambda p, fn: ("/animations/" in p) or ("/anims/" in p) or ("/animsequence/" in p) or ("/montages/" in p) or re.match(r"^(a_|am_)", fn)),
    ("mesh", lambda p, fn: ("/meshes/" in p) or ("/skeletalmeshes/" in p) or ("/staticmeshes/" in p) or re.match(r"^(sk_|sm_)", fn)),
    ("environment", lambda p, fn: ("/environment/" in p) or ("/environments/" in p) or ("/env/" in p) or ("/maps/" in p) or ("/levels/" in p) or ("/world/" in p)),
    ("map", lambda p, fn: fn.endswith(('.umap', '.world'))),
    ("texture", lambda p, fn: ("/textures/" in p) or re.match(r"^(t_)", fn)),
    ("material", lambda p, fn: ("/materials/" in p) or ("/materialfunctions/" in p) or ("/materialinstances/" in p) or re.match(r"^(m_|mi_|mf_)", fn)),
    ("blueprint", lambda p, fn: ("/blueprints/" in p) or re.match(r"^(bp_)", fn)),
]

# ---------- Entity detection ----------

ENTITY_SYNONYMS = {
    'characters','character','char','chars',
    'heroes','villains','pawns','npcs','enemies','units',
    'weapons','items','vehicles','outfits','skins','costumes'
}

# Generic folder names to ignore when guessing entity from path tail
STOPWORD_SEGMENTS = {
    'game','content','assets','asset','art','common','shared','global','core',
    'ui','hud','widgets','menus','interface','icons',
    'audio','sound','sounds','sfx','vo','voice','music','wwiseaudio',
    'vfx','fx','niagara','particles',
    'materials','materialinstances','materialfunctions',
    'textures','texture',
    'meshes','skeletalmeshes','staticmeshes','mesh',
    'animations','anims','animsequence','montages','animation',
    'blueprints','blueprint','bp',
    'maps','levels','level','world',
    'data','datatable','curve','curves','db',
    'props','environment','env','shaders','unknown','default'
}

def find_category(path: str) -> Optional[str]:
    p = path.replace('\\', '/').lower()
    fn = Path(p).name.lower()
    for cat, pred in CATEGORY_RULES:
        try:
            if pred(p, fn):
                return cat
        except Exception:
            continue
    return None

def find_entity_key(path_segments: List[str]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    # Returns (char_id4, skin_id7, alpha_key)
    # NEW LOGIC: Strict path-based pattern matching
    # Look for /XXXXXXX/ (7-digit skin ID) first, then /XXXX/ (4-digit character ID)
    
    # Convert path segments to normalized path string for pattern matching
    path_str = "/" + "/".join(path_segments) + "/"
    
    # Pattern 1: Look for 7-digit skin IDs in path (e.g., /1011100/)
    skin_pattern = r'/(\d{7})/'
    skin_matches = re.findall(skin_pattern, path_str)
    if skin_matches:
        # Take the last match (closest to filename)
        skin_id7 = skin_matches[-1]
        char_id4 = skin_id7[:4]
        return char_id4, skin_id7, None
    
    # Pattern 2: Look for 4-digit character IDs in path (e.g., /1011/)
    char_pattern = r'/(\d{4})/'
    char_matches = re.findall(char_pattern, path_str)
    if char_matches:
        # Take the last match (closest to filename)
        char_id4 = char_matches[-1]
        return char_id4, None, None
    
    # No pattern match found
    return None, None, None

def resolve_entity(char_id4: Optional[str], skin_id7: Optional[str], alpha_key: Optional[str], entity_map: Dict[str, str]) -> str:
    # If we have a 7-digit skin ID, look it up directly (highest priority)
    if skin_id7 and skin_id7 in entity_map:
        return entity_map[skin_id7]
    
    # If the 4-digit character ID is known, return the canonical name from the mapping
    if char_id4 and char_id4 in entity_map:
        return entity_map[char_id4]
    
    # Fallback: try to match folder-derived alpha_key to known entity names (loose match)
    if alpha_key:
        # Build loose name map from entity_map values
        def loose(s: str) -> str:
            # Normalize for matching only: lowercase and strip non-alphanumerics
            return re.sub(r"[^a-z0-9]", "", normalize_name(s))
        known: Dict[str, str] = {}
        for v in entity_map.values():
            # Use original value for output
            k = loose(v)
            if k and k not in known:
                known[k] = v
        # Attempt match
        ak_norm = loose(alpha_key)
        if ak_norm in known:
            return known[ak_norm]
    return "unknown"

# ---------- Main tagging ----------

def tag_asset(path: str, entity_map: Dict[str, str]) -> str:
    # Normalize separators, strip
    raw = path.strip()
    if not raw:
        return ""
    norm = raw.replace('\\', '/').strip()
    segs = [s for s in norm.split('/') if s]
    cat = find_category(norm)
    char_id4, skin_id7, alpha_key = find_entity_key(segs)
    
    # Build tags as a list
    tags = []
    
    # If we found a skin ID, add both character and skin name
    if skin_id7 and skin_id7 in entity_map:
        # Add character name first
        if char_id4 and char_id4 in entity_map:
            tags.append(entity_map[char_id4])
        # Add skin name second
        tags.append(entity_map[skin_id7])
    # Otherwise just add character if found
    elif char_id4 and char_id4 in entity_map:
        tags.append(entity_map[char_id4])
    else:
        # Try alpha key fallback
        entity = resolve_entity(char_id4, skin_id7, alpha_key, entity_map)
        if entity != "unknown":
            tags.append(entity)
    
    # Add category if present
    if cat:
        tags.append(cat)
    
    # Return comma-separated tags (all lowercase)
    return ",".join(tags).lower() if tags else ""

# ---------- CLI ----------

def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Generate 'entity,category' tags from UE asset paths.")
    p.add_argument('--map', dest='map_path', default=None, help='Optional JSON map file (e.g., character_ids.json)')
    p.add_argument('paths', nargs='*', help='Asset paths (if omitted, read from stdin)')
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    entity_map = load_entity_map(args.map_path)
    inputs: List[str] = []
    if args.paths:
        inputs = args.paths
    else:
        # Read from stdin lines
        for line in sys.stdin:
            line = line.rstrip('\n')
            if line:
                inputs.append(line)
    for p in inputs:
        print(tag_asset(p, entity_map))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
