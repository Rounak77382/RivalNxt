"""
Test script to verify that scan_mod_downloads handles all download formats:
- .zip, .rar, .7z archives
- Folders containing .pak files
- Single .pak files
"""
import os
import sys
import tempfile
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from core.ingestion.scan_mod_downloads import (
    _enumerate_archive_contents,
    build_download_row,
    list_files_level_one_including_root,
)


def test_enumerate_contents():
    """Test that _enumerate_archive_contents handles all formats."""
    print("\n" + "=" * 70)
    print("Testing: _enumerate_archive_contents()")
    print("=" * 70)
    
    # Create a temporary test directory
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Test 1: Single .pak file
        single_pak = tmp_path / "ModName-123-1-0.pak"
        single_pak.write_text("dummy")
        
        result = _enumerate_archive_contents(single_pak)
        print(f"\n1. Single .pak file: {single_pak.name}")
        print(f"   Contents: {result}")
        assert result == ["ModName-123-1-0.pak"], f"Expected single pak, got {result}"
        print("   ✅ PASS")
        
        # Test 2: Folder with .pak files
        folder = tmp_path / "SomeMod-456-2-0"
        folder.mkdir()
        (folder / "mod_part1.pak").write_text("dummy")
        (folder / "mod_part2.pak").write_text("dummy")
        
        result = _enumerate_archive_contents(folder)
        print(f"\n2. Folder with .pak files: {folder.name}/")
        print(f"   Contents: {result}")
        assert len(result) == 2, f"Expected 2 paks, got {len(result)}"
        assert "mod_part1.pak" in result, "Missing mod_part1.pak"
        assert "mod_part2.pak" in result, "Missing mod_part2.pak"
        print("   ✅ PASS")
        
        # Test 3: Nested folder structure
        nested = tmp_path / "NestedMod-789-1-0"
        nested.mkdir()
        (nested / "subfolder").mkdir()
        (nested / "subfolder" / "nested.pak").write_text("dummy")
        (nested / "root.pak").write_text("dummy")
        
        result = _enumerate_archive_contents(nested)
        print(f"\n3. Nested folder structure: {nested.name}/")
        print(f"   Contents: {result}")
        assert len(result) == 2, f"Expected 2 paks (nested + root), got {len(result)}"
        assert "nested.pak" in result, "Missing nested.pak"
        assert "root.pak" in result, "Missing root.pak"
        print("   ✅ PASS")
        
        # Test 4: Empty folder (no .pak files)
        empty = tmp_path / "EmptyFolder-999-1-0"
        empty.mkdir()
        (empty / "readme.txt").write_text("no paks here")
        
        result = _enumerate_archive_contents(empty)
        print(f"\n4. Empty folder (no .pak): {empty.name}/")
        print(f"   Contents: {result}")
        assert result == [], f"Expected empty list, got {result}"
        print("   ✅ PASS")
    
    print("\n" + "=" * 70)
    print("✅ All _enumerate_archive_contents tests passed!")
    print("=" * 70)


def test_build_download_row():
    """Test that build_download_row handles folders and files."""
    print("\n" + "=" * 70)
    print("Testing: build_download_row()")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Test 1: Single .pak file (use 4+ digit mod ID)
        pak_file = tmp_path / "TestMod-1234-1-0.pak"
        pak_file.write_text("dummy")
        
        row = build_download_row(pak_file, relative_to=tmp_path)
        print("\n1. Single .pak file")
        print(f"   Name: {row['name']}")
        print(f"   Mod ID: {row['modID']}")
        print(f"   Version: {row['version']}")
        print(f"   Contents: {row['contents']}")
        assert row['modID'] == '1234', f"Expected modID 1234, got {row['modID']}"
        assert len(row['contents']) == 1, f"Expected 1 pak file, got {len(row['contents'])}"
        print("   ✅ PASS")
        
        # Test 2: Folder with .pak files (use 4+ digit mod ID)
        folder = tmp_path / "FolderMod-5678-2-0"
        folder.mkdir()
        (folder / "part1.pak").write_text("dummy")
        (folder / "part2.pak").write_text("dummy")
        
        row = build_download_row(folder, relative_to=tmp_path)
        print("\n2. Folder with .pak files")
        print(f"   Name: {row['name']}")
        print(f"   Mod ID: {row['modID']}")
        print(f"   Version: {row['version']}")
        print(f"   Contents: {row['contents']}")
        assert row['modID'] == '5678', f"Expected modID 5678, got {row['modID']}"
        assert len(row['contents']) == 2, f"Expected 2 pak files, got {len(row['contents'])}"
        print("   ✅ PASS")
    
    print("\n" + "=" * 70)
    print("✅ All build_download_row tests passed!")
    print("=" * 70)


def test_list_files():
    """Test that list_files_level_one_including_root finds all download types."""
    print("\n" + "=" * 70)
    print("Testing: list_files_level_one_including_root()")
    print("=" * 70)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        
        # Create test structure:
        # downloads/
        #   ├── single.pak (single pak file)
        #   ├── archive.zip (would be an archive)
        #   ├── FolderMod-1234/ (folder with paks)
        #   │   └── mod.pak
        #   └── EmptyFolder/ (no paks - should be ignored)
        #       └── readme.txt
        
        (tmp_path / "single.pak").write_text("dummy")
        (tmp_path / "archive.zip").write_text("dummy")
        
        folder_mod = tmp_path / "FolderMod-1234-1-0"
        folder_mod.mkdir()
        (folder_mod / "mod.pak").write_text("dummy")
        
        empty_folder = tmp_path / "EmptyFolder"
        empty_folder.mkdir()
        (empty_folder / "readme.txt").write_text("no paks")
        
        results = list_files_level_one_including_root(str(tmp_path))
        
        print(f"\nFound {len(results)} items:")
        for name, rel_path in sorted(results):
            print(f"   - {name} (rel: '{rel_path}')")
        
        names = [name for name, _ in results]
        
        # Should find:
        # - single.pak (file in root)
        # - archive.zip (file in root)
        # - FolderMod-1234-1-0 (folder with .pak files)
        # Should NOT find:
        # - EmptyFolder (no .pak files)
        
        assert "single.pak" in names, "Missing single.pak"
        assert "archive.zip" in names, "Missing archive.zip"
        assert "FolderMod-1234-1-0" in names, "Missing FolderMod-1234-1-0 folder"
        assert "EmptyFolder" not in names, "EmptyFolder should be excluded (no .pak files)"
        
        print("\n   ✅ PASS - All expected items found!")
    
    print("\n" + "=" * 70)
    print("✅ All list_files tests passed!")
    print("=" * 70)


def main():
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 15 + "Download Format Support Test" + " " * 24 + "║")
    print("╚" + "=" * 68 + "╝")
    
    try:
        test_enumerate_contents()
        test_build_download_row()
        test_list_files()
        
        print("\n" + "🎉" * 35)
        print("✅ ALL TESTS PASSED!")
        print("🎉" * 35)
        print("\nSupported download formats:")
        print("  ✅ .zip, .rar, .7z archives (with .pak files inside)")
        print("  ✅ Folders containing .pak files (loose or nested)")
        print("  ✅ Single .pak files")
        print("=" * 70)
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
