from pathlib import Path
import re
from typing import Optional, Tuple

# Mocking server.py logic
def parse_mod_filename(filename: str) -> Tuple[str, Optional[int], str]:
    base = Path(filename or "").stem
    if not base: return "", None, ""
    tokens = re.split(r"[-_ ]", base)
    tokens = [t for t in tokens if t]
    mod_id_val = None
    mod_id_index = -1
    for i in range(1, len(tokens)):
        token = tokens[i]
        if token.isdigit() and 1 <= len(token) <= 10:
            if token.startswith("9999999") and len(token) >= 7: continue
            mod_id_val = int(token)
            mod_id_index = i
            break
    if mod_id_index != -1:
        name_tokens = tokens[:mod_id_index]
        name = " ".join(name_tokens) if name_tokens else tokens[0]
        version_all = "-".join(tokens[mod_id_index + 1:])
        v_match = re.search(r"(\d+(?:\.\d+)*)", version_all)
        version = v_match.group(1) if v_match else version_all
        return name, mod_id_val, version
    version_match = re.search(r"(?:[vV]\s?|[-_ ])(\d+\.\d+(?:\.\d+)*)", base)
    if not version_match: version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", base)
    if version_match:
        version = version_match.group(1)
        name = base[:version_match.start()].strip("-_ ")
        return (name or base), None, version
    return base, None, ""

def _resolve_mod_metadata(path: Path, provided_name=None, provided_mod_id=None, provided_version=None):
    if provided_name:
        lower_name = provided_name.lower()
        if lower_name.endswith(('.zip', '.7z', '.rar', '.pak')) or '-' in provided_name:
            p_name, p_mod_id, p_version = parse_mod_filename(provided_name)
            if p_name:
                provided_name = p_name
                provided_mod_id = provided_mod_id or p_mod_id
                provided_version = provided_version or p_version
    filename_name, filename_mod_id, filename_version = parse_mod_filename(path.name)
    final_name = provided_name or filename_name
    final_mod_id = provided_mod_id or filename_mod_id
    final_version = provided_version or filename_version
    return final_name.strip(), final_mod_id, final_version.strip()

test_path = Path("MrME's Thicc Skimpy Widow-3073-1-0-1747334812.zip")
# Case 1: Drag & drop (no provided name)
name, mid, ver = _resolve_mod_metadata(test_path)
print(f"D&D   -> Name: '{name}' | ID: {mid} | Ver: {ver}")

# Case 2: Nexus Handoff (provided_name = file_name from Nexus)
name, mid, ver = _resolve_mod_metadata(test_path, provided_name="MrME's Thicc Skimpy Widow-3073-1-0-1747334812.zip", provided_mod_id=3073)
print(f"Nexus -> Name: '{name}' | ID: {mid} | Ver: {ver}")
