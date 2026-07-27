from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional


def _default_data_dir() -> Path:
	"""
	Determine the default data directory with platform-specific logic.
	
	Uses platform-specific app data directory (NOT temp directory):
	   - Windows: %APPDATA%/com.rivalnxt.modmanager
	   - macOS: ~/Library/Application Support/com.rivalnxt.modmanager
	   - Linux: ~/.local/share/com.rivalnxt.modmanager

	Falls back to ~/.com.rivalnxt.modmanager if platform detection fails.
	
	Returns:
		Path: The resolved data directory
	"""
	import platform
	
	system = platform.system()
	
	# Platform-specific paths (matching Tauri's data directory structure)
	if system == "Windows":
		# Windows: Use %APPDATA% (Roaming)
		appdata = os.environ.get("APPDATA")
		if appdata:
			appdata_path = Path(appdata) / "com.rivalnxt.modmanager"
		else:
			appdata_path = Path.home() / "AppData" / "Roaming" / "com.rivalnxt.modmanager"
	elif system == "Darwin":
		# macOS: Use ~/Library/Application Support
		appdata_path = Path.home() / "Library" / "Application Support" / "com.rivalnxt.modmanager"
	else:
		# Linux/Unix: Use XDG_DATA_HOME or ~/.local/share
		xdg_data = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
		appdata_path = Path(xdg_data) / "com.rivalnxt.modmanager"
	
	# Create the directory if it doesn't exist
	try:
		appdata_path.mkdir(parents=True, exist_ok=True)
	except Exception:
		# Fallback to home directory
		appdata_path = Path.home() / ".com.rivalnxt.modmanager"
		try:
			appdata_path.mkdir(parents=True, exist_ok=True)
		except Exception:
			pass
	
	return appdata_path


@dataclass(frozen=True)
class AppSettings:

	backend_host: str = "127.0.0.1"
	backend_port: int = 8000
	data_dir: Path = _default_data_dir()
	marvel_rivals_root: Optional[Path] = None
	marvel_rivals_local_downloads_root: Optional[Path] = None
	nexus_api_key: str = ""  
	aes_key_hex: str = "0x0C263D8C22DCB085894899C3A3796383E9BF9DE0CBFB08C9BF2DEF2E84F29D74"
	allow_direct_api_downloads: bool = False
	seven_zip_bin: Optional[Path] = None



# Path to settings file in data_dir
def _settings_file_path(data_dir: Path | None = None) -> Path:
	if data_dir is None:
		data_dir = _default_data_dir()
	return Path(data_dir).expanduser().resolve() / "settings.json"


def _candidate_data_dirs() -> list[Path]:
	"""Resolve candidate data directories, honoring env and current settings.

	Order of precedence:
	1. MOD_MANAGER_DATA_DIR env override (if set)
	2. Currently-loaded SETTINGS.data_dir (if available)
	3. Default platform-specific data dir
	"""

	candidates: list[Path] = []

	env_data_dir = os.environ.get("MOD_MANAGER_DATA_DIR")
	if env_data_dir:
		candidates.append(Path(env_data_dir).expanduser())

	current_settings = globals().get("SETTINGS")
	if isinstance(current_settings, AppSettings) and current_settings.data_dir:
		candidates.append(Path(current_settings.data_dir))

	candidates.append(_default_data_dir())

	normalized: list[Path] = []
	seen: set[str] = set()
	for candidate in candidates:
		resolved = candidate.expanduser().resolve()
		key = str(resolved).lower()
		if key in seen:
			continue
		seen.add(key)
		normalized.append(resolved)
	return normalized

def _normalize_path(value: Optional[str | Path]) -> Optional[Path]:
	if value is None or value == "":
		return None
	if isinstance(value, Path):
		return value.expanduser().resolve()
	return Path(value).expanduser().resolve()

def save_settings(settings: AppSettings) -> None:
	import json
	path = _settings_file_path(settings.data_dir)
	try:
		# Ensure the directory exists
		path.parent.mkdir(parents=True, exist_ok=True)
		data = {
			"backend_host": settings.backend_host,
			"backend_port": settings.backend_port,
			"data_dir": str(settings.data_dir),
			"marvel_rivals_root": str(settings.marvel_rivals_root) if settings.marvel_rivals_root else None,
			"marvel_rivals_local_downloads_root": str(settings.marvel_rivals_local_downloads_root) if settings.marvel_rivals_local_downloads_root else None,
			"nexus_api_key": settings.nexus_api_key,
			"aes_key_hex": settings.aes_key_hex,
			"allow_direct_api_downloads": settings.allow_direct_api_downloads,
			"seven_zip_bin": str(settings.seven_zip_bin) if settings.seven_zip_bin else None,
		}
		with open(path, "w", encoding="utf-8") as f:
			json.dump(data, f, indent=2)
		print(f"[Settings] Saved settings to {path}")
		print(f"[Settings] marvel_rivals_root: {data.get('marvel_rivals_root')}")
		print(f"[Settings] marvel_rivals_local_downloads_root: {data.get('marvel_rivals_local_downloads_root')}")
	except Exception as e:
		print(f"[Settings] ERROR saving settings: {e}")
		import traceback
		traceback.print_exc()

