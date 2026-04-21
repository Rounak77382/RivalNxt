from pathlib import Path
import re
from typing import Optional, Tuple

def parse_mod_filename(filename: str) -> Tuple[str, Optional[int], str]:
    base = Path(filename or "").stem
    if not base: return "", None, ""

    for sep in ["-", "_"]:
        tokens = base.split(sep)
        tokens = [t.strip() for t in tokens if t.strip()]
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
            name_parts = tokens[:mod_id_index]
            name = sep.join(name_parts) if name_parts else tokens[0]
            version_parts = tokens[mod_id_index + 1:]
            if version_parts:
                version_raw = ".".join(version_parts)
                v_match = re.search(r"(\d+(?:\.\d+)*)", version_raw)
                version = v_match.group(1) if v_match else version_raw
                return name, mod_id_val, version
            else:
                return name, mod_id_val, ""

    version_match = re.search(r"(?:[vV]\s?|[-_ ])(\d+\.\d+(?:\.\d+)*)", base)
    if not version_match: version_match = re.search(r"(\d+\.\d+(?:\.\d+)*)", base)
    if version_match:
        version = version_match.group(1)
        name = base[:version_match.start()].strip("-_ ")
        if not name: name = base
        return name, None, version
    return base, None, ""

test_cases = [
    "Rogue_Oasis-5664-1-1767064163.rar",
    "MrME's Thicc Skimpy Widow-3073-1-0-1747334812.zip",
    "IronMan_12345_1.0.7z",
]

for tc in test_cases:
    name, mid, ver = parse_mod_filename(tc)
    print(f"File: {tc:35} -> Name: '{name:15}' | ID: {str(mid):10} | Ver: {ver}")
