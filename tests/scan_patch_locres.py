
"""
Scan Patch PAKs for any .locres files and search them for skin 1029303.
"""

import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS
from rust_ue_tools import PyUnpacker
from pylocres import LocresFile

def scan_patch_paks():
    paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
    output_dir = Path("temp_patch_scan_locres")
    
    # Find all patch PAKs
    patch_paks = list(paks_dir.glob("*Patch*Windows*.pak"))
    
    print(f"Found {len(patch_paks)} Patch PAKs to scan.")

    found_entries = []

    try:
        for pak_path in patch_paks:
            print(f"\nScanning: {pak_path.name}...")
            
            # Create a unique temp dir for this pak to avoid collisions/cleanup issues
            pak_output_dir = output_dir / pak_path.stem
            pak_output_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                unpacker = PyUnpacker()
                # We interpret "unpack_pak" as extracting everything. 
                # This might be slow for large patches, but it's the most reliable way to find hidden .locres
                unpacker.unpack_pak(
                    str(pak_path),
                    str(pak_output_dir),
                    aes_key=SETTINGS.aes_key_hex,
                    force=True,
                    quiet=True
                )
                
                # Search for .locres files
                locres_files = list(pak_output_dir.rglob("*.locres"))
                
                if not locres_files:
                    print("  No .locres files found in this PAK.")
                    continue
                
                print(f"  Found {len(locres_files)} .locres files.")
                
                for lf_path in locres_files:
                    # Filter for English if possible, otherwise check all
                    if 'en' not in lf_path.parent.name.lower() and 'game' not in lf_path.name.lower():
                        # Optional: skip non-english/non-game if specifically looking for English strings
                        # But for 1029303 search, we check everything.
                        pass

                    print(f"  Reading: {lf_path.relative_to(pak_output_dir)}")
                    
                    try:
                        lf = LocresFile()
                        lf.read(str(lf_path))
                        
                        entry_count = 0
                        for ns_name, namespace in lf.namespaces.items():
                            for entry_key, entry in namespace.entrys.items():
                                entry_count += 1
                                if '1029303' in entry_key or '1029303' in entry.translation:
                                    found_entries.append({
                                        'pak': pak_path.name,
                                        'file': lf_path.name,
                                        'namespace': ns_name,
                                        'key': entry_key,
                                        'value': entry.translation
                                    })
                        print(f"    Scanned {entry_count} entries.")

                    except Exception as e:
                        print(f"    Error reading locres file: {e}")

            except Exception as e:
                print(f"  Error unpacking {pak_path.name}: {e}")
            
            # Clean up individual pak output to save space
            shutil.rmtree(pak_output_dir, ignore_errors=True)

    finally:
        if output_dir.exists():
            shutil.rmtree(output_dir, ignore_errors=True)
    
    print(f"\n{'='*80}")
    print("PATCH SCAN RESULTS")
    print(f"{'='*80}\n")
    
    if found_entries:
        print(f"✅ FOUND {len(found_entries)} entries containing '1029303':\n")
        for entry in found_entries:
            print(f"PAK: {entry['pak']}")
            print(f"File: {entry['file']}")
            print(f"Namespace: {entry['namespace']}")
            print(f"Key: {entry['key']}")
            print(f"Value: {entry['value']}")
            print("-" * 40)
    else:
        print("❌ '1029303' was NOT found in any Patch PAK .locres files.")

if __name__ == "__main__":
    scan_patch_paks()
