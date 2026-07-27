"""In-memory tracking for Nexus handoff metadata."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from core.nexus.nxm import NXMRequest

NXM_HANDOFF_TTL_SECONDS = 600
MAX_HANDOFF_RETRIES = 3
HANDOFF_FAILURE_BACKOFF_SECONDS = 60

_HANDOFFS: Dict[str, Dict[str, Any]] = {}
_HANDOFF_LOCK = threading.Lock()

# Track handoff failures to prevent infinite retry loops
_HANDOFF_FAILURES: Dict[str, Dict[str, Any]] = {}
_FAILURE_LOCK = threading.Lock()


def _purge_expired_locked(now: Optional[float] = None) -> None:
    current = time.time() if now is None else now
    expired = [identifier for identifier, record in list(_HANDOFFS.items()) if record.get("expires_at", current) <= current]
    for identifier in expired:
        _HANDOFFS.pop(identifier, None)


def _purge_expired(now: Optional[float] = None) -> None:
    with _HANDOFF_LOCK:
        _purge_expired_locked(now)


def _purge_expired_failures(now: Optional[float] = None) -> None:
    """Remove failure records that are beyond the backoff window.

    Also drops benign "duplicate download" rows from the persistent table.
    That cleanup used to live inside list_handoffs(), which meant a plain GET
    /api/nxm/handoffs performed writes -- and the frontend polled that endpoint
    once per second.
    """
    current = time.time() if now is None else now
    with _FAILURE_LOCK:
        expired = [
            handoff_id
            for handoff_id, failure in list(_HANDOFF_FAILURES.items())
            if current - failure.get("last_attempt", 0) > HANDOFF_FAILURE_BACKOFF_SECONDS * 2
        ]
        for handoff_id in expired:
            _HANDOFF_FAILURES.pop(handoff_id, None)

    _purge_benign_duplicate_failures()


def _purge_benign_duplicate_failures() -> int:
    """Delete "duplicate download" failure rows; they mean the file is already
    present, not that anything failed. Returns the number of rows removed."""
    from core.api.dependencies import get_db

    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM handoff_failures
            WHERE LOWER(COALESCE(error_message, '')) LIKE '%duplicate download detected%'
               OR LOWER(COALESCE(error_message, '')) LIKE '%already exists%'
            """
        )
        removed = cur.rowcount or 0
        conn.commit()
        return removed
    except Exception as db_err:
        import sys
        print(f"[PURGE DUPLICATE FAILURES ERROR] {db_err}", file=sys.stderr)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_handoff(nxm: NXMRequest, *, metadata: Dict[str, Any]) -> Dict[str, Any]:
    with _HANDOFF_LOCK:
        _purge_expired_locked()
        identifier = str(uuid.uuid4())
        created_at = time.time()
        record = {
            "id": identifier,
            "created_at": created_at,
            "expires_at": created_at + NXM_HANDOFF_TTL_SECONDS,
            "request": {
                "raw": nxm.raw,
                "game": nxm.game_domain,
                "mod_id": nxm.mod_id,
                "file_id": nxm.file_id,
                "query": dict(nxm.query),
            },
            "metadata": metadata,
        }
        _HANDOFFS[identifier] = record

    # Clear any previous persistent failure record for this file_id when a new handoff is registered
    if nxm.file_id is not None:
        fid_str = str(nxm.file_id).strip()
        from core.api.dependencies import get_db
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM handoff_failures WHERE file_id = ?", (fid_str,))
            conn.commit()
        except Exception as db_err:
            import sys
            print(f"[HANDOFF REGISTER DB CLEAR ERROR] {db_err}", file=sys.stderr)
        finally:
            try:
                conn.close()
            except Exception:
                pass

    # NOTE: this return must stay at function scope. It previously sat inside
    # the `if nxm.file_id is not None` block, so a handoff without a file_id
    # returned None -- and submit_nxm_handoff dereferences record["id"]
    # unguarded, turning that into a TypeError 500.
    return record


