"""
Quick check of PAK directory
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config.settings import SETTINGS

# Try both possible paths
paks_dir1 = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
paks_dir2 = Path(SETTINGS.marvel_rivals_root) / "MarvelGame" / "Content" / "Paks"

print("Checking PAK directories...")
print(f"\nPath 1: {paks_dir1}")
print(f"Exists: {paks_dir1.exists()}")

print(f"\nPath 2: {paks_dir2}")
print(f"Exists: {paks_dir2.exists()}")

# Use whichever exists
paks_dir = paks_dir1 if paks_dir1.exists() else paks_dir2

if paks_dir.exists():
    print(f"\n✓ Using: {paks_dir}\n")
    print("="*60)
    
    # List all PAK files
    pak_files = sorted(paks_dir.glob("*.pak"))
    
    print(f"Found {len(pak_files)} PAK files\n")
    
    # Show character-related and patch PAKs
    for pak in pak_files:
        name = pak.name
        size_mb = pak.stat().st_size / (1024 * 1024)
        
        # Highlight relevant ones
        if any(keyword in name for keyword in ['Character', 'Locres', 'patch', '_p', 'DLC', 'Season']):
            print(f"  ⭐ {name} ({size_mb:.1f} MB)")
else:
    print("\n❌ No PAK directory found at either location!")