def load_settings() -> AppSettings:
	import json

	candidates = _candidate_data_dirs()
	last_error: Exception | None = None

	try:
		print("[Settings] Candidate data directories:")
		for idx, candidate in enumerate(candidates, start=1):
			print(f"[Settings]   {idx}. {candidate}")
	except Exception:
		pass

	for data_dir in candidates:
		path = _settings_file_path(data_dir)
		if not path.exists():
			continue
		try:
			with open(path, "r", encoding="utf-8") as f:
				data = json.load(f)
			print(f"[Settings] Loaded settings from {path}")
			print(f"[Settings] marvel_rivals_root: {data.get('marvel_rivals_root')}")
			print(f"[Settings] marvel_rivals_local_downloads_root: {data.get('marvel_rivals_local_downloads_root')}")
			if data.get("nexus_api_key"):
				print(f"[Settings] nexus_api_key length: {len(str(data.get('nexus_api_key')))}")
			else:
				print("[Settings] nexus_api_key missing or empty in file")
			defaults = AppSettings()
			resolved_data_dir = _normalize_path(data.get("data_dir")) or data_dir
			return AppSettings(
				backend_host=data.get("backend_host", defaults.backend_host),
				backend_port=data.get("backend_port", defaults.backend_port),
				data_dir=resolved_data_dir,
				marvel_rivals_root=_normalize_path(data.get("marvel_rivals_root")),
				marvel_rivals_local_downloads_root=_normalize_path(data.get("marvel_rivals_local_downloads_root")),
				nexus_api_key=data.get("nexus_api_key", defaults.nexus_api_key),
				aes_key_hex=data.get("aes_key_hex", defaults.aes_key_hex),
				allow_direct_api_downloads=bool(data.get("allow_direct_api_downloads", defaults.allow_direct_api_downloads)),
				seven_zip_bin=_normalize_path(data.get("seven_zip_bin")),
			)
		except Exception as e:
			last_error = e
			print(f"[Settings] ERROR loading settings from {path}: {e}")
			import traceback
			traceback.print_exc()

	# If we reach here, no existing settings file was found; fall back to first candidate
	if last_error is None:
		# Provide feedback on where we expect to write new settings
		fallback_dir = candidates[0] if candidates else _default_data_dir()
		path = _settings_file_path(fallback_dir)
		print(f"[Settings] No settings file found. Using defaults with data_dir={fallback_dir}")
		return AppSettings(data_dir=fallback_dir)

	# If we failed to load due to persistent error, return defaults but surface via print
	print("[Settings] Falling back to default settings due to previous errors")
	return AppSettings()

# Load settings from disk on startup
SETTINGS = load_settings()


def reload_settings() -> AppSettings:
	"""Reload settings from disk and update the global SETTINGS object.
	
	This is useful when settings.json has been updated by another process
	or by the API server, and we need to pick up the new values.
	"""
	global SETTINGS
	SETTINGS = load_settings()
	return SETTINGS


def configure(**overrides: object) -> AppSettings:
	print(f"[Settings] configure() called with overrides: {list(overrides.keys())}")
	normalized: dict[str, object] = {}
	path_keys = {
		"data_dir",
		"marvel_rivals_root",
		"marvel_rivals_local_downloads_root",
		"seven_zip_bin",
	}
	for key, value in overrides.items():
		if key in path_keys:
			normalized[key] = _normalize_path(value) if value is not None else None
		else:
			normalized[key] = value
	global SETTINGS
	SETTINGS = replace(SETTINGS, **normalized)
	target = SETTINGS.data_dir
	try:
		Path(target).mkdir(parents=True, exist_ok=True)
	except Exception:
		pass
	print("[Settings] Updated SETTINGS object:")
	print(f"[Settings]   marvel_rivals_root: {SETTINGS.marvel_rivals_root}")
	print(f"[Settings]   marvel_rivals_local_downloads_root: {SETTINGS.marvel_rivals_local_downloads_root}")
	save_settings(SETTINGS)
	return SETTINGS


# ---------------------------------------------------------------------------
# Canonical game-directory layout helpers
# ---------------------------------------------------------------------------
# Marvel Rivals stores its PAK containers at:
#   <game root>/MarvelGame/Marvel/Content/Paks
# and user mods in the "~mods" subfolder of that directory.
#
# This layout was previously open-coded at 9 separate call sites, one of which
# (core/db/db.py `_get_mods_folder_for_deletion`) used the wrong 2-segment path
# "MarvelGame/~mods". Because the pak-removal helpers early-return when the
# directory does not exist, delete_outdated_versions silently removed nothing
# from disk while still deleting the DB rows -- leaving orphaned .pak files
# active in-game with no database record. Route every caller through here.

RELATIVE_PAKS_PATH = Path("MarvelGame") / "Marvel" / "Content" / "Paks"
RELATIVE_MODS_PATH = RELATIVE_PAKS_PATH / "~mods"


def get_paks_dir(marvel_rivals_root: Optional[Path | str] = None) -> Optional[Path]:
	"""Return <game root>/MarvelGame/Marvel/Content/Paks.

	Falls back to the live SETTINGS value when no root is supplied. Returns
	None when no game root is configured, so callers must handle the unset case
	rather than silently operating on a bogus relative path.
	"""
	root = marvel_rivals_root if marvel_rivals_root is not None else SETTINGS.marvel_rivals_root
	if not root:
		return None
	return Path(root).expanduser() / RELATIVE_PAKS_PATH


def get_mods_dir(marvel_rivals_root: Optional[Path | str] = None) -> Optional[Path]:
	"""Return <game root>/MarvelGame/Marvel/Content/Paks/~mods.

	Falls back to the live SETTINGS value when no root is supplied. Returns
	None when no game root is configured.
	"""
	root = marvel_rivals_root if marvel_rivals_root is not None else SETTINGS.marvel_rivals_root
	if not root:
		return None
	return Path(root).expanduser() / RELATIVE_MODS_PATH
