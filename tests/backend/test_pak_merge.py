"""M3: _merge_pak_bundle collapses IoStore bundles into one logical container.

This logic existed twice verbatim inside _ingest_resolved_download -- once for
the initial upsert, once after a mod_id was discovered. Two copies of
extension-normalisation and io_store tracking is exactly how "conflicts show for
one mod but not another" bugs appear.
"""
from __future__ import annotations

import pytest

from core.api.server import _merge_pak_bundle


def test_standalone_pak_passes_through():
    merged, io = _merge_pak_bundle({"Thing.pak": ["/Game/a.uasset"]})
    assert merged == {"Thing.pak": ["/Game/a.uasset"]}
    assert io == {"Thing.pak": False}


def test_utoc_is_rekeyed_onto_pak():
    merged, io = _merge_pak_bundle({"Thing.utoc": ["/Game/a.uasset"]})
    assert merged == {"Thing.pak": ["/Game/a.uasset"]}
    assert io == {"Thing.pak": True}, "a .utoc member marks the bundle as IoStore"


def test_ucas_is_rekeyed_onto_pak_without_setting_io_store():
    """Only .utoc implies IoStore; .ucas is the payload sibling."""
    merged, io = _merge_pak_bundle({"Thing.ucas": []})
    assert set(merged) == {"Thing.pak"}
    assert io == {"Thing.pak": False}


def test_full_iostore_triple_merges_into_one_entry():
    merged, io = _merge_pak_bundle(
        {
            "Luna.pak": ["/Game/a.uasset"],
            "Luna.utoc": ["/Game/b.uasset"],
            "Luna.ucas": ["/Game/c.uasset"],
        }
    )
    assert set(merged) == {"Luna.pak"}
    assert merged["Luna.pak"] == [
        "/Game/a.uasset",
        "/Game/b.uasset",
        "/Game/c.uasset",
    ]
    assert io == {"Luna.pak": True}


def test_multiple_bundles_stay_separate():
    merged, io = _merge_pak_bundle(
        {
            "A.pak": ["/Game/a.uasset"],
            "A.utoc": [],
            "B.pak": ["/Game/b.uasset"],
        }
    )
    assert set(merged) == {"A.pak", "B.pak"}
    assert io == {"A.pak": True, "B.pak": False}


def test_assets_are_deduplicated():
    merged, _ = _merge_pak_bundle(
        {
            "X.pak": ["/Game/dup.uasset", "/Game/dup.uasset"],
            "X.utoc": ["/Game/dup.uasset"],
        }
    )
    assert merged["X.pak"] == ["/Game/dup.uasset"]


def test_assets_are_sorted_for_deterministic_rows():
    merged, _ = _merge_pak_bundle({"X.pak": ["/z.uasset", "/a.uasset", "/m.uasset"]})
    assert merged["X.pak"] == ["/a.uasset", "/m.uasset", "/z.uasset"]


def test_is_idempotent():
    """Calling twice with the same input must give identical results -- this is
    what guarantees the two former copies cannot diverge."""
    pak_map = {
        "Luna.pak": ["/Game/b.uasset", "/Game/a.uasset"],
        "Luna.utoc": ["/Game/a.uasset"],
        "Other.pak": [],
    }
    first = _merge_pak_bundle(pak_map)
    second = _merge_pak_bundle(pak_map)
    assert first == second


def test_does_not_mutate_input():
    pak_map = {"X.pak": ["/a.uasset"], "X.utoc": ["/b.uasset"]}
    snapshot = {k: list(v) for k, v in pak_map.items()}
    _merge_pak_bundle(pak_map)
    assert pak_map == snapshot, "input pak_map was mutated"


@pytest.mark.parametrize("empty", [{}, None])
def test_empty_input(empty):
    merged, io = _merge_pak_bundle(empty)
    assert merged == {}
    assert io == {}


def test_case_insensitive_extension_matching():
    merged, io = _merge_pak_bundle({"Loud.UTOC": ["/a.uasset"]})
    assert set(merged) == {"Loud.pak"}
    assert io["Loud.pak"] is True


def test_empty_asset_lists_still_register_the_pak():
    """The empty-asset-map fallback relies on this: a pak with no known assets
    must still produce a row so io_store can be recorded."""
    merged, io = _merge_pak_bundle({"Empty.pak": [], "Empty.utoc": []})
    assert merged == {"Empty.pak": []}
    assert io == {"Empty.pak": True}


def test_blank_keys_are_skipped():
    merged, io = _merge_pak_bundle({"": ["/a.uasset"], "Ok.pak": ["/b.uasset"]})
    assert set(merged) == {"Ok.pak"}


def test_matches_the_original_inline_algorithm():
    """Differential test against a transcription of the pre-refactor code, so the
    extraction is provably behaviour-preserving."""

    def original(pak_map):
        merged_pak_map = {}
        merged_io_store = {}
        for raw_pak_name, assets in pak_map.items():
            lower_pak = raw_pak_name.lower()
            if lower_pak.endswith(".utoc"):
                normalized_name = raw_pak_name[:-5] + ".pak"
            elif lower_pak.endswith(".ucas"):
                normalized_name = raw_pak_name[:-5] + ".pak"
            else:
                normalized_name = raw_pak_name
            is_utoc = lower_pak.endswith(".utoc")
            if normalized_name not in merged_io_store:
                merged_io_store[normalized_name] = False
            if is_utoc:
                merged_io_store[normalized_name] = True
            if normalized_name not in merged_pak_map:
                merged_pak_map[normalized_name] = []
            merged_pak_map[normalized_name].extend(assets)
        # The callers then did: assets = sorted(list(set(assets)))
        return (
            {k: sorted(set(v)) for k, v in merged_pak_map.items()},
            merged_io_store,
        )

    fixtures = [
        {"A.pak": ["/1", "/2"]},
        {"A.utoc": ["/1"], "A.pak": ["/2"], "A.ucas": ["/1"]},
        {"A.pak": [], "B.utoc": [], "C.ucas": ["/9"]},
        {"Mixed.UTOC": ["/x"], "Mixed.pak": ["/y"]},
        {"Dup.pak": ["/same", "/same", "/other"]},
    ]
    for pak_map in fixtures:
        assert _merge_pak_bundle(pak_map) == original(pak_map), pak_map
