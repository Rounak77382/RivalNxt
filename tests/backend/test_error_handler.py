"""H1: structured error responses with correlation IDs.

Only CORSMiddleware was registered on the app -- there was no exception handler
at all. Any unhandled exception became FastAPI's default 500 with an opaque
"Internal Server Error" body and nothing linking the user's toast to a log line.
10 endpoints also hand-rolled `raise HTTPException(500, detail=str(e))`, leaking
raw exception text to the UI.
"""
from __future__ import annotations

import re
import uuid

import pytest
from fastapi.testclient import TestClient

import core.api.server as server
from core.api.server import (
    HTTP_STATUS_CANCELLED,
    DownloadCancelledError,
    DuplicateDownloadError,
)

# Routes that raise on demand. Registered once, at import time.
_PROBE_PREFIX = "/__test_errors__"


@pytest.fixture(scope="module", autouse=True)
def _probe_routes():
    @server.app.get(f"{_PROBE_PREFIX}/cancelled")
    def _raise_cancelled():
        raise DownloadCancelledError("Cancelled by user")

    @server.app.get(f"{_PROBE_PREFIX}/duplicate")
    def _raise_duplicate():
        raise DuplicateDownloadError(
            77,
            existing_name="Cool Mod",
            existing_version="1.2",
            existing_path="C:/dl/CoolMod.zip",
            candidate_name="Cool Mod",
            candidate_version="1.2",
        )

    @server.app.get(f"{_PROBE_PREFIX}/boom")
    def _raise_runtime():
        raise RuntimeError("something went very wrong internally")

    @server.app.get(f"{_PROBE_PREFIX}/http-error")
    def _raise_http():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="not found here")

    yield


