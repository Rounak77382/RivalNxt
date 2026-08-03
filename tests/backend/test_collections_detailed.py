"""F4 (batching): /api/collections/detailed must be O(1) queries, not O(N).

The frontend called GET /api/collections and then GET /api/collections/{id} once
per collection. With 20 collections that is 21 HTTP requests -- each with its own
connection, round trip and JSON encode -- to render one page, repeated on every
poll.
"""
from __future__ import annotations

import pytest

import core.api.server as server


@pytest.fixture
def wired(monkeypatch, schema_db):
    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoClose(schema_db))
    return schema_db


def _seed(conn, n_collections: int, files_each: int) -> None:
    for c in range(n_collections):
        conn.execute(
            """
            INSERT INTO collections(slug, revision_num, name, author, total_mods, fetched_at)
            VALUES(?, 1, ?, 'someone', ?, ?)
            """,
            (f"slug-{c}", f"Collection {c}", files_each, f"2026-01-{c + 1:02d}"),
        )
        cid = conn.execute(
            "SELECT id FROM collections WHERE slug = ?", (f"slug-{c}",)
        ).fetchone()[0]
        for f in range(files_each):
            conn.execute(
                """
                INSERT INTO collection_mod_files
                    (collection_id, file_id, mod_id, version, file_name, mod_name, download_state)
                VALUES(?, ?, ?, '1.0', ?, ?, 'pending')
                """,
                (cid, 1000 * c + f, 500 + f, f"c{c}f{f}.zip", f"Mod {c}-{f}"),
            )
    conn.commit()


def test_returns_all_collections_with_their_files(wired):
    _seed(wired, n_collections=3, files_each=4)
    result = server.list_collections_detailed()

    assert result["ok"] is True
    assert result["count"] == 3
    assert len(result["collections"]) == 3
    for coll in result["collections"]:
        assert "mod_files" in coll
        assert len(coll["mod_files"]) == 4


def test_query_count_is_constant_in_collection_count(wired, recorder):
    """The whole point: 2 queries whether there are 3 collections or 30."""
    _seed(wired, n_collections=3, files_each=2)

    wired.set_trace_callback(recorder)
    server.list_collections_detailed()
    wired.set_trace_callback(None)
    small = recorder.count("SELECT")

    wired.execute("DELETE FROM collection_mod_files")
    wired.execute("DELETE FROM collections")
    wired.commit()
    _seed(wired, n_collections=30, files_each=2)

    recorder.reset()
    wired.set_trace_callback(recorder)
    server.list_collections_detailed()
    wired.set_trace_callback(None)
    large = recorder.count("SELECT")

    assert small == large, (
        f"query count scaled with collections: {small} for 3, {large} for 30"
    )
    assert large <= 3, f"expected a constant handful of queries, got {large}"


def test_shape_matches_the_per_collection_endpoint(wired):
    """The client swaps N calls to /api/collections/{id} for one call here, so the
    per-collection shape must be identical."""
    _seed(wired, n_collections=2, files_each=3)

    batched = {c["id"]: c for c in server.list_collections_detailed()["collections"]}
    for cid, batched_coll in batched.items():
        single = server.get_collection(cid)["collection"]
        assert set(batched_coll.keys()) == set(single.keys()), (
            f"key mismatch for collection {cid}"
        )
        assert batched_coll["mod_files"] == single["mod_files"]
        for key in single:
            assert batched_coll[key] == single[key], f"{key} differs for {cid}"


def test_files_are_grouped_to_the_right_collection(wired):
    _seed(wired, n_collections=3, files_each=2)
    result = server.list_collections_detailed()

    for coll in result["collections"]:
        # Seeded file_ids are 1000*c + f, so every file must belong to its own
        # collection's block.
        index = int(coll["slug"].split("-")[1])
        for f in coll["mod_files"]:
            assert 1000 * index <= f["file_id"] < 1000 * index + 100, (
                f"file {f['file_id']} attached to the wrong collection {coll['slug']}"
            )


def test_collection_with_no_files_gets_an_empty_list(wired):
    _seed(wired, n_collections=1, files_each=0)
    result = server.list_collections_detailed()
    assert result["collections"][0]["mod_files"] == []


def test_empty_database(wired):
    result = server.list_collections_detailed()
    assert result["ok"] is True
    assert result["count"] == 0
    assert result["collections"] == []


def test_applies_the_same_mod_id_filter_as_the_single_endpoint(wired):
    """_serialize_collection excludes mod_id 2940; the batched route must agree or
    the two endpoints would disagree about a collection's contents."""
    _seed(wired, n_collections=1, files_each=1)
    cid = wired.execute("SELECT id FROM collections").fetchone()[0]
    wired.execute(
        """
        INSERT INTO collection_mod_files
            (collection_id, file_id, mod_id, version, file_name, mod_name, download_state)
        VALUES(?, 99999, 2940, '1.0', 'excluded.zip', 'Excluded', 'pending')
        """,
        (cid,),
    )
    wired.commit()

    batched = server.list_collections_detailed()["collections"][0]
    single = server.get_collection(cid)["collection"]

    assert all(f["mod_id"] != 2940 for f in batched["mod_files"])
    assert len(batched["mod_files"]) == len(single["mod_files"])


def test_ordering_matches_the_list_endpoint(wired):
    """Both order by fetched_at DESC, so the UI order does not change."""
    _seed(wired, n_collections=4, files_each=1)
    batched = [c["slug"] for c in server.list_collections_detailed()["collections"]]
    listed = [c["slug"] for c in server.list_collections()["collections"]]
    assert batched == listed


def test_route_is_registered_before_the_id_route(wired):
    """FastAPI matches in declaration order: if /api/collections/{collection_id}
    came first, "detailed" would be parsed as an int and 422."""
    paths = [getattr(r, "path", "") for r in server.app.routes]
    assert "/api/collections/detailed" in paths
    assert "/api/collections/{collection_id}" in paths
    assert paths.index("/api/collections/detailed") < paths.index(
        "/api/collections/{collection_id}"
    ), "the literal route must be declared before the parameterised one"


def test_endpoint_reachable_over_http(monkeypatch, schema_db):
    """Proves the ordering above actually works through the router, not just in
    the route table.

    TestClient dispatches on a worker thread, and a sqlite3 connection is bound to
    the thread that created it. So this opens its own connection to the same file
    with check_same_thread=False rather than reusing the fixture's.
    """
    import sqlite3

    from fastapi.testclient import TestClient

    _seed(schema_db, n_collections=2, files_each=1)
    db_path = schema_db.execute("PRAGMA database_list").fetchone()[2]

    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    def _get_db():
        conn = sqlite3.connect(db_path, check_same_thread=False)
        return _NoClose(conn)

    monkeypatch.setattr(server, "get_db", _get_db)

    with TestClient(server.app) as client:
        r = client.get("/api/collections/detailed")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert all("mod_files" in c for c in body["collections"])
