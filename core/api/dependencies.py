"""Shared dependencies and environment checks for the API layer."""
from __future__ import annotations

import logging
import socket
from typing import Iterable, Tuple

from core.db import get_connection, init_schema

logger = logging.getLogger("modmanager.api.dependencies")

_REQUIRED_DNS_HOSTS: Tuple[str, ...] = ("api.nexusmods.com",)
_SCHEMA_READY = False


def verify_required_dns_hosts(hosts: Iterable[str] = _REQUIRED_DNS_HOSTS) -> None:
    """Ensure critical Nexus hosts resolve before accepting requests."""
    failures = []
    for host in hosts:
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror as exc:  # pragma: no cover - network failure
            failures.append(f"{host} ({exc})")
    if failures:
        logger.warning(
            "DNS check failed for Nexus hosts: %s. Update your DNS resolver before using Mod Manager Download.",
            ", ".join(failures),
        )


def _ensure_schema_initialised() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    print("Initializing database schema...")
    conn = get_connection()
    try:
        init_schema(conn)
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive close
            pass
    _SCHEMA_READY = True


def get_db():
    """Return a SQLite connection with schema guaranteed to exist."""
    _ensure_schema_initialised()
    conn = get_connection()
    return conn


def reset_schema_cache() -> None:
    """Force schema re-initialization on next get_db() call.
    
    Use this after operations that rebuild the database structure,
    such as migrations or full bootstrap rebuilds.
    """
    global _SCHEMA_READY
    _SCHEMA_READY = False


__all__ = ["get_db", "verify_required_dns_hosts", "reset_schema_cache"]
