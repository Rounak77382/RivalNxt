from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

__all__ = ["parse_mod_filename", "parse_mod_filename_to_row"]


def parse_mod_filename(filename: str) -> Tuple[str, Optional[int], str]:
    """Extract name, mod_id, and version from a mod filename."""
    base = Path(filename or "").stem
    if not base:
        return "", None, ""

    # Try different separators to find a Mod ID
    # We prioritize hyphens as they are standard for Nexus filenames
    for sep in ["-", "_"]:
        tokens = base.split(sep)
        tokens = [t.strip() for t in tokens if t.strip()]
        
        mod_id_val = None
        mod_id_index = -1
        
        for i in range(1, len(tokens)):
            token = tokens[i]
            if token.isdigit() and 1 <= len(token) <= 10:
                if token.startswith("9999999") and len(token) >= 7:
                    continue
                mod_id_val = int(token)
                mod_id_index = i
                break
                
        if mod_id_index != -1:
            name_parts = tokens[:mod_id_index]
            # Normalize name: join parts and replace all underscores with spaces
            raw_name = " ".join(name_parts) if name_parts else tokens[0]
            name = raw_name.replace("_", " ").strip()
            name = re.sub(r"\s+", " ", name)
            
            # Everything after mod_id is potentially version
            # Join components with dots for a cleaner version string
            version_parts = tokens[mod_id_index + 1:]
            if version_parts:
                version_raw = ".".join(version_parts)
                # Extra extraction for version-like strings (digits and dots)
                v_match = re.search(r"(\d+(?:\.\d+)*)", version_raw)
                version = v_match.group(1) if v_match else version_raw
                return name, mod_id_val, version
            else:
                return name, mod_id_val, ""

    # 2. Fallback: Search for version patterns like v1.2.3 or 1.2.3
    version_match = re.search(r"(?:[vV]\s?|[-_ ])(\d+\.\d+(?:\.\d+)*)", base)
    if not version_match:
        version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", base)

    if version_match:
        version = version_match.group(1)
        name = base[:version_match.start()].replace("_", " ").strip("-_ ")
        name = re.sub(r"\s+", " ", name)
        if not name:
            name = base
        return name, None, version
        
    # Final cleanup of underscores in base name fallback
    final_name = base.replace("_", " ").strip()
    final_name = re.sub(r"\s+", " ", final_name)
    return final_name, None, ""





def parse_mod_filename_to_row(filename: str) -> tuple[str, str, str]:
	"""Compatibility helper returning ``(name, mod_id_string, version)``."""
	name, mod_id_val, version = parse_mod_filename(filename)
	return name, str(mod_id_val) if mod_id_val is not None else "", version
