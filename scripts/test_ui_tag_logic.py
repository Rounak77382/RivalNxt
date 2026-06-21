"""
Test UI asset filename character ID extraction and regression guard
for the character tag detection fix.
"""
import sys
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.tag_assets as ta
importlib.reload(ta)
from scripts.tag_assets import load_entity_map_from_db, tag_asset, find_entity_key

em = load_entity_map_from_db()

PASS = 0
FAIL = 0

def check(desc, path, expected_contains, expected_absent=None):
    global PASS, FAIL
    result = tag_asset(path, em)
    fname = path.split("/")[-1]
    ok = all(e in result for e in expected_contains)
    if expected_absent:
        ok = ok and not any(e in result for e in expected_absent)
    status = "[PASS]" if ok else "[FAIL]"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"{status} | {desc}")
    print(f"       File: {fname}")
    print(f"       Result: {result!r}")
    if not ok:
        if expected_contains:
            print(f"       Expected to contain: {expected_contains}")
        if expected_absent:
            print(f"       Expected to NOT contain: {expected_absent}")
    print()

print("=" * 60)
print("UI Filename Character ID Extraction Tests")
print("=" * 60)

# --- Scheme A: img_battle_2{char4}{3-suffix} ---
check(
    "Scheme A: img_battle_21039020 -> Thor (1039)",
    "/marvel/content/marvel/ui/textures/battle/playerinfo/v2/proficiency/img_battle_21039020_avatar.uasset",
    expected_contains=["thor"],
)
check(
    "Scheme A: img_battle_21029020 -> Magik (1029)",
    "/marvel/content/marvel/ui/textures/battle/playerinfo/v2/proficiency/img_battle_21029020_avatar.uasset",
    expected_contains=["magik"],
)
check(
    "Scheme A: img_selecthero_21039020 -> Thor (1039)",
    "/marvel/content/marvel/ui/heroportrait/selecthero/proficiency/img_selecthero_21039020.uasset",
    expected_contains=["thor"],
)

# --- Scheme B: img_X_{char4}{4-suffix} ---
check(
    "Scheme B: img_squarehead_10270010 -> Groot (1027)",
    "/marvel/content/marvel/ui/img_squarehead_10270010_avatar.uasset",
    expected_contains=["groot"],
)
check(
    "Scheme B: img_commontransverse_10300010 -> Moon Knight (1030)",
    "/marvel/content/marvel/ui/img_commontransverse_10300010_avatar.uasset",
    expected_contains=["moon knight"],
)

# --- Scheme C: img_X_{char4}{1-suffix} (5 digits) ---
check(
    "Scheme C: img_battle_10270 -> Groot (1027)",
    "/marvel/content/marvel/ui/img_battle_10270_avatar.uasset",
    expected_contains=["groot"],
)

# --- Regression: character folder paths MUST use directory ID only ---
check(
    "Regression: Mantis folder with Hela-ref texture -> mantis only",
    "/marvel/content/marvel/characters/1020/1020800/textures/t_1024301_equip_01_d.uasset",
    expected_contains=["mantis"],
    expected_absent=["hela", "psylocke", "cloak"],
)
check(
    "Regression: Thor folder with IronMan-ref texture -> thor only",
    "/marvel/content/marvel/characters/1039/1039301/textures/t_1034503_equip_03_d.uasset",
    expected_contains=["thor"],
    expected_absent=["iron man", "doctor strange", "mister fantastic"],
)
check(
    "Regression: C&D skin path -> cloak & dagger only",
    "/marvel/content/marvel/characters/1025/1025303/materials/cloak.uasset",
    expected_contains=["cloak & dagger"],
    expected_absent=["captain america"],
)

# --- Spec examples ---
check(
    "Spec: character asset -> Thor (1039) from /characters/1039/1039301/ dir",
    "/marvel/content/marvel/characters/1039/1039301/materials/lobby/mi_1039301_eyes.uasset",
    expected_contains=["thor"],
)

print("=" * 60)
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL == 0:
    print("All tests passed!")
else:
    print("SOME TESTS FAILED!")
    sys.exit(1)
