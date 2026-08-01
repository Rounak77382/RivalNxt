"""L7: /api/debug/log must be bounded in size and rate.

The endpoint took an unbounded Dict[str, Any], json.dumps'd the whole `data`
field into backend.log, and had no rate limit -- a frontend loop could fill the
user's disk.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import core.api.server as server
from core.api.server import (
    DEBUG_LOG_MAX_DATA_CHARS,
    DEBUG_LOG_MAX_MESSAGE_CHARS,
    DEBUG_LOG_RATE_LIMIT_PER_SEC,
)


@pytest.fixture
def client():
    server._reset_debug_log_bucket()
    with TestClient(server.app) as c:
        yield c
    server._reset_debug_log_bucket()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
def test_normal_request_succeeds(client):
    r = client.post("/api/debug/log", json={"message": "hello", "level": "INFO"})
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "logged"}


def test_request_with_small_data_succeeds(client):
    r = client.post(
        "/api/debug/log",
        json={"message": "with data", "data": {"a": 1, "b": "two"}},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("level", ["INFO", "WARN", "ERROR", "debug", "weird"])
def test_all_levels_accepted(client, level):
    r = client.post("/api/debug/log", json={"message": "lvl", "level": level})
    assert r.status_code == 200, r.text


def test_message_at_exact_limit_is_accepted(client):
    r = client.post(
        "/api/debug/log", json={"message": "x" * DEBUG_LOG_MAX_MESSAGE_CHARS}
    )
    assert r.status_code == 200, r.text


def test_non_string_message_is_coerced_not_rejected(client):
    r = client.post("/api/debug/log", json={"message": 12345})
    assert r.status_code == 200, r.text


def test_unserialisable_data_does_not_500(client):
    """json.dumps(default=str) must absorb exotic values."""
    r = client.post(
        "/api/debug/log",
        json={"message": "nested", "data": {"deep": {"x": [1, 2, {"y": None}]}}},
    )
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 413 size caps
# ---------------------------------------------------------------------------
def test_oversized_message_returns_413(client):
    r = client.post(
        "/api/debug/log", json={"message": "x" * (DEBUG_LOG_MAX_MESSAGE_CHARS + 1)}
    )
    assert r.status_code == 413, r.text
    assert str(DEBUG_LOG_MAX_MESSAGE_CHARS) in r.json()["detail"]


def test_oversized_data_returns_413(client):
    r = client.post(
        "/api/debug/log",
        json={"message": "ok", "data": {"blob": "y" * (DEBUG_LOG_MAX_DATA_CHARS + 100)}},
    )
    assert r.status_code == 413, r.text
    assert str(DEBUG_LOG_MAX_DATA_CHARS) in r.json()["detail"]


def test_oversized_data_via_many_keys_returns_413(client):
    """The cap is on serialized length, so breadth counts too, not just one big
    string."""
    payload = {f"key_{i}": "z" * 40 for i in range(400)}
    r = client.post("/api/debug/log", json={"message": "ok", "data": payload})
    assert r.status_code == 413, r.text


# ---------------------------------------------------------------------------
# 429 rate limit
# ---------------------------------------------------------------------------
@pytest.fixture
def frozen_clock(monkeypatch):
    """Freeze the rate limiter's clock so the burst tests are deterministic.

    Without this, 25 TestClient round-trips can span more than 1/20th of a
    second, the bucket refills mid-burst, and the test flakes.
    """
    monkeypatch.setattr(server, "_debug_log_clock", lambda: 5000.0)
    server._reset_debug_log_bucket()
    yield
    server._reset_debug_log_bucket()


def test_burst_beyond_limit_returns_429(client, frozen_clock):
    """25 rapid requests within one window: exactly the capacity may pass."""
    statuses = [
        client.post("/api/debug/log", json={"message": f"m{i}"}).status_code
        for i in range(25)
    ]
    assert 200 in statuses, statuses
    assert 429 in statuses, f"no request was rate limited: {statuses}"
    assert statuses.count(200) == DEBUG_LOG_RATE_LIMIT_PER_SEC, statuses
    assert statuses.count(429) == 25 - DEBUG_LOG_RATE_LIMIT_PER_SEC, statuses
    # Rejections must all come after the allowance is spent.
    assert statuses[:DEBUG_LOG_RATE_LIMIT_PER_SEC] == [200] * DEBUG_LOG_RATE_LIMIT_PER_SEC


def test_rate_limit_message_is_informative(client, frozen_clock):
    for i in range(30):
        r = client.post("/api/debug/log", json={"message": f"m{i}"})
        if r.status_code == 429:
            assert "rate limit" in r.json()["detail"].lower()
            return
    pytest.fail("never hit the rate limit")


def test_rate_limit_recovers_after_window(client, monkeypatch):
    """A rejected client must be able to log again once the bucket refills."""
    clock = {"t": 6000.0}
    monkeypatch.setattr(server, "_debug_log_clock", lambda: clock["t"])
    server._reset_debug_log_bucket()

    for _ in range(DEBUG_LOG_RATE_LIMIT_PER_SEC):
        assert client.post("/api/debug/log", json={"message": "x"}).status_code == 200
    assert client.post("/api/debug/log", json={"message": "x"}).status_code == 429

    clock["t"] += 1.0  # one full second -> full refill
    assert client.post("/api/debug/log", json={"message": "x"}).status_code == 200


# ---------------------------------------------------------------------------
# Token bucket unit behaviour (deterministic, no wall-clock dependency)
# ---------------------------------------------------------------------------
def test_bucket_allows_capacity_then_blocks():
    server._reset_debug_log_bucket()
    now = 1000.0
    allowed = sum(1 for _ in range(DEBUG_LOG_RATE_LIMIT_PER_SEC) if server._debug_log_take_token(now))
    assert allowed == DEBUG_LOG_RATE_LIMIT_PER_SEC
    assert server._debug_log_take_token(now) is False


def test_bucket_refills_over_time():
    server._reset_debug_log_bucket()
    now = 2000.0
    for _ in range(DEBUG_LOG_RATE_LIMIT_PER_SEC):
        server._debug_log_take_token(now)
    assert server._debug_log_take_token(now) is False

    # Half a second later: roughly half the capacity is back.
    later = now + 0.5
    refilled = sum(1 for _ in range(DEBUG_LOG_RATE_LIMIT_PER_SEC) if server._debug_log_take_token(later))
    assert 1 <= refilled <= DEBUG_LOG_RATE_LIMIT_PER_SEC, refilled


def test_bucket_never_exceeds_capacity():
    server._reset_debug_log_bucket()
    now = 3000.0
    server._debug_log_take_token(now)
    # A long idle period must not let the bucket grow unbounded.
    far_later = now + 10_000.0
    allowed = sum(1 for _ in range(200) if server._debug_log_take_token(far_later))
    assert allowed == DEBUG_LOG_RATE_LIMIT_PER_SEC, allowed


def test_bucket_is_thread_safe():
    """Concurrent renderers must not over-draw the bucket."""
    import threading

    server._reset_debug_log_bucket()
    now = 4000.0
    results: list[bool] = []
    lock = threading.Lock()

    def worker():
        got = server._debug_log_take_token(now)
        with lock:
            results.append(got)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == DEBUG_LOG_RATE_LIMIT_PER_SEC, sum(results)
