from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterable, List

from core.config.settings import SETTINGS

__all__ = ["known_download_roots", "normalize_download_path"]

_ROOT = Path(__file__).resolve().parents[2]


def _expand(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except Exception:
        return path.expanduser()


def _iter_config_roots() -> Iterable[Path]:
    candidates = [
        SETTINGS.marvel_rivals_local_downloads_root,
        SETTINGS.marvel_rivals_root,
    ]
    for candidate in candidates:
        if candidate:
            yield candidate


@lru_cache(maxsize=1)
def known_download_roots() -> List[Path]:
    roots: List[Path] = []
    for candidate in _iter_config_roots():
        expanded = _expand(candidate)
        if expanded not in roots:
            roots.append(expanded)
    default_guess = SETTINGS.marvel_rivals_local_downloads_root
    if default_guess:
        candidate = _expand(default_guess)
        if candidate not in roots:
            roots.append(candidate)
    fallback = _expand(_ROOT / "downloads")
    if fallback not in roots:
        roots.append(fallback)
    return roots


def normalize_download_path(path: str | Path) -> str:
    if path is None:
        return ""
    if isinstance(path, Path):
        raw = path
        raw_str = str(path)
    else:
        raw_str = str(path).strip()
        if not raw_str:
            return ""
        raw = Path(raw_str)
    if not raw_str:
        return ""
    if not raw.is_absolute():
        return raw.as_posix()
    try:
        resolved = raw.expanduser().resolve()
    except Exception:
        resolved = raw.expanduser()
    for root in known_download_roots():
        try:
            rel = resolved.relative_to(root)
            return rel.as_posix()
        except ValueError:
            root_str = str(root).rstrip("/\\")
            resolved_str = str(resolved)
            root_norm = os.path.normcase(root_str)
            resolved_norm = os.path.normcase(resolved_str)
            if resolved_norm.startswith(root_norm + os.sep) or resolved_norm.startswith(root_norm + "/"):
                remainder = resolved_str[len(root_str) + 1 :]
                return remainder.replace("\\", "/")
            continue
    return resolved.name


def resolve_absolute_download_path(path_str: str) -> Path:
    """Resolve a potentially relative download path back to an absolute filesystem Path."""
    if not path_str:
        return Path()
    p = Path(path_str)
    if p.is_absolute():
        return p
    
    # Try against known roots
    for root in known_download_roots():
        candidate = root / p
        if candidate.exists():
            return candidate
            
    # Fallback: if not found, return against the primary root
    roots = known_download_roots()
    if roots:
        return roots[0] / p
    return p