def snapshot_metadata(nxm: NXMRequest) -> Dict[str, Any]:
    return {
        "mod_id": nxm.mod_id,
        "file_id": nxm.file_id,
        "key": nxm.key,
        "expires": nxm.expires,
    }


def get_handoff_or_404(handoff_id: str) -> Dict[str, Any]:
    with _HANDOFF_LOCK:
        _purge_expired_locked()
        record = _HANDOFFS.get(handoff_id)
        if record is None:
            raise HTTPException(status_code=404, detail="handoff not found or expired")
        return record


def list_handoffs() -> List[Dict[str, Any]]:
    with _HANDOFF_LOCK:
        _purge_expired_locked()
        in_memory = list(_HANDOFFS.values())

    in_memory_file_ids = set()
    for rec in in_memory:
        req = rec.get("request") or {}
        fid = req.get("file_id")
        if fid is not None:
            in_memory_file_ids.add(str(fid).strip())

    # Load persistent failures from SQLite
    db_failures = []
    from core.api.dependencies import get_db
    conn = get_db()
    try:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT file_id, mod_id, error_message, retry_count, last_attempt_at, handoff_id FROM handoff_failures"
        ).fetchall()
        for file_id_str, mod_id, error_message, retry_count, last_attempt_at, handoff_id in rows:
            db_failures.append({
                "file_id": file_id_str,
                "mod_id": mod_id,
                "error_message": error_message,
                "retry_count": retry_count,
                "last_attempt_at": last_attempt_at,
                "handoff_id": handoff_id
            })
    except Exception as db_err:
        import sys
        print(f"[LIST HANDOFFS DB ERROR] {db_err}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Merge persistent failures as synthetic failed handoffs if not already in-memory.
    # NOTE: this function is a pure read. Cleanup of benign duplicate rows lives in
    # _purge_benign_duplicate_failures(), called from _purge_expired_failures() on
    # the write paths -- a polled GET must not issue DELETEs.
    for fail in db_failures:
        fid_str = fail["file_id"].strip()
        if fid_str in in_memory_file_ids:
            continue

        # Skip "duplicate download detected" failures — these mean the file IS already
        # downloaded, not that there was a real error, so they are not surfaced as
        # failed handoffs.
        error_msg = (fail.get("error_message") or "").lower()
        if "duplicate download detected" in error_msg or "already exists" in error_msg:
            continue

        synthetic_id = fail["handoff_id"] or f"failed-{fid_str}"
        import dateutil.parser
        try:
            parsed_time = dateutil.parser.parse(fail["last_attempt_at"])
            created_at = parsed_time.timestamp()
        except Exception:
            created_at = time.time()

        synthetic_record = {
            "id": synthetic_id,
            "created_at": created_at,
            "expires_at": created_at + NXM_HANDOFF_TTL_SECONDS,
            "request": {
                "file_id": int(fid_str) if fid_str.isdigit() else fid_str,
                "mod_id": fail["mod_id"],
            },
            "progress": {
                "stage": "failed",
                "error": fail["error_message"],
                "message": fail["error_message"],
                "retry_count": fail["retry_count"],
                "permanently_failed": fail["retry_count"] >= MAX_HANDOFF_RETRIES,
            }
        }
        in_memory.append(synthetic_record)

    return in_memory


def delete_handoff(handoff_id: str) -> Dict[str, Any]:
    with _HANDOFF_LOCK:
        _purge_expired_locked()
        record = _HANDOFFS.get(handoff_id)
        if record is None:
            raise HTTPException(status_code=404, detail="handoff not found or expired")
        _HANDOFFS.pop(handoff_id, None)
    
    # Clear failure record when handoff is deleted
    with _FAILURE_LOCK:
        _HANDOFF_FAILURES.pop(handoff_id, None)

    # Also clear from SQLite database
    from core.api.dependencies import get_db
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM handoff_failures WHERE handoff_id = ?", (handoff_id,))
        conn.commit()
    except Exception as db_err:
        import sys
        print(f"[HANDOFF DELETE DB ERROR] {db_err}", file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    
    return record


def update_handoff_progress(
    handoff_id: str,
    *,
    stage: str,
    bytes_downloaded: Optional[int] = None,
    bytes_total: Optional[int] = None,
    message: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    now = time.time()
    percent_value: Optional[float] = None
    if bytes_downloaded is not None and bytes_total:
        try:
            percent_value = max(0.0, min(100.0, (bytes_downloaded / bytes_total) * 100.0))
        except Exception:
            percent_value = None
    with _HANDOFF_LOCK:
        record = _HANDOFFS.get(handoff_id)
        if record is None:
            return
        progress = record.setdefault("progress", {})
        progress["stage"] = stage
        progress["updated_at"] = now
        if bytes_downloaded is not None:
            progress["bytes_downloaded"] = int(bytes_downloaded)
        if bytes_total is not None:
            progress["bytes_total"] = int(bytes_total)
        if percent_value is not None:
            progress["percent"] = percent_value
        elif "percent" in progress:
            progress.pop("percent", None)
        if message is not None:
            progress["message"] = message
        if error is not None:
            progress["error"] = error


def serialize_handoff(record: Dict[str, Any], *, include_metadata: bool = False) -> Dict[str, Any]:
    payload = {
        "id": record.get("id"),
        "created_at": record.get("created_at"),
        "expires_at": record.get("expires_at"),
        "request": record.get("request"),
    }
    progress = record.get("progress")
    if isinstance(progress, dict) and progress:
        payload["progress"] = dict(progress)
    if include_metadata:
        metadata = record.get("metadata") or {}
        filtered = metadata.get("collect_all_filtered") if isinstance(metadata, dict) else None
        mod_info = None
        if isinstance(filtered, dict):
            maybe = filtered.get("mod_info")
            if isinstance(maybe, dict):
                mod_info = maybe
        # Fallback: use the eagerly-resolved mod_name set at registration
        if mod_info is None and isinstance(metadata, dict):
            eager_name = metadata.get("mod_name")
            if isinstance(eager_name, str) and eager_name.strip():
                mod_info = {"name": eager_name.strip()}
        if mod_info is not None:
            payload["metadata"] = {
                "mod_info": mod_info,
                "fetched_at": metadata.get("collect_all_timestamp") if isinstance(metadata, dict) else None,
            }
    return payload


def _parse_last_attempt(value):
    """Parse handoff_failures.last_attempt_at into a UTC epoch float.

    Written by SQLite's datetime('now'): "YYYY-MM-DD HH:MM:SS" in UTC with no
    timezone marker, so it must be interpreted as UTC explicitly rather than as
    local time.
    """
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        import datetime as _dt
        try:
            parsed = _dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            import dateutil.parser
            parsed = dateutil.parser.parse(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed.timestamp()
    except Exception:
        return None


def _file_id_for_handoff(handoff_id):
    with _HANDOFF_LOCK:
        record = _HANDOFFS.get(handoff_id)
    if not record:
        return None
    fid = (record.get("request") or {}).get("file_id")
    if fid is None:
        return None
    text = str(fid).strip()
    return text or None


def _lookup_failure_row(handoff_id):
    """Read the authoritative failure row for a handoff, if any.

    Looks up by file_id (the PRIMARY KEY) and then by handoff_id (its own index)
    as two separate seeks. The previous single query used
    "WHERE file_id = ? OR handoff_id = ?"; an OR across two different indexes
    stops SQLite using either and degrades to a table scan.
    """
    file_id_str = _file_id_for_handoff(handoff_id)
    from core.api.dependencies import get_db

    conn = get_db()
    try:
        cur = conn.cursor()
        cols = ("file_id", "mod_id", "error_message", "retry_count",
                "last_attempt_at", "handoff_id")
        select = ("SELECT file_id, mod_id, error_message, retry_count, "
                  "last_attempt_at, handoff_id FROM handoff_failures WHERE ")
        row = None
        if file_id_str:
            row = cur.execute(select + "file_id = ?", (file_id_str,)).fetchone()
        if row is None:
            row = cur.execute(select + "handoff_id = ?", (handoff_id,)).fetchone()
        if row is None:
            row = cur.execute(
                select + "file_id = ?", ("handoff:" + str(handoff_id),)
            ).fetchone()
        if row is None:
            return None
        return dict(zip(cols, row))
    except Exception as db_err:
        import sys
        print("[HANDOFF FAILURE LOOKUP ERROR] " + str(db_err), file=sys.stderr)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def register_handoff_failure(handoff_id: str, error: str) -> None:
    """Record a handoff failure. SQLite is the source of truth.

    The in-memory dict is now a read cache only. Previously the two stores were
    keyed differently -- memory by handoff_id, SQLite by file_id -- the retry
    count was reconciled ad hoc between them, and the backoff window existed ONLY
    in memory. A backend restart therefore kept the retry count but reset the
    backoff, so a failing download retried immediately after every restart.
    """
    _purge_expired_failures()

    file_id_str = _file_id_for_handoff(handoff_id)
    mod_id = None
    with _HANDOFF_LOCK:
        record = _HANDOFFS.get(handoff_id)
        if record:
            mod_id = (record.get("request") or {}).get("mod_id")

    # file_id is the PRIMARY KEY, so a handoff without one gets a synthetic key
    # instead of being dropped -- the old code persisted nothing at all then.
    key = file_id_str or ("handoff:" + str(handoff_id))

    new_count = 1
    last_attempt_iso = None

    from core.api.dependencies import get_db

    conn = get_db()
    try:
        cur = conn.cursor()
        existing = cur.execute(
            "SELECT retry_count FROM handoff_failures WHERE file_id = ?", (key,)
        ).fetchone()
        if existing is None:
            existing = cur.execute(
                "SELECT retry_count FROM handoff_failures WHERE handoff_id = ?",
                (handoff_id,),
            ).fetchone()
        if existing is not None:
            new_count = int(existing[0] or 0) + 1

        cur.execute(
            """
            INSERT INTO handoff_failures
                (file_id, mod_id, error_message, retry_count, last_attempt_at, handoff_id)
            VALUES (?, ?, ?, ?, datetime('now'), ?)
            ON CONFLICT(file_id) DO UPDATE SET
                mod_id = excluded.mod_id,
                error_message = excluded.error_message,
                retry_count = excluded.retry_count,
                last_attempt_at = excluded.last_attempt_at,
                handoff_id = excluded.handoff_id
            """,
            (key, mod_id, error, new_count, handoff_id),
        )
        conn.commit()
        row = cur.execute(
            "SELECT last_attempt_at FROM handoff_failures WHERE file_id = ?", (key,)
        ).fetchone()
        if row:
            last_attempt_iso = row[0]
    except Exception as db_err:
        import sys
        print("[HANDOFF FAILURE DB ERROR] " + str(db_err), file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Refresh the read cache so it matches what was persisted.
    with _FAILURE_LOCK:
        failure = _HANDOFF_FAILURES.get(handoff_id, {"errors": []})
        failure["count"] = new_count
        failure["last_attempt"] = _parse_last_attempt(last_attempt_iso) or time.time()
        failure["last_error"] = error
        errors = failure.get("errors", [])
        errors.append({"error": error, "timestamp": time.time()})
        failure["errors"] = errors[-5:]
        _HANDOFF_FAILURES[handoff_id] = failure


def get_handoff_failure_count(handoff_id: str) -> int:
    """Number of recorded failures, read from the authoritative store."""
    row = _lookup_failure_row(handoff_id)
    if row is not None:
        return int(row["retry_count"] or 0)
    with _FAILURE_LOCK:
        failure = _HANDOFF_FAILURES.get(handoff_id)
        return int(failure.get("count", 0)) if failure else 0


def clear_handoff_failure(handoff_id: str) -> None:
    """Clear failure state for a handoff (e.g. after successful processing)."""
    with _FAILURE_LOCK:
        _HANDOFF_FAILURES.pop(handoff_id, None)

    file_id_str = _file_id_for_handoff(handoff_id)
    from core.api.dependencies import get_db

    conn = get_db()
    try:
        cur = conn.cursor()
        # Clear by every key the row could have been written under: the caller may
        # only know the handoff_id while the row is keyed by file_id, or vice versa.
        cur.execute("DELETE FROM handoff_failures WHERE handoff_id = ?", (handoff_id,))
        cur.execute(
            "DELETE FROM handoff_failures WHERE file_id = ?",
            ("handoff:" + str(handoff_id),),
        )
        if file_id_str:
            cur.execute(
                "DELETE FROM handoff_failures WHERE file_id = ?", (file_id_str,)
            )
        conn.commit()
    except Exception as db_err:
        import sys
        print("[HANDOFF CLEAR DB ERROR] " + str(db_err), file=sys.stderr)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def should_skip_handoff(handoff_id: str) -> Tuple[bool, Optional[str]]:
    """Whether a handoff should be skipped due to repeated failures.

    Both the retry ceiling AND the backoff window are evaluated against the
    persisted row, so they survive a restart together. Previously the ceiling came
    from SQLite but the backoff only from the in-memory dict, so restarting reset
    the backoff while keeping the count.

    Returns (should_skip, reason).
    """
    _purge_expired_failures()

    row = _lookup_failure_row(handoff_id)
    if row is not None:
        retry_count = int(row["retry_count"] or 0)
        if retry_count >= MAX_HANDOFF_RETRIES:
            return True, (
                "Handoff has failed %d times (max %d). Last error: %s"
                % (retry_count, MAX_HANDOFF_RETRIES, row["error_message"])
            )

        last_attempt = _parse_last_attempt(row["last_attempt_at"])
        if last_attempt is not None:
            elapsed = time.time() - last_attempt
            if 0 <= elapsed < HANDOFF_FAILURE_BACKOFF_SECONDS:
                remaining = int(HANDOFF_FAILURE_BACKOFF_SECONDS - elapsed)
                return True, (
                    "Handoff is in backoff period (%ds remaining after %d failures)"
                    % (remaining, retry_count)
                )
        return False, None

    # No persisted row: fall back to the in-memory cache.
    with _FAILURE_LOCK:
        failure = _HANDOFF_FAILURES.get(handoff_id)
        if not failure:
            return False, None

        failure_count = failure.get("count", 0)
        if failure_count >= MAX_HANDOFF_RETRIES:
            last_error = failure.get("last_error", "Unknown error")
            return True, (
                "Handoff has failed %d times (max %d). Last error: %s"
                % (failure_count, MAX_HANDOFF_RETRIES, last_error)
            )

        last_attempt = failure.get("last_attempt", 0)
        time_since_last = time.time() - last_attempt
        if time_since_last < HANDOFF_FAILURE_BACKOFF_SECONDS:
            remaining = int(HANDOFF_FAILURE_BACKOFF_SECONDS - time_since_last)
            return True, (
                "Handoff is in backoff period (%ds remaining after %d failures)"
                % (remaining, failure_count)
            )

        return False, None


def mark_handoff_consumed(handoff_id: str) -> None:
    """Mark a handoff as consumed to prevent reprocessing on frontend restart."""
    with _HANDOFF_LOCK:
        record = _HANDOFFS.get(handoff_id)
        if record is not None:
            record["consumed"] = True
            record["consumed_at"] = time.time()


__all__ = [
    "NXM_HANDOFF_TTL_SECONDS",
    "MAX_HANDOFF_RETRIES",
    "HANDOFF_FAILURE_BACKOFF_SECONDS",
    "delete_handoff",
    "get_handoff_or_404",
    "list_handoffs",
    "register_handoff",
    "serialize_handoff",
    "snapshot_metadata",
    "update_handoff_progress",
    "register_handoff_failure",
    "get_handoff_failure_count",
    "clear_handoff_failure",
    "should_skip_handoff",
    "mark_handoff_consumed",
]
