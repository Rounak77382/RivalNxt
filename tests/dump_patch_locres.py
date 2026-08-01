
"""
Dump content of the Game.locres from the specific Patch PAK for pattern analysis.
"""

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS
from rust_ue_tools import PyUnpacker
from pylocres import LocresFile

def dump_patch_locres():
    paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
    # Target the specific patch we found earlier
    target_pak_name = "Patch_-Windows_1.1.2673388_P.pak" 
    target_pak = paks_dir / target_pak_name
    
    if not target_pak.exists():
        # Fallback to finding *any* patch pak if the specific one is missing (unlikely)
        patch_paks = list(paks_dir.glob("*Patch*Windows*.pak"))
        if patch_paks:
            target_pak = patch_paks[-1] # Take the last one (usually latest)
        else:
            print("No Patch PAKs found!")
            return

    output_dir = Path("temp_patch_dump_analysis")
    dump_file = Path("patch_locres_dump.txt")
    
    print(f"Targeting PAK: {target_pak.name}")
    print(f"Unpacking to: {output_dir}")

    try:
        unpacker = PyUnpacker()
        unpacker.unpack_pak(
            str(target_pak),
            str(output_dir),
            aes_key=SETTINGS.aes_key_hex,
            force=True,
            quiet=True
        )
        
        # Find Game.locres
        locres_files = list(output_dir.rglob("Game.locres"))
        # Prefer English path if multiple
        en_locres = next((f for f in locres_files if "en" in f.parent.name.lower()), None)
        target_locres = en_locres if en_locres else (locres_files[0] if locres_files else None)
        
        if not target_locres:
            print("No Game.locres found in this patch PAK.")
            return

        print(f"Reading: {target_locres}")
        
        lf = LocresFile()
        lf.read(str(target_locres))
        
        print(f"Writing dump to {dump_file}...")
        
        with open(dump_file, "w", encoding="utf-8") as f:
            for ns_name, namespace in lf.namespaces.items():
                for entry_key, entry in namespace.entrys.items():
                    f.write(f"[{ns_name}] {entry_key} = {entry.translation}\n")
        
        print("Dump complete.")
        
        # Simple analysis preview
        print("\n--- Preview of POTENTIAL Skin/Character entries ---")
        preview_count = 0
        with open(dump_file, "r", encoding="utf-8") as f:
            for line in f:
                # Filter for things that look like our known patterns or contain IDs
                if "MarvelItemTable" in line or "UISkinTable" in line or "HeroUIAsset" in line or "Name" in line:
                    # Just show lines with numbers in them to narrow it down
                    if any(char.isdigit() for char in line):
                        if preview_count < 20:
                            print(line.strip())
                            preview_count += 1
                        else:
                            break
        print("...\n(See full file for more)")

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

if __name__ == "__main__":
    dump_patch_locres()
