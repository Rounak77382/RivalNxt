import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from core.config import settings

BASE_URL = "https://api.nexusmods.com/v1"
DEFAULT_GAME = "marvelrivals"
APP_NAME = "Project_ModManager_Rivals"
APP_VERSION = "0.7.0"

def _coerce_key(raw: str | None) -> str:
    return raw.strip() if raw else ""


def get_api_key(*_, **__) -> Optional[str]:
    active_settings = settings.SETTINGS
    value = _coerce_key(getattr(active_settings, "nexus_api_key", ""))
    if value:
        return value

    # If the in-memory settings are stale, reload once from disk.
    refreshed = settings.reload_settings()
    value = _coerce_key(getattr(refreshed, "nexus_api_key", ""))
    return value or None

def _get(api_key: str, path: str) -> Tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    headers = {
        "apikey": api_key,
        "User-Agent": f"{APP_NAME}/{APP_VERSION}",
        "Application-Name": APP_NAME,
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.getcode()
            data = resp.read()
            try:
                return status, json.loads(data.decode("utf-8"))
            except json.JSONDecodeError:
                return status, data.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8")
            parsed = json.loads(body)
        except Exception:
            parsed = body if body else str(e)
        return e.code, {"error": True, "message": str(e), "body": parsed}
    except urllib.error.URLError as e:
        return 0, {"error": True, "message": str(e)}

def get_mod_info(api_key: str, game: str, mod_id: int) -> Tuple[int, Any]:
    return _get(api_key, f"/games/{game}/mods/{mod_id}.json")

def get_mod_files(api_key: str, game: str, mod_id: int) -> Tuple[int, Any]:
    return _get(api_key, f"/games/{game}/mods/{mod_id}/files.json")

def get_mod_changelogs(api_key: str, game: str, mod_id: int) -> Tuple[int, Any]:
    return _get(api_key, f"/games/{game}/mods/{mod_id}/changelogs.json")


def get_mod_file_download_link(api_key: str, game: str, mod_id: int, file_id: int) -> Tuple[int, Any]:
    """Retrieve the temporary download link metadata for a specific mod file."""
    return _get(api_key, f"/games/{game}/mods/{mod_id}/files/{file_id}/download_link.json")


def collect_all_for_mod(api_key: str, game: str, mod_id: int) -> Dict[str, Any]:
    out: Dict[str, Any] = {"game": game, "mod_id": mod_id}
    s, d = get_mod_info(api_key, game, mod_id)
    out["mod_info_status"], out["mod_info"] = s, d
    s, d = get_mod_files(api_key, game, mod_id)
    out["files_status"], out["files"] = s, d
    s, d = get_mod_changelogs(api_key, game, mod_id)
    out["changelogs_status"], out["changelogs"] = s, d
    return out

__all__ = [
    'DEFAULT_GAME','get_api_key','collect_all_for_mod','get_mod_files','get_mod_info','get_mod_changelogs','get_mod_file_download_link'
]
