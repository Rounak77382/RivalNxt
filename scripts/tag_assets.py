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
    # SAFE LOGIC: Only extract character/skin IDs from DIRECTORY SEGMENTS,
    # never from filenames. This prevents texture filenames like
    # t_1024301_equip_01_d.uasset (a Hela skin texture reference) inside a Mantis
    # character folder (/characters/1020/1020800/) from being mistakenly tagged as Hela.
    #
    # Valid character asset paths look like:
    #   /marvel/content/marvel/characters/{char_id4}/{skin_id7}/materials/...
    # The char_id4 and skin_id7 must appear as complete, isolated directory
    # segments (not embedded in filenames or longer numeric sequences).
    #
    # UI/shared asset paths like:
    #   /ui/textures/proficiency/img_battle_21039020_avatar.uasset
    # produce no character tag because the numeric sequences are inside filenames
    # or are 8+ digit numbers, not isolated 4/7-digit directory segments.

    # Separate directory segments from the filename (last segment).
    # We only inspect directory segments for IDs, NEVER the filename itself.
    dir_segments = path_segments[:-1] if len(path_segments) > 1 else []

    # Strategy 1: Detect the canonical /characters/{char_id4}/{skin_id7}/ structure.
    # Walk the directory segments looking for a 'characters' (or synonym) segment
    # followed immediately by a 4-digit char_id and optionally a 7-digit skin_id.
    for i, seg in enumerate(dir_segments):
        if seg.lower() in ENTITY_SYNONYMS:
            # Next segment should be the 4-digit character ID
            if i + 1 < len(dir_segments):
                next_seg = dir_segments[i + 1]
                if re.fullmatch(r'\d{4}', next_seg):
                    char_id4 = next_seg
                    # Segment after that may be the 7-digit skin ID
                    if i + 2 < len(dir_segments):
                        skin_seg = dir_segments[i + 2]
                        if re.fullmatch(r'\d{7}', skin_seg):
                            return char_id4, skin_seg, None
                    # Only char_id found (no skin sub-folder)
                    return char_id4, None, None

    # Strategy 2: Look for an isolated 7-digit directory segment not preceded by
    # a 'characters' keyword. Catches paths like /SK_1011100/ or /1011100/ as
    # complete segments. Still never looks at the filename (last segment).
    for seg in dir_segments:
        if re.fullmatch(r'\d{7}', seg):
            skin_id7 = seg
            char_id4 = skin_id7[:4]
            return char_id4, skin_id7, None

    # Strategy 3: Look for an isolated 4-digit directory segment.
    # Directory segments only — not filenames.
    for seg in dir_segments:
        if re.fullmatch(r'\d{4}', seg):
            return seg, None, None

    # Strategy 4: Known UI filename encoding patterns.
    # UI assets encode the character ID directly inside the filename number.
    # Two schemes observed in Marvel Rivals:
    #
    #   Scheme A — 8-digit with leading type byte:
    #     img_battle_21039020_avatar  ->  2 + 1039 + 020
    #     img_selecthero_21039020     ->  2 + 1039 + 020
    #     The leading "2" is a UI type/version prefix; char_id is digits 1-4 (0-indexed).
    #
    #   Scheme B — 8-digit char-first:
    #     img_squarehead_10270010     ->  1027 + 0010
    #     img_commontransverse_10270010 -> 1027 + 0010
    #     char_id is the first 4 digits directly.
    #
    #   Scheme C — 5-digit char-first:
    #     img_battle_10270_avatar     ->  1027 + 0
    #     char_id is the first 4 digits.
    #
    # Safe extraction rule: search the filename for any 5-8 digit numeric run,
    # then try ALL 4-digit windows within it and check against the entity_map
    # (caller does the lookup; here we just return candidate char_id4).
    # We return the FIRST valid candidate found.
    # This avoids false positives from arbitrary numbers — only known char IDs match.
    if path_segments:
        filename = path_segments[-1]
        # Strip extension
        stem = filename.rsplit('.', 1)[0]
        # Find all runs of digits (5 to 8 length are the known UI ranges)
        for num_match in re.finditer(r'\d{5,8}', stem):
            num_str = num_match.group(0)
            # Slide a 4-digit window from left to right through the number.
            # Return the first 4-digit substring that is a known character ID;
            # the caller's entity_map lookup will validate it.
            # We return all candidates as a sorted tuple so caller picks first valid.
            for start in range(len(num_str) - 3):
                candidate = num_str[start:start + 4]
                # Only yield candidates that look like Marvel Rivals char IDs (1xxx or 4xxx)
                if re.fullmatch(r'[14]\d{3}', candidate):
                    return candidate, None, None

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
    
    # If we found a skin ID
    if skin_id7:
        # Add character name if known
        if char_id4 and char_id4 in entity_map:
            tags.append(entity_map[char_id4])
        
        # Add skin name if known
        if skin_id7 in entity_map:
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
