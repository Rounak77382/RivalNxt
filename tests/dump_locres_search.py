
"""
Dump entire locres content to a text file and search it as a raw string check.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS
from rust_ue_tools import PyUnpacker
from pylocres import LocresFile

def dump_and_search():
    paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
    output_dir = Path("temp_locres_dump")
    output_dir.mkdir(exist_ok=True)
    dump_file_path = Path("full_locres_dump.txt")

    print(f"Dump file will be saved to: {dump_file_path.absolute()}")

    try:
        print("\nUnpacking pakchunkLocres-Windows.pak...")
        unpacker = PyUnpacker()
        unpacker.unpack_pak(
            str(paks_dir / "pakchunkLocres-Windows.pak"),
            str(output_dir),
            aes_key=SETTINGS.aes_key_hex,
            force=True,
            quiet=True
        )
        
        locres_files = list(output_dir.rglob("*.locres"))
        en_files = [lf for lf in locres_files if 'en' in lf.parent.name.lower()]
        en_file = en_files[0] if en_files else locres_files[0]
        
        print(f"Reading: {en_file.name}")
        
        lf = LocresFile()
        lf.read(str(en_file))
        
        print(f"Writing all entries to {dump_file_path}...")
        
        count = 0
        with open(dump_file_path, "w", encoding="utf-8") as f:
            for ns_name, namespace in lf.namespaces.items():
                for entry_key, entry in namespace.entrys.items():
                    # Write in a structured but raw format
                    f.write(f"[{ns_name}] {entry_key} = {entry.translation}\n")
                    count += 1
        
        print(f"Wrote {count} lines.")
        
        # Now search the file as raw text
        print("\nSearching generated text file for '1029303'...")
        found = False
        with open(dump_file_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if "1029303" in line:
                    print(f"MATCH FOUND at line {i}:")
                    print(line.strip())
                    found = True
        
        if not found:
            print("❌ '1029303' was NOT found in the text dump.")
        else:
            print("✅ '1029303' WAS found in the text dump.")

    finally:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)

if __name__ == "__main__":
    dump_and_search()
