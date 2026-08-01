"""L2: `register_handoff` must return a record on every path.

The `return record` statement was indented inside `if nxm.file_id is not None`,
so a handoff whose file_id is None fell through to an implicit `return None`.
submit_nxm_handoff (core/api/server.py) then does `record["id"]`, which raises
TypeError -> unhandled 500.
"""
from __future__ import annotations

import pytest

from core.api.services import handoffs as handoffs_mod
from core.nexus.nxm import NXMRequest


@pytest.fixture(autouse=True)
def _isolate_handoff_state(monkeypatch, schema_db):
    """Point handoff DB writes at the throwaway schema DB and clear the
    in-memory registry between tests."""
    monkeypatch.setattr(
        "core.api.dependencies.get_db", lambda: schema_db, raising=True
    )
    handoffs_mod._HANDOFFS.clear()
    handoffs_mod._HANDOFF_FAILURES.clear()
    yield
    handoffs_mod._HANDOFFS.clear()
    handoffs_mod._HANDOFF_FAILURES.clear()


def _req(file_id, mod_id=1234):
    return NXMRequest(
        raw=f"nxm://marvelrivals/mods/{mod_id}/files/{file_id}",
        game_domain="marvelrivals",
        mod_id=mod_id,
        file_id=file_id,
        query={},
    )


def test_returns_record_when_file_id_is_none():
    """The regression case: this returned None before the fix."""
    record = handoffs_mod.register_handoff(_req(None), metadata={})

    assert record is not None, "register_handoff returned None for file_id=None"
    assert "id" in record
    assert isinstance(record["id"], str) and record["id"]


def test_returns_record_when_file_id_is_present():
    record = handoffs_mod.register_handoff(_req(7689), metadata={"file_id": 7689})

    assert record is not None
    assert "id" in record
    assert record["request"]["file_id"] == 7689


def test_returns_record_when_file_id_is_zero():
    """Collection NXM URIs parse to file_id=0 (core/nexus/nxm.py). 0 is falsy,
    so this guards against anyone 'simplifying' the None check to a truthiness
    check."""
    record = handoffs_mod.register_handoff(_req(0, mod_id=0), metadata={})

    assert record is not None
    assert "id" in record


def test_registered_record_is_retrievable():
    """A returned record must actually be in the registry, not a detached dict."""
    record = handoffs_mod.register_handoff(_req(None), metadata={})
    fetched = handoffs_mod.get_handoff_or_404(record["id"])
    assert fetched["id"] == record["id"]


def test_record_shape_is_unchanged():
    """Guards the contract submit_nxm_handoff and serialize_handoff rely on."""
    record = handoffs_mod.register_handoff(_req(4242), metadata={"k": "v"})

    for key in ("id", "created_at", "expires_at", "request", "metadata"):
        assert key in record, f"missing key {key}"
    assert record["expires_at"] > record["created_at"]
    assert handoffs_mod.serialize_handoff(record)["id"] == record["id"]


def test_no_crash_path_through_submit_style_dereference():
    """Reproduces exactly what server.py:2778 does with the return value."""
    for file_id in (None, 0, 999):
        record = handoffs_mod.register_handoff(_req(file_id), metadata={})
        assert record["id"]  # would TypeError on None before the fix
