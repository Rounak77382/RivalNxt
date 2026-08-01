"""
List ALL PAK files in detail
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS

paks_dir = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"

print("="*60)
print("All PAK Files")
print("="*60)
print(f"\nDirectory: {paks_dir}\n")

pak_files = sorted(paks_dir.glob("*.pak"))

print(f"Found {len(pak_files)} PAK files:\n")

for pak in pak_files:
    name = pak.name
    size_mb = pak.stat().st_size / (1024 * 1024)
    print(f"  {name} ({size_mb:.1f} MB)")

# Look for specific patch patterns
print("\n" + "="*60)
print("Patch PAKs (containing 'Patch' or '_P'):")
print("="*60)

patch_paks = [p for p in pak_files if 'patch' in p.name.lower() or '_p' in p.name.lower()]

if patch_paks:
    for pak in patch_paks:
        size_mb = pak.stat().st_size / (1024 * 1024)
        print(f"  ⭐ {pak.name} ({size_mb:.1f} MB)")
else:
    print("  None found")