@pytest.fixture
def client():
    # raise_server_exceptions=False so the handler's response is returned rather
    # than the exception being re-raised into the test.
    with TestClient(server.app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# DownloadCancelledError -> not a 500
# ---------------------------------------------------------------------------
def test_cancelled_is_not_a_500(client):
    r = client.get(f"{_PROBE_PREFIX}/cancelled")
    assert r.status_code != 500
    assert r.status_code == HTTP_STATUS_CANCELLED == 499


def test_cancelled_body_shape(client):
    body = client.get(f"{_PROBE_PREFIX}/cancelled").json()
    assert body["error"] == "download_cancelled"
    assert "cancel" in body["message"].lower()
    assert body["endpoint"] == f"{_PROBE_PREFIX}/cancelled"


def test_cancelled_has_no_correlation_id(client):
    """A user cancelling is not a defect, so there is nothing to report."""
    assert "correlation_id" not in client.get(f"{_PROBE_PREFIX}/cancelled").json()


# ---------------------------------------------------------------------------
# DuplicateDownloadError -> 409 in the established shape
# ---------------------------------------------------------------------------
def test_duplicate_returns_409(client):
    assert client.get(f"{_PROBE_PREFIX}/duplicate").status_code == 409


def test_duplicate_body_matches_duplicate_detail_helper(client):
    body = client.get(f"{_PROBE_PREFIX}/duplicate").json()
    expected = server._duplicate_detail_from_error(
        DuplicateDownloadError(
            77,
            existing_name="Cool Mod",
            existing_version="1.2",
            existing_path="C:/dl/CoolMod.zip",
            candidate_name="Cool Mod",
            candidate_version="1.2",
        )
    )
    assert body == expected


def test_duplicate_body_carries_the_existing_id(client):
    body = client.get(f"{_PROBE_PREFIX}/duplicate").json()
    assert body["error"] == "duplicate_download"
    assert body["existing_download_id"] == 77
    assert body["existing_name"] == "Cool Mod"


# ---------------------------------------------------------------------------
# Unhandled exception -> 500 with a correlation id
# ---------------------------------------------------------------------------
def test_unhandled_error_returns_500(client):
    assert client.get(f"{_PROBE_PREFIX}/boom").status_code == 500


def test_unhandled_error_has_correlation_id(client):
    body = client.get(f"{_PROBE_PREFIX}/boom").json()
    assert "correlation_id" in body, body
    cid = body["correlation_id"]
    assert isinstance(cid, str)
    assert re.fullmatch(r"[0-9a-f]{12}", cid), f"not a hex id: {cid!r}"


def test_correlation_id_is_a_uuid_fragment():
    """The id must come from uuid4, not a counter that repeats across restarts."""
    ids = {server._new_correlation_id() for _ in range(200)}
    assert len(ids) == 200, "correlation ids collided"
    for cid in list(ids)[:5]:
        # Must be a valid prefix of a uuid4 hex.
        uuid.UUID(cid + "0" * (32 - len(cid)))


def test_correlation_id_differs_per_request(client):
    a = client.get(f"{_PROBE_PREFIX}/boom").json()["correlation_id"]
    b = client.get(f"{_PROBE_PREFIX}/boom").json()["correlation_id"]
    assert a != b


def test_unhandled_error_does_not_leak_exception_text(client):
    """The raw message must stay in the log, not go to the UI."""
    body = client.get(f"{_PROBE_PREFIX}/boom").json()
    serialized = str(body)
    assert "something went very wrong internally" not in serialized, body
    assert "RuntimeError" not in serialized, body


def test_unhandled_error_body_shape(client):
    body = client.get(f"{_PROBE_PREFIX}/boom").json()
    assert body["error"] == "internal_error"
    assert body["endpoint"] == f"{_PROBE_PREFIX}/boom"
    assert body["correlation_id"] in body["message"], (
        "the user must be able to quote the id from the message"
    )


def test_correlation_id_is_logged(client, caplog):
    """The id is worthless unless it appears in the log for correlation."""
    import logging

    with caplog.at_level(logging.ERROR, logger="modmanager.api"):
        cid = client.get(f"{_PROBE_PREFIX}/boom").json()["correlation_id"]
    assert any(cid in rec.getMessage() for rec in caplog.records), (
        f"correlation id {cid} not found in log records"
    )


def test_traceback_is_logged(client, caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger="modmanager.api"):
        client.get(f"{_PROBE_PREFIX}/boom")
    assert any(rec.exc_info for rec in caplog.records), "no traceback captured"


# ---------------------------------------------------------------------------
# Explicit HTTPExceptions must pass through untouched
# ---------------------------------------------------------------------------
def test_http_exception_is_not_swallowed(client):
    r = client.get(f"{_PROBE_PREFIX}/http-error")
    assert r.status_code == 404
    assert r.json()["detail"] == "not found here"


def test_validation_errors_still_return_422(client):
    """FastAPI's own handler must keep priority over the catch-all."""
    r = client.post("/api/debug/log", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text


def test_known_endpoint_still_works(client):
    """Sanity: the handlers must not break normal responses."""
    r = client.get("/health")
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# The hand-rolled 500s are gone
# ---------------------------------------------------------------------------
def test_no_generic_500_reraise_sites_remain():
    from pathlib import Path

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert "raise HTTPException(status_code=500, detail=str(e))" not in src, (
        "a hand-rolled generic 500 re-raise is back; it leaks str(e) to the UI "
        "and produces no correlation id"
    )


def test_rollback_handlers_were_preserved():
    """Endpoints that rolled back before raising must still roll back."""
    from pathlib import Path

    src = Path(server.__file__).read_text(encoding="utf-8")
    # Four write endpoints previously did logger.error + conn.rollback() + raise 500.
    # They must still roll back, then re-raise so the global handler assigns an id.
    assert src.count("conn.rollback()\n\t\traise") == 4, (
        "rollback-then-reraise blocks were lost or duplicated during the 500 "
        f"cleanup (found {src.count('conn.rollback()' + chr(10) + chr(9) * 2 + 'raise')})"
    )
    # And none of them may still convert the error into a bare 500.
    assert "conn.rollback()\n\t\traise HTTPException" not in src
