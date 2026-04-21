import sys
from pathlib import Path
# Add core to sys.path
sys.path.append(str(Path.cwd()))

from core.utils.mod_filename import parse_mod_filename

test_cases = [
    "Rogue_Oasis-5664-1-1767064163.rar",
    "MrME's Thicc Skimpy Widow-3073-1-0-1747334812.zip",
    "IronMan_12345_1.0.7z",
    "PENI_S7_PASS_BLOODY_9999999_P.pak",
    "PENI S7 PASS BLOODY_9999999_P.pak"
]

for tc in test_cases:
    name, mid, ver = parse_mod_filename(tc)
    print(f"File: {tc:35} -> Name: '{name:25}' | ID: {str(mid):10} | Ver: {ver}")
