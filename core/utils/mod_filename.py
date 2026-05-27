from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Tuple

__all__ = ["parse_mod_filename", "parse_mod_filename_to_row"]


def parse_mod_filename(filename: str) -> Tuple[str, Optional[int], str]:
    """Extract name, mod_id, and version from a mod filename.
    
    Prioritizes the official Nexus Mods naming convention:
    <name>-<mod_id>-<version>-<timestamp>.<ext>

    Also handles the space-separated NMM/browser style:
    <name words> <mod_id> <version tokens> <timestamp> [optional extra]
    e.g. "Azure Shade Up 4985 1 0 1763999096 8"
    """
    if not filename:
        return "", None, ""
        
    p = Path(filename)
    base = p.stem
    ext = p.suffix
    
    # 1. Check for official Nexus naming convention (dash-separated)
    # Pattern: <Name>-<ModID>-<Version>-<Timestamp>[optional suffix]
    #
    # The key to parsing this correctly is anchoring on the TIMESTAMP, which is
    # always a 9-11 digit Unix epoch value (e.g. 1776061797).
    # We then parse BACKWARDS from the timestamp to find:
    #   - timestamp  : 9-11 digits at the end
    #   - version    : 1 or more digit segments (e.g. "1-1", "4-5")
    #   - mod_id     : 1-7 digits (Nexus mod IDs are currently 1-6 digits)
    #   - name       : everything before the mod_id
    #
    # Using a non-greedy name group (.+?) and anchoring the timestamp size
    # prevents the regex from greedily consuming numbers that are part of the mod_id.
    nexus_pattern = re.compile(
        r"^(.+?)"           # Name (non-greedy)
        r"-(\d{1,7})"       # -ModID (1-7 digits, currently Nexus IDs are 1-6 digits)
        r"-([\w][\w\.-]*\w|[\w])"  # -Version (alphanumeric led, alphanumeric, dots, and dashes)
        r"-(\d{9,11})"      # -Timestamp (9-11 digit Unix epoch)
        r"(?:[\s\-_]+(?:\(\d+\)|\d+))?"   # Optional browser duplicate suffix like " (1)", "-1", "_1"
        r"$"
    )
    nexus_match = nexus_pattern.match(base)
    
    if nexus_match:
        name_raw = nexus_match.group(1)
        mod_id = int(nexus_match.group(2))
        version = nexus_match.group(3).replace("-", ".")
        # Clean up name: normalize underscores but preserve intentional dashes-as-spaces
        name = name_raw.replace("_", " ").strip()
        name = re.sub(r"\s+", " ", name)
        return name, mod_id, version

    # 1b. Space-separated Nexus convention (NMM / browser download-manager style).
    #     Format: <Name words> <ModID> <version tokens> <Timestamp> [optional extra number]
    #     Examples:
    #       "Azure Shade Up 4985 1 0 1763999096 8"
    #       "Thicc Scarlet Witch 5704 1 3 1774422798 1"
    #       "Chaos Radiance User Interface Overhaul 4804 4 5 1762687295 5"
    #
    # Strategy: anchor on the timestamp (9-11 digit epoch), then read backwards:
    #   - optional trailing extra number after timestamp
    #   - version segments (one or more space-separated digit groups before timestamp)
    #   - ModID (single 1-7 digit number just before version)
    #   - name = everything before the ModID
    #
    # We use a non-greedy version group so the ModID is correctly separated.
    nexus_space_pattern = re.compile(
        r"^(.+?)"                   # Name (non-greedy)
        r"\s+(\d{1,7})"             # space + ModID (1-7 digits)
        r"\s+([\w\.]+(?:\s+[\w\.]+)*?)"  # space + Version tokens (non-greedy, 1+ alphanumeric groups)
        r"\s+(\d{9,11})"            # space + Timestamp (9-11 digit epoch)
        r"(?:\s+\d+)?"              # optional trailing extra number (e.g. "8", "5", "1")
        r"$"
    )
    nexus_space_match = nexus_space_pattern.match(base)

    if nexus_space_match:
        name_raw = nexus_space_match.group(1)
        mod_id_candidate = int(nexus_space_match.group(2))
        version_raw = nexus_space_match.group(3)

        # Sanity check: the "name" part must contain at least one non-digit character
        # to avoid misidentifying a pure-numeric string as a name.
        if re.search(r"[^\d\s]", name_raw):
            version = version_raw.replace(" ", ".")
            name = name_raw.replace("_", " ").strip()
            name = re.sub(r"\s+", " ", name)
            return name, mod_id_candidate, version

    # 2. Heuristic parsing for non-Nexus files
    # Strip Unreal Engine .pak suffixes like _9999999_P, _P, _p
    base = re.sub(r"_(?:\d+_)?(?:P|p)$", "", base)

    # Fallback: Search for version patterns like v1.2.3 or 1.2.3
    # We do this BEFORE the general token split to avoid taking a version-like number as a Mod ID
    version_match = re.search(r"(?:[vV]\s?|[-_ ])(\d+\.\d+(?:\.\d+)*)", base)
    if not version_match:
        version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", base)

    version = ""
    name_for_id_search = base
    if version_match:
        version = version_match.group(1)
        name_for_id_search = base[:version_match.start()].strip("-_ ")

    # For non-official files, we are much stricter about what looks like a Mod ID.
    # We only take it if it's explicitly labeled or if it's a large number 
    # separated by a dash at the end of the name part.
    
    # Try to find an explicit Mod ID like "ModID_12345"
    explicit_match = re.search(r"ModID[_-]?(\d+)", base, re.I)
    if explicit_match:
        mod_id = int(explicit_match.group(1))
        name = base[:explicit_match.start()].replace("_", " ").replace("-", " ").strip("-_ ")
        name = re.sub(r"\s+", " ", name) or base
        return name, mod_id, version

    # If no official pattern and no explicit ID, we treat the whole thing as the name
    # unless it matches a very specific "Name-12345" where 12345 is at the end.
    # This avoids "Luna-Mirae-2099" being parsed as Mod #2099.
    
    final_name = base.replace("_", " ").replace("-", " ").strip()
    final_name = re.sub(r"\s+", " ", final_name)
    return final_name, None, version





def parse_mod_filename_to_row(filename: str) -> tuple[str, str, str]:
	"""Compatibility helper returning ``(name, mod_id_string, version)``."""
	name, mod_id_val, version = parse_mod_filename(filename)
	return name, str(mod_id_val) if mod_id_val is not None else "", version
