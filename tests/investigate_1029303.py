"""
Investigate PAK directory and search for skin 1029303 in locres
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS

def list_pak_files():
    """List all PAK files in the game directory."""
    print("="*60)
    print("Checking PAK Directory")
    print("="*60)
    
    # Correct path based on extraction script
    paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
    
    print(f"\nPAK Directory: {paks_dir}")
    print(f"Exists: {paks_dir.exists()}\n")
    
    if not paks_dir.exists():
        print("❌ PAK directory not found!")
        # Try alternative path
        alt_path = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Content" / "Paks"
        print(f"\nTrying alternative: {alt_path}")
        print(f"Exists: {alt_path.exists()}\n")
        if alt_path.exists():
            paks_dir = alt_path
        else:
            return
    
    # List all PAK files
    pak_files = sorted(paks_dir.glob("*.pak"))
    
    print(f"Found {len(pak_files)} PAK files:\n")
    
    # Categorize PAKs
    base_paks = []
    patch_paks = []
    dlc_paks = []
    other_paks = []
    
    for pak in pak_files:
        name = pak.name
        size_mb = pak.stat().st_size / (1024 * 1024)
        
        if '_p' in name.lower() or 'patch' in name.lower():
            patch_paks.append((name, size_mb))
        elif 'dlc' in name.lower() or 'season' in name.lower():
            dlc_paks.append((name, size_mb))
        elif 'Character' in name:
            base_paks.append((name, size_mb))
        else:
            other_paks.append((name, size_mb))
    
    if base_paks:
        print("📦 Base Character PAKs:")
        for name, size in base_paks:
            print(f"  {name} ({size:.1f} MB)")
    
    if patch_paks:
        print("\n🔧 Patch PAKs:")
        for name, size in patch_paks:
            print(f"  {name} ({size:.1f} MB)")
    else:
        print("\n❌ No patch PAKs found (_p suffix or 'patch' in name)")
    
    if dlc_paks:
        print("\n🎁 DLC/Season PAKs:")
        for name, size in dlc_paks:
            print(f"  {name} ({size:.1f} MB)")
    else:
        print("\n❌ No DLC/Season PAKs found")
    
    if other_paks:
        print(f"\n📄 Other PAKs ({len(other_paks)} files)")
        for name, size in other_paks[:10]:
            print(f"  {name} ({size:.1f} MB)")
        if len(other_paks) > 10:
            print(f"  ... and {len(other_paks) - 10} more")

def search_skin_in_locres():
    """Search for skin 1029303 in locres files."""
    print("\n" + "="*60)
    print("Searching for Skin 1029303 in Locres")
    print("="*60)
    
    from rust_ue_tools import PyUnpacker
    from pylocres import LocresFile
    
    # Use same path logic as list function
    paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
    if not paks_dir.exists():
        paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Content" / "Paks"
    
    if not paks_dir.exists():
        print("❌ PAK directory not found!")
        return
    
    output_dir = Path("temp_locres_search")
    output_dir.mkdir(exist_ok=True)
    
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
        
        print(f"Reading locres file: {en_file.name}")
        
        lf = LocresFile()
        lf.read(str(en_file))
        
        # Search for 1029303
        found_entries = []
        search_patterns = ['1029303', '1029_303', 'magik']
        
        for ns_name, namespace in lf.namespaces.items():
            for entry_key, entry in namespace.entrys.items():
                key_lower = entry_key.lower()
                value_lower = entry.translation.lower()
                
                # Look for 1029303 in keys or values
                if '1029303' in key_lower or '1029303' in value_lower:
                    found_entries.append({
                        'namespace': ns_name,
                        'key': entry_key,
                        'value': entry.translation
                    })
                # Also search for magik entries with 303
                elif 'magik' in value_lower and '303' in key_lower:
                    found_entries.append({
                        'namespace': ns_name,
                        'key': entry_key,
                        'value': entry.translation
                    })
        
        if found_entries:
            print(f"\n✅ Found {len(found_entries)} entries mentioning 1029303 or related:")
            for entry in found_entries[:10]:
                print(f"\n  Namespace: {entry['namespace']}")
                print(f"  Key: {entry['key']}")
                print(f"  Value: {entry['value']}")
        else:
            print("\n❌ No entries found for skin ID 1029303")
            
            # Show what Magik skins ARE in the locres
            print("\nSearching for ALL Magik skins (1029xxx)...")
            magik_skins = []
            for ns_name, namespace in lf.namespaces.items():
                for entry_key, entry in namespace.entrys.items():
                    if '1029' in entry_key and any(pattern in entry_key for pattern in ['Skin', 'Item']):
                        magik_skins.append({
                            'key': entry_key,
                            'value': entry.translation
                        })
            
            if magik_skins:
                print(f"Found {len(magik_skins)} Magik skin entries:")
                for skin in sorted(magik_skins, key=lambda x: x['key'])[:20]:
                    print(f"  {skin['key']}: {skin['value']}")
            
    finally:
        import shutil
        shutil.rmtree(output_dir, ignore_errors=True)

if __name__ == "__main__":
    list_pak_files()
    search_skin_in_locres()
