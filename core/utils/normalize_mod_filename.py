from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Dict, Any, Optional

from core.utils.mod_filename import parse_mod_filename
from core.nexus.nexus_api import get_mod_files, get_mod_by_md5
import hashlib

def build_canonical_filename(
    mod_name: str,
    mod_id: int,
    version: str,
    uploaded_timestamp: int,
    ext: str
) -> str:
    """
    Build the canonical Nexus filename for a mod file.
    Convention: {name}-{modId}-{version}-{timestamp}.{ext}
    """
    safe_name = mod_name.lower()
    safe_name = re.sub(r'[^a-z0-9]+', '-', safe_name)
    safe_name = re.sub(r'^-|-$', '', safe_name)
    suffix = f".{ext}" if ext else ""
    return f"{safe_name}-{mod_id}-{version}-{uploaded_timestamp}{suffix}"


def normalize_mod_filename(
    file_path: Path | str,
    game_domain: str,
    *,
    api_key: str,
    db_conn,
    known_mod_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Attempt to normalize a mod file's name by verifying its contents against the Nexus API.
    If a match is found (either via parsing the name or via a known override), 
    the file is renamed to the canonical format.
    
    Returns a dict with:
      - backendModId: int | None
      - renamed: bool
      - canonical_path: Path | None
      - needsManualModId: bool
      - version: str | None
      - file_md5: str | None  (only computed for non-conforming files)
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return {"backendModId": None, "renamed": False, "canonical_path": None, "needsManualModId": True, "version": None, "file_md5": None}

    ext = path.suffix.lstrip('.')
    file_size = path.stat().st_size
    
    # 1. Try parsing the existing filename
    name, parsed_mod_id, version = parse_mod_filename(path.name)
    candidate_mod_id = parsed_mod_id or known_mod_id

    # 2. If parsing failed, check the mod_id_overrides table
    if candidate_mod_id is None:
        cur = db_conn.cursor()
        override_row = cur.execute(
            "SELECT nexus_mod_id FROM mod_id_overrides WHERE local_path = ?",
            (path.name,)
        ).fetchone()
        if override_row:
            candidate_mod_id = override_row[0]

    computed_md5: Optional[str] = None
    md5_matched_file = None
    # 2.5 If still None, let's try the MD5 lookup on Nexus!
    if candidate_mod_id is None and api_key:
        try:
            with open(path, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()
            computed_md5 = file_hash
            status, md5_response = get_mod_by_md5(api_key, game_domain, file_hash)
            if status == 200 and isinstance(md5_response, list) and len(md5_response) > 0:
                # md5 response is an array of match objects
                first_match = md5_response[0]
                if "mod" in first_match and "mod_id" in first_match["mod"]:
                    candidate_mod_id = first_match["mod"]["mod_id"]
                    # Optionally extract file info to skip verifying again
                    if "file_details" in first_match:
                        md5_matched_file = first_match["file_details"]
        except Exception:
            pass

    # 3. If we have a candidate, verify with Nexus API
    if candidate_mod_id is not None and api_key:
        # If we got md5_matched_file, we don't necessarily need to fetch all files to verify,
        # but fetching ensures we have the full array if we need it. To keep it simple, we just fetch normally.
        status, response = get_mod_files(api_key, game_domain, candidate_mod_id)
        if status == 200 and "files" in response:
            for f in response["files"]:
                api_file_name = f.get("file_name", "")
                api_size = f.get("size_in_bytes")
                
                # Match if filenames match (case-insensitive) OR sizes match OR it was our exact MD5 match
                is_md5_match = md5_matched_file and str(md5_matched_file.get("file_id")) == str(f.get("file_id"))
                if api_file_name.lower() == path.name.lower() or api_size == file_size or is_md5_match:
                    uploaded_ts = f.get("uploaded_timestamp", 0)
                    api_version = f.get("version", version)
                    
                    canonical_name = build_canonical_filename(
                        mod_name=name or f.get("name", "mod"),
                        mod_id=candidate_mod_id,
                        version=api_version,
                        uploaded_timestamp=uploaded_ts,
                        ext=ext
                    )
                    
                    canonical_path = path.parent / canonical_name
                    renamed = False
                    
                    if path.name != canonical_name:
                        try:
                            # Avoid replacing if canonical already exists (shouldn't happen typically)
                            if not canonical_path.exists():
                                os.rename(path, canonical_path)
                                renamed = True
                            else:
                                canonical_path = path # fallback
                        except Exception:
                            canonical_path = path
                    else:
                        canonical_path = path
                        
                    return {
                        "backendModId": candidate_mod_id,
                        "renamed": renamed,
                        "canonical_path": canonical_path,
                        "needsManualModId": False,
                        "version": api_version,
                        "file_md5": computed_md5
                    }
                    
    # 4. Failed to verify via API, or no API key, or file not found in API response
    # If we have a candidate_mod_id, we can still assume it's valid to prevent showing 'Assign Mod ID' when we already know it.
    return {
        "backendModId": candidate_mod_id,
        "renamed": False,
        "canonical_path": path,
        "needsManualModId": candidate_mod_id is None,
        "version": version,
        "file_md5": computed_md5
    }
