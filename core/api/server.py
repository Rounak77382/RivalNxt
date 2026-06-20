from __future__ import annotations

# Minimal FastAPI server wiring the SQLite backend to a TS/Tauri frontend.
# Endpoints:
# - GET /health
# - GET /api/conflicts?limit=20
# - POST /api/mods/add { localPath, name?, modId? }
# - POST /api/refresh/conflicts

import contextlib
import io
import json
import logging
import os
import re
import shutil
import sys as _sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.parse
import urllib.request
import uuid
import ssl

# Globally disable SSL certificate verification to bypass expired cert issues (especially for Nexus API)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Set, Tuple, Union

from fastapi import Body, FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Ensure project root on path for local runs
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in _sys.path:
	_sys.path.insert(0, str(_ROOT))

from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder
from core.api.dependencies import get_db, verify_required_dns_hosts
from core.api.services.handoffs import (
	delete_handoff,
	get_handoff_or_404,
	list_handoffs,
	register_handoff_failure,
	clear_handoff_failure,
	should_skip_handoff,
	register_handoff,
	serialize_handoff,
	snapshot_metadata,
	update_handoff_progress,
	mark_handoff_consumed,
)
from core.db import (
	bulk_upsert_pak_assets,
	delete_local_downloads,
	get_changelogs,
	get_latest_file,
	get_file_by_id,
	get_latest_file_by_version,
	get_mod,
	init_schema,
	list_mod_files,
	make_version_key,
	next_local_download_id,
	mod_with_local_and_latest,
	rebuild_conflicts,
	resolve_created_at,
	fetch_pak_version_status,
	replace_mod_changelogs,
	replace_mod_files,
	upsert_api_cache,
	upsert_mod_info,
	upsert_mod_pak,
	upsert_pak_assets_json,
	update_local_download_active_paks,
	versions_equivalent,
)
from core.ingestion.scan_active_mods import main as scan_active_main
from core.nexus import DEFAULT_GAME, collect_all_for_mod, get_api_key, get_mod_file_download_link
from core.nexus.nxm import NXMParseError, NXMRequest, parse_nxm_uri
from core.utils.archive import build_entry_lookup, extract_archive, extract_member, list_entries, resolve_entry
from core.utils.download_paths import normalize_download_path, resolve_absolute_download_path
from core.utils.pak_files import collapse_pak_bundle

from core.utils.mod_filename import parse_mod_filename
from core.utils.nexus_metadata import derive_changelogs_from_files, extract_description_text
from core.config.settings import SETTINGS, configure, save_settings, load_settings
from core.extraction.service import run_extraction_if_needed

from field_prefs import filter_aggregate_payload, load_prefs

# Global cache for Nexus preferences
_NEXUS_PREFS_CACHE = None

app = FastAPI(title="Mod Manager Backend", version="0.7.2")

# Register character API routes
from core.api.characters import router as characters_router
app.include_router(characters_router)

logger = logging.getLogger("modmanager.api")




# Reload settings from disk on startup (in case of external changes)
SETTINGS = load_settings()
logger.info("=" * 70)
logger.info("FastAPI Backend - Database Configuration")
logger.info("=" * 70)
logger.info(f"Data Directory: {SETTINGS.data_dir}")
logger.info(f"Database Path: {SETTINGS.data_dir / 'mods.db'}")
logger.info(f"Database Exists: {(SETTINGS.data_dir / 'mods.db').exists()}")
logger.info("=" * 70)

# Run character data extraction if needed (first build)
try:
	run_extraction_if_needed()
except Exception as e:
	logger.warning(f"Character data extraction failed: {e}")

UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB chunks for uploads

# Store the last received NXM URL for testing/debugging purposes
_LAST_NXM_URL: Optional[Dict[str, Any]] = None

# Set of handoff IDs that the user has requested to cancel.
# Checked every chunk inside _download_remote_archive so the download
# stops as soon as possible after the cancel request arrives.
_CANCELLED_HANDOFFS: set = set()
_CANCELLED_HANDOFFS_LOCK = threading.Lock()


class DownloadCancelledError(Exception):
	"""Raised when a download is stopped by a user cancel request."""


verify_required_dns_hosts()

_SETTINGS_TASK_LOCK = threading.Lock()
_SETTINGS_TASK_JOBS: Dict[str, Dict[str, Any]] = {}
_SETTINGS_TASK_MAX_JOBS = 25


# =============================================================================
# Parent Process Monitor - Auto-close backend when frontend exits
# =============================================================================
def _monitor_parent_process(pid: int) -> None:
	"""Monitor the parent process and exit if it dies."""
	import psutil
	
	logger.info(f"[PID Monitor] Starting parent process monitor for PID {pid}")
	
	# Check if the PID exists initially
	if not psutil.pid_exists(pid):
		logger.warning(f"[PID Monitor] Parent PID {pid} doesn't exist at startup! Exiting.")
		os._exit(0)
	
	try:
		proc = psutil.Process(pid)
		logger.info(f"[PID Monitor] Parent process: {proc.name()} (PID {pid})")
	except psutil.NoSuchProcess:
		logger.warning(f"[PID Monitor] Parent PID {pid} doesn't exist! Exiting.")
		os._exit(0)
	except Exception as e:
		logger.error(f"[PID Monitor] Error getting parent process info: {e}")
	
	check_counter = 0
	while True:
		try:
			check_counter += 1
			if not psutil.pid_exists(pid):
				logger.warning(f"[PID Monitor] Parent process {pid} is gone after {check_counter} checks. Shutting down backend.")
				os._exit(0)
			
			# Heartbeat log removed to reduce spam
			# if check_counter % 12 == 0:
			# 	logger.debug(f"[PID Monitor] Parent process {pid} still alive (check #{check_counter})")
			
			time.sleep(5)
		except Exception as e:
			logger.error(f"[PID Monitor] Error in parent monitor: {e}")
			time.sleep(5)


# Start parent monitor if PID is provided via environment variable
_RIVALNXT_PID = os.environ.get("RIVALNXT_PID")
if _RIVALNXT_PID:
	try:
		_pid = int(_RIVALNXT_PID)
		logger.info(f"[PID Monitor] RIVALNXT_PID environment variable found: {_pid}")
		_monitor_thread = threading.Thread(
			target=_monitor_parent_process,
			args=(_pid,),
			daemon=True,
			name="ParentProcessMonitor"
		)
		_monitor_thread.start()
		logger.info("[PID Monitor] Monitor thread started successfully")
	except ValueError:
		logger.warning(f"[PID Monitor] Invalid RIVALNXT_PID value: {_RIVALNXT_PID}")
	except Exception as e:
		logger.error(f"[PID Monitor] Failed to start monitor thread: {e}")
else:
	logger.info("[PID Monitor] No RIVALNXT_PID environment variable found - monitor not started")
# =============================================================================


def _safe_rebuild_conflicts(
	conn,
	*,
	active_only: Optional[bool],
	purpose: str,
	raise_on_error: bool = False,
) -> Optional[Dict[str, int]]:
	"""Rebuild conflict tables, logging failures with context and optional re-raise."""
	try:
		return rebuild_conflicts(conn, active_only=active_only)
	except Exception:
		logger.exception(
			"Failed to rebuild conflict tables during %s (active_only=%s)",
			purpose,
			active_only,
		)
		if raise_on_error:
			raise
		return None


def _get_current_settings():
	"""Get the current global SETTINGS object from settings module."""
	from core.config.settings import SETTINGS as CURRENT_SETTINGS
	return CURRENT_SETTINGS


def _get_actually_active_filenames(logger) -> Optional[Set[str]]:
	"""Return a set of lowercase pak filenames actually present in ~mods,
	or None if the game directory or settings are not configured.
	"""
	try:
		current_settings = _get_current_settings()
		if current_settings.marvel_rivals_root:
			mods_dir = current_settings.marvel_rivals_root.expanduser() / "MarvelGame" / "Marvel" / "Content" / "Paks" / "~mods"
			if mods_dir.is_dir():
				filenames = set()
				for file in mods_dir.rglob("*.pak"):
					if file.is_file():
						filenames.add(file.name.lower())
				return filenames
			else:
				# mods_dir doesn't exist but root is set -> 0 files are present
				return set()
	except Exception as e:
		logger.warning(f"[active_paks_check] Failed to scan ~mods: {e}")
	return None


def _seed_env_from_settings() -> None:
	# Import SETTINGS directly from module to get the latest global value
	current = _get_current_settings()
	
	mapping = {
		"MARVEL_RIVALS_ROOT": current.marvel_rivals_root,
		"MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT": current.marvel_rivals_local_downloads_root,
		"NEXUS_API_KEY": current.nexus_api_key,
		"AES_KEY_HEX": current.aes_key_hex,
		"SEVEN_ZIP_BIN": current.seven_zip_bin,
		"MOD_MANAGER_DATA_DIR": current.data_dir,
	}
	for key, value in mapping.items():
		if value is None or value == "":
			os.environ.pop(key, None)
		else:
			os.environ[key] = str(value)


_seed_env_from_settings()

# Allow all origins in dev; Tauri embeds UI so this is safe for local usage.
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

try:  # pragma: no cover - optional dependency
	import multipart  # type: ignore
	_HAS_MULTIPART = True
except Exception:  # pragma: no cover - fallback when optional dep missing
	_HAS_MULTIPART = False

_MEMBER_ID_RE = re.compile(r"(\d+)(?:\D*$)")

_ARCHIVE_UE_EXTS: set[str] = {".pak", ".utoc", ".ucas", ".sig"}
_KNOWN_CATEGORIES: set[str] = {
	"main",
	"miscellaneous",
	"audio",
	"visuals",
	"models",
	"model",  # singular
	"textures",
	"texture",  # singular
	"material",  # added
	"mesh",  # added
	"effects",
	"ui",
	"utilities",
	"tools",
	"cheats",
	"savegames",
	"patches",
	"gameplay",
	"uimods",
	"fixes",
}

_CANON_CHAR_NAMES: Optional[Set[str]] = None


def _create_settings_task_job(task: SettingsTaskName) -> Dict[str, Any]:
	job_id = uuid.uuid4().hex
	now = datetime.utcnow().isoformat() + "Z"
	job: Dict[str, Any] = {
		"id": job_id,
		"task": task,
		"status": "pending",
		"ok": None,
		"exit_code": None,
		"error": None,
		"started_at": None,
		"finished_at": None,
		"duration_ms": None,
		"created_at": now,
		"updated_at": now,
		"output_chunks": [],
	}
	with _SETTINGS_TASK_LOCK:
		_SETTINGS_TASK_JOBS[job_id] = job
		if len(_SETTINGS_TASK_JOBS) > _SETTINGS_TASK_MAX_JOBS:
			overflow = len(_SETTINGS_TASK_JOBS) - _SETTINGS_TASK_MAX_JOBS
			if overflow > 0:
				sorted_jobs = sorted(
					_SETTINGS_TASK_JOBS.items(),
					key=lambda item: item[1].get("created_at") or "",
				)
				for remove_id, _ in sorted_jobs[:overflow]:
					_SETTINGS_TASK_JOBS.pop(remove_id, None)
		snapshot = {
			**{k: v for k, v in job.items() if k != "output_chunks"},
			"output": "".join(job.get("output_chunks", [])),
		}
	return snapshot


def _append_job_output(job_id: str, chunk: str) -> None:
	if not chunk:
		return
	with _SETTINGS_TASK_LOCK:
		job = _SETTINGS_TASK_JOBS.get(job_id)
		if not job:
			return
		chunks = job.setdefault("output_chunks", [])
		chunks.append(chunk)
		job["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _update_job(job_id: str, **updates: Any) -> None:
	with _SETTINGS_TASK_LOCK:
		job = _SETTINGS_TASK_JOBS.get(job_id)
		if not job:
			return
		if "output" in updates:
			output_value = updates.pop("output")
			job["output_chunks"] = [output_value]
		job.update(updates)
		job["updated_at"] = datetime.utcnow().isoformat() + "Z"


def _job_snapshot(job_id: str) -> Dict[str, Any]:
	with _SETTINGS_TASK_LOCK:
		job = _SETTINGS_TASK_JOBS.get(job_id)
		if not job:
			raise KeyError(job_id)
		snapshot = {k: v for k, v in job.items() if k != "output_chunks"}
		snapshot["output"] = "".join(job.get("output_chunks", []))
		return snapshot


def _list_job_snapshots() -> List[Dict[str, Any]]:
	with _SETTINGS_TASK_LOCK:
		jobs = list(_SETTINGS_TASK_JOBS.values())
	return [
		{
			**{k: v for k, v in job.items() if k != "output_chunks"},
			"output": "".join(job.get("output_chunks", [])),
		}
		for job in sorted(jobs, key=lambda item: item.get("created_at") or "", reverse=True)
	]


def _execute_settings_task_async(job_id: str, task: SettingsTaskName) -> None:
	started_at = datetime.utcnow().isoformat() + "Z"
	_update_job(job_id, status="running", started_at=started_at, ok=None, exit_code=None)

	def on_output(chunk: str) -> None:
		_append_job_output(job_id, chunk)

	try:
		result = _run_settings_task(task, on_output=on_output)
	except Exception as exc:  # pragma: no cover - defensive guard
		traceback.print_exc()
		_update_job(
			job_id,
			status="failed",
			ok=False,
			exit_code=1,
			error=str(exc),
			finished_at=datetime.utcnow().isoformat() + "Z",
			duration_ms=None,
		)
		return

	# Special handling after bootstrap rebuild: force schema cache reset
	# to ensure all future connections see the rebuilt data
	if task == "bootstrap_rebuild" and result.get("ok"):
		from core.api.dependencies import reset_schema_cache
		reset_schema_cache()
		print("Schema cache reset after bootstrap - all future connections will see fresh data")

	status = "succeeded" if result.get("ok") else "failed"
	_update_job(
		job_id,
		status=status,
		ok=result.get("ok"),
		exit_code=result.get("exit_code"),
		error=result.get("error"),
		finished_at=result.get("finished_at"),
		duration_ms=result.get("duration_ms"),
		output=result.get("output", ""),
	)

class SettingsUpdatePayload(BaseModel):

	data_dir: Optional[str] = None
	marvel_rivals_root: Optional[str] = None
	marvel_rivals_local_downloads_root: Optional[str] = None
	nexus_api_key: Optional[str] = None
	aes_key_hex: Optional[str] = None
	allow_direct_api_downloads: Optional[bool] = None
	seven_zip_bin: Optional[str] = None

	class Config:
		extra = "forbid"


SettingsTaskName = Literal[
	"ingest_download_assets",
	"scan_active_mods",
	"sync_nexus",
	"rebuild_tags",
	"rebuild_conflicts",
	"bootstrap_rebuild",
	"rebuild_character_data",
	"delete_outdated_versions",
]


class SettingsTaskRequest(BaseModel):

	task: SettingsTaskName


def _serialize_path(value: Optional[Path]) -> Optional[str]:
	if value is None:
		return None
	try:
		return str(Path(value).expanduser().resolve())
	except Exception:
		return str(value)


def _to_path(value: Union[str, Path, None]) -> Optional[Path]:
	if value in (None, ""):
		return None
	if isinstance(value, Path):
		return value
	try:
		return Path(str(value))
	except Exception:
		return None


def _serialize_validation(result: Dict[str, Any]) -> Dict[str, Any]:
	serialized: Dict[str, Any] = {}
	for key, value in result.items():
		if isinstance(value, dict):
			serialized[key] = {
				inner_key: (str(inner_value) if isinstance(inner_value, Path) else inner_value)
				for inner_key, inner_value in value.items()
			}
		else:
			serialized[key] = value
	return serialized


def _validate_directory_path(path: Union[str, Path, None], *, required: bool) -> Dict[str, Any]:
	resolved = _to_path(path)
	optional = not required
	if resolved is None:
		return {
			"ok": not required,
			"path": None,
			"exists": False,
			"reason": "not_configured",
			"optional": optional,
			"message": "Not configured" + (" (optional)" if optional else ""),
		}
	resolved = resolved.expanduser().resolve()
	if not resolved.exists():
		return {
			"ok": False,
			"path": str(resolved),
			"exists": False,
			"reason": "missing",
			"optional": optional,
			"message": f"Directory not found: {resolved}",
		}
	if not resolved.is_dir():
		return {
			"ok": False,
			"path": str(resolved),
			"exists": True,
			"reason": "not_directory",
			"optional": optional,
			"message": f"Path is not a directory: {resolved}",
		}
	writable = os.access(str(resolved), os.W_OK)
	return {
		"ok": writable or optional,
		"path": str(resolved),
		"exists": True,
		"reason": None if writable else "not_writable",
		"optional": optional,
		"message": "Ready" if writable else f"Directory is read-only: {resolved}",
	}


def _validate_executable_path(path: Union[str, Path, None], *, label: str, required: bool) -> Dict[str, Any]:
	resolved = _to_path(path)
	optional = not required
	if resolved is None:
		return {
			"ok": not required,
			"path": None,
			"exists": False,
			"reason": "not_configured",
			"optional": optional,
			"message": f"{label} not configured" + (" (optional)" if optional else ""),
		}
	resolved = resolved.expanduser().resolve()
	if not resolved.exists():
		return {
			"ok": False,
			"path": str(resolved),
			"exists": False,
			"reason": "missing",
			"optional": optional,
			"message": f"File not found: {resolved}",
		}
	if not resolved.is_file():
		return {
			"ok": False,
			"path": str(resolved),
			"exists": True,
			"reason": "not_file",
			"optional": optional,
			"message": f"Path is not a file: {resolved}",
		}
	suffix = resolved.suffix.lower()
	if os.name == "nt" and suffix != ".exe":
		# allow optional exe enforcement; warn if not .exe on Windows
		return {
			"ok": False,
			"path": str(resolved),
			"exists": True,
			"reason": "unexpected_extension",
			"optional": optional,
			"message": f"Expected a .exe file for {label} on Windows: {resolved}",
		}
	executable = os.access(str(resolved), os.X_OK) if os.name != "nt" else True
	return {
		"ok": executable or optional,
		"path": str(resolved),
		"exists": True,
		"reason": None if executable or os.name == "nt" else "not_executable",
		"optional": optional,
		"message": "Ready" if executable or os.name == "nt" else f"File is not executable: {resolved}",
	}


def _validate_api_key(value: Optional[str]) -> Dict[str, Any]:
	trimmed = (value or "").strip()
	optional = True
	if not trimmed:
		return {
			"ok": False,
			"exists": False,
			"reason": "not_configured",
			"optional": optional,
			"message": "Nexus API key not configured",
		}
	return {
		"ok": True,
		"exists": True,
		"reason": None,
		"optional": optional,
		"message": f"Ready (length {len(trimmed)} chars)",
	}


def _collect_settings_validation(settings) -> Dict[str, Any]:
	validation = {
		"data_dir": _validate_directory_path(settings.data_dir, required=True),
		"marvel_rivals_root": _validate_directory_path(settings.marvel_rivals_root, required=True),
		"marvel_rivals_local_downloads_root": _validate_directory_path(settings.marvel_rivals_local_downloads_root, required=True),
		"seven_zip_bin": _validate_executable_path(settings.seven_zip_bin, label="7-Zip", required=False),
		"nexus_api_key": _validate_api_key(settings.nexus_api_key),
	}
	return _serialize_validation(validation)


def _serialize_settings(settings, *, validation: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
	return {
		"backend_host": settings.backend_host,
		"backend_port": settings.backend_port,
		"data_dir": _serialize_path(settings.data_dir),
		"marvel_rivals_root": _serialize_path(settings.marvel_rivals_root),
		"marvel_rivals_local_downloads_root": _serialize_path(settings.marvel_rivals_local_downloads_root),
		"nexus_api_key": settings.nexus_api_key,
		"aes_key_hex": settings.aes_key_hex,
		"allow_direct_api_downloads": bool(settings.allow_direct_api_downloads),
		"seven_zip_bin": _serialize_path(settings.seven_zip_bin),
		"validation": validation or _collect_settings_validation(settings),
	}


def _normalize_optional_str(value: Optional[str]) -> Optional[str]:
	if value is None:
		return None
	trimmed = value.strip()
	return trimmed or None


def _task_delete_outdated_versions() -> int:
	from core.db.db import get_connection, delete_local_downloads, make_version_key
	import os, re, shutil
	from pathlib import Path
	
	conn = get_connection()
	try:
		cur = conn.cursor()
		cur.execute("SELECT id, path, name, mod_id, created_at, version FROM local_downloads")
		all_downloads = cur.fetchall()
		
		# Separate into all tracked paths and only the ones with mod_id (for version checks)
		tracked_paths = set()
		downloads_with_mod = []
		
		for row in all_downloads:
			ld_id, ld_path, ld_name, mod_id, created_at, version = row
			if mod_id is not None:
				downloads_with_mod.append(row)
			if ld_path:
				try:
					resolved = Path(_resolve_download_source_path(str(ld_path))).expanduser().resolve()
					tracked_paths.add(str(resolved).lower())
				except Exception:
					pass
		
		# Group by mod_id
		mod_groups = {}
		for row in downloads_with_mod:
			mod_id = row[3]
			mod_groups.setdefault(mod_id, []).append(row)
		
		to_delete_ids = []
		for mod_id, variants in mod_groups.items():
			if len(variants) < 2:
				continue
				
			# Group by variant identity (matching update detection logic)
			identity_groups = {}
			for row in variants:
				name = row[2] or ""
				identity = name.lower().replace(' ', '').replace('-', '').replace('_', '')
				identity_groups.setdefault(identity, []).append(row)
				
			for identity, items in identity_groups.items():
				if len(items) < 2:
					continue
				# Sort by semantic version DESC, then created_at DESC (newest first)
				def sort_key(x):
					ver = x[5]
					created = x[4] or ""
					v_key = make_version_key(ver)[0] or ""
					return (v_key, created)
					
				items.sort(key=sort_key, reverse=True)
				# First one is kept, others are outdated
				for outdated in items[1:]:
					ld_id = outdated[0]
					ld_name = outdated[2]
					print(f"Queueing outdated version for deletion: {ld_name} (ID: {ld_id})")
					to_delete_ids.append(int(ld_id))
					
		deleted_count = 0
		if to_delete_ids:
			# 1. Use the main DB deletion function which handles deactivation and cascading
			deleted_count, removed_mod_ids, source_paths = delete_local_downloads(conn, to_delete_ids)
			
			# Also remove these from tracked_paths so we don't think they are still tracked
			for raw_path in source_paths:
				if raw_path:
					try:
						resolved = Path(_resolve_download_source_path(str(raw_path))).expanduser().resolve()
						tracked_paths.discard(str(resolved).lower())
					except Exception:
						pass
			
			# 2. Delete physical files securely
			downloads_root = _downloads_root_from_env().resolve()
			removed_files = []
			seen_paths = set()
			for raw_path in source_paths:
				if not raw_path or not isinstance(raw_path, str):
					continue
				key = raw_path.strip()
				if not key or key in seen_paths:
					continue
				seen_paths.add(key)
				try:
					absolute = Path(_resolve_download_source_path(key))
				except Exception:
					continue
				try:
					resolved = absolute.expanduser().resolve()
				except Exception:
					resolved = absolute.expanduser()
				if resolved == downloads_root:
					continue
				try:
					if not resolved.is_relative_to(downloads_root):
						continue
				except AttributeError:
					try:
						resolved.relative_to(downloads_root)
					except Exception:
						continue
				if not resolved.exists():
					print(f"  File not found on disk: {resolved}")
					continue
				try:
					if resolved.is_dir():
						shutil.rmtree(resolved)
					else:
						resolved.unlink()
					removed_files.append(str(resolved))
					print(f"  Deleted file: {resolved}")
				except Exception as e:
					print(f"  Failed to delete file {resolved}: {e}")
					
			print(f"\nSuccessfully removed {deleted_count} outdated variants ({len(removed_files)} files deleted).")
			
		else:
			print("No outdated tracked versions found to delete.")

		# --- PHASE 2: Orphaned Files Cleanup ---
		downloads_root = _downloads_root_from_env().resolve()
		orphaned_count = 0
		deleted_folders = 0
		
		if downloads_root.exists():
			print("\nScanning for untracked/orphaned mods...")
			for f in downloads_root.rglob('*'):
				if f.is_file() and f.suffix.lower() in ['.zip', '.rar', '.7z', '.pak']:
					abs_path = str(f.resolve()).lower()
					if abs_path not in tracked_paths:
						try:
							f.unlink()
							orphaned_count += 1
							print(f"  Deleted orphaned file: {f.relative_to(downloads_root)}")
						except Exception as e:
							print(f"  Failed to delete orphaned {f.relative_to(downloads_root)}: {e}")
			
			# Clean up empty folders recursively
			for d in sorted([d for d in downloads_root.rglob('*') if d.is_dir()], key=lambda p: len(p.parts), reverse=True):
				try:
					if not any(d.iterdir()):
						d.rmdir()
						deleted_folders += 1
						print(f"  Deleted empty folder: {d.relative_to(downloads_root)}")
				except Exception:
					pass
		
		if orphaned_count > 0 or deleted_folders > 0:
			print(f"\nSuccessfully deleted {orphaned_count} untracked mod files and {deleted_folders} empty folders.")
		else:
			print("No untracked mod files found.")
			
		# 3. Rebuild tags and conflicts (fast) rather than full ingest
		if to_delete_ids or orphaned_count > 0:
			try:
				from scripts import build_asset_tags as _bat  # type: ignore
				from scripts import build_pak_tags as _bpt  # type: ignore
				_bat.main([])
				_bpt.main([])
			except Exception as e:
				print(f"Warning: Failed to rebuild tags: {e}")
				
			_safe_rebuild_conflicts(conn, active_only=None, purpose="delete_outdated_versions")
			
		return 0
	except Exception as e:
		print(f"Error deleting outdated versions: {e}")
		import traceback
		traceback.print_exc()
		return 1
	finally:
		try:
			conn.close()
		except Exception:
			pass


def _apply_settings_update(payload: SettingsUpdatePayload) -> Dict[str, Any]:
	overrides: Dict[str, Any] = {}
	if payload.data_dir is not None:
		value = payload.data_dir.strip()
		if value:
			overrides["data_dir"] = value
	if payload.marvel_rivals_root is not None:
		overrides["marvel_rivals_root"] = _normalize_optional_str(payload.marvel_rivals_root)
	if payload.marvel_rivals_local_downloads_root is not None:
		overrides["marvel_rivals_local_downloads_root"] = _normalize_optional_str(payload.marvel_rivals_local_downloads_root)
	if payload.nexus_api_key is not None:
		overrides["nexus_api_key"] = payload.nexus_api_key.strip()
	if payload.aes_key_hex is not None:
		overrides["aes_key_hex"] = payload.aes_key_hex.strip()
	if payload.allow_direct_api_downloads is not None:
		overrides["allow_direct_api_downloads"] = bool(payload.allow_direct_api_downloads)
	if payload.seven_zip_bin is not None:
		overrides["seven_zip_bin"] = _normalize_optional_str(payload.seven_zip_bin)
	if not overrides:
		current = _get_current_settings()
		return _serialize_settings(current)
	updated = configure(**overrides)
	_seed_env_from_settings()
	validation = _collect_settings_validation(updated)
	
	# Don't block saving settings even if validation fails
	# Just return the validation results so frontend can show warnings
	return _serialize_settings(updated, validation=validation)


def _task_ingest_download_assets() -> int:
	from scripts import ingest_download_assets as ingest_mod

	args = ["--extract"]
	return int(ingest_mod.main(args) or 0)


def _get_scan_active_args() -> list:
	"""Build arguments for scan_active_main with game-root from settings."""
	from core.config.settings import SETTINGS, load_settings
	
	# Reload settings to ensure we have the latest saved configuration
	current_settings = load_settings()
	
	args = []
	if current_settings.marvel_rivals_root:
		args.extend(["--game-root", str(current_settings.marvel_rivals_root)])
	else:
		print(f"[WARNING] marvel_rivals_root is not configured in settings")
		print(f"[WARNING] Current SETTINGS: {current_settings}")
	
	return args


def _task_scan_active_mods() -> int:
	return int(scan_active_main(_get_scan_active_args()) or 0)


def _task_sync_nexus() -> int:
	from core.db.db import get_connection, init_schema
	from scripts.sync_nexus_to_db import iter_mod_ids_from_db, sync_mods

	conn = get_connection()
	try:
		init_schema(conn)
		mod_ids = list(iter_mod_ids_from_db(conn))
	finally:
		try:
			conn.close()
		except Exception:
			pass
	if not mod_ids:
		print("No Nexus-linked mods found; nothing to sync.")
		return 0
	sync_mods(mod_ids)
	print(f"Synced {len(mod_ids)} mod(s) from Nexus API.")
	return 0


def _task_rebuild_tags() -> int:
	from scripts import rebuild_tags as rebuild

	return int(rebuild.main([]) or 0)


def _task_rebuild_conflicts() -> int:
	from core.db.db import get_connection, init_schema, run_migrations

	conn = get_connection()
	results: Dict[str, int] = {}
	try:
		init_schema(conn)
		run_migrations(conn)
		results = _safe_rebuild_conflicts(
			conn,
			active_only=None,
			purpose="cli_rebuild_conflicts",
			raise_on_error=True,
		) or {}
	finally:
		try:
			conn.close()
		except Exception:
			pass

	if results:
		for table_name, count in sorted(results.items()):
			print(f"{table_name}: {count}")
	else:
		print("Rebuild conflicts completed with no reported changes.")
	return 0


def _task_rebuild_character_data() -> Tuple[int, Optional[Dict[str, Any]]]:
	"""Rebuild character and skin data from PAK files."""
	from core.config.settings import load_settings
	
	# Reload settings to ensure we have the latest marvel_rivals_root path
	current_settings = load_settings()
	
	# Verify marvel_rivals_root is configured
	if not current_settings.marvel_rivals_root:
		print("ERROR: marvel_rivals_root is not configured")
		print("Please set your Marvel Rivals installation path in Settings")
		return 1, None
	
	try:
		from core.extraction.service import extract_and_ingest
		print("Extracting character and skin data from PAK files...")
		changes = extract_and_ingest()
		print("Character data rebuild complete!")
		return 0, changes
	except Exception as exc:
		print(f"Character data rebuild failed: {exc}")
		import traceback
		traceback.print_exc()
		return 1, None


def _task_bootstrap_rebuild() -> int:
	"""Run full database rebuild including tags, conflicts, and all metadata.
	
	Forces a complete rebuild of:
	- local_downloads table (from downloads directory scan)
	- Nexus API metadata sync (mods, files, changelogs)
	- pak_assets ingestion (extraction and tagging)
	- asset_tags and pak_tags_json (character/category detection)
	- conflict detection tables
	- active pak snapshot
	"""
	import sqlite3
	from pathlib import Path
	from scripts import rebuild_sqlite as rebuild
	from core.api.dependencies import reset_schema_cache
	from core.db.db import _data_root, DB_FILENAME

	# Determine the database path that the API uses
	db_path = str(_data_root() / DB_FILENAME)
	
	print("=" * 70)
	print("BOOTSTRAP REBUILD - Starting comprehensive database rebuild")
	print("This will rebuild ALL tables: downloads, tags, conflicts, etc.")
	print(f"Database location: {db_path}")
	print("=" * 70)
	
	# CRITICAL: Pass --db parameter to ensure rebuild writes to the SAME database
	# that the API reads from. Without this, rebuild writes to project root but
	# API reads from data_dir!
	rebuild_args = ["--db", db_path, "--log-level", "INFO"]
	
	# CRITICAL: Extract character data BEFORE running rebuild_sqlite
	# This ensures the character/skin database is populated when tag building happens,
	# allowing tag_assets.py to load character data from the database
	print("\n" + "=" * 70)
	print("BOOTSTRAP REBUILD - Extracting character and skin data")
	print("=" * 70)
	
	char_exit_code = _task_rebuild_character_data()
	if char_exit_code != 0:
		print(f"⚠ Warning: Character data extraction failed with code {char_exit_code}")
		print("You can manually rebuild character data from Settings if needed")
		# Don't fail the entire bootstrap if character extraction fails
	else:
		print("✓ Character data extraction completed successfully")
		
		# Count character and skin data
		try:
			conn = sqlite3.connect(db_path)
			cur = conn.cursor()
			
			characters_count = cur.execute("SELECT COUNT(*) FROM characters").fetchone()[0]
			skins_count = cur.execute("SELECT COUNT(*) FROM skins").fetchone()[0]
			print(f"✓ characters: {characters_count} entries")
			print(f"✓ skins: {skins_count} entries")
			
			conn.close()
		except Exception as e:
			print(f"Warning: Could not count character data: {e}")
	
	# Now run the main database rebuild - tags will use extracted character data
	exit_code = int(rebuild.main(rebuild_args) or 0)
	
	if exit_code == 0:
		print("\n" + "=" * 70)
		print("BOOTSTRAP REBUILD - Database rebuild completed successfully")
		print("=" * 70)
		
		# Count what we rebuilt
		db_path = str(_data_root() / DB_FILENAME)
		try:
			conn = sqlite3.connect(db_path)
			cur = conn.cursor()
			
			downloads_count = cur.execute("SELECT COUNT(*) FROM local_downloads").fetchone()[0]
			print(f"✓ local_downloads: {downloads_count} entries")
			
			# DIAGNOSTIC: Check if contents field is populated
			null_contents = cur.execute("SELECT COUNT(*) FROM local_downloads WHERE contents IS NULL OR contents = ''").fetchone()[0]
			if null_contents > 0:
				print(f"⚠ WARNING: {null_contents} downloads have NULL/empty contents field!")
			
			# DIAGNOSTIC: Show sample of contents
			sample = cur.execute("SELECT id, name, contents FROM local_downloads LIMIT 3").fetchall()
			for dl_id, dl_name, dl_contents in sample:
				contents_preview = dl_contents[:100] if dl_contents else "NULL"
				print(f"  Sample [{dl_id}] {dl_name}: contents={contents_preview}...")
			
			mods_count = cur.execute("SELECT COUNT(*) FROM mods").fetchone()[0]
			print(f"✓ mods: {mods_count} entries")
			
			assets_count = cur.execute("SELECT COUNT(*) FROM pak_assets").fetchone()[0]
			print(f"✓ pak_assets: {assets_count} entries")
			
			tags_count = cur.execute("SELECT COUNT(*) FROM asset_tags").fetchone()[0]
			print(f"✓ asset_tags: {tags_count} entries")
			
			conflicts_count = cur.execute("SELECT COUNT(*) FROM v_asset_conflicts").fetchone()[0]
			print(f"✓ v_asset_conflicts: {conflicts_count} entries")
			
			conn.close()
			print("=" * 70)
		except Exception as e:
			print(f"Warning: Could not count rebuilt entries: {e}")
		
		print("\nResetting schema cache to ensure fresh connections...")
		reset_schema_cache()
		
		# CRITICAL FIX: Force COMPLETE WAL checkpoint to merge all data into main DB file
		# This ensures ALL future connections (even those opened before checkpoint) see new data
		print("Forcing COMPLETE SQLite WAL checkpoint to merge all transactions...")
		import gc
		import time
		
		# Give Python a moment to close any lingering connections
		gc.collect()
		time.sleep(0.2)
		
		try:
			# Open a fresh connection for checkpoint
			conn = sqlite3.connect(db_path)
			
			# First, try to force close any other connections (best effort)
			try:
				# Set a short busy timeout to avoid blocking
				conn.execute("PRAGMA busy_timeout = 5000;")
			except Exception:
				pass
			
			# TRUNCATE mode: Most aggressive checkpoint that forces WAL to be completely
			# written to main DB and truncates WAL file. This ensures all readers,
			# even those with stale connections, will be forced to read from main DB.
			result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
			
			# result is (busy, log, checkpointed):
			# - busy: number of frames not checkpointed due to locks
			# - log: total frames in WAL 
			# - checkpointed: frames checkpointed
			busy, log_frames, checkpointed = result if result else (0, 0, 0)
			
			if busy > 0:
				print(f"⚠ Warning: {busy} WAL frames could not be checkpointed (DB busy)")
				print("  Some connections may still be reading. Retrying...")
				time.sleep(0.5)
				result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
				busy, log_frames, checkpointed = result if result else (0, 0, 0)
			
			if busy == 0:
				print(f"✓ WAL checkpoint completed successfully")
				print(f"  - Checkpointed {checkpointed} frames from WAL")
				print(f"  - Total WAL frames: {log_frames}")
				
				# CRITICAL: Force a new read transaction to pick up checkpointed data
				# This ensures the connection advances its read mark to see latest data
				print("  - Forcing read transaction cycle to refresh snapshot...")
				conn.execute("BEGIN;")
				conn.execute("SELECT 1;")
				conn.execute("COMMIT;")
				
				print("  All database connections will now see the rebuilt data")
			else:
				print(f"⚠ Warning: Still {busy} frames not checkpointed")
				print("  Backend may need restart to ensure all connections see new data")
			
			conn.close()
			
			# CRITICAL FIX: Delete WAL and SHM files to force all connections to see main DB
			# This is the nuclear option but ensures no stale reads after bootstrap
			print("  - Removing WAL files to force fresh reads...")
			wal_file = Path(db_path).with_suffix(".db-wal")
			shm_file = Path(db_path).with_suffix(".db-shm")
			try:
				if wal_file.exists():
					wal_file.unlink()
					print(f"  - Deleted {wal_file.name}")
				if shm_file.exists():
					shm_file.unlink()
					print(f"  - Deleted {shm_file.name}")
			except Exception as exc:
				print(f"  ⚠ Warning: Could not delete WAL files: {exc}")
		except Exception as e:
			print(f"⚠ Warning: WAL checkpoint failed: {e}")
			print("  You may need to restart the backend to see all changes")
		
		# Give the file system a moment to settle after checkpoint
		import time
		time.sleep(0.5)
		
		print("\n" + "=" * 70)
		print("BOOTSTRAP REBUILD - All operations completed")
		print("Database is ready for immediate use (no restart required)")
		print("=" * 70)
	else:
		print(f"\n✗ Bootstrap rebuild failed with exit code {exit_code}")
	
	return exit_code


def _run_settings_task(
	task: SettingsTaskName,
	*,
	on_output: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
	_seed_env_from_settings()
	started_at = datetime.utcnow().isoformat() + "Z"
	start_time = time.perf_counter()

	class _StreamingBuffer(io.StringIO):
		def __init__(self, callback: Optional[Callable[[str], None]]) -> None:
			super().__init__()
			self._callback = callback

		def write(self, s: str) -> int:  # pragma: no cover - passthrough
			written = super().write(s)
			if s and self._callback is not None:
				self._callback(s)
			return written

	buffer: io.StringIO = _StreamingBuffer(on_output)
	ok = True
	output_error: Optional[str] = None
	exit_code = 0

	class _TaskLogHandler(logging.Handler):
		"""Capture logging records emitted during maintenance tasks."""

		def __init__(self, stream: io.TextIOBase) -> None:
			super().__init__(level=logging.INFO)
			self._stream = stream
			self.setFormatter(
				logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
			)

		def emit(self, record: logging.LogRecord) -> None:  # pragma: no cover - glue code
			try:
				msg = self.format(record)
			except Exception:
				msg = record.getMessage()
			self._stream.write(msg)
			if not msg.endswith("\n"):
				self._stream.write("\n")

	log_handler = _TaskLogHandler(buffer)
	root_logger = logging.getLogger()
	previous_level = root_logger.level
	root_logger.addHandler(log_handler)
	try:
		if previous_level == logging.NOTSET or previous_level > logging.INFO:
			root_logger.setLevel(logging.INFO)
	except Exception:
		# Best-effort; never let logging adjustments interrupt the task runner
		pass

	buffer.write(f"Starting maintenance task '{task}'...\n")

	def runner() -> int:
		if task == "ingest_download_assets":
			return _task_ingest_download_assets()
		if task == "scan_active_mods":
			return _task_scan_active_mods()
		if task == "sync_nexus":
			return _task_sync_nexus()
		if task == "rebuild_tags":
			return _task_rebuild_tags()
		if task == "rebuild_conflicts":
			return _task_rebuild_conflicts()
		if task == "bootstrap_rebuild":
			return _task_bootstrap_rebuild()
		if task == "rebuild_character_data":
			return _task_rebuild_character_data()
		if task == "delete_outdated_versions":
			return _task_delete_outdated_versions()
		raise HTTPException(status_code=400, detail=f"Unknown task: {task}")

	metadata: Optional[Any] = None
	try:
		with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
			res = runner()
			if isinstance(res, tuple):
				exit_code, metadata = res
			else:
				exit_code = res
	except HTTPException:
		raise
	except SystemExit as exc:
		exit_code = int(exc.code or 0)
	except Exception as exc:
		ok = False
		if exit_code == 0:
			exit_code = 1
		output_error = str(exc)
		buffer.write("\n")
		buffer.write(traceback.format_exc())
	else:
		ok = exit_code == 0

	finally:
		buffer.write(f"Task '{task}' finished with exit code {exit_code}.\n")
		task_duration = int((time.perf_counter() - start_time) * 1000)
		buffer.write(f"Duration: {task_duration / 1000:.2f}s\n")
		root_logger.removeHandler(log_handler)
		try:
			root_logger.setLevel(previous_level)
		except Exception:
			pass

	finished_at = datetime.utcnow().isoformat() + "Z"
	duration_ms = int((time.perf_counter() - start_time) * 1000)
	return {
		"ok": ok and exit_code == 0,
		"task": task,
		"exit_code": int(exit_code),
		"error": output_error,
		"output": buffer.getvalue(),
		"started_at": started_at,
		"finished_at": finished_at,
		"duration_ms": duration_ms,
		"metadata": metadata,
	}

def _extract_member_id(value: Any) -> Optional[int]:
	"""Best-effort parsing for Nexus member identifiers from diverse inputs."""
	if value is None:
		return None
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value
	if isinstance(value, float):
		if value.is_integer():
			return int(value)
		return None
	if isinstance(value, str):
		s = value.strip()
		if not s:
			return None
		if s.isdigit():
			try:
				return int(s)
			except ValueError:
				return None
		match = _MEMBER_ID_RE.search(s)
		if match:
			try:
				return int(match.group(1))
			except (TypeError, ValueError):
				return None
	return None


def _author_avatar_url(member_id: Optional[int], profile_url: Optional[str]) -> Optional[str]:
	"""Derive a usable avatar URL for Nexus authors when possible."""
	resolved = member_id
	if resolved is None and profile_url:
		resolved = _extract_member_id(profile_url)
	if resolved is None:
		return None
	return f"https://avatars.nexusmods.com/{resolved}/100"


class DuplicateDownloadError(Exception):
	"""Raised when an ingest matches an existing local download."""

	def __init__(
		self,
		download_id: int,
		*,
		existing_name: Optional[str] = None,
		existing_version: Optional[str] = None,
		existing_path: Optional[str] = None,
		candidate_name: Optional[str] = None,
		candidate_version: Optional[str] = None,
	) -> None:
		super().__init__(f"duplicate download detected (id={download_id})")
		self.download_id = download_id
		self.existing_name = existing_name
		self.existing_version = existing_version
		self.existing_path = existing_path
		self.candidate_name = candidate_name
		self.candidate_version = candidate_version


def _normalize_download_name(value: Optional[str]) -> str:
	return str(value or "").strip().lower()


def _normalize_download_version(value: Optional[str]) -> str:
	return str(value or "").strip().lower()


def _normalize_contents_for_compare(values: Iterable[Any]) -> List[str]:
	items: Set[str] = set()
	for raw in values:
		if not isinstance(raw, str):
			continue
		normalized = raw.replace("\\", "/").strip().lower()
		if normalized:
			items.add(normalized)
	return sorted(items)


_CREATED_AT_KEYS = (
	"created_at",
	"createdAt",
	"uploaded_at",
	"uploadedAt",
	"uploaded_time",
	"uploadedTime",
	"uploaded_timestamp",
	"uploadedTimestamp",
	"file_uploaded_at",
	"fileUploadedAt",
)


def _extract_created_at_hint(source: Optional[Dict[str, Any]]) -> Optional[Any]:
	if not isinstance(source, dict):
		return None
	for key in _CREATED_AT_KEYS:
		value = source.get(key)
		if value is None:
			continue
		if isinstance(value, str) and not value.strip():
			continue
		return value
	return None


def _duplicate_detail_from_error(error: DuplicateDownloadError) -> Dict[str, Any]:
	name_hint = error.candidate_name or error.existing_name
	version_hint = error.candidate_version or error.existing_version
	if name_hint and version_hint:
		message = f"Mod '{name_hint}' version '{version_hint}' already exists"
	elif name_hint:
		message = f"Mod '{name_hint}' already exists"
	else:
		message = "Mod already exists"
	detail: Dict[str, Any] = {
		"error": "duplicate_download",
		"message": message,
		"existing_download_id": error.download_id,
	}
	if error.existing_name:
		detail["existing_name"] = error.existing_name
	if error.existing_version:
		detail["existing_version"] = error.existing_version
	if error.existing_path:
		detail["existing_path"] = error.existing_path
	if error.candidate_name and error.candidate_name != error.existing_name:
		detail["requested_name"] = error.candidate_name
	if error.candidate_version and error.candidate_version != error.existing_version:
		detail["requested_version"] = error.candidate_version
	return detail


def _resolve_mod_metadata(
	path: Path,
	provided_name: Optional[str] = None,
	provided_mod_id: Optional[int] = None,
	provided_version: Optional[str] = None,
) -> Tuple[str, Optional[int], str]:
	"""Unified metadata resolver for mod installation.
	
	Extracts name, mod_id, and version from filename if not provided,
	or cleans up provided names that look like raw filenames.
	"""
	# 1. If provided_name looks like a filename, parse it to extract the clean name
	if provided_name:
		p_name, p_mod_id, p_version = parse_mod_filename(provided_name)
		if p_name:
			provided_name = p_name
			provided_mod_id = provided_mod_id or p_mod_id
			provided_version = provided_version or p_version

	# 2. Start with filename parsing of the actual path on disk as fallback
	filename_name, filename_mod_id, filename_version = parse_mod_filename(path.name)
	
	# 3. Prefer cleaned-up provided values
	final_name = provided_name or filename_name
	final_mod_id = provided_mod_id or filename_mod_id
	final_version = provided_version or filename_version
	
	# 4. Clean up and Normalize
	if final_name:
		# Always normalize underscores to spaces for consistent duplication checking
		final_name = final_name.replace("_", " ").strip()
		# Collapse multiple spaces
		final_name = re.sub(r'\s+', ' ', final_name)
	if final_version:
		final_version = final_version.strip()

	
	return final_name, final_mod_id, final_version



def _find_duplicate_download(
	cur,
	candidate_name: str,
	candidate_version: str,
	mod_id: Optional[int],
) -> Optional[Tuple[int, Optional[str], Optional[str], Optional[str]]]:
	"""Check if a download with the same name + version + mod_id already exists.
	
	Returns (download_id, name, version, path) if duplicate found AND file exists.
	Uses exact string matching for name and version (case-insensitive).
	"""
	if mod_id is not None:
		rows = cur.execute(
			"""
			SELECT id, name, version, path
			FROM local_downloads
			WHERE LOWER(name) = LOWER(?) AND mod_id = ?
			""",
			(candidate_name, mod_id),
		).fetchall()
	else:
		rows = cur.execute(
			"""
			SELECT id, name, version, path
			FROM local_downloads
			WHERE LOWER(name) = LOWER(?)
			""",
			(candidate_name,),
		).fetchall()
	
	candidate_version_normalized = (candidate_version or "").strip().lower()
	for existing_id, existing_name, existing_version, existing_path in rows:
		existing_version_normalized = (existing_version or "").strip().lower()
		# Use prefix-aware matching: "2" matches "2.177.1" because "2" is the
		# real Nexus version and "177.1" are file-sub-ID / timestamp artifacts
		# from filename parsing.  Also handles the reverse direction.
		# Use robust version matching logic: handles prefix matching (e.g., "2" matches "2.177.1")
		# and other specific cases requested by the user.
		versions_match = (
			not existing_version_normalized
			or not candidate_version_normalized
			or versions_equivalent(existing_version, candidate_version)
		)
		if versions_match:
			# PHYSICAL EXISTENCE CHECK:
			# If the file is missing from disk, we allow re-ingestion.
			abs_path = resolve_absolute_download_path(existing_path)
			if abs_path.exists():
				return existing_id, existing_name, existing_version, existing_path
			else:
				logger.info(f"[dupe_check] Duplicate '{existing_name}' ({existing_version}) found in DB but file is missing at '{existing_path}'. Permitting re-ingestion.")
	
	return None


def _ingest_resolved_download(
	path: Path,
	*,
	name: str,
	mod_id: Optional[int],
	version: str,
	source_url: Optional[str] = None,
	metadata_snapshot: Optional[Dict[str, Any]] = None,
	filtered_metadata: Optional[Dict[str, Any]] = None,
	created_at_hint: Optional[Any] = None,
) -> Dict[str, Any]:
	"""Ingest a resolved local archive/pak into ``local_downloads`` and related tables."""

	path = path.resolve()
	normalized_path = normalize_download_path(path)
	
	# 1. EARLY DUPLICATION CHECK (Before expensive extraction)
	conn = get_db()
	try:
		cur = conn.cursor()
		
		# A. Check by name + version + mod_id (logical check)
		duplicate = _find_duplicate_download(cur, name, version, mod_id)
		
		# B. Check by physical path (physical check)
		# If the exact same path is already in DB, it's a duplicate.
		if duplicate is None:
			existing_by_path = cur.execute(
				"SELECT id, name, version FROM local_downloads WHERE path = ?", 
				(normalized_path,)
			).fetchone()
			if existing_by_path:
				duplicate = (existing_by_path[0], existing_by_path[1], existing_by_path[2], normalized_path)

		if duplicate is not None:
			existing_id, existing_name, existing_version, existing_path = duplicate
			raise DuplicateDownloadError(
				existing_id,
				existing_name=existing_name,
				existing_version=existing_version,
				existing_path=existing_path,
				candidate_name=name,
				candidate_version=version,
			)
	finally:
		try:
			conn.close()
		except Exception:
			pass


	repo_root = _ROOT
	current = _get_current_settings()
	aes_key = current.aes_key_hex or None

	suffix = path.suffix.lower()
	is_archive = suffix in {".zip", ".rar", ".7z"}
	is_pak = suffix == ".pak"
	contents: List[str] = []
	pak_map: Dict[str, List[str]] = {}
	ingest_prep_error: Optional[Exception] = None


	# Extract and enumerate PAK files from archives
	if is_archive and path.exists():
		tmpdir = None
		try:
			logger.info(f"[ingest] Extracting archive to enumerate PAK files: {path.name}")
			# Extract archive to temporary directory
			tmpdir = tempfile.mkdtemp(prefix="ingest_mod_")
			extract_archive(str(path), tmpdir)
			
			# Enumerate archive contents to preserve hierarchical paths for the UI
			try:
				all_entries = list_entries(str(path))
				# Filter for relevant Unreal Engine file types
				contents = [
					e for e in all_entries 
					if e.lower().endswith(('.pak', '.utoc', '.ucas', '.sig'))
				]
				logger.info(f"[ingest] Enumerated {len(contents)} relevant file(s) from archive: {contents}")
			except Exception as e:
				logger.warning(f"[ingest] Failed to list archive entries for {path.name}: {e}")
				contents = []

			# Extract PAK asset map from the extracted folder for conflict detection
			pak_map = extract_pak_asset_map_from_folder(tmpdir, aes_key=aes_key)
			
			if contents or pak_map:
				if not contents and pak_map:
					# Fallback to pak_map keys if archive enumeration failed but map has entries
					contents = list(pak_map.keys())
				
				# Collapse bundled .pak + .utoc pairs (preserves hierarchical paths correctly)
				contents = collapse_pak_bundle(contents)
				logger.info(f"[ingest] Final contents after collapsing: {contents}")
			else:
				logger.warning(f"[ingest] No PAK files found via asset map (Rust) or enumeration (list_entries) in archive {path.name}. Falling back to directory scan.")
				fallback_paks = []
				for root, _, files in os.walk(tmpdir):
					for file in files:
						lower = file.lower()
						if lower.endswith(".pak") or lower.endswith(".utoc"):
							fallback_paks.append(file)
							# Populate pak_map with empty assets so io_store check works
							pak_map[file] = []
				
				if fallback_paks:
					contents = fallback_paks
					contents = collapse_pak_bundle(contents)
					logger.info(f"[ingest] Fallback found {len(contents)} PAK file(s): {contents}")
				else:
					logger.warning(f"[ingest] No PAK files found in archive {path.name}")
		except Exception as e:
			# Log the error but don't fail - we'll store minimal info
			ingest_prep_error = e
			logger.warning(f"[ingest] Failed to extract/enumerate PAK files from {path.name}: {e}", exc_info=True)
		finally:
			# Clean up temporary directory
			if tmpdir:
				try:
					shutil.rmtree(tmpdir, ignore_errors=True)
				except Exception as cleanup_error:
					logger.debug(f"[ingest] Failed to cleanup temp dir {tmpdir}: {cleanup_error}")
	elif is_pak and path.exists():
		# For standalone PAK files, just use the filename
		logger.info(f"[ingest] Processing standalone PAK file: {path.name}")
		contents = [path.name]
		# We could optionally try to enumerate assets from the PAK file directly
		# but for now we'll just store the filename


	if not contents:
		contents = [path.name]

	contents = collapse_pak_bundle(contents)
	if not contents:
		fallback = collapse_pak_bundle([path.name])
		contents = fallback or [path.name]

	conn = get_db()
	try:
		cur = conn.cursor()
		

		local_download_id = next_local_download_id(conn)
		created_at_hints: List[Any] = []
		if created_at_hint is not None:
			created_at_hints.append(created_at_hint)
		created_at_iso = resolve_created_at(path=path, hints=created_at_hints)

		cur.execute(
			"""
			INSERT OR REPLACE INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, created_at)
			VALUES(?, ?, ?, ?, ?, ?, ?, ?)
			""",
			(
				normalized_path,
				local_download_id,
				name,
				mod_id,
				version,
				json.dumps(contents, ensure_ascii=False),
				json.dumps([], ensure_ascii=False),
				created_at_iso,
			),
		)
		conn.commit()

		if ingest_prep_error is not None:
			result = {
				"ok": True,
				"inserted": 1,
				"name": name,
				"mod_id": mod_id,
				"version": version,
				"path": normalized_path,
				"contents": contents,
				"ingest_warning": f"Asset extraction failed: {ingest_prep_error}",
				"download_id": local_download_id,
			}
			if source_url:
				result["source_url"] = source_url
			return result

		metadata_mod_id_hint: Optional[int] = mod_id
		resolved_mod_id: Optional[int] = None
		if mod_id is not None:
			row = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (mod_id,)).fetchone()
			if row:
				resolved_mod_id = mod_id

		source_zip = path.name
		io_store_flag: Optional[int]
		try:
			io_store_flag = 1 if any(k.lower().endswith(".utoc") for k in pak_map.keys()) else 0
		except Exception:
			io_store_flag = None

		# Merge paks (e.g. .pak + .utoc) into a single entry keyed by the .pak name
		merged_pak_map: Dict[str, List[str]] = {}
		merged_io_store: Dict[str, bool] = {}
		
		for raw_pak_name, assets in pak_map.items():
			# Normalize extension: .utoc/.ucas -> .pak
			lower_pak = raw_pak_name.lower()
			if lower_pak.endswith(".utoc"):
				normalized_name = raw_pak_name[:-5] + ".pak"
			elif lower_pak.endswith(".ucas"):
				normalized_name = raw_pak_name[:-5] + ".pak"
			else:
				normalized_name = raw_pak_name
				
			# Track if this bundle involves IoStore (if any part is .utoc)
			is_utoc = lower_pak.endswith(".utoc")
			if normalized_name not in merged_io_store:
				merged_io_store[normalized_name] = False
			if is_utoc:
				merged_io_store[normalized_name] = True
				
			if normalized_name not in merged_pak_map:
				merged_pak_map[normalized_name] = []
			merged_pak_map[normalized_name].extend(assets)

		total_paks = 0
		total_assets = 0
		for pak_name, assets in merged_pak_map.items():
			# Deduplicate assets
			assets = sorted(list(set(assets)))
			io_store = merged_io_store.get(pak_name, False)
			
			total_paks += 1
			total_assets += len(assets)
			upsert_mod_pak(
				conn,
				pak_name=pak_name,
				mod_id=resolved_mod_id,
				source_zip=source_zip,
				local_download_id=local_download_id,
				io_store=io_store,
			)
			bulk_upsert_pak_assets(conn, pak_name, assets, replace=True)
			upsert_pak_assets_json(conn, pak_name, assets, mod_id=resolved_mod_id)

		try:
			from scripts import build_asset_tags as _bat  # type: ignore
			from scripts import build_pak_tags as _bpt  # type: ignore
			_bat.main([])
			_bpt.main([])
		except Exception:
			pass

		metadata_info = _sync_mod_metadata(
			conn,
			metadata_mod_id_hint,
			name,
			pre_fetched=metadata_snapshot,
			filtered_payload=filtered_metadata,
		)
		synced_mod_id = metadata_info.get("synced_mod_id")
		if synced_mod_id and resolved_mod_id != synced_mod_id:
			resolved_mod_id = int(synced_mod_id)
			try:
				cur.execute("UPDATE local_downloads SET mod_id = ? WHERE id = ?", (resolved_mod_id, local_download_id))
				conn.commit()
			except Exception:
				metadata_info.setdefault("metadata_warning", "Failed to link discovered mod ID to local download")
			if "metadata_warning" not in metadata_info:
				try:
					io_bool = bool(io_store_flag) if io_store_flag is not None else None
					# Merge paks when updating with discovered mod_id
					update_merged_pak_map: Dict[str, List[str]] = {}
					update_merged_io_store: Dict[str, bool] = {}
					
					for raw_pak_name, assets in pak_map.items():
						# Normalize extension: .utoc/.ucas -> .pak
						lower_pak = raw_pak_name.lower()
						if lower_pak.endswith(".utoc"):
							normalized_name = raw_pak_name[:-5] + ".pak"
						elif lower_pak.endswith(".ucas"):
							normalized_name = raw_pak_name[:-5] + ".pak"
						else:
							normalized_name = raw_pak_name
							
						# Track if this bundle involves IoStore
						is_utoc = lower_pak.endswith(".utoc")
						if normalized_name not in update_merged_io_store:
							update_merged_io_store[normalized_name] = False
						if is_utoc:
							update_merged_io_store[normalized_name] = True
							
						if normalized_name not in update_merged_pak_map:
							update_merged_pak_map[normalized_name] = []
						update_merged_pak_map[normalized_name].extend(assets)
					
					for pak_name, assets in update_merged_pak_map.items():
						# Deduplicate assets
						assets = sorted(list(set(assets)))
						io_store = update_merged_io_store.get(pak_name, False)
						
						upsert_mod_pak(
							conn,
							pak_name=pak_name,
							mod_id=resolved_mod_id,
							source_zip=source_zip,
							local_download_id=local_download_id,
							io_store=io_store,
						)
						upsert_pak_assets_json(conn, pak_name, assets, mod_id=resolved_mod_id)
				except Exception:
					metadata_info.setdefault(
						"metadata_warning",
						"Metadata linked, but updating pak records with new mod ID failed",
					)

		if resolved_mod_id is None and metadata_mod_id_hint is not None:
			resolved_mod_id = metadata_mod_id_hint

		# Refresh conflict tables after finalizing mod IDs so new installs register
		_safe_rebuild_conflicts(conn, active_only=None, purpose="ingest_mod")

		res = {
			"ok": True,
			"inserted": 1,
			"name": name,
			"mod_id": resolved_mod_id,
			"version": version,
			"path": normalized_path,
			"contents": contents,
			"ingested_paks": total_paks,
			"ingested_assets": total_assets,
			"download_id": local_download_id,
		}
		if source_url:
			res["source_url"] = source_url
		res.update(metadata_info)
		return res
	finally:
		try:
			conn.close()
		except Exception:
			pass

def _load_canonical_names() -> set[str]:
	"""Load character names from database instead of character_ids.json."""
	global _CANON_CHAR_NAMES
	if _CANON_CHAR_NAMES is not None:
		return _CANON_CHAR_NAMES
	try:
		from core.db.db import get_connection, get_character_names
		conn = get_connection()
		try:
			names = get_character_names(conn)
			_CANON_CHAR_NAMES = set(names)
			return _CANON_CHAR_NAMES
		finally:
			conn.close()
	except Exception as e:
		logger.warning(f"Failed to load character names from database: {e}")
		_CANON_CHAR_NAMES = set()
		return _CANON_CHAR_NAMES

_SEP_RE = re.compile(r"[\s_\-\+]+")
def _normalize(s: str) -> tuple[str, str]:
	"""Return (spaced, compact) lowercase normalized variants for matching.
	spaced replaces separators with single spaces; compact removes spaces entirely.
	"""
	try:
		spaced = _SEP_RE.sub(" ", s.lower()).strip()
		compact = spaced.replace(" ", "")
		return spaced, compact
	except Exception:
		ls = str(s).lower()
		return ls, ls.replace(" ", "")


def _canonicalize_tokens(raw_tokens: set[str]) -> list[str]:
	"""Keep only known categories and character tokens present in character_ids.json.
	Map any variant to its canonical token using normalization against the canonical set.
	Unknown non-category tokens are dropped.
	"""
	canon = _load_canonical_names()
	if not canon:
		# If canon not available, return categories only plus the raw tokens as-is (best-effort)
		return sorted(raw_tokens)
	# Build normalized lookup for canon tokens
	canon_by_compact: dict[str, str] = {}
	for c in canon:
		_, cc = _normalize(c)
		if cc:
			canon_by_compact[cc] = c
	final: set[str] = set()
	for t in list(raw_tokens):
		lt = str(t).strip().lower()
		if not lt:
			continue
		# preserve known categories directly
		if lt in _KNOWN_CATEGORIES:
			final.add(lt)
			continue
		# try map to canonical character token
		_, cc = _normalize(lt)
		canon_tok = canon_by_compact.get(cc)
		if canon_tok:
			final.add(canon_tok)
			continue
		# drop anything that isn't canonical
		continue
	return sorted(final)


@app.get("/health")
def health() -> Dict[str, Any]:
	try:
		conn = get_db()
		cur = conn.cursor()
		mods = cur.execute("SELECT COUNT(*) FROM mods").fetchone()[0]
		paks = cur.execute("SELECT COUNT(*) FROM mod_paks").fetchone()[0]
		assets = cur.execute("SELECT COUNT(*) FROM pak_assets").fetchone()[0]
		return {"ok": True, "mods": mods, "paks": paks, "assets": assets}
	except Exception as e:
		return {"ok": False, "error": str(e)}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/favourites/toggle")
def toggle_favourite(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Toggle a mod's favourite status. Body: { "mod_id": int }"""
	mod_id = payload.get("mod_id")
	if mod_id is None:
		raise HTTPException(status_code=400, detail="mod_id is required")
	try:
		mod_id = int(mod_id)
	except (TypeError, ValueError):
		raise HTTPException(status_code=400, detail="mod_id must be an integer")
	conn = get_db()
	try:
		cur = conn.cursor()
		existing = cur.execute("SELECT 1 FROM favourites WHERE mod_id = ?", (mod_id,)).fetchone()
		if existing:
			cur.execute("DELETE FROM favourites WHERE mod_id = ?", (mod_id,))
			conn.commit()
			return {"ok": True, "favourited": False}
		else:
			cur.execute("INSERT INTO favourites (mod_id) VALUES (?)", (mod_id,))
			conn.commit()
			return {"ok": True, "favourited": True}
	except Exception as e:
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/favourites")
def list_favourites() -> Dict[str, Any]:
	"""Return all favourited mod IDs."""
	conn = get_db()
	try:
		rows = conn.execute("SELECT mod_id FROM favourites ORDER BY created_at DESC").fetchall()
		return {"ok": True, "mod_ids": [row[0] for row in rows]}
	except Exception as e:
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/game-version/check")
def check_game_version() -> Dict[str, Any]:
	"""Check the latest modification timestamp of game PAK files.

	Scans the Paks directory inside the configured marvel_rivals_root,
	excluding the ~mods folder.  Returns the ISO-8601 timestamp of the
	most recently modified file so the frontend can compare it to a
	previously stored value and detect game updates.
	"""
	from core.config.settings import load_settings

	current_settings = load_settings()
	if not current_settings.marvel_rivals_root:
		return {
			"ok": False,
			"error": "marvel_rivals_root is not configured",
			"latest_modified": None,
			"file_count": 0,
			"latest_file": None,
		}

	paks_dir = Path(current_settings.marvel_rivals_root) / "MarvelGame" / "Marvel" / "Content" / "Paks"
	if not paks_dir.exists() or not paks_dir.is_dir():
		return {
			"ok": False,
			"error": f"Paks directory not found: {paks_dir}",
			"latest_modified": None,
			"file_count": 0,
			"latest_file": None,
		}

	latest_mtime: float = 0.0
	latest_file: Optional[str] = None
	file_count = 0

	for entry in paks_dir.rglob("*"):
		# Skip the ~mods folder and everything inside it
		try:
			rel = entry.relative_to(paks_dir)
			parts = rel.parts
			if parts and parts[0].lower() == "~mods":
				continue
		except ValueError:
			continue

		if not entry.is_file():
			continue

		file_count += 1
		try:
			mtime = entry.stat().st_mtime
			if mtime > latest_mtime:
				latest_mtime = mtime
				latest_file = str(rel)
		except OSError:
			continue

	if file_count == 0:
		return {
			"ok": False,
			"error": "No files found in Paks directory",
			"latest_modified": None,
			"file_count": 0,
			"latest_file": None,
		}

	latest_dt = datetime.fromtimestamp(latest_mtime, tz=timezone.utc)
	return {
		"ok": True,
		"latest_modified": latest_dt.isoformat(),
		"file_count": file_count,
		"latest_file": latest_file,
	}


@app.get("/api/bootstrap/status")
def get_bootstrap_status() -> Dict[str, Any]:
	"""Check if database and settings exist and need bootstrapping.
	
	IMPORTANT: This endpoint checks if the database file exists BEFORE
	calling get_db() to avoid inadvertently creating an empty database.
	
	Bootstrap is needed when:
	1. Database doesn't exist OR settings.json doesn't exist
	2. OR database is empty (no downloads and no mods)
	"""
	from core.db.db import _data_root, DB_FILENAME
	from core.config.settings import _settings_file_path
	
	# Check if database file exists BEFORE calling get_db()
	# to avoid creating it when we're just checking status
	expected_db_path = _data_root() / DB_FILENAME
	db_exists = expected_db_path.exists()
	
	# Check if settings.json exists
	settings_path = _settings_file_path()
	settings_exists = settings_path.exists()
	
	downloads_count = 0
	mods_count = 0
	migrations_count = 0
	db_path = str(expected_db_path)
	
	# Only query the database if it exists
	if db_exists:
		conn = get_db()
		try:
			cur = conn.cursor()
			try:
				row = cur.execute("SELECT COUNT(*) FROM local_downloads;").fetchone()
				downloads_count = int(row[0] or 0) if row else 0
			except Exception:
				downloads_count = 0
			try:
				row = cur.execute("SELECT COUNT(*) FROM mods;").fetchone()
				mods_count = int(row[0] or 0) if row else 0
			except Exception:
				mods_count = 0
			try:
				row = cur.execute("SELECT COUNT(*) FROM schema_migrations;").fetchone()
				migrations_count = int(row[0] or 0) if row else 0
			except Exception:
				migrations_count = 0
		finally:
			try:
				conn.close()
			except Exception:
				pass

	# Bootstrap needed if:
	# 1. Database doesn't exist OR settings doesn't exist
	# 2. OR database is empty (no downloads and no mods)
	needs_bootstrap = (not db_exists) or (not settings_exists) or (downloads_count == 0 and mods_count == 0)
	
	import logging
	logger = logging.getLogger("modmanager.api.bootstrap")
	logger.info(f"[Bootstrap Status] db_exists={db_exists}, settings_exists={settings_exists}, downloads={downloads_count}, mods={mods_count}, needs_bootstrap={needs_bootstrap}")
	
	return {
		"db_exists": bool(db_exists),
		"settings_exists": bool(settings_exists),
		"db_path": db_path,
		"settings_path": str(settings_path),
		"downloads_count": int(downloads_count),
		"mods_count": int(mods_count),
		"schema_migrations": int(migrations_count),
		"needs_bootstrap": bool(needs_bootstrap),
	}


@app.post("/api/debug/log")
def debug_log(body: Dict[str, Any]) -> Dict[str, str]:
	"""Frontend debug logging endpoint - logs to backend.log"""
	import logging
	logger = logging.getLogger("modmanager.frontend")
	
	message = body.get("message", "")
	data = body.get("data", {})
	level = body.get("level", "INFO").upper()
	
	log_msg = f"[FRONTEND] {message}"
	if data:
		log_msg += f" | Data: {json.dumps(data)}"
	
	if level == "ERROR":
		logger.error(log_msg)
	elif level == "WARN":
		logger.warning(log_msg)
	else:
		logger.info(log_msg)
	
	return {"status": "logged"}


@app.get("/api/settings")
def get_settings_route() -> Dict[str, Any]:
	# Get the latest global value
	current = _get_current_settings()
	return _serialize_settings(current)


@app.put("/api/settings")
def update_settings_route(payload: SettingsUpdatePayload) -> Dict[str, Any]:
	try:
		return _apply_settings_update(payload)
	except HTTPException:
		raise
	except Exception as exc:
		raise HTTPException(status_code=400, detail=f"Failed to update settings: {exc}") from exc


@app.post("/api/settings/run-task")
def run_settings_task_route(payload: SettingsTaskRequest) -> Dict[str, Any]:
	job_snapshot = _create_settings_task_job(payload.task)
	thread = threading.Thread(
		target=_execute_settings_task_async,
		args=(job_snapshot["id"], payload.task),
		daemon=True,
	)
	thread.start()
	return job_snapshot


@app.get("/api/settings/tasks/{job_id}")
def get_settings_task_job(job_id: str) -> Dict[str, Any]:
	try:
		return _job_snapshot(job_id)
	except KeyError as exc:
		raise HTTPException(status_code=404, detail=f"Unknown task job: {job_id}") from exc


@app.get("/api/settings/tasks")
def list_settings_task_jobs() -> List[Dict[str, Any]]:
	return _list_job_snapshots()


@app.post("/api/settings/validate-path")
def validate_path(payload: Dict[str, Any]) -> Dict[str, Any]:
	"""
	Validate a single path field.
	Expects: { "field": "data_dir"|"marvel_rivals_root"|..., "value": "C:\\path\\to\\dir" }
	Returns: { "ok": bool, "message": str, "exists": bool, "reason": str|None }
	"""
	field = payload.get("field", "")
	value = payload.get("value", "")
	
	# Define field types
	directory_fields = {"data_dir", "marvel_rivals_root", "marvel_rivals_local_downloads_root"}
	executable_fields = {
		"seven_zip_bin": "7-Zip"
	}
	
	if field in directory_fields:
		result = _validate_directory_path(value, required=True)
	elif field in executable_fields:
		label = executable_fields[field]
		result = _validate_executable_path(value, label=label, required=False)
	else:
		return {"ok": False, "message": f"Unknown field: {field}", "exists": False, "reason": "invalid_field"}
	
	return result


@app.get("/api/nxm/protocol/status")
def get_nxm_protocol_status() -> Dict[str, Any]:
	"""Check if nxm:// protocol is registered on the system."""
	from core.utils.nxm_protocol import check_nxm_status
	try:
		return check_nxm_status()
	except Exception as e:
		return {
			"registered": False,
			"error": str(e)
		}


@app.post("/api/nxm/protocol/register")
def register_nxm_protocol(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Register nxm:// protocol to launch the Tauri app.
	
	Expects: { "tauri_path": "C:\\path\\to\\Mod Manager.exe" }
	"""
	from pathlib import Path
	from core.utils.nxm_protocol import register_nxm_windows
	
	tauri_path = payload.get("tauri_path")
	if not tauri_path:
		raise HTTPException(status_code=400, detail="tauri_path is required")
	
	exe_path = Path(tauri_path)
	if not exe_path.exists():
		raise HTTPException(status_code=400, detail=f"Tauri executable not found at {tauri_path}")
	
	result = register_nxm_windows(exe_path)
	if not result.get("ok"):
		raise HTTPException(status_code=500, detail=result.get("error", "Registration failed"))
	
	return result


@app.post("/api/nxm/protocol/unregister")
def unregister_nxm_protocol() -> Dict[str, Any]:
	"""Unregister nxm:// protocol from the system."""
	from core.utils.nxm_protocol import unregister_nxm_windows
	
	result = unregister_nxm_windows()
	if not result.get("ok"):
		raise HTTPException(status_code=500, detail=result.get("error", "Unregistration failed"))
	
	return result


def _shape_conflicts_from_view(
	conn,
	view_sql: str,
	limit: int,
	*,
	active_only: bool = False,
) -> List[Dict[str, Any]]:
	cur = conn.cursor()
	rows = cur.execute(view_sql, (limit,)).fetchall()
	results: List[Dict[str, Any]] = []
	pak_meta_cache: Dict[str, Tuple[Optional[str], Optional[int]]] = {}
	for asset_path, pak_count, mod_count, conflict_paks_json, detected_at in rows:
		cat_row = cur.execute("SELECT category FROM asset_tags WHERE asset_path=?", (asset_path,)).fetchone()
		category = cat_row[0] if cat_row else None
		try:
			paks = json.loads(conflict_paks_json)
		except Exception:
			paks = []
		participants: List[Dict[str, Any]] = []
		winner_mod_id = None
		for p in paks:
			pak_name = p.get("pak_name")
			mod_id = p.get("mod_id")
			source_zip = p.get("source_zip")
			local_download_id: Optional[int] = None
			local_download_id_val = p.get("local_download_id")
			if isinstance(local_download_id_val, (int, float)) and not isinstance(local_download_id_val, bool):
				local_download_id = int(local_download_id_val)
			elif isinstance(local_download_id_val, str) and local_download_id_val.strip():
				try:
					local_download_id = int(local_download_id_val.strip())
				except Exception:
					local_download_id = None
			tag_row = (
				cur.execute("SELECT tags_json FROM pak_tags_json WHERE pak_name=?", (pak_name,)).fetchone()
				if pak_name
				else None
			)
			merged_tag = None
			if tag_row and tag_row[0]:
				try:
					tj = json.loads(tag_row[0])
					if isinstance(tj, list) and tj:
						merged_tag = tj[0]
				except Exception:
					merged_tag = None
			mod = (
				cur.execute("SELECT name, picture_url FROM mods WHERE mod_id=?", (mod_id,)).fetchone()
				if mod_id is not None
				else None
			)
			mod_name = mod[0] if mod and mod[0] else None
			icon = mod[1] if mod else None
			local_name: Optional[str] = None
			if pak_name:
				cached = pak_meta_cache.get(pak_name)
				if cached is None:
					row = cur.execute(
						"""
						SELECT ld.name, ld.id
						FROM mod_paks mp
						JOIN local_downloads ld ON ld.id = mp.local_download_id
						WHERE mp.pak_name = ?
						LIMIT 1
						""",
						(pak_name,),
					).fetchone()
					cached = (
						(row[0] if row and row[0] else None, int(row[1]) if row and row[1] is not None else None)
						if row
						else (None, None)
					)
					pak_meta_cache[pak_name] = cached
				local_name, local_download_id_db = cached
				if local_download_id is None:
					local_download_id = local_download_id_db
			if not mod_name:
				candidate_name = local_name or source_zip
				if isinstance(candidate_name, str) and candidate_name.strip():
					mod_name = candidate_name.strip()
				elif isinstance(pak_name, str) and pak_name.strip():
					mod_name = pak_name.strip()
				else:
					mod_name = "Unknown Mod"
			participants.append(
				{
					"pak_name": pak_name,
					"merged_tag": merged_tag,
					"mods": [
						{
							"mod_id": mod_id,
							"mod_name": mod_name,
							"pak_file": pak_name,
							"icon": icon,
							"is_current": bool(active_only),
							"local_download_id": local_download_id,
						}
					],
				}
			)
			if winner_mod_id is None:
				winner_mod_id = mod_id
		results.append(
			{
				"asset_path": asset_path,
				"category": category,
				"conflicting_mod_count": mod_count,
				"total_paks": pak_count,
				"winner_mod_id": winner_mod_id,
				"participants": participants,
				"detected_at": detected_at,
			}
		)
	return results


@app.get("/api/conflicts")
def get_conflicts(limit: int = 10) -> List[Dict[str, Any]]:
	conn = get_db()
	try:
		return _shape_conflicts_from_view(
			conn,
		"""
		SELECT asset_path, pak_count, mod_count, conflict_paks_json, detected_at
		FROM v_asset_conflicts_all
		ORDER BY detected_at DESC
		LIMIT ?
		""",
		limit,
		active_only=False,
	)
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/conflicts/active")
def get_conflicts_active(limit: int = 10) -> List[Dict[str, Any]]:
	conn = get_db()
	try:
		return _shape_conflicts_from_view(
			conn,
		"""
		SELECT asset_path, pak_count, mod_count, conflict_paks_json, detected_at
		FROM v_asset_conflicts_active
		ORDER BY detected_at DESC
		LIMIT ?
		""",
		limit,
		active_only=True,
	)
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/mods/add")
def add_mod(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Register a local mod archive or pak in local_downloads; minimal ingestion.
	Body: { localPath: str, name?: str, mod_id?: int, version?: str }
	"""
	local_path_val = payload.get("localPath")
	if not local_path_val or not isinstance(local_path_val, str):
		raise HTTPException(status_code=400, detail="localPath is required")
	local_path = local_path_val.strip()
	if not local_path:
		raise HTTPException(status_code=400, detail="localPath is required")
	
	source_url_val = payload.get("sourceUrl")
	source_url = source_url_val.strip() if isinstance(source_url_val, str) and source_url_val.strip() else None

	if _looks_like_url(local_path):
		source_url = source_url or local_path
		path = _download_remote_archive(local_path)
	else:
		candidate = Path(local_path).expanduser()
		if not candidate.exists():
			alt = (_downloads_root_from_env() / local_path).expanduser()
			if alt.exists():
				candidate = alt
		if not candidate.exists():
			raise HTTPException(status_code=400, detail="localPath not found")
		path = candidate

	# Unified metadata resolution
	name, mod_id, version = _resolve_mod_metadata(
		path,
		provided_name=payload.get("name"),
		provided_mod_id=payload.get("modId"),
		provided_version=payload.get("version"),
	)

	created_at_hint = _extract_created_at_hint(payload)
	try:
		return _ingest_resolved_download(
			path,
			name=name,
			mod_id=mod_id,
			version=version,
			source_url=source_url,
			created_at_hint=datetime.now(timezone.utc).isoformat(),
		)
	except DuplicateDownloadError as exc:
		raise HTTPException(status_code=409, detail=_duplicate_detail_from_error(exc))


def _eager_resolve_mod_name(record: Dict[str, Any], nxm_request) -> None:
	"""Best-effort resolve the mod name and store it in the handoff metadata.

	Checks the local ``mods`` table first (instant, no network).  If
	the mod isn't known locally, makes a single lightweight Nexus API
	call (``get_mod_info``).  Failures are silently ignored — the
	frontend will fall back to ``Mod #<id>`` if the name isn't set.
	"""
	mod_id = nxm_request.mod_id
	if mod_id is None:
		return
	metadata = record.setdefault("metadata", {})
	# 1) Try local DB (no API call)
	try:
		conn = get_db()
		try:
			row = conn.execute("SELECT name FROM mods WHERE mod_id = ?", (mod_id,)).fetchone()
			if row and isinstance(row[0], str) and row[0].strip():
				metadata["mod_name"] = row[0].strip()
				return
		finally:
			try:
				conn.close()
			except Exception:
				pass
	except Exception:
		pass
	# 2) Fallback — quick Nexus API call
	try:
		from core.nexus import get_mod_info, get_api_key
		key = get_api_key()
		if key:
			game = nxm_request.game_domain or DEFAULT_GAME
			status, data = get_mod_info(key, game, mod_id)
			if status == 200 and isinstance(data, dict):
				name = data.get("name")
				if isinstance(name, str) and name.strip():
					metadata["mod_name"] = name.strip()
	except Exception:
		pass

@app.post("/api/nxm/handoff")
def submit_nxm_handoff(payload: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
	global _LAST_NXM_URL
	
	nxm_value: Optional[str] = None
	if payload is not None:
		nxm_value = payload.get("nxm")
	if not isinstance(nxm_value, str) or not nxm_value.strip():
		raise HTTPException(status_code=400, detail="nxm field is required")
	
	# DEBUG: Log the exact URL received
	logger.info("[NXM DEBUG] ===== RECEIVED NXM URL =====")
	logger.info("[NXM DEBUG] Full URL: %s", nxm_value)
	logger.info("[NXM DEBUG] URL length: %d", len(nxm_value))
	logger.info("[NXM DEBUG] Contains '?': %s", "?" in nxm_value)
	logger.info("[NXM DEBUG] Contains '&': %s", "&" in nxm_value)
	if "?" in nxm_value:
		query_part = nxm_value.split("?", 1)[1] if "?" in nxm_value else ""
		logger.info("[NXM DEBUG] Query string: %s", query_part)
	logger.info("[NXM DEBUG] =============================")
	
	# Store the last received NXM URL for testing/debugging
	_LAST_NXM_URL = {
		"url": nxm_value,
		"received_at": datetime.utcnow().isoformat() + "Z",
	}
	
	try:
		nxm_request = parse_nxm_uri(nxm_value)
		
		# Add parsed details to last NXM URL info
		_LAST_NXM_URL["parsed"] = {
			"game_domain": nxm_request.game_domain,
			"mod_id": nxm_request.mod_id,
			"file_id": nxm_request.file_id,
			"query_params": nxm_request.query,
			"has_key": bool(nxm_request.key),
			"has_expires": bool(nxm_request.expires),
			"has_user_id": bool(nxm_request.user_id),
			"is_collection": nxm_request.is_collection,
			"collection_slug": nxm_request.collection_slug,
			"collection_revision": nxm_request.collection_revision,
		}
		
		# Detect test URLs and skip handoff creation to prevent background processing
		# Test URLs use the fake credential "TEST_KEY_123" as the key parameter
		is_test_url = nxm_request.key == "TEST_KEY_123"
		if is_test_url:
			logger.info(
				"[nxm_handoff] test URL detected (key=TEST_KEY_123), skipping handoff creation"
			)
			return {
				"ok": True,
				"test_mode": True,
				"message": "Test URL received and parsed successfully (no handoff created)"
			}

		if nxm_request.is_collection and nxm_request.collection_slug:
			logger.info(f"[nxm_handoff] Received collection URL for slug {nxm_request.collection_slug}")
			revision_data = _fetch_collection_from_nexus(nxm_request.collection_slug, nxm_request.collection_revision)
			conn = get_db()
			try:
				cid = _upsert_collection(conn, revision_data, nxm_request.collection_slug)
				return {
					"ok": True,
					"message": f"Collection {nxm_request.collection_slug} imported successfully",
					"is_collection": True,
					"collection_id": cid
				}
			finally:
				try:
					conn.close()
				except Exception:
					pass
		
	except NXMParseError as exc:
		# Even if parsing fails, we still stored the raw URL
		if _LAST_NXM_URL:
			_LAST_NXM_URL["parse_error"] = str(exc)
		raise HTTPException(status_code=400, detail=str(exc))
	
	metadata = snapshot_metadata(nxm_request)
	record = register_handoff(nxm_request, metadata=metadata)

	# Eagerly resolve the mod name so the frontend can display it in the
	# download progress toaster instead of just "Mod #XXXX".
	_eager_resolve_mod_name(record, nxm_request)


	logger.info(
		"[nxm_handoff] received id=%s game=%s mod_id=%s file_id=%s query_params=%s",
		record["id"],
		nxm_request.game_domain,
		nxm_request.mod_id,
		nxm_request.file_id,
		nxm_request.query,
	)
	return {"ok": True, "handoff": serialize_handoff(record)}


@app.get("/api/nxm/handoff/{handoff_id}")
def get_nxm_handoff(handoff_id: str) -> Dict[str, Any]:
	if not handoff_id:
		raise HTTPException(status_code=400, detail="handoff_id is required")
	record = get_handoff_or_404(handoff_id)
	return {"ok": True, "handoff": serialize_handoff(record)}


@app.get("/api/nxm/last-received")
def get_last_nxm_url() -> Dict[str, Any]:
	"""Get the last NXM URL received by the backend for testing/debugging purposes."""
	if _LAST_NXM_URL is None:
		return {
			"ok": True,
			"last_url": None,
			"message": "No NXM URL has been received yet",
		}
	
	return {
		"ok": True,
		"last_url": _LAST_NXM_URL,
	}


@app.get("/api/nxm/handoffs")
def list_nxm_handoffs() -> Dict[str, Any]:
	# Filter out consumed handoffs to prevent reprocessing
	# Consumed handoffs are those that have been successfully ingested
	all_handoffs = list_handoffs()
	unconsumed = [rec for rec in all_handoffs if not rec.get("consumed", False)]
	ordered = sorted(
		unconsumed,
		key=lambda rec: rec.get("created_at") or 0,
		reverse=True,
	)
	return {
		"ok": True,
		"handoffs": [serialize_handoff(rec, include_metadata=True) for rec in ordered],
	}


@app.delete("/api/nxm/handoff/{handoff_id}")
def delete_nxm_handoff(handoff_id: str) -> Dict[str, Any]:
	if not handoff_id:
		raise HTTPException(status_code=400, detail="handoff_id is required")
	record = delete_handoff(handoff_id)
	return {"ok": True, "handoff": serialize_handoff(record, include_metadata=True)}


@app.post("/api/nxm/handoff/{handoff_id}/cancel")
def cancel_nxm_handoff(handoff_id: str) -> Dict[str, Any]:
	"""Signal an in-progress NXM download to stop.

	This sets a cancellation flag that the download loop checks every chunk.
	The partial file is deleted and the handoff record is removed so it never
	appears in the mod list.
	"""
	if not handoff_id:
		raise HTTPException(status_code=400, detail="handoff_id is required")

	# Add to the cancellation set — the download loop checks this every chunk
	with _CANCELLED_HANDOFFS_LOCK:
		_CANCELLED_HANDOFFS.add(handoff_id)

	# Update progress to reflect cancellation intent
	try:
		update_handoff_progress(
			handoff_id,
			stage="cancelling",
			message="Cancelling download…",
		)
	except Exception:
		pass  # Record may already be gone

	return {"ok": True, "cancelled": True, "handoff_id": handoff_id}


def _clear_handoff_failure_by_file_id(file_id: Optional[int]) -> None:
	"""Remove any handoff_failures row for a given Nexus file_id.
	
	Used when a duplicate-download detection confirms the file IS already present
	on disk, so we should NOT keep a "failed" record for that file_id.
	"""
	if file_id is None:
		return
	file_id_str = str(file_id).strip()
	conn = get_db()
	try:
		cur = conn.cursor()
		cur.execute("DELETE FROM handoff_failures WHERE file_id = ?", (file_id_str,))
		conn.commit()
		logger.info(f"[nxm_handoff] Cleared stale handoff_failure for file_id={file_id_str} (duplicate = already downloaded)")
	except Exception as exc:
		logger.debug(f"[nxm_handoff] _clear_handoff_failure_by_file_id: {exc}")
	finally:
		try:
			conn.close()
		except Exception:
			pass


def _normalize_game_domain(domain: Optional[str]) -> str:
	if not domain:
		return DEFAULT_GAME
	normalized = str(domain).strip().lower()
	if not normalized:
		return DEFAULT_GAME
	if normalized != DEFAULT_GAME:
		raise HTTPException(status_code=400, detail=f"Unsupported game domain for nxm handoff: {normalized}")
	return normalized


def _coerce_int(value: Any) -> Optional[int]:
	if isinstance(value, bool):
		return None
	if isinstance(value, int):
		return value
	if isinstance(value, float) and value.is_integer():
		return int(value)
	if isinstance(value, str):
		try:
			return int(value.strip())
		except (TypeError, ValueError):
			return None
	return None


def _collect_nexus_metadata_for_record(record: Dict[str, Any]) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
	metadata = record.setdefault("metadata", {})
	cached = metadata.get("collect_all")
	cached_ts = metadata.get("collect_all_timestamp")
	now = time.time()
	if isinstance(cached, dict) and isinstance(cached_ts, (int, float)) and now - cached_ts < 300:
		filtered = metadata.get("collect_all_filtered")
		if not isinstance(filtered, dict):
			prefs = _load_nexus_prefs_cached()
			filtered = filter_aggregate_payload(cached, prefs)
			metadata["collect_all_filtered"] = filtered
		request_data = record.get("request", {})
		game_domain = _normalize_game_domain(request_data.get("game"))
		return game_domain, cached, filtered
	key = get_api_key()
	if not key:
		raise HTTPException(status_code=400, detail="NEXUS_API_KEY not configured; cannot contact Nexus")
	request_data = record.get("request", {})
	mod_id = request_data.get("mod_id")
	if not isinstance(mod_id, int):
		raise HTTPException(status_code=400, detail="nxm handoff missing mod id")
	game_domain = _normalize_game_domain(request_data.get("game"))
	payload = collect_all_for_mod(key, game_domain, mod_id)
	prefs = _load_nexus_prefs_cached()
	filtered = filter_aggregate_payload(payload, prefs)
	metadata["collect_all"] = payload
	metadata["collect_all_timestamp"] = now
	metadata["collect_all_filtered"] = filtered
	return game_domain, payload, filtered


def _find_matching_handoff(
	mod_id: int,
	*,
	target_file_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
	"""Return the most recent handoff compatible with the given mod and file.

	Parameters
	----------
	mod_id: int
		The Nexus mod id we want to fulfill.
	target_file_id: Optional[int]
		Optionally restrict the search to handoffs that reference a specific file id.

	Returns
	-------
	Optional[Dict[str, Any]]
		The newest matching handoff record, or ``None`` if no compatible handoff exists.
	"""
	candidates: List[Dict[str, Any]] = []
	for record in list_handoffs():
		req = record.get("request") or {}
		req_mod_id = _coerce_int(req.get("mod_id"))
		if req_mod_id != mod_id:
			continue
		if target_file_id is not None:
			req_file_id = _coerce_int(req.get("file_id"))
			if req_file_id is not None and req_file_id != target_file_id:
				continue
		candidates.append(record)
	if not candidates:
		return None
	candidates.sort(key=lambda rec: rec.get("created_at") or 0, reverse=True)
	return candidates[0]


def _summarize_mod_files(files_payload: Any) -> List[Dict[str, Any]]:
	entries: List[Dict[str, Any]] = []
	if isinstance(files_payload, dict):
		candidate = files_payload.get("files")
		if isinstance(candidate, list):
			iterable = candidate
		else:
			iterable = []
	elif isinstance(files_payload, list):
		iterable = files_payload
	else:
		iterable = []
	for item in iterable:
		if not isinstance(item, dict):
			continue
		file_id = item.get("file_id")
		if file_id is None and isinstance(item.get("id"), (list, tuple)):
			try:
				file_id = int(item["id"][0])
			except Exception:
				file_id = None
		file_id = _coerce_int(file_id)
		if file_id is None:
			continue
		size_bytes = item.get("size_in_bytes")
		if size_bytes is None:
			size_val = item.get("size") or item.get("size_kb")
			size_bytes = None
			if isinstance(size_val, (int, float)):
				size_bytes = int(size_val * 1024) if size_val and not isinstance(size_val, bool) else int(size_val)
		uploaded_ts = _coerce_int(item.get("uploaded_timestamp"))
		entries.append(
			{
				"file_id": file_id,
				"name": item.get("name"),
				"version": item.get("version") or item.get("mod_version"),
				"category_id": item.get("category_id"),
				"category_name": item.get("category_name"),
				"is_primary": bool(item.get("is_primary")),
				"size_in_bytes": size_bytes,
				"file_name": item.get("file_name"),
				"uploaded_timestamp": uploaded_ts,
				"uploaded_time": item.get("uploaded_time"),
				"mod_version": item.get("mod_version"),
			}
		)
	return entries


def _select_file_entry(entries: List[Dict[str, Any]], requested_file_id: Optional[int]) -> Optional[Dict[str, Any]]:
	if requested_file_id is not None:
		for entry in entries:
			if entry.get("file_id") == requested_file_id:
				return entry
	if not entries:
		return None
	primaries = [e for e in entries if e.get("is_primary")]
	if primaries:
		primaries.sort(key=lambda e: e.get("uploaded_timestamp") or 0, reverse=True)
		return primaries[0]
	main_entries = [
		e
		for e in entries
		if (isinstance(e.get("category_name"), str) and e["category_name"].strip().lower() == "main")
		or (isinstance(e.get("category_id"), int) and e["category_id"] == 1)
	]
	if main_entries:
		main_entries.sort(key=lambda e: e.get("uploaded_timestamp") or 0, reverse=True)
		return main_entries[0]
	entries_sorted = sorted(entries, key=lambda e: e.get("uploaded_timestamp") or 0, reverse=True)
	return entries_sorted[0]


@app.get("/api/nxm/handoff/{handoff_id}/preview")
def preview_nxm_handoff(handoff_id: str) -> Dict[str, Any]:
	if not handoff_id:
		raise HTTPException(status_code=400, detail="handoff_id is required")
	record = get_handoff_or_404(handoff_id)
	game_domain, raw_metadata, filtered_metadata = _collect_nexus_metadata_for_record(record)
	files_summary = _summarize_mod_files(raw_metadata.get("files"))
	req = record.get("request", {})
	requested_file_id = _coerce_int(req.get("file_id"))
	selected_entry = _select_file_entry(files_summary, requested_file_id)
	mod_info = filtered_metadata.get("mod_info") if isinstance(filtered_metadata, dict) else None
	response: Dict[str, Any] = {
		"ok": True,
		"handoff": serialize_handoff(record),
		"game": game_domain,
		"mod_info": mod_info,
		"files": files_summary,
	}
	if selected_entry is not None:
		response["selected_file_id"] = selected_entry.get("file_id")
		response["selected_file"] = selected_entry
	return response



@app.post("/api/mods/{mod_id}/check-update")
def check_mod_update(mod_id: int) -> Dict[str, Any]:
	"""Refresh Nexus metadata for a mod and report whether updates are available."""
	conn = get_db()
	try:
		metadata_info = _sync_mod_metadata(conn, mod_id, None)
		rows = fetch_pak_version_status(conn, mod_id=mod_id, only_needs_update=False)
		pending: List[Dict[str, Any]] = []
		checked_downloads: Set[int] = set()
		for entry in rows:
			needs_update = bool(entry.get("needs_update"))
			local_download_id = entry.get("local_download_id")
			if isinstance(local_download_id, int):
				checked_downloads.add(local_download_id)
			if not needs_update:
				continue
			pending.append(
				{
					"pak_name": entry.get("pak_name"),
					"local_download_id": local_download_id,
					"local_version": entry.get("local_version"),
					"reference_version": entry.get("reference_version"),
					"reference_file_id": entry.get("reference_file_id"),
					"version_status": entry.get("version_status"),
					"display_version": entry.get("display_version"),
				}
			)

		result: Dict[str, Any] = {
			"ok": True,
			"mod_id": mod_id,
			"needs_update": bool(pending),
			"pending": pending,
			"checked_download_ids": sorted(checked_downloads),
		}
		if "metadata_warning" in metadata_info:
			result["metadata_warning"] = metadata_info["metadata_warning"]
		synced = metadata_info.get("synced_mod_id")
		if synced is not None:
			result["synced_mod_id"] = synced

		# Persist a record of the most recent update-check time so the
		# frontend can display an authoritative "Last Check" timestamp.
		try:
			from pathlib import Path as _Path
			_last_check_path = _Path(SETTINGS.data_dir) / "last_update_check.json"
			_last_iso = datetime.utcnow().isoformat() + "Z"
			try:
				_last_check_path.write_text(json.dumps({"last_check": _last_iso}), encoding="utf-8")
			except TypeError:
				# Python <3.11 Path.write_text doesn't accept encoding kw in some envs
				_last_check_path.write_text(json.dumps({"last_check": _last_iso}))
		except Exception:
			# Non-fatal: log and continue
			logging.getLogger("modmanager.api.checks").exception(
				"Failed to persist last update-check timestamp"
			)
		return result
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/nxm/handoff/{handoff_id}/ingest")
def ingest_nxm_handoff(handoff_id: str, payload: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
	import sys
	print(f"\n{'='*80}", file=sys.stderr)
	print(f"[INGEST START] Handoff ID: {handoff_id}", file=sys.stderr)
	print(f"[INGEST START] Payload: {payload}", file=sys.stderr)
	print(f"{'='*80}\n", file=sys.stderr)
	
	if not handoff_id:
		raise HTTPException(status_code=400, detail="handoff_id is required")
	
	# Circuit breaker: Check if this handoff should be skipped due to repeated failures
	should_skip, skip_reason = should_skip_handoff(handoff_id)
	if should_skip:
		logger.warning(f"[nxm_handoff] Skipping handoff {handoff_id}: {skip_reason}")
		raise HTTPException(status_code=429, detail=f"Too many failed attempts. {skip_reason}")
	
	print(f"[INGEST] Fetching handoff record...", file=sys.stderr)
	record = get_handoff_or_404(handoff_id)
	handoff_identifier = record.get("id") if isinstance(record.get("id"), str) else None
	print(f"[INGEST] Handoff record fetched: {handoff_identifier}", file=sys.stderr)
	
	# Early API key validation to prevent wasted processing
	print(f"[INGEST] Checking API key...", file=sys.stderr)
	api_key = get_api_key()
	if not api_key:
		error_msg = "NEXUS_API_KEY not configured. Please add your Nexus Mods API key in Settings."
		logger.error(f"[nxm_handoff] {error_msg}")
		if handoff_identifier:
			update_handoff_progress(
				handoff_identifier,
				stage="failed",
				error=error_msg,
				message=error_msg,
			)
			register_handoff_failure(handoff_identifier, error_msg)
		raise HTTPException(status_code=400, detail=error_msg)
	
	if handoff_identifier:
		update_handoff_progress(
			handoff_identifier,
			stage="preparing",
			message="Preparing download…",
			bytes_downloaded=0,
		)
	options = payload or {}
	requested_file_id = options.get("file_id")
	if requested_file_id is not None:
		requested_file_id = _coerce_int(requested_file_id)
	if options.get("desired_paks") is not None and not isinstance(options.get("desired_paks"), list):
		raise HTTPException(status_code=400, detail="desired_paks must be an array of strings when provided")
	deactivate_existing_opt = options.get("deactivate_existing")
	if deactivate_existing_opt is None:
		deactivate_existing = True
	elif isinstance(deactivate_existing_opt, bool):
		deactivate_existing = deactivate_existing_opt
	elif isinstance(deactivate_existing_opt, (int, float)):
		deactivate_existing = bool(deactivate_existing_opt)
	else:
		raise HTTPException(status_code=400, detail="deactivate_existing must be a boolean when provided")
	auto_activate = bool(options.get("activate", True))
	game_domain, raw_metadata, filtered_metadata = _collect_nexus_metadata_for_record(record)
	files_summary = _summarize_mod_files(raw_metadata.get("files"))
	if requested_file_id is None:
		req = record.get("request", {})
		req_file_id = _coerce_int(req.get("file_id"))
		requested_file_id = req_file_id
	selected_entry = _select_file_entry(files_summary, requested_file_id)
	if not selected_entry:
		error_msg = "Unable to resolve target file from Nexus metadata"
		if handoff_identifier:
			update_handoff_progress(
				handoff_identifier,
				stage="failed",
				error=error_msg,
				message=error_msg,
			)
			register_handoff_failure(handoff_identifier, error_msg)
		raise HTTPException(status_code=404, detail=error_msg)
	file_id = selected_entry["file_id"]
	req_data = record.get("request", {})
	mod_id = req_data.get("mod_id")
	if not isinstance(mod_id, int):
		error_msg = "nxm handoff missing mod id"
		if handoff_identifier:
			update_handoff_progress(
				handoff_identifier,
				stage="failed",
				error=error_msg,
				message="NXM handoff missing mod id",
			)
			register_handoff_failure(handoff_identifier, error_msg)
		raise HTTPException(status_code=400, detail=error_msg)
	logger.info(
		"[nxm_handoff] resolving mod_id=%s file_id=%s handoff=%s via nxm redirect", mod_id, file_id, record.get("id")
	)
	# 1. EARLY METADATA RESOLUTION
	# We clean the name and version BEFORE checking for duplicates or downloading.
	version = selected_entry.get("version") or selected_entry.get("mod_version") or ""
	remote_name = selected_entry.get("file_name") or selected_entry.get("name") or ""
	
	clean_name, clean_mod_id, clean_version = _resolve_mod_metadata(
		Path(selected_entry.get("file_name") or "unknown.zip"),
		provided_name=remote_name,
		provided_mod_id=mod_id,
		provided_version=version,
	)

	try:
		# EARLY DUPLICATE CHECK: Check if this mod+version already exists BEFORE downloading
		if clean_name and clean_version:
			conn = get_db()
			try:
				cur = conn.cursor()
				# UNIFIED EARLY DUPLICATE CHECK using clean metadata
				duplicate = _find_duplicate_download(cur, clean_name, clean_version, clean_mod_id)
				if duplicate:
					existing_id, existing_name, existing_version, existing_path = duplicate
					logger.info(
						f"[nxm_handoff] SKIPPING DOWNLOAD - duplicate found: '{clean_name}' v{clean_version} already exists (id={existing_id})"
					)

					# Mark handoff as consumed to prevent retries
					if handoff_identifier:
						update_handoff_progress(
							handoff_identifier,
							stage="complete",
							message=f"Already downloaded: {clean_name} v{clean_version}",
						)
						mark_handoff_consumed(handoff_identifier)
						# Clear any stale failure record so the collection frontend
						# no longer shows this file_id as "failed".
						try:
							_clear_handoff_failure_by_file_id(file_id)
						except Exception as _clr_err:
							logger.debug(f"[nxm_handoff] early-dupe clear failure: {_clr_err}")
					
					raise DuplicateDownloadError(
						existing_id,
						existing_name=existing_name,
						existing_version=existing_version,
						existing_path=existing_path,
						candidate_name=clean_name,
						candidate_version=clean_version,
					)

			finally:
				try:
					conn.close()
				except Exception:
					pass
		
		download_path, resolved_url = _download_archive_via_nxm(record, game_domain, file_id, desired_filename=remote_name)
		logger.info(
			"[nxm_handoff] download complete path=%s mod_id=%s file_id=%s", download_path, mod_id, file_id
		)
		
		# Unified metadata resolution from the downloaded file
		final_name, final_mod_id, final_version = _resolve_mod_metadata(
			download_path,
			provided_name=remote_name,
			provided_mod_id=mod_id,
			provided_version=version,
		)
		
		file_created_at_hint = _extract_created_at_hint(selected_entry)
		ingest_result = _ingest_resolved_download(
			download_path,
			name=final_name,
			mod_id=final_mod_id,
			version=final_version,
			source_url=resolved_url,
			metadata_snapshot=raw_metadata,
			filtered_metadata=filtered_metadata,
			created_at_hint=datetime.now(timezone.utc).isoformat(),
		)
	except DownloadCancelledError:
		# User explicitly cancelled — dismiss the handoff, clean up, return 499
		if handoff_identifier:
			update_handoff_progress(
				handoff_identifier,
				stage="cancelled",
				message="Cancelled by user",
			)
			mark_handoff_consumed(handoff_identifier)
		# Remove from the cancellation set now that we've handled it
		with _CANCELLED_HANDOFFS_LOCK:
			_CANCELLED_HANDOFFS.discard(handoff_id)
		raise HTTPException(status_code=499, detail="Download cancelled by user")

	except DuplicateDownloadError as exc:
		# A duplicate means the mod IS already downloaded — treat it as a success, not a failure.
		# Do NOT register this as a handoff failure (it's not an error; the file is already present).
		if handoff_identifier:
			# Ensure the progress shows "complete" (the early check may have already set this,
			# but the ingest path might have hit the same check a second time).
			update_handoff_progress(
				handoff_identifier,
				stage="complete",
				message=f"Already downloaded: {exc.existing_name or exc.candidate_name}",
			)
			mark_handoff_consumed(handoff_identifier)
			# Also clear any stale failure record for this file_id so the frontend
			# stops showing these as "failed" in the collection view.
			try:
				_clear_handoff_failure_by_file_id(file_id)
			except Exception as _clear_err:
				logger.debug(f"[nxm_handoff] Could not clear stale failure for file_id={file_id}: {_clear_err}")
		
		raise HTTPException(status_code=409, detail=_duplicate_detail_from_error(exc))

	except HTTPException:
		raise

	except Exception as e:
		# Graceful error handling for fatal errors
		import traceback
		import sys
		print(f"[ingest_debug] ERROR during ingestion: {e}", file=sys.stderr)
		traceback.print_exc(file=sys.stderr)
		
		if handoff_identifier:
			register_handoff_failure(handoff_identifier, str(e))
			update_handoff_progress(
				handoff_identifier,
				stage="failed",
				error=str(e),
				message="Ingestion Failed"
			)
			# Force consume handoff on fatal error to prevent infinite restart loops
			# If it failed this badly, retrying the same handoff is likely futile
			print(f"[ingest_debug] Marking handoff {handoff_identifier} as consumed due to fatal error", file=sys.stderr)
			mark_handoff_consumed(handoff_identifier)
			
		# Return 400 instead of 500 to ensure frontend receives the error detail
		# and to avoid partial CORS issues with 500s
		raise HTTPException(status_code=400, detail=f"Ingestion failed: {str(e)}")
	new_download_id = ingest_result.get("download_id")
	if not isinstance(new_download_id, int):
		raise HTTPException(status_code=500, detail="Ingestion completed but download id missing")
	conn = get_db()
	try:
		cur = conn.cursor()
		ctx = _snapshot_local_downloads(cur, mod_id)
	finally:
		try:
			conn.close()
		except Exception:
			pass
	mod_name = ctx.get("mod_name")
	if not mod_name:
		if isinstance(filtered_metadata, dict):
			info = filtered_metadata.get("mod_info")
			if isinstance(info, dict):
				name_val = info.get("name")
				if isinstance(name_val, str) and name_val.strip():
					mod_name = name_val.strip()
	contents = ingest_result.get("contents") or []
	if not isinstance(contents, list):
		contents = []
	contents_lookup = {str(c).lower(): str(c) for c in contents if isinstance(c, str)}

	def _normalize_list(values: Iterable[Any]) -> List[str]:
		resolved: List[str] = []
		for v in values:
			if isinstance(v, str) and v.strip():
				key = v.strip()
				match = contents_lookup.get(key.lower())
				if match:
					resolved.append(match)
		return resolved

	desired_active: List[str] = []
	if isinstance(options.get("desired_paks"), list) and options["desired_paks"]:
		desired_active = _normalize_list(options["desired_paks"])
	if not desired_active and ctx.get("active_union"):
		desired_active = _normalize_list(ctx["active_union"])
	if not desired_active:
		desired_active = [v for v in contents if isinstance(v, str) and v.lower().endswith(".pak")]
	if not desired_active and contents:
		desired_active = [contents[0]]

	activation_warning: Optional[str] = None
	activated_snapshot: Optional[List[str]] = None
	if auto_activate and desired_active:
		try:
			result = set_active_paks(new_download_id, {"active_paks": desired_active})
			activated_snapshot = result.get("active_paks") if isinstance(result, dict) else desired_active
		except HTTPException as e:
			activation_warning = str(e.detail)
		except Exception as e:
			activation_warning = str(e)

	deactivated_ids: List[int] = []
	deactivation_warnings: List[str] = []
	if deactivate_existing:
		for old_id in ctx.get("active_download_ids", []):
			if int(old_id) == new_download_id:
				continue
			try:
				set_active_paks(int(old_id), {"active_paks": []})
				deactivated_ids.append(int(old_id))
			except HTTPException as e:
				deactivation_warnings.append(f"{old_id}: {e.detail}")
			except Exception as e:
				deactivation_warnings.append(f"{old_id}: {e}")

	if handoff_identifier:
		try:
			final_size = download_path.stat().st_size if download_path.exists() else None
		except Exception:
			final_size = None
		update_handoff_progress(
			handoff_identifier,
			stage="complete",
			message="Mod downloaded successfully",
			bytes_downloaded=final_size or 0,
			bytes_total=final_size,
		)
	
	# Clear failure tracking on success
	if handoff_identifier:
		clear_handoff_failure(handoff_identifier)
	
	# Mark handoff as consumed instead of deleting
	# This allows frontend to skip reprocessing on reconnect
	mark_handoff_consumed(handoff_id)
	
	# Re-fetch the handoff to get the updated consumed state
	# This fixes a race condition where the response would contain stale data
	# with consumed=false, causing the frontend to reprocess on restart
	updated_record = get_handoff_or_404(handoff_id)
	
	response: Dict[str, Any] = {
		"ok": True,
		"handoff": serialize_handoff(updated_record),
		"mod_id": mod_id,
		"mod_name": mod_name,
		"file_id": file_id,
		"download_id": new_download_id,
		"download": ingest_result,
		"selected_file": selected_entry,
		"activated_paks": activated_snapshot or [],
		"activation_warning": activation_warning,
		"deactivated_download_ids": deactivated_ids,
		"deactivation_warnings": deactivation_warnings,
		"deactivated_existing": deactivate_existing,
		"desired_active_paks": desired_active,
		"needs_refresh": True,
		"handoff_consumed": True,
	}
	return response


def _extract_download_uri(payload: Any) -> Optional[str]:
	if isinstance(payload, dict):
		for key in ("URI", "uri", "url", "URL", "download_url"):
			val = payload.get(key)
			if isinstance(val, str) and val.strip():
				return val.strip()
		for key in ("download_links", "links", "mirrors", "download_link"):
			val = payload.get(key)
			if isinstance(val, list):
				for item in val:
					uri = _extract_download_uri(item)
					if uri:
						return uri
	elif isinstance(payload, list):
		for item in payload:
			uri = _extract_download_uri(item)
			if uri:
				return uri
	return None


def _snapshot_local_downloads(cur, mod_id: int) -> Dict[str, Any]:
	rows = cur.execute(
		"""
		SELECT id, name, version, contents, active_paks, path, created_at
		FROM local_downloads
		WHERE mod_id = ?
		ORDER BY created_at ASC
		""",
		(mod_id,),
	).fetchall()
	mod_row = cur.execute("SELECT name FROM mods WHERE mod_id = ?", (mod_id,)).fetchone()
	mod_name = mod_row[0] if mod_row else None
	active_union: set[str] = set()
	active_download_ids: List[int] = []
	local_versions_summary: List[Dict[str, Any]] = []
	local_version_strings: set[str] = set()
	best_local_key: Optional[str] = None
	for dl_id, name, version, contents_json, active_json, path_value, created_at in rows:
		try:
			contents = json.loads(contents_json) if contents_json else []
			if not isinstance(contents, list):
				contents = []
		except Exception:
			contents = []
		try:
			active_paks = json.loads(active_json) if active_json else []
			if not isinstance(active_paks, list):
				active_paks = []
		except Exception:
			active_paks = []
		if active_paks:
			active_download_ids.append(int(dl_id))
			for p in active_paks:
				if isinstance(p, str) and p.strip():
					active_union.add(p.strip())
		version_str = (version or "").strip()
		local_version_strings.add(version_str)
		vkey = make_version_key(version_str)[0]
		if vkey and (best_local_key is None or vkey > best_local_key):
			best_local_key = vkey
		local_versions_summary.append(
			{
				"download_id": dl_id,
				"name": name,
				"version": version_str,
				"created_at": created_at,
				"active_paks": active_paks,
				"contents": contents,
				"path": path_value,
			}
		)
	return {
		"found": bool(rows),
		"mod_name": mod_name,
		"active_union": active_union,
		"active_download_ids": active_download_ids,
		"local_versions_summary": local_versions_summary,
		"local_version_strings": local_version_strings,
		"best_local_key": best_local_key,
	}


def _complete_update_from_handoff(
	handoff_id: str,
	*,
	mod_id: int,
	mod_name: Optional[str],
	requested_file_id: Optional[int],
	auto_activate: bool,
	desired_paks_opt: Optional[List[Any]],
	preflight_metadata: Dict[str, Any],
	fallback_latest_version: str,
	fallback_file_id: int,
	fallback_uploaded_at: Any,
) -> Dict[str, Any]:
	ingest_options: Dict[str, Any] = {"activate": auto_activate}
	if requested_file_id is not None:
		ingest_options["file_id"] = requested_file_id
	if desired_paks_opt is not None:
		ingest_options["desired_paks"] = desired_paks_opt
	ingest_response = ingest_nxm_handoff(handoff_id, ingest_options)
	new_download_id = ingest_response.get("download_id")
	if not isinstance(new_download_id, int):
		raise HTTPException(status_code=500, detail="Ingestion completed but download id missing")
	try:
		conn = get_db()
		cur = conn.cursor()
		post_ctx = _snapshot_local_downloads(cur, mod_id)
	finally:
		try:
			conn.close()
		except Exception:
			pass
	mod_name_resolved = post_ctx.get("mod_name") or ingest_response.get("mod_name") or mod_name
	local_versions_summary = post_ctx.get("local_versions_summary") or []
	download_payload = ingest_response.get("download") or {}
	selected_file = ingest_response.get("selected_file") or {}
	version_resolved = (
		selected_file.get("version")
		or selected_file.get("mod_version")
		or download_payload.get("version")
		or fallback_latest_version
	)
	uploaded_at_resolved = (
		selected_file.get("uploaded_time")
		or selected_file.get("uploaded_timestamp")
		or fallback_uploaded_at
	)
	file_id_resolved = selected_file.get("file_id") or fallback_file_id
	desired_active_paks = ingest_response.get("desired_active_paks")
	if not isinstance(desired_active_paks, list) or not desired_active_paks:
		desired_active_paks = []
		if isinstance(desired_paks_opt, list):
			desired_active_paks = [str(v) for v in desired_paks_opt if isinstance(v, str) and v.strip()]
	response: Dict[str, Any] = {
		"ok": True,
		"mod_id": mod_id,
		"mod_name": mod_name_resolved,
		"latest_version": version_resolved,
		"latest_file_id": file_id_resolved,
		"latest_uploaded_at": uploaded_at_resolved,
		"download_id": new_download_id,
		"download": download_payload,
		"activated_paks": ingest_response.get("activated_paks") or [],
		"activation_warning": ingest_response.get("activation_warning"),
		"deactivated_download_ids": ingest_response.get("deactivated_download_ids") or [],
		"deactivation_warnings": ingest_response.get("deactivation_warnings") or [],
		"preflight_metadata": preflight_metadata,
		"local_versions": local_versions_summary,
		"desired_active_paks": desired_active_paks,
		"needs_refresh": True,
		"handoff_consumed": ingest_response.get("handoff_consumed", True),
	}
	handoff_serialized = ingest_response.get("handoff")
	if handoff_serialized:
		response["handoff"] = handoff_serialized
	if selected_file:
		response["selected_file"] = selected_file
	logger.info(
		"[update_mod] success via nxm handoff mod_id=%s download_id=%s activated=%d",
		mod_id,
		new_download_id,
		len(response.get("activated_paks") or []),
	)
	return response


@app.post("/api/mods/{mod_id}/update")
def update_mod(mod_id: int, payload: Optional[Dict[str, Any]] = Body(default=None)) -> Dict[str, Any]:
	"""Download the latest Nexus file for a mod, ingest it, and activate it while deactivating older versions."""
	options = payload or {}
	logger.info("[update_mod] request mod_id=%s payload_keys=%s", mod_id, list(options.keys()))
	requested_file_id = options.get("file_id")
	if requested_file_id is not None:
		try:
			requested_file_id = int(requested_file_id)
		except Exception:
			logger.warning("[update_mod] invalid file_id mod_id=%s value=%r", mod_id, requested_file_id)
			raise HTTPException(status_code=400, detail="file_id must be numeric")
	auto_activate = bool(options.get("activate", True))
	desired_paks_opt = options.get("desired_paks")
	if desired_paks_opt is not None and not isinstance(desired_paks_opt, list):
		logger.warning("[update_mod] desired_paks not array mod_id=%s type=%s", mod_id, type(desired_paks_opt).__name__)
		raise HTTPException(status_code=400, detail="desired_paks must be an array of strings")
	handoff_id_raw = options.get("handoff_id")
	handoff_id: Optional[str] = None
	if handoff_id_raw is not None:
		if isinstance(handoff_id_raw, str) and handoff_id_raw.strip():
			handoff_id = handoff_id_raw.strip()
		else:
			logger.warning("[update_mod] invalid handoff_id mod_id=%s value=%r", mod_id, handoff_id_raw)
			raise HTTPException(status_code=400, detail="handoff_id must be a non-empty string when provided")

	conn = get_db()
	try:
		cur = conn.cursor()
		ctx = _snapshot_local_downloads(cur, mod_id)
		if not ctx["found"]:
			logger.error("[update_mod] no local downloads mod_id=%s", mod_id)
			raise HTTPException(status_code=404, detail="No local downloads registered for this mod")
		logger.info("[update_mod] found %d local downloads for mod_id=%s", len(ctx["local_versions_summary"]), mod_id)
		mod_name = ctx["mod_name"]
		active_union = ctx["active_union"]
		active_download_ids = ctx["active_download_ids"]
		local_versions_summary = ctx["local_versions_summary"]
		local_version_strings = ctx["local_version_strings"]
		best_local_key = ctx["best_local_key"]

		related_versions = sorted([s for s in local_version_strings if s])
		preflight_metadata = _sync_mod_metadata(conn, mod_id, mod_name)
		if requested_file_id is not None:
			latest = get_file_by_id(conn, mod_id, requested_file_id)
		else:
			latest = get_latest_file_by_version(conn, mod_id)
	finally:
		try:
			conn.close()
		except Exception:
			pass

	if not latest or latest.get("file_id") is None:
		logger.error("[update_mod] no remote files mod_id=%s latest=%r", mod_id, latest)
		raise HTTPException(status_code=404, detail="No remote files available for this mod")
	latest_file_id = int(requested_file_id or latest.get("file_id"))
	latest_version = (latest.get("file_version") or "").strip()
	latest_uploaded_at = latest.get("uploaded_at") or latest.get("latest_uploaded_at")
	latest_version_key = latest.get("version_key") or latest.get("latest_version_key")
	if not latest_version:
		logger.error("[update_mod] missing latest version mod_id=%s latest=%r", mod_id, latest)
		raise HTTPException(status_code=400, detail="Latest Nexus file is missing a version string")
	if not latest_version_key:
		latest_version_key = make_version_key(latest_version)[0]

	already_installed = False
	
	# If a specific file was requested, we bypass the global heuristic checks
	# because the frontend already verified this specific variant needs an update.
	# Global checks would falsely flag variants as updated if another variant has a higher version.
	if requested_file_id is None:
		for local_v in local_version_strings:
			if versions_equivalent(local_v, latest_version):
				already_installed = True
				break
		if not already_installed and latest_version_key and best_local_key and latest_version_key <= best_local_key:
			already_installed = True

	if already_installed and not options.get("force", False):
		logger.info(
			"[update_mod] already on latest mod_id=%s latest=%s local_versions=%s",
			mod_id,
			latest_version,
			related_versions,
		)
		return {
			"ok": True,
			"already_latest": True,
			"mod_id": mod_id,
			"mod_name": mod_name,
			"latest_version": latest_version,
			"latest_file_id": latest_file_id,
			"latest_uploaded_at": latest_uploaded_at,
			"preflight_metadata": preflight_metadata,
			"local_versions": local_versions_summary,
		}

	allow_direct_api = _allow_direct_api_downloads()

	if handoff_id:
		return _complete_update_from_handoff(
			handoff_id,
			mod_id=mod_id,
			mod_name=mod_name,
			requested_file_id=requested_file_id,
			auto_activate=auto_activate,
			desired_paks_opt=desired_paks_opt,
			preflight_metadata=preflight_metadata,
			fallback_latest_version=latest_version,
			fallback_file_id=latest_file_id,
			fallback_uploaded_at=latest_uploaded_at,
		)

	matching_handoff = _find_matching_handoff(
		mod_id,
		target_file_id=requested_file_id or latest_file_id,
	)
	if matching_handoff and isinstance(matching_handoff.get("id"), str):
		record_id = matching_handoff["id"]
		logger.info(
			"[update_mod] auto-consuming nxm handoff mod_id=%s file_id=%s handoff=%s",
			mod_id,
			requested_file_id or latest_file_id,
			record_id,
		)
		return _complete_update_from_handoff(
			record_id,
			mod_id=mod_id,
			mod_name=mod_name,
			requested_file_id=requested_file_id,
			auto_activate=auto_activate,
			desired_paks_opt=desired_paks_opt,
			preflight_metadata=preflight_metadata,
			fallback_latest_version=latest_version,
			fallback_file_id=latest_file_id,
			fallback_uploaded_at=latest_uploaded_at,
		)

	if not allow_direct_api:
		detail = _nxm_required_detail(
			mod_id,
			latest_file_id,
			mod_name=mod_name,
			latest_version=latest_version,
			uploaded_at=latest_uploaded_at,
		)
		raise HTTPException(status_code=428, detail=detail)

	api_key = get_api_key()
	if not api_key:
		logger.error("[update_mod] missing API key for direct download mod_id=%s", mod_id)
		raise HTTPException(
			status_code=400,
			detail=(
				"NEXUS_API_KEY not configured; direct Nexus API downloads are disabled. "
				"Configure a Nexus API key or trigger an nxm handoff via 'Mod Manager Download'."
			),
		)
	status, download_payload = get_mod_file_download_link(api_key, DEFAULT_GAME, mod_id, latest_file_id)
	logger.info("[update_mod] download link status=%s mod_id=%s file_id=%s", status, mod_id, latest_file_id)
	if status != 200:
		error_detail = download_payload if isinstance(download_payload, dict) else {"detail": download_payload}
		detail_msg = None
		if isinstance(error_detail, dict):
			body = error_detail.get("body") if isinstance(error_detail.get("body"), (dict, str)) else None
			if isinstance(body, dict):
				body_msg = body.get("message") or body.get("detail")
				if isinstance(body_msg, str):
					detail_msg = body_msg.strip()
			elif isinstance(body, str):
				detail_msg = body.strip()
		if not detail_msg and isinstance(error_detail, dict):
			msg = error_detail.get("message") or error_detail.get("detail")
			if isinstance(msg, str):
				detail_msg = msg.strip()
		tail_msg = detail_msg or f"Failed to obtain download link (status {status})"
		if status == 403:
			detail = _nxm_required_detail(
				mod_id,
				latest_file_id,
				mod_name=mod_name,
				latest_version=latest_version,
				uploaded_at=latest_uploaded_at,
			)
			if detail_msg:
				detail["message"] = (
					f"{detail_msg} Use 'Mod Manager Download' on Nexus Mods to continue without a premium API key."
				)
			raise HTTPException(status_code=428, detail=detail)
		logger.error(
			"[update_mod] download link failure mod_id=%s status=%s file_id=%s payload=%r",
			mod_id,
			status,
			latest_file_id,
			error_detail,
		)
		raise HTTPException(status_code=status or 502, detail=tail_msg)
	download_url = _extract_download_uri(download_payload)
	if not download_url:
		logger.error(
			"[update_mod] missing download URL mod_id=%s file_id=%s payload=%r",
			mod_id,
			latest_file_id,
			download_payload,
		)
		raise HTTPException(status_code=502, detail="Nexus download link missing from API response")

	logger.info("[update_mod] downloading mod_id=%s file_id=%s", mod_id, latest_file_id)
	download_path = _download_remote_archive(download_url, force=True)
	logger.info("[update_mod] download complete mod_id=%s path=%s", mod_id, download_path)
	remote_file_name = latest.get("file_name") or Path(download_path).name
	safe_remote_name = _safe_filename(remote_file_name) or download_path.name
	if safe_remote_name:
		target_path = download_path.with_name(safe_remote_name)
		if target_path.exists() and target_path != download_path:
			stem = target_path.stem
			suffix = target_path.suffix
			counter = 1
			while target_path.exists():
				target_path = target_path.with_name(f"{stem}-{counter}{suffix}")
				counter += 1
		if target_path != download_path:
			try:
				download_path.rename(target_path)
				download_path = target_path
			except Exception:
				pass
		else:
			target_path = download_path
			download_path = target_path

	# Parse the filename to get a clean display name (strip mod_id/version suffix)
	parsed_update_name, _, _ = parse_mod_filename(safe_remote_name)
	try:
		ingest_result = _ingest_resolved_download(
			download_path,
			name=parsed_update_name or safe_remote_name,
			mod_id=mod_id,
			version=latest_version,
			source_url=download_url,
			created_at_hint=datetime.now(timezone.utc).isoformat(),
		)
	except DuplicateDownloadError as exc:
		raise HTTPException(status_code=409, detail=_duplicate_detail_from_error(exc))
	new_download_id = ingest_result.get("download_id")
	if not isinstance(new_download_id, int):
		raise HTTPException(status_code=500, detail="Ingestion completed but download id missing")
	contents = ingest_result.get("contents") or []
	if not isinstance(contents, list):
		contents = []
	contents_lookup = {str(c).lower(): str(c) for c in contents if isinstance(c, str)}

	def _normalize_list(values: Iterable[Any]) -> List[str]:
		out: List[str] = []
		for v in values:
			if isinstance(v, str) and v.strip():
				key = v.strip()
				match = contents_lookup.get(key.lower())
				if match:
					out.append(match)
		return out

	desired_active: List[str] = []
	if isinstance(desired_paks_opt, list) and desired_paks_opt:
		desired_active = _normalize_list(desired_paks_opt)
	if not desired_active and active_union:
		desired_active = _normalize_list(active_union)
	if not desired_active:
		desired_active = [v for v in contents if isinstance(v, str) and v.lower().endswith(".pak")]
	if not desired_active and contents:
		desired_active = [contents[0]]

	activation_warning: Optional[str] = None
	activated_snapshot: Optional[List[str]] = None
	if auto_activate and desired_active:
		try:
			result = set_active_paks(new_download_id, {"active_paks": desired_active})
			activated_snapshot = result.get("active_paks") if isinstance(result, dict) else desired_active
		except HTTPException as e:
			activation_warning = str(e.detail)
		except Exception as e:  # pragma: no cover - safety net
			activation_warning = str(e)

	deactivated_ids: List[int] = []
	deactivation_warnings: List[str] = []
	for old_id in active_download_ids:
		if int(old_id) == new_download_id:
			continue
		try:
			set_active_paks(int(old_id), {"active_paks": []})
			deactivated_ids.append(int(old_id))
		except HTTPException as e:
			deactivation_warnings.append(f"{old_id}: {e.detail}")
		except Exception as e:  # pragma: no cover - safety net
			deactivation_warnings.append(f"{old_id}: {e}")

	response: Dict[str, Any] = {
		"ok": True,
		"mod_id": mod_id,
		"mod_name": mod_name,
		"latest_version": latest_version,
		"latest_file_id": latest_file_id,
		"latest_uploaded_at": latest_uploaded_at,
		"download_id": new_download_id,
		"download": ingest_result,
		"activated_paks": activated_snapshot or [],
		"activation_warning": activation_warning,
		"deactivated_download_ids": deactivated_ids,
		"deactivation_warnings": deactivation_warnings,
		"preflight_metadata": preflight_metadata,
		"local_versions": local_versions_summary,
		"desired_active_paks": desired_active,
		"needs_refresh": True,
	}
	logger.info(
		"[update_mod] success mod_id=%s download_id=%s activated=%d deactivated=%d activation_warning=%s deactivation_warnings=%d",
		mod_id,
		new_download_id,
		len(response.get("activated_paks", []) or []),
		len(deactivated_ids),
		bool(activation_warning),
		len(deactivation_warnings),
	)
	return response


if _HAS_MULTIPART:
	@app.post("/api/mods/upload")
	async def upload_mod_file(file: UploadFile = File(...)) -> Dict[str, Any]:
		"""Accept an uploaded mod archive/pak and store it under the downloads root.
		Returns the absolute path that can be supplied to /api/mods/add.
		"""
		if not file or not file.filename:
			raise HTTPException(status_code=400, detail="Uploaded file is missing a filename")
		downloads_root = _downloads_root_from_env()
		_ensure_dir(downloads_root)
		safe_name = _safe_filename(file.filename)
		if not safe_name:
			safe_name = "mod"
		dest_path = _unique_destination(downloads_root, safe_name)
		if dest_path.exists():
			try:
				relative_existing = str(dest_path.relative_to(downloads_root))
			except ValueError:
				relative_existing = dest_path.name
			return {
				"ok": True,
				"already_existed": True,
				"path": str(dest_path.resolve()),
				"filename": dest_path.name,
				"size": dest_path.stat().st_size if dest_path.exists() else 0,
				"relative_path": relative_existing,
				"downloads_root": str(downloads_root),
			}
		size = 0
		try:
			with dest_path.open("wb") as out:
				while True:
					chunk = await file.read(UPLOAD_CHUNK_SIZE)
					if not chunk:
						break
					out.write(chunk)
					size += len(chunk)
		except Exception as e:
			if dest_path.exists():
				try:
					dest_path.unlink()
				except Exception:
					pass
			raise HTTPException(status_code=500, detail=f"Failed to save upload: {e}")
		finally:
			try:
				await file.close()
			except Exception:
				pass
		if size == 0 and dest_path.exists():
			try:
				dest_path.unlink()
			except Exception:
				pass
			raise HTTPException(status_code=400, detail="Uploaded file was empty")
		try:
			relative = str(dest_path.relative_to(downloads_root))
		except ValueError:
			relative = dest_path.name
		return {
			"ok": True,
			"path": str(dest_path.resolve()),
			"filename": dest_path.name,
			"size": size,
			"relative_path": relative,
			"downloads_root": str(downloads_root),
		}
else:
	@app.post("/api/mods/upload")
	async def upload_mod_file() -> Dict[str, Any]:
		"""Fallback upload endpoint when python-multipart is unavailable."""
		raise HTTPException(
			status_code=503,
			detail="File upload support requires the optional dependency 'python-multipart'. Install it with 'pip install python-multipart' or provide a local path/URL directly to /api/mods/add.",
		)


class CopyToDownloadsRequest(BaseModel):
	"""Request body for copying a file to the downloads folder."""
	source_path: str


@app.post("/api/mods/copy-to-downloads")
def copy_to_downloads(request: CopyToDownloadsRequest) -> Dict[str, Any]:
	"""Copy a file from an external path to the downloads folder.
	
	This endpoint is used by Tauri drag-and-drop to copy dropped files
	to the managed downloads location before ingestion.
	"""
	source = Path(request.source_path)
	if not source.exists():
		raise HTTPException(status_code=404, detail=f"Source file not found: {request.source_path}")
	if not source.is_file():
		raise HTTPException(status_code=400, detail="Source path must be a file, not a directory")
	
	downloads_root = _downloads_root_from_env()
	_ensure_dir(downloads_root)
	
	# Sanitize filename
	safe_name = _safe_filename(source.name)
	if not safe_name:
		safe_name = "mod" + source.suffix
	
	dest_path = downloads_root / safe_name
	
	# PRE-COPY DUPLICATE CHECK:
	# If a file with the same name already exists in the downloads folder,
	# check if it's already ingested. If so, return the existing path instead
	# of creating a renamed copy (e.g. ModName-1.zip) which bypasses dupe detection.
	if dest_path.exists():
		candidate_name, candidate_mod_id, candidate_version = _resolve_mod_metadata(
			dest_path,
			provided_name=None,
			provided_mod_id=None,
			provided_version=None,
		)
		if candidate_name:
			conn = get_db()
			try:
				cur = conn.cursor()
				duplicate = _find_duplicate_download(cur, candidate_name, candidate_version, candidate_mod_id)
				if duplicate is None:
					# Also check by exact path
					existing_by_path = cur.execute(
						"SELECT id FROM local_downloads WHERE path = ?",
						(normalize_download_path(dest_path),)
					).fetchone()
					if existing_by_path:
						duplicate = (existing_by_path[0], candidate_name, candidate_version, normalize_download_path(dest_path))
			finally:
				try:
					conn.close()
				except Exception:
					pass
			
			if duplicate:
				# File already exists in DB — skip copy, return existing path
				# so addMod will correctly raise a 409 DuplicateDownloadError
				logger.info(f"[copy_to_downloads] Skipping copy — duplicate already in DB: {dest_path}")
				size = dest_path.stat().st_size
				try:
					relative = str(dest_path.relative_to(downloads_root))
				except ValueError:
					relative = dest_path.name
				return {
					"ok": True,
					"path": str(dest_path.resolve()),
					"filename": dest_path.name,
					"size": size,
					"relative_path": relative,
					"downloads_root": str(downloads_root),
				}
		
		# Not a duplicate — add counter suffix so we don't overwrite
		stem = dest_path.stem
		suffix = dest_path.suffix
		counter = 1
		while dest_path.exists():
			dest_path = downloads_root / f"{stem}-{counter}{suffix}"
			counter += 1
	
	try:
		shutil.copy2(source, dest_path)
	except Exception as e:
		logger.error(f"[copy_to_downloads] Failed to copy file: {e}")
		raise HTTPException(status_code=500, detail=f"Failed to copy file: {e}")
	
	size = dest_path.stat().st_size
	try:
		relative = str(dest_path.relative_to(downloads_root))
	except ValueError:
		relative = dest_path.name
	
	logger.info(f"[copy_to_downloads] Copied {source} to {dest_path} ({size} bytes)")
	return {
		"ok": True,
		"path": str(dest_path.resolve()),
		"filename": dest_path.name,
		"size": size,
		"relative_path": relative,
		"downloads_root": str(downloads_root),
	}



@app.post("/api/refresh/conflicts")
def refresh_conflicts() -> Dict[str, Any]:
	"""Rebuild conflict materialization tables.
	
	Gracefully handles empty/new databases by ensuring schema is ready first.
	"""
	conn = get_db()
	try:
		init_schema(conn)
		
		# Check if we have any pak_assets data to work with
		cursor = conn.cursor()
		pak_count = cursor.execute("SELECT COUNT(*) FROM pak_assets").fetchone()[0]
		
		if pak_count == 0:
			logger.info("No pak_assets data yet - skipping conflict rebuild")
			return {
				"ok": True,
				"results": {},
				"message": "No data to process yet - database needs bootstrapping"
			}
		
		res = _safe_rebuild_conflicts(
			conn,
			active_only=None,
			purpose="manual_refresh_conflicts",
			raise_on_error=True,
		) or {}
		return {"ok": True, "results": res}
	except Exception as e:
		logger.error(f"Error refreshing conflicts: {e}")
		return {
			"ok": False,
			"error": str(e),
			"message": "Failed to refresh conflicts - database may need bootstrapping"
		}
	finally:
		try:
			conn.close()
		except Exception:
			pass


# Optional: run with uvicorn if executed directly
if __name__ == "__main__":
	import uvicorn
	uvicorn.run("core.api.server:app", host="127.0.0.1", port=8000, reload=True)


# Mods endpoints
@app.get("/api/mods")
def list_mods(limit: int = 100) -> List[Dict[str, Any]]:
	conn = get_db()
	cur = conn.cursor()
	rows = cur.execute(
		"""
		SELECT m.mod_id,
			   m.name,
			   m.author,
			   m.version,
			   m.picture_url,
			   COALESCE(vc.active_conflicting_assets, 0) AS active_conflicting_assets,
			   COALESCE(vc.active_opposing_mods, 0) AS active_opposing_mods
		FROM mods m
		LEFT JOIN v_mod_conflicts_active vc ON vc.mod_id = m.mod_id
		ORDER BY m.name COLLATE NOCASE
		LIMIT ?
		""",
		(limit,),
	).fetchall()
	out: List[Dict[str, Any]] = []
	for r in rows:
		out.append(
			{
				"mod_id": r[0],
				"name": r[1],
				"author": r[2],
				"version": r[3],
				"icon": r[4],
				"active_conflicting_assets": r[5],
				"active_opposing_mods": r[6],
			}
		)
	try:
		return out
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/pak-version-status")
def get_pak_version_status_endpoint(
	mod_id: Optional[int] = None,
	download_ids: Optional[str] = None,
	only_needs_update: bool = False,
) -> List[Dict[str, Any]]:
	conn = get_db()
	try:
		ids: Set[int] = set()
		if download_ids:
			for token in re.split(r"[,\s]+", str(download_ids)):
				if not token:
					continue
				try:
					value = int(token)
				except (TypeError, ValueError):
					continue
				if value >= 0:
					ids.add(value)
		filtered_ids = sorted(ids)
		rows = fetch_pak_version_status(
			conn,
			only_needs_update=only_needs_update,
			mod_id=mod_id,
			download_ids=filtered_ids if filtered_ids else None,
		)
		return rows
	finally:
		try:
			conn.close()
		except Exception:
			pass


def _downscale_base64_image(base64_str: str, max_size: int = 400) -> str:
	"""Downscale a base64 encoded image to reduce transfer size."""
	if not base64_str:
		return base64_str
	try:
		from PIL import Image
		import io
		import base64
		
		# Decode
		img_data = base64.b64decode(base64_str)
		img = Image.open(io.BytesIO(img_data))
		
		# Skip if already small
		if max(img.width, img.height) <= max_size:
			return base64_str
			
		# Resize preserving aspect ratio
		img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
		
		# Encode back to JPEG for maximum compression
		output = io.BytesIO()
		# Convert to RGB if needed (for JPEG)
		if img.mode in ("RGBA", "P"):
			img = img.convert("RGB")
		img.save(output, format="JPEG", quality=85, optimize=True)
		return base64.b64encode(output.getvalue()).decode('utf-8')
	except Exception as e:
		import logging
		logging.getLogger("modmanager.api").warning(f"Failed to downscale image: {e}")
		return base64_str


@app.get("/api/mods/custom-images-preview")

def get_custom_images_preview(mod_ids: str = Query(..., description="Comma-separated mod IDs")) -> Dict[str, Any]:
	"""
	Returns first custom image (base64) for each mod_id provided.
	Optimized for bulk retrieval when loading mod list.
	"""
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		# Parse mod_ids
		try:
			parsed_ids = [int(x.strip()) for x in mod_ids.split(",") if x.strip()]
		except ValueError:
			raise HTTPException(status_code=400, detail="Invalid mod_ids format")
		
		if not parsed_ids:
			return {"ok": True, "images": {}}
		
		cur = conn.cursor()
		
		# Fetch first custom image for each mod_id
		# Use a placeholder string repeated for each ID
		placeholders = ",".join("?" * len(parsed_ids))
		query = f"""
			SELECT mod_id, image_data, mime_type
			FROM mod_custom_images
			WHERE mod_id IN ({placeholders})
			  AND image_data IS NOT NULL
			GROUP BY mod_id
			HAVING id = MIN(id)
		"""
		
		rows = cur.execute(query, parsed_ids).fetchall()
		
		# Build result map
		result = {}
		for mod_id, image_data, mime_type in rows:
			if image_data:
				# Downscale for preview to save bandwidth
				downscaled = _downscale_base64_image(image_data, max_size=400)
				# Return as data URL (Force image/jpeg as we convert during downscale)
				result[str(mod_id)] = f"data:image/jpeg;base64,{downscaled}"
		
		logger.info(f"[get_custom_images_preview] Fetched {len(result)} custom images for {len(parsed_ids)} mods")
		return {"ok": True, "images": result}
		
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"[get_custom_images_preview] Error: {e}")
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/mods/{mod_id}")
def get_mod_details(mod_id: int, response: Response) -> Dict[str, Any]:
	# Disable caching for dynamic content
	response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
	response.headers["Pragma"] = "no-cache"
	response.headers["Expires"] = "0"
	
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	
	# Check if mod exists, if not and it's a synthetic ID, create placeholder
	cur = conn.cursor()
	mod_exists = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (mod_id,)).fetchone()
	
	if not mod_exists and mod_id < 0:
		# Synthetic ID for local mod - create placeholder
		local_download_id = -mod_id
		dl_row = cur.execute("SELECT name FROM local_downloads WHERE id = ?", (local_download_id,)).fetchone()
		
		if dl_row:
			mod_name = dl_row[0] or f"Local Mod {local_download_id}"
			logger.info(f"[get_mod_details] Creating placeholder mod for synthetic mod_id={mod_id}, download_id={local_download_id}")
			
			upsert_mod_info(
				conn,
				game=DEFAULT_GAME,
				mod_id=mod_id,
				mod_info_status=0,
				mod_info={
					"name": mod_name,
					"summary": "Local mod (auto-generated)",
					"description": "Auto-generated placeholder for local mod.",
					"author": "Local",
					"status": "plaintext",
					"category_id": 1,
				}
			)
			# Fetch the newly created mod
			data = mod_with_local_and_latest(conn, mod_id)
		else:
			raise HTTPException(status_code=404, detail=f"Local download {local_download_id} not found")
	else:
		data = mod_with_local_and_latest(conn, mod_id)
	
	if not data or not data.get("mod"):
		raise HTTPException(status_code=404, detail="Mod not found")
	# Ensure description field is exposed as HTML (merged summary+description from storage if present)
	try:
		if data.get("mod"):
			m = data["mod"]
			# If DB has description_html, surface it as 'description' for frontend
			desc_html = m.get("description_html") if isinstance(m, dict) else None
			logger.info(f"[get_mod_details] mod_id={mod_id}, has description_html: {bool(desc_html)}, length: {len(desc_html) if desc_html else 0}")
			if desc_html:
				# Inject a presentation field 'description'
				m["description"] = desc_html
				data["mod"] = m
			if isinstance(m, dict):
				desc_bbcode = m.get("description_bbcode")
				if desc_bbcode:
					m["description_bbcode"] = desc_bbcode
			else:
				logger.warning(f"[get_mod_details] mod_id={mod_id}, no description_html in DB. Keys: {list(m.keys())}")
	except Exception as e:
		logger.error(f"[get_mod_details] Error processing description: {e}")
		pass
	mod_row = data.get("mod") if isinstance(data, dict) else None
	if isinstance(mod_row, dict):
		member_id = _extract_member_id(mod_row.get("author_member_id"))
		profile_url = mod_row.get("author_profile_url")
		avatar_url = _author_avatar_url(member_id, profile_url)
		mod_row["author_member_id"] = member_id
		if profile_url is not None:
			mod_row["author_profile_url"] = profile_url
		if avatar_url:
			mod_row["author_avatar_url"] = avatar_url
		data["mod"] = mod_row
	# Also include active conflict badge counts if present
	cur = conn.cursor()
	try:
		vc = cur.execute(
			"SELECT active_conflicting_assets, active_opposing_mods FROM v_mod_conflicts_active WHERE mod_id = ?",
			(mod_id,),
		).fetchone()
		if vc:
			data["active_conflicting_assets"] = vc[0]
			data["active_opposing_mods"] = vc[1]
		else:
			data["active_conflicting_assets"] = 0
			data["active_opposing_mods"] = 0
	except Exception as e:
		# View might not exist yet, default to 0
		logger.debug(f"[get_mod_details] Could not query v_mod_conflicts_active: {e}")
		data["active_conflicting_assets"] = 0
		data["active_opposing_mods"] = 0
	# Aggregate tags for this mod from pak_tags_json (SQLite source of truth)
	try:
		tags_tokens: set[str] = set()
		rows = cur.execute(
			"SELECT tags_json FROM pak_tags_json WHERE mod_id = ?",
			(mod_id,),
		).fetchall()
		for r in rows:
			tj = r[0]
			if not tj:
				continue
			try:
				arr = json.loads(tj)
				if isinstance(arr, list):
					# Each element is already a separate tag (no comma splitting needed)
					for elem in arr:
						tok = str(elem).strip()
						if tok:
							tags_tokens.add(tok)
			except Exception:
				continue
		# Canonicalize tokens (categories + canonical characters only)
		data["tags"] = _canonicalize_tokens(tags_tokens)
	except Exception:
		data["tags"] = []
	try:
		return data
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/mods/{mod_id}/conflicts")
def get_mod_conflicts(mod_id: int, limit: int = 200) -> List[Dict[str, Any]]:
	conn = get_db()
	cur = conn.cursor()
	rows = cur.execute(
		"""
		SELECT asset_path, self_pak, opponents_json
		FROM v_mod_conflict_assets_active_named
		WHERE mod_id = ?
		ORDER BY asset_path, self_pak
		LIMIT ?
		""",
		(mod_id, limit),
	).fetchall()
	out: List[Dict[str, Any]] = []
	for asset_path, self_pak, opponents_json in rows:
		# Category for the asset
		cat_row = cur.execute("SELECT category FROM asset_tags WHERE asset_path = ?", (asset_path,)).fetchone()
		category = cat_row[0] if cat_row else None
		# Parse opponents
		try:
			opponents = json.loads(opponents_json) if opponents_json else []
		except Exception:
			opponents = []
		# Attach icons for opponents
		enriched: List[Dict[str, Any]] = []
		for o in opponents:
			omod_id = o.get("mod_id")
			icon = None
			if omod_id is not None:
				m = cur.execute("SELECT picture_url FROM mods WHERE mod_id = ?", (omod_id,)).fetchone()
				icon = m[0] if m else None
			enriched.append({**o, "icon": icon})
		out.append(
			{
				"asset_path": asset_path,
				"category": category,
				"self_pak": self_pak,
				"opponents": enriched,
			}
		)
	try:
		return out
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/mods/{mod_id}/files")
def get_mod_files_endpoint(mod_id: int) -> List[Dict[str, Any]]:
	conn = get_db()
	try:
		files = list_mod_files(conn, mod_id)
	except Exception as e:
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass
	return files


@app.get("/api/mods/{mod_id}/changelogs")
def get_mod_changelogs_endpoint(mod_id: int, response: Response) -> List[Dict[str, Any]]:
	# Disable caching for dynamic content
	response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
	response.headers["Pragma"] = "no-cache"
	response.headers["Expires"] = "0"
	
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		logs = get_changelogs(conn, mod_id)
		logger.info(f"[get_mod_changelogs] mod_id={mod_id}, returned {len(logs)} changelogs")
		if logs:
			logger.info(f"[get_mod_changelogs] First changelog: version={logs[0].get('version')}, changelog_len={len(logs[0].get('changelog', ''))}")
	except Exception as e:
		logger.error(f"[get_mod_changelogs] Error: {e}")
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass
	return logs


@app.get("/api/mods/{mod_id}/images")
def get_mod_images(mod_id: int) -> Dict[str, Any]:
	"""Get all images for a mod (Nexus images + custom uploaded images)."""
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		# Get Nexus image if available
		cur = conn.cursor()
		nexus_images = []
		mod_row = cur.execute("SELECT picture_url FROM mods WHERE mod_id = ?", (mod_id,)).fetchone()
		if mod_row and mod_row[0]:
			nexus_images.append({
				"id": 0,
				"source": "nexus",
				"url": mod_row[0],
			})
		
		# Get custom uploaded images
		custom_rows = cur.execute(
			"SELECT id, image_data, filename, mime_type, uploaded_at FROM mod_custom_images WHERE mod_id = ? ORDER BY uploaded_at ASC",
			(mod_id,)
		).fetchall()
		
		custom_images = []
		for img_id, image_data, filename, mime_type, uploaded_at in custom_rows:
			custom_images.append({
				"id": img_id,
				"source": "custom",
				"data": image_data,  # base64 data
				"filename": filename,
				"mimeType": mime_type,
				"uploadedAt": uploaded_at,
			})
		
		logger.info(f"[get_mod_images] mod_id={mod_id}, nexus_images={len(nexus_images)}, custom_images={len(custom_images)}")
		return {
			"ok": True,
			"nexus_images": nexus_images,
			"custom_images": custom_images,
		}
	except Exception as e:
		logger.error(f"[get_mod_images] Error: {e}")
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass




class UpdateModDetailsPayload(BaseModel):
	description: Optional[str] = None


@app.patch("/api/mods/{mod_id}")
def update_mod_details(mod_id: int, payload: UpdateModDetailsPayload) -> Dict[str, Any]:
	"""Update mod details (description only for now)."""
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		cur = conn.cursor()
		
		# Validated mod ID or synthetic ID for local mod?
		real_mod_id = mod_id
		
		# Check if mod exists
		mod_exists = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (real_mod_id,)).fetchone()
		
		if not mod_exists:
			# If it's a synthetic ID (negative), we attempt to "materialize" a placeholder mod record
			if real_mod_id < 0:
				local_download_id = -real_mod_id
				dl_row = cur.execute("SELECT name FROM local_downloads WHERE id = ?", (local_download_id,)).fetchone()
				if not dl_row:
					raise HTTPException(status_code=404, detail=f"Local download {local_download_id} not found")
				
				# Create placeholder mod
				mod_name = dl_row[0] or f"Local Mod {local_download_id}"
				upsert_mod_info(
					conn,
					game=DEFAULT_GAME,
					mod_id=real_mod_id,
					mod_info_status=0,
					mod_info={
						"name": mod_name,
						"summary": "Local mod (auto-generated)",
						"description": "Auto-generated placeholder for local mod.",
						"author": "Local",
						"status": "plaintext",
						"category_id": 1,
					}
				)
			else:
				raise HTTPException(status_code=404, detail=f"Mod {real_mod_id} not found")

		# Update fields if present
		if payload.description is not None:
			# Simple formatting: preserve paragraphs
			# We store primarily in description_html for now as that's what get_mod_details reads
			raw_desc = payload.description.strip()
			
			logger.debug(f"[update_mod_details] Processing description for mod_id={real_mod_id}. Raw length: {len(raw_desc)}")
			
			# Check if input contains BBCode tags
			import re
			# Check for all supported BBCode tags including custom ones
			bbcode_pattern = r'\[(?:b|i|u|s|url|img|quote|code|list|color|size|font|center|left|right|justify|sub|sup|hr|spoiler|youtube|email)'
			has_bbcode = bool(re.search(bbcode_pattern, raw_desc, re.IGNORECASE))
			logger.debug(f"[update_mod_details] BBCode detected: {has_bbcode} for mod_id={real_mod_id}")
			
			if has_bbcode:
				# Convert BBCode to HTML
				try:
					from core.utils.bbcode_wrapper import bbcode_to_html
					description_html = bbcode_to_html(raw_desc)
					
					# Debug: Log the actual HTML being generated
					logger.info(f"[update_mod_details] Converted BBCode to HTML for mod_id={real_mod_id}")
					logger.debug(f"[update_mod_details] Generated HTML (first 500 chars): {description_html[:500]}")
					logger.debug(f"[update_mod_details] Contains <img tag: {'<img' in description_html}")
					
				except Exception as e:
					logger.error(f"[update_mod_details] BBCode conversion failed: {e}")
					# Fallback to plain text handling
					import html
					safe_desc = html.escape(raw_desc)
					description_html = safe_desc.replace("\n", "<br>")
					logger.debug(f"[update_mod_details] Fallback to plain text HTML for mod_id={real_mod_id} due to BBCode conversion error.")
			else:
				# Plain text: escape HTML and convert newlines
				import html
				safe_desc = html.escape(raw_desc)
				description_html = safe_desc.replace("\n", "<br>")
				logger.debug(f"[update_mod_details] Converted plain text to HTML for mod_id={real_mod_id}.")

			
			logger.info(f"[update_mod_details] Updating mod_id={real_mod_id}, new description length={len(description_html)}")
			
			cur.execute(
				"UPDATE mods SET description_html = ?, description_bbcode = ? WHERE mod_id = ?",
				(description_html, raw_desc, real_mod_id)
			)
			rows_affected = cur.rowcount
			logger.info(f"[update_mod_details] UPDATE executed, rows affected: {rows_affected}")
			
			conn.commit()
			logger.info(f"[update_mod_details] Transaction committed for mod_id={real_mod_id}")
			
		logger.info(f"[update_mod_details] Updated details for mod_id={real_mod_id}")
		return {"ok": True}

	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"[update_mod_details] Error: {e}")
		conn.rollback()
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


class UploadImagePayload(BaseModel):
	images: List[Dict[str, str]]  # Each dict: { data: base64, filename: str, mimeType: str }


@app.post("/api/mods/{mod_id}/images")
def upload_mod_images(mod_id: int, payload: UploadImagePayload) -> Dict[str, Any]:
	"""Upload custom images for a mod."""
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		cur = conn.cursor()
		
		# Validated mod ID or synthetic ID for local mod?
		# If mod_id < 0, it represents a local_download_id (negated)
		real_mod_id = mod_id
		
		# Check if mod exists
		mod_exists = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (real_mod_id,)).fetchone()
		
		if not mod_exists:
			# If it's a synthetic ID (negative), we attempt to "materialize" a placeholder mod record
			# using info from the local download so that the FK constraint on mod_custom_images is satisfied.
			if real_mod_id < 0:
				local_download_id = -real_mod_id
				dl_row = cur.execute("SELECT name FROM local_downloads WHERE id = ?", (local_download_id,)).fetchone()
				if not dl_row:
					raise HTTPException(status_code=404, detail=f"Local download {local_download_id} not found for synthetic mod ID {real_mod_id}")
				
				# Create placeholder mod
				mod_name = dl_row[0] or f"Local Mod {local_download_id}"
				upsert_mod_info(
					conn,
					game=DEFAULT_GAME,
					mod_id=real_mod_id,
					mod_info_status=0,
					mod_info={
						"name": mod_name,
						"summary": "Local mod (auto-generated)",
						"description": "Auto-generated placeholder for local mod images.",
						"author": "Local",
						"status": "plaintext",
						"category_id": 1,
					}
				)
				logger.info(f"[upload_mod_images] Created placeholder mod record for synthetic ID {real_mod_id}")
			else:
				# Positive ID but not found in DB -> 404
				pass 
				# Actually the original code raised 404 here. But wait, if it's a positive ID that 
				# simply hasn't been cached yet (unlikely if we are on the page), strictly we should 404.
				# However, let's stick to the check.
				mod_exists_after = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (real_mod_id,)).fetchone()
				if not mod_exists_after:
					raise HTTPException(status_code=404, detail=f"Mod {real_mod_id} not found")
		
		uploaded_ids = []
		for img in payload.images:
			image_data = img.get("data", "")
			filename = img.get("filename", "")
			mime_type = img.get("mimeType", "")
			
			if not image_data:
				continue
			
			cur.execute(
				"""
				INSERT INTO mod_custom_images (mod_id, image_data, filename, mime_type)
				VALUES (?, ?, ?, ?)
				""",
				(real_mod_id, image_data, filename, mime_type)
			)
			uploaded_ids.append(cur.lastrowid)
		
		conn.commit()
		logger.info(f"[upload_mod_images] mod_id={real_mod_id}, uploaded {len(uploaded_ids)} images")
		return {
			"ok": True,
			"uploaded_count": len(uploaded_ids),
			"image_ids": uploaded_ids,
		}
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"[upload_mod_images] Error: {e}")
		conn.rollback()
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


class UploadImageByPathPayload(BaseModel):
	paths: List[str]


@app.post("/api/mods/{mod_id}/images/upload-by-path")
def upload_mod_images_by_path(mod_id: int, payload: UploadImageByPathPayload) -> Dict[str, Any]:
	"""Upload custom images for a mod by local paths."""
	import logging
	import base64
	import mimetypes
	from pathlib import Path
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		cur = conn.cursor()
		real_mod_id = mod_id
		mod_exists = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (real_mod_id,)).fetchone()
		
		if not mod_exists:
			if real_mod_id < 0:
				local_download_id = -real_mod_id
				dl_row = cur.execute("SELECT name FROM local_downloads WHERE id = ?", (local_download_id,)).fetchone()
				if not dl_row:
					raise HTTPException(status_code=404, detail=f"Local download {local_download_id} not found for synthetic mod ID {real_mod_id}")
				
				mod_name = dl_row[0] or f"Local Mod {local_download_id}"
				upsert_mod_info(
					conn,
					game=DEFAULT_GAME,
					mod_id=real_mod_id,
					mod_info_status=0,
					mod_info={
						"name": mod_name,
						"summary": "Local mod (auto-generated)",
						"description": "Auto-generated placeholder for local mod images.",
						"author": "Local",
						"status": "plaintext",
						"category_id": 1,
					}
				)
				logger.info(f"[upload_mod_images_by_path] Created placeholder mod record for synthetic ID {real_mod_id}")
			else:
				mod_exists_after = cur.execute("SELECT 1 FROM mods WHERE mod_id = ?", (real_mod_id,)).fetchone()
				if not mod_exists_after:
					raise HTTPException(status_code=404, detail=f"Mod {real_mod_id} not found")
		
		uploaded_ids = []
		for path_str in payload.paths:
			if not path_str:
				continue
			p = Path(path_str)
			if not p.exists() or not p.is_file():
				logger.warning(f"[upload_mod_images_by_path] Path {path_str} does not exist or is not a file")
				continue
			
			filename = p.name
			mime_type, _ = mimetypes.guess_type(path_str)
			if not mime_type:
				mime_type = "image/png"  # fallback
			
			# Read file and encode to base64
			with open(p, "rb") as f:
				image_data = base64.b64encode(f.read()).decode("utf-8")
			
			cur.execute(
				"""
				INSERT INTO mod_custom_images (mod_id, image_data, filename, mime_type)
				VALUES (?, ?, ?, ?)
				""",
				(real_mod_id, image_data, filename, mime_type)
			)
			uploaded_ids.append(cur.lastrowid)
		
		conn.commit()
		logger.info(f"[upload_mod_images_by_path] mod_id={real_mod_id}, uploaded {len(uploaded_ids)} images")
		return {
			"ok": True,
			"uploaded_count": len(uploaded_ids),
			"image_ids": uploaded_ids,
		}
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"[upload_mod_images_by_path] Error: {e}")
		conn.rollback()
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.delete("/api/mods/images/{image_id}")
def delete_mod_image(image_id: int) -> Dict[str, Any]:
	"""Delete a custom uploaded image."""
	import logging
	logger = logging.getLogger("modmanager.api")
	conn = get_db()
	try:
		cur = conn.cursor()
		# Check if image exists
		image_row = cur.execute("SELECT id FROM mod_custom_images WHERE id = ?", (image_id,)).fetchone()
		if not image_row:
			raise HTTPException(status_code=404, detail=f"Image {image_id} not found")
		
		# Delete the image
		cur.execute("DELETE FROM mod_custom_images WHERE id = ?", (image_id,))
		conn.commit()
		
		logger.info(f"[delete_mod_image] Deleted image_id={image_id}")
		return {"ok": True, "deleted_id": image_id}
	except HTTPException:
		raise
	except Exception as e:
		logger.error(f"[delete_mod_image] Error: {e}")
		conn.rollback()
		raise HTTPException(status_code=500, detail=str(e))
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/downloads")
def list_downloads() -> List[Dict[str, Any]]:
	"""List local downloads with joined mod info and tags sourced strictly from v_local_downloads_with_tags.

	Returns items like: { id, name, mod_id, version, path, contents[], active_paks[], created_at,
						  mod_name, mod_author, picture_url, tags: [token,...] }
	"""
	import logging
	logger = logging.getLogger("modmanager.api.downloads")
	
	conn = get_db()
	cur = conn.cursor()
	
	# Log table counts for debugging
	try:
		dl_count = cur.execute("SELECT COUNT(*) FROM local_downloads").fetchone()[0]
		logger.info(f"[list_downloads] Found {dl_count} rows in local_downloads table")
	except Exception as e:
		logger.warning(f"[list_downloads] Could not count local_downloads: {e}")
	
	rows = cur.execute(
		"""
		SELECT l.id, l.name, l.mod_id, l.version, l.path, l.contents, l.active_paks, l.created_at,
		   m.name AS mod_name, m.author AS mod_author, m.picture_url,
		   m.created_time AS mod_created_time, m.updated_at AS mod_updated_at,
		   m.mod_downloads, m.endorsement_count,
		   m.author_profile_url, m.author_member_id,
		   m.contains_adult_content,
		   v.tags_json,
		   variant_latest.version AS file_version,
		   variant_latest.uploaded_at AS latest_uploaded_at,
		   variant_latest.file_id AS latest_file_id,
		   variant_latest.version_key AS latest_version_key,
		   variant_latest.name AS file_name
		FROM local_downloads l
		LEFT JOIN mods m ON m.mod_id = l.mod_id
		LEFT JOIN v_local_downloads_with_tags v ON v.download_id = l.id
		LEFT JOIN v_mods_with_latest_by_version overall_latest ON overall_latest.mod_id = l.mod_id
		LEFT JOIN (
			SELECT mod_id, file_id, version, uploaded_at, name, version_key,
			       ROW_NUMBER() OVER (PARTITION BY mod_id, REPLACE(REPLACE(REPLACE(LOWER(name), ' ', ''), '-', ''), '_', '') ORDER BY uploaded_at DESC, file_id DESC) as rn
			FROM mod_files
		) variant_latest ON variant_latest.mod_id = l.mod_id 
		    AND variant_latest.rn = 1 
		    AND REPLACE(REPLACE(REPLACE(LOWER(variant_latest.name), ' ', ''), '-', ''), '_', '') = REPLACE(REPLACE(REPLACE(LOWER(l.name), ' ', ''), '-', ''), '_', '')
		ORDER BY l.created_at DESC
		"""
	).fetchall()
	
	logger.info(f"[list_downloads] Query returned {len(rows)} rows")
	
	actual_active_filenames = _get_actually_active_filenames(logger)
	db_updates = []
	
	out: List[Dict[str, Any]] = []
	for (
		dl_id,
		name,
		mod_id,
		version,
		path,
		contents_json,
		active_json,
		created_at,
		mod_name,
		mod_author,
		picture_url,
		mod_created_time,
		mod_updated_at,
		mod_downloads,
		endorsement_count,
		mod_author_profile_url,
		mod_author_member_id,
		contains_adult_content,
		view_tags_json,
		latest_version,
		latest_uploaded_at,
		latest_file_id,
		latest_version_key,
		latest_file_name,
	) in rows:
		# contents / active paks parsing
		try:
			contents = json.loads(contents_json) if contents_json else []
			if not isinstance(contents, list):
				contents = []
		except Exception:
			contents = []
		try:
			active_paks = json.loads(active_json) if active_json else []
			if not isinstance(active_paks, list):
				active_paks = []
		except Exception:
			active_paks = []

		if actual_active_filenames is not None:
			filtered_active_paks = []
			for p in active_paks:
				basename = os.path.basename(p).lower()
				if basename in actual_active_filenames:
					filtered_active_paks.append(p)
			if len(filtered_active_paks) != len(active_paks):
				logger.info(f"[list_downloads] download_id={dl_id} active_paks changed from {active_paks} to {filtered_active_paks} (files not found in ~mods)")
				db_updates.append((dl_id, filtered_active_paks))
				active_paks = filtered_active_paks

		# Tags strictly from the view; no heuristics
		tags_list: List[str] = []
		if view_tags_json:
			try:
				arr = json.loads(view_tags_json)
				if isinstance(arr, list):
					# Flatten elements to strings, optionally split comma-delimited entries
					flat: List[str] = []
					for elem in arr:
						if elem is None:
							continue
						s = str(elem).strip()
						if not s:
							continue
						if "," in s:
							flat.extend([t.strip() for t in s.split(",") if t.strip()])
						else:
							flat.append(s)
					# Deduplicate while preserving order
					seen: set[str] = set()
					for t in flat:
						if t not in seen:
							seen.add(t)
							tags_list.append(t)
			except Exception:
				tags_list = []

		resolved_member_id = _extract_member_id(mod_author_member_id)
		avatar_url = _author_avatar_url(resolved_member_id, mod_author_profile_url)

		local_version_key = make_version_key(version)[0]
		needs_update = False
		if versions_equivalent(version, latest_version):
			needs_update = False
		elif latest_version_key and local_version_key:
			needs_update = latest_version_key > local_version_key
		elif latest_version and (version or "").strip():
			needs_update = latest_version.strip() != (version or "").strip()

		# Fetch custom tags for this mod (keyed by Nexus mod_id or synthetic negative download id)
		custom_tag_names: list[str] = []
		try:
			effective_mod_id = mod_id if mod_id is not None else -dl_id
			ct_rows = cur.execute(
				"SELECT tag FROM mod_custom_tags WHERE mod_id = ? ORDER BY added_at ASC",
				(effective_mod_id,),
			).fetchall()
			custom_tag_names = [r[0] for r in ct_rows if r[0]]
		except Exception:
			pass

		out.append(
			{
				"id": dl_id,
				"download_id": dl_id,
				"name": name,
				"mod_id": mod_id,
				"version": version,
				"path": path,
				"contents": contents,
				"active_paks": active_paks,
				"created_at": created_at,
				"mod_name": mod_name,
				"mod_author": mod_author,
				"picture_url": picture_url,
				"tags": tags_list,
				"custom_tag_names": custom_tag_names,
				"mod_downloads": mod_downloads,
				"endorsement_count": endorsement_count,
				"mod_author_profile_url": mod_author_profile_url,
				"mod_author_member_id": resolved_member_id,
				"mod_author_avatar_url": avatar_url,
				"mod_created_time": mod_created_time,
				"mod_updated_at": mod_updated_at,
				"latest_version": latest_version,
				"latest_uploaded_at": latest_uploaded_at,
				"latest_file_id": latest_file_id,
				"latest_version_key": latest_version_key,
				"latest_file_name": latest_file_name,
				"local_version_key": local_version_key,
				"needs_update": needs_update,
				"contains_adult_content": bool(contains_adult_content) if contains_adult_content else False,
			}
		)

	
	logger.info(f"[list_downloads] Returning {len(out)} download entries to client")
	# Debug: Log NSFW content status for troubleshooting
	nsfw_count = sum(1 for item in out if item.get("contains_adult_content"))
	logger.info(f"[list_downloads] NSFW mods count: {nsfw_count} out of {len(out)} entries")
	
	if db_updates:
		try:
			from core.db.db import update_local_download_active_paks
			for dl_id, filtered_paks in db_updates:
				update_local_download_active_paks(conn, dl_id, filtered_paks)
			logger.info(f"[list_downloads] Auto-updated {len(db_updates)} out-of-sync local_downloads rows in DB")
		except Exception as update_err:
			logger.warning(f"[list_downloads] Failed to run batch database updates: {update_err}")

	try:
		return out
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/downloads/summary")
def downloads_summary() -> Dict[str, Any]:
	"""Return aggregated summary for local downloads.

	Response fields:
	  - total_size_bytes: int
	  - total_size_human: str
	  - download_count: int
	  - missing_paths: list[str]
	  - last_check: ISO-8601 timestamp (UTC) or None
	"""
	import logging
	logger = logging.getLogger("modmanager.api.downloads.summary")

	conn = get_db()
	cur = conn.cursor()
	try:
		rows = cur.execute(
			"SELECT id, path, created_at FROM local_downloads ORDER BY created_at DESC"
		).fetchall()
	finally:
		try:
			conn.close()
		except Exception:
			pass

	downloads_root = _downloads_root_from_env()
	total_bytes = 0
	missing: List[str] = []
	latest_mtime: Optional[float] = None
	any_existing = False
	count = 0

	for _id, raw_path, created_at in rows:
		try:
			# Resolve with the same helper the server uses elsewhere
			candidate = _resolve_download_source_path(str(raw_path or ""))
			p = Path(candidate)
		except Exception:
			missing.append(str(raw_path or ""))
			continue

		try:
			if p.exists():
				any_existing = True
				# If directory, sum files recursively; if file, take stat
				if p.is_dir():
					for root, _dirs, files in os.walk(p):
						for fn in files:
							try:
								fp = Path(root) / fn
								size = fp.stat().st_size
								total_bytes += int(size)
								m = fp.stat().st_mtime
								if latest_mtime is None or m > latest_mtime:
									latest_mtime = m
							except Exception:
								continue
				else:
					try:
						size = p.stat().st_size
						total_bytes += int(size)
						m = p.stat().st_mtime
						if latest_mtime is None or m > latest_mtime:
							latest_mtime = m
					except Exception:
						pass
			else:
				missing.append(str(raw_path or ""))
		except Exception:
			missing.append(str(raw_path or ""))
		count += 1

	def _human(n: int) -> str:
		# Simple human readable formatter
		try:
			if n < 1024:
				return f"{n} B"
			for unit in ("KB", "MB", "GB", "TB"):
				n = float(n) / 1024.0
				if n < 1024.0:
					return f"{n:.2f} {unit}"
			return f"{n:.2f} PB"
		except Exception:
			return str(n)

	# Prefer a persisted last-check timestamp written by update-check operations.
	last_check_iso = None
	try:
		from pathlib import Path as _Path
		_last_check_file = _Path(SETTINGS.data_dir) / "last_update_check.json"
		logger.debug(f"[downloads_summary] looking for persisted last_check at {_last_check_file}")
		if _last_check_file.exists():
			logger.debug("[downloads_summary] persisted last_check file exists")
			try:
				_payload = json.loads(_last_check_file.read_text(encoding="utf-8"))
			except TypeError:
				_payload = json.loads(_last_check_file.read_text())
			logger.debug(f"[downloads_summary] read persisted payload: {_payload}")
			if isinstance(_payload, dict) and _payload.get("last_check"):
				last_check_iso = _payload.get("last_check")
	except Exception:
		# ignore read errors and fall back to mtime/created_at
		last_check_iso = None

	# If no persisted timestamp, fall back to filesystem latest modified time
	if last_check_iso is None:
		if latest_mtime is not None:
			last_check_iso = datetime.fromtimestamp(latest_mtime, tz=timezone.utc).isoformat()
		else:
			# fallback: use newest created_at from DB rows if present
			if rows:
				try:
					# rows are ordered by created_at DESC
					newest_created = rows[0][2]
					if isinstance(newest_created, str) and newest_created:
						# assume ISO already
						last_check_iso = newest_created
					elif isinstance(newest_created, (int, float)):
						last_check_iso = datetime.fromtimestamp(float(newest_created), tz=timezone.utc).isoformat()
				except Exception:
					last_check_iso = None

	result: Dict[str, Any] = {
		"ok": True,
		"total_size_bytes": int(total_bytes),
		"total_size_human": _human(int(total_bytes)),
		"download_count": int(count),
		"missing_paths": missing,
		"last_check": last_check_iso,
	}
	logger.info(f"[downloads_summary] count={count} total_bytes={total_bytes} missing={len(missing)} last_check={last_check_iso}")
	return result


# --- Activation endpoints ---

def _mods_folder_from_env() -> Path:
	current = _get_current_settings()
	root = current.marvel_rivals_root
	if not root:
		raise HTTPException(
			status_code=400,
			detail="MARVEL_RIVALS_ROOT is not configured. Update core/config/settings.py with your Marvel Rivals installation path.",
		)
	mods_dir = root.expanduser() / "MarvelGame/Marvel/Content/Paks/~mods"
	return mods_dir


def _downloads_root_from_env() -> Path:
	current = _get_current_settings()
	root = current.marvel_rivals_local_downloads_root or current.marvel_rivals_root
	if root:
		return root.expanduser().resolve()
	return (_ROOT / "downloads").resolve()


def _load_nexus_prefs_cached() -> Dict[str, Dict[str, str]]:
	global _NEXUS_PREFS_CACHE
	if _NEXUS_PREFS_CACHE is None:
		try:
			_NEXUS_PREFS_CACHE = load_prefs()
		except Exception:
			_NEXUS_PREFS_CACHE = {}
	return _NEXUS_PREFS_CACHE


def _lookup_mod_id_by_name(conn, name: Optional[str]) -> Optional[int]:
	if not name:
		return None
	cur = conn.cursor()
	row = cur.execute(
		"SELECT mod_id FROM mods WHERE name = ? COLLATE NOCASE LIMIT 1",
		(name.strip(),),
	).fetchone()
	if row and row[0]:
		return int(row[0])
	row = cur.execute(
		"SELECT mod_id FROM mods WHERE name LIKE ? COLLATE NOCASE ORDER BY LENGTH(name) ASC LIMIT 1",
		(f"%{name.strip()}%",),
	).fetchone()
	if row and row[0]:
		return int(row[0])
	return None


def _search_mod_id_remote(name: str, api_key: str, game: str = DEFAULT_GAME) -> Optional[int]:
	if not name:
		return None
	params = urllib.parse.urlencode({"terms": name})
	url = f"https://api.nexusmods.com/v1/games/{game}/mods.json?{params}"
	headers = {
		"apikey": api_key,
		"User-Agent": "Project_ModManager_Rivals/0.7.2",
		"Application-Name": "Project_ModManager_Rivals",
	}
	req = urllib.request.Request(url, headers=headers, method="GET")
	try:
		with urllib.request.urlopen(req, timeout=30) as resp:
			data = json.loads(resp.read().decode("utf-8"))
	except Exception:
		return None
	if isinstance(data, dict):
		results = data.get("mods") or data.get("results") or data.get("data")
	else:
		results = data
	if isinstance(results, list):
		for item in results:
			if isinstance(item, dict):
				mid = item.get("mod_id") or item.get("id")
				if isinstance(mid, int):
					return mid
	return None


def _sync_mod_metadata(
	conn,
	mod_id: Optional[int],
	mod_name: Optional[str],
	*,
	pre_fetched: Optional[Dict[str, Any]] = None,
	filtered_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
	result: Dict[str, Any] = {}
	try:
		key = get_api_key()
		if not key and pre_fetched is None:
			result["metadata_warning"] = "NEXUS_API_KEY not configured; skipped metadata sync"
			return result
		resolved_mod_id = mod_id
		if resolved_mod_id is None:
			if mod_name:
				_, parsed_mod_id, _ = parse_mod_filename(mod_name)
				if parsed_mod_id is not None:
					resolved_mod_id = parsed_mod_id
			if resolved_mod_id is None:
				resolved_mod_id = _lookup_mod_id_by_name(conn, mod_name)
			if resolved_mod_id is None and mod_name:
				resolved_mod_id = _search_mod_id_remote(mod_name, key, DEFAULT_GAME)
		if resolved_mod_id is None:
			result["metadata_warning"] = "Unable to resolve Nexus mod ID from name"
			return result
		prefs = None
		payload = pre_fetched
		if payload is None:
			if not key:
				result["metadata_warning"] = "Unable to contact Nexus; no metadata payload available"
				return result
			payload = collect_all_for_mod(key, DEFAULT_GAME, resolved_mod_id)
		if filtered_payload is not None:
			filtered = filtered_payload
		else:
			prefs = _load_nexus_prefs_cached()
			filtered = filter_aggregate_payload(payload, prefs)
		mod_info_payload = dict(filtered.get("mod_info") or {})
		desc_text = extract_description_text(filtered.get("description"))
		if desc_text:
			mod_info_payload["description"] = desc_text
		upsert_api_cache(conn, resolved_mod_id, filtered)
		mod_info_status = int(payload.get("mod_info_status", 0))
		upsert_mod_info(conn, DEFAULT_GAME, resolved_mod_id, mod_info_status, mod_info_payload)
		replace_mod_files(conn, resolved_mod_id, filtered.get("files"))
		changelogs_payload = filtered.get("changelogs") or {}
		if not changelogs_payload or (
			isinstance(changelogs_payload, dict) and not changelogs_payload.get("changelogs")
		):
			changelogs_payload = derive_changelogs_from_files(filtered.get("files"))
		replace_mod_changelogs(conn, resolved_mod_id, changelogs_payload)
		result["synced_mod_id"] = resolved_mod_id
		return result
	except Exception as e:
		result["metadata_warning"] = f"Metadata sync failed: {e}"
		return result



def _looks_like_url(value: str) -> bool:
	return value.lower().startswith(("http://", "https://"))


def _safe_filename(name: str) -> str:
	base = os.path.basename(name or "").strip()
	if not base:
		return ""
	stem, ext = os.path.splitext(base)
	stem = re.sub(r"[^A-Za-z0-9._-]", "_", stem)
	stem = re.sub(r"_+", "_", stem).strip("._") or "mod"
	clean_ext = "".join(ch for ch in ext if ch.isalnum())
	ext_part = f".{clean_ext}" if clean_ext else ""
	return f"{stem}{ext_part}"


def _unique_destination(directory: Path, filename: str) -> Path:
	"""Return a deterministic destination under ``directory`` without suffixing.

	If the target already exists we reuse the same path, allowing callers to decide
	whether to overwrite or short-circuit when a duplicate is detected.
	"""
	return directory / filename


def _allow_direct_api_downloads() -> bool:
	current = _get_current_settings()
	return current.allow_direct_api_downloads


def _nxm_required_detail(
	mod_id: int,
	file_id: int,
	*,
	mod_name: Optional[str],
	latest_version: Optional[str],
	uploaded_at: Optional[Any],
) -> Dict[str, Any]:
	nxm_uri = f"nxm://{DEFAULT_GAME}/mods/{mod_id}/files/{file_id}"
	detail: Dict[str, Any] = {
		"requires_nxm_handoff": True,
		"message": (
			"Nexus Mods requires a browser-initiated handoff for this download. "
			"Click 'Mod Manager Download' on the Nexus Mods file page to continue."
		),
		"nxm_uri": nxm_uri,
		"game": DEFAULT_GAME,
		"mod_id": mod_id,
		"file_id": file_id,
	}
	if mod_name:
		detail["mod_name"] = mod_name
	if latest_version:
		detail["latest_version"] = latest_version
	if uploaded_at:
		detail["latest_uploaded_at"] = uploaded_at
	return detail


def _download_remote_archive(
	url: str,
	*,
	force: bool = False,
	progress_callback: Optional[Callable[[int, Optional[int]], None]] = None,
	handoff_id: Optional[str] = None,
	desired_filename: Optional[str] = None,
) -> Path:
	downloads_root = _downloads_root_from_env()
	_ensure_dir(downloads_root)
	parsed = urllib.parse.urlparse(url)
	unquoted_path = urllib.parse.unquote(parsed.path or "")
	filename_guess = desired_filename or Path(unquoted_path or "download").name or "download"
	sanitized_path = urllib.parse.quote(unquoted_path, safe="/%:@&=+$,;.-_~!'()*")
	if sanitized_path != parsed.path:
		url = urllib.parse.urlunparse(
			(
				parsed.scheme,
				parsed.netloc,
				sanitized_path,
				parsed.params,
				parsed.query,
				parsed.fragment,
			)
		)
		parsed = urllib.parse.urlparse(url)
	safe_name = _safe_filename(filename_guess) or "download"
	dest = _unique_destination(downloads_root, safe_name)
	if dest.exists():
		if force:
			base_stem = dest.stem
			suffix = dest.suffix
			counter = 1
			while True:
				candidate = dest.with_name(f"{base_stem}-{counter}{suffix}")
				if not candidate.exists():
					dest = candidate
					break
				counter += 1
		else:
			return dest.resolve()
	req = urllib.request.Request(url, headers={"User-Agent": "MarvelRivalsModManager/0.1"})
	def _emit_progress(downloaded: int, total: Optional[int]) -> None:
		if progress_callback is None:
			return
		try:
			progress_callback(downloaded, total)
		except Exception:
			pass

	def _is_cancelled() -> bool:
		if handoff_id is None:
			return False
		with _CANCELLED_HANDOFFS_LOCK:
			return handoff_id in _CANCELLED_HANDOFFS

	try:
		with urllib.request.urlopen(req, timeout=120) as response, dest.open("wb") as out:
			total_bytes: Optional[int] = getattr(response, "length", None)
			if total_bytes is None:
				try:
					headers = getattr(response, "headers", None)
					if headers is not None:
						header_value = headers.get("Content-Length")
						total_bytes = int(header_value) if header_value else None
				except Exception:
					total_bytes = None
			downloaded = 0
			chunk_size = 1024 * 1024
			_emit_progress(downloaded, total_bytes)
			while True:
				# Check for cancellation before reading each chunk
				if _is_cancelled():
					logger.info("[download] Cancellation detected mid-download for handoff=%s, aborting", handoff_id)
					# Close the socket by breaking — the with-block will handle cleanup
					break
				chunk = response.read(chunk_size)
				if not chunk:
					break
				out.write(chunk)
				downloaded += len(chunk)
				_emit_progress(downloaded, total_bytes)
	except Exception as e:
		if dest.exists():
			try:
				dest.unlink()
			except Exception:
				pass
		raise HTTPException(status_code=400, detail=f"Failed to download {url}: {e}")

	# After the with-block (and its try-except), file handles are guaranteed closed.
	# Check if we stopped because of cancellation
	if _is_cancelled():
		# Remove partial file now that it is no longer locked by the process
		try:
			if dest.exists():
				dest.unlink()
				logger.info("[download] Partial file deleted: %s", dest)
		except Exception as del_err:
			logger.warning("[download] Failed to delete partial file %s: %s", dest, del_err)
		raise DownloadCancelledError(f"Download cancelled by user (handoff={handoff_id})")

	try:
		if dest.stat().st_size <= 0:
			dest.unlink(missing_ok=True)
			raise HTTPException(status_code=400, detail=f"Downloaded file was empty: {url}")
	except FileNotFoundError:
		raise HTTPException(status_code=400, detail=f"Downloaded file missing after fetch: {url}")
	return dest.resolve()


def _resolve_nexus_download_candidates(
	record: Dict[str, Any],
	game_domain: str,
	file_id: int,
) -> List[Tuple[str, Optional[str]]]:
	request_data = record.get("request", {}) if isinstance(record, dict) else {}
	metadata = record.get("metadata", {}) if isinstance(record.get("metadata"), dict) else {}
	mod_id = request_data.get("mod_id")
	if not isinstance(mod_id, int):
		mod_id = metadata.get("mod_id") if isinstance(metadata.get("mod_id"), int) else None
	if not isinstance(mod_id, int):
		raise HTTPException(status_code=400, detail="nxm handoff missing mod id; please click Mod Manager Download again")
	query = request_data.get("query") if isinstance(request_data.get("query"), dict) else {}
	key = str(query.get("key") or metadata.get("key") or "").strip()
	expires = str(query.get("expires") or metadata.get("expires") or "").strip()
	user_id = str(query.get("user_id") or "").strip()
	
	# DEBUG: Log what we extracted
	logger.info("[NXM DEBUG] Extracted from URL - key: %s, expires: %s, user_id: %s", 
		"(present)" if key else "(MISSING)", 
		"(present)" if expires else "(MISSING)", 
		"(present)" if user_id else "(MISSING)")
	
	if not key or not expires:
		error_msg = (
			"NXM download authorization missing or expired. "
			"Please ensure you are logged into NexusMods in your browser, "
			"then click 'Download with Manager' button again. "
			f"(key={'present' if key else 'MISSING'}, expires={'present' if expires else 'MISSING'})"
		)
		logger.error("[NXM DEBUG] %s", error_msg)
		raise HTTPException(status_code=400, detail=error_msg)
	domain = (game_domain or DEFAULT_GAME or "marvelrivals").strip().lower() or DEFAULT_GAME
	params = {"key": key, "expires": expires}
	if user_id:
		params["user_id"] = user_id
	api_query = urllib.parse.urlencode(params)
	api_url = (
		f"https://api.nexusmods.com/v1/games/{domain}/mods/{mod_id}/files/{file_id}/download_link.json"
	)
	if api_query:
		api_url = f"{api_url}?{api_query}"
	headers = {
		"User-Agent": "MarvelRivalsModManager/0.1",
		"Accept": "application/json",
	}
	api_key = get_api_key()
	if api_key:
		headers["apikey"] = api_key
		headers["Application-Name"] = "MarvelRivalsModManager"
		headers["Application-Version"] = "0.7.2"
	req = urllib.request.Request(api_url, headers=headers, method="GET")
	try:
		with urllib.request.urlopen(req, timeout=30) as resp:
			status = resp.getcode() or 0
			raw = resp.read()
	except urllib.error.HTTPError as exc:
		body = None
		try:
			body = exc.read().decode("utf-8", errors="replace")
		except Exception:
			pass
		detail = body or exc.reason or str(exc)
		
		# Parse the error message
		error_context = ""
		if exc.code == 400 and body:
			try:
				error_data = json.loads(body)
				if isinstance(error_data, dict):
					error_message = error_data.get("message", "")
					if "key and expire time isn't correct" in str(error_message).lower():
						error_context = (
							"\n\nThis error typically means:\n"
							"1. The download link has EXPIRED (they expire in ~10 minutes)\n"
							"2. You are not logged into NexusMods in your browser\n"
							"3. The link was generated for a different user\n\n"
							"SOLUTION: Log into YOUR NexusMods account in your browser, "
							"then click 'Download with Manager' button AGAIN to generate a fresh link."
						)
			except Exception:
				pass
		
		if exc.code in (401, 403):
			raise HTTPException(
				status_code=exc.code,
				detail=(
					"Nexus download link request was denied ("
					f"{exc.code}). Ensure you're logged into Nexus Mods in your browser and click Mod Manager Download again. "
					"If the issue persists, configure a Nexus API key. "
					f"Details: {detail}"
				),
			)
		elif exc.code == 400:
			raise HTTPException(
				status_code=exc.code,
				detail=f"Nexus download link request failed ({exc.code}): {detail}{error_context}"
			)
		raise HTTPException(status_code=exc.code or 502, detail=f"Nexus download link request failed ({exc.code}): {detail}")
	except urllib.error.URLError as exc:
		reason = exc.reason
		host = urllib.parse.urlparse(api_url).netloc
		raise HTTPException(
			status_code=502,
			detail=f"Unable to reach Nexus download link API at {host}: {reason}",
		)
	if status != 200:
		raise HTTPException(status_code=502, detail=f"Unexpected response {status} from Nexus download link API")
	if not raw:
		raise HTTPException(status_code=502, detail="Nexus download link API returned an empty payload")
	try:
		payload = json.loads(raw.decode("utf-8"))
	except json.JSONDecodeError as exc:
		raise HTTPException(status_code=502, detail=f"Failed to parse Nexus download link JSON: {exc}")
	if isinstance(payload, dict):
		error_detail = None
		if payload.get("error"):
			error_detail = payload.get("message") or payload.get("detail") or payload.get("error")
		elif payload.get("errors"):
			error_detail = payload.get("errors")
		if error_detail:
			error_text = error_detail if isinstance(error_detail, str) else str(error_detail)
			raise HTTPException(status_code=502, detail=f"Nexus download link API error: {error_text}")
	candidates: List[Tuple[str, Optional[str]]] = []
	iterable: List[Any]
	if isinstance(payload, list):
		iterable = payload
	else:
		iterable = [payload]
	for entry in iterable:
		uri = _extract_download_uri(entry)
		if uri:
			label: Optional[str] = None
			if isinstance(entry, dict):
				label_val = entry.get("short_name") or entry.get("name") or entry.get("cdn") or entry.get("label")
				if isinstance(label_val, str) and label_val.strip():
					label = label_val.strip()
			candidates.append((uri, label))
	if not candidates:
		raise HTTPException(status_code=502, detail="Nexus download link API did not return any usable URLs")
	return candidates


def _download_archive_via_nxm(
	record: Dict[str, Any],
	game_domain: str,
	file_id: int,
	desired_filename: Optional[str] = None,
) -> Tuple[Path, str]:
	download_errors: List[str] = []
	handoff_id = record.get("id") if isinstance(record.get("id"), str) else None
	if handoff_id:
		update_handoff_progress(
			handoff_id,
			stage="resolving",
			message="Resolving Nexus CDN mirrors…",
			bytes_downloaded=0,
		)
	candidates = _resolve_nexus_download_candidates(record, game_domain, file_id)
	for download_url, label in candidates:
		host = urllib.parse.urlparse(download_url).netloc
		logger.info(
			"[nxm_handoff] attempting Nexus CDN download host=%s label=%s file_id=%s",
			host,
			label or "",
			file_id,
		)
		progress_message = f"Downloading from {label or host}" if (label or host) else "Downloading from Nexus CDN"
		progress_fn: Optional[Callable[[int, Optional[int]], None]] = None
		if handoff_id:
			update_handoff_progress(
				handoff_id,
				stage="downloading",
				message=progress_message,
				bytes_downloaded=0,
			)
			def _on_progress(downloaded: int, total: Optional[int]) -> None:
				update_handoff_progress(
					handoff_id,
					stage="downloading",
					message=progress_message,
					bytes_downloaded=downloaded,
					bytes_total=total,
				)
			progress_fn = _on_progress
		try:
			download_path = _download_remote_archive(
				download_url,
				force=True,
				progress_callback=progress_fn,
				handoff_id=handoff_id,
				desired_filename=desired_filename,
			)
			logger.info(
				"[nxm_handoff] download succeeded host=%s saved_as=%s",
				host,
				download_path.name,
			)
			if handoff_id:
				size = download_path.stat().st_size if download_path.exists() else None
				update_handoff_progress(
					handoff_id,
					stage="downloaded",
					message="Download complete",
					bytes_downloaded=size or 0,
					bytes_total=size,
				)
			return download_path, download_url
		except DownloadCancelledError:
			# User cancelled — clean up progress and re-raise so ingest catches it
			if handoff_id:
				update_handoff_progress(
					handoff_id,
					stage="cancelled",
					message="Download cancelled by user",
				)
			raise
		except HTTPException as exc:
			detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
			download_errors.append(detail)
			if handoff_id:
				update_handoff_progress(
					handoff_id,
					stage="retrying",
					message=detail,
					error=detail,
				)
			logger.warning("[nxm_handoff] download attempt failed url=%s detail=%s", download_url, detail)
		except Exception as exc:
			download_errors.append(str(exc))
			if handoff_id:
				update_handoff_progress(
					handoff_id,
					stage="retrying",
					message=str(exc),
					error=str(exc),
				)
			logger.warning("[nxm_handoff] download attempt failed url=%s detail=%s", download_url, exc)
	message = "; ".join(download_errors) if download_errors else "unknown error"
	if handoff_id:
		update_handoff_progress(
			handoff_id,
			stage="failed",
			error=message,
			message="Failed to download from Nexus CDN",
		)
	raise HTTPException(status_code=502, detail=f"Failed to download from Nexus CDN: {message}")


def _to_folder_name(tag: str) -> str:
	"""Convert a canonical character tag to a safe folder name (snake case)."""
	s = str(tag).lower()
	# Replace separators and special chars with underscores
	s = re.sub(r"[^a-z0-9]+", "_", s)
	# Collapse repeats and trim underscores
	s = re.sub(r"_+", "_", s).strip("_")
	return s or "misc"


def _infer_character_tag(cur, name: Optional[str], pak_candidates: List[str]) -> Optional[str]:
	"""Infer a canonical character tag for a download using pak_tags_json; fallback to name heuristics.
	Returns a canonical character name if found, otherwise None.
	"""
	# Aggregate tags for all candidate pak names from pak_tags_json
	tokens: set[str] = set()
	for pak in pak_candidates:
		if not pak:
			continue
		tr = cur.execute("SELECT tags_json FROM pak_tags_json WHERE pak_name = ?", (pak,)).fetchone()
		if (not tr or not tr[0]) and "." in pak:
			stem = os.path.splitext(pak)[0]
			for alt in (f"{stem}.utoc", f"{stem}.pak"):
				tr = cur.execute("SELECT tags_json FROM pak_tags_json WHERE pak_name = ?", (alt,)).fetchone()
				if tr and tr[0]:
					break
		if tr and tr[0]:
			try:
				arr = json.loads(tr[0])
				if isinstance(arr, list) and arr:
					for elem in arr:
						for t in str(elem).split(","):
							tok = t.strip()
							if tok:
								tokens.add(tok)
				else:
					for t in str(arr).split(","):
						tok = t.strip()
						if tok:
							tokens.add(tok)
			except Exception:
				pass
	# Fallback heuristics using name and candidate filenames
	if not tokens:
		try:
			canon = _load_canonical_names()
			text_parts = [name or ""] + list(pak_candidates)
			joined = " ".join([t for t in text_parts if isinstance(t, str)])
			spaced, compact = _normalize(joined)
			for cname in canon:
				cs, cc = _normalize(cname)
				if cs and (cs in spaced or cc in compact):
					tokens.add(cname)
		except Exception:
			pass
	# Canonicalize and pick first non-category token
	canon = _canonicalize_tokens(tokens)
	for t in canon:
		if t not in _KNOWN_CATEGORIES:
			return t
	return None


def _resolve_download_source_path(identifier: str) -> str:
	"""Resolve a local download source path from either a path-like string or a local_downloads.name.

	- If 'identifier' is an existing path (absolute or relative), return it.
	- Else, if it looks like an absolute path but doesn't exist, return as-is (to aid debugging).
	- Else, attempt to treat it as a local_downloads.name and fetch its 'path' from DB.
	- Finally, resolve any non-absolute file path relative to downloads root (MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT).
	"""
	try:
		p = Path(identifier)
		if p.exists():
			return str(p.resolve())
		if p.is_absolute():
			# Absolute but missing, return as-is
			return str(p)
	except Exception:
		pass
	# Try DB lookup by name
	try:
		conn = get_db()
		cur = conn.cursor()
		row = cur.execute(
			"SELECT path FROM local_downloads WHERE name = ? ORDER BY id DESC LIMIT 1",
			(identifier,),
		).fetchone()
		if row and row[0]:
			candidate = row[0]
			cp = Path(candidate)
			if cp.exists():
				return str(cp.resolve())
			# join with downloads root when relative
			if not cp.is_absolute():
				return str((_downloads_root_from_env() / cp).resolve())
	finally:
		try:
			conn.close()
		except Exception:
			pass
	# Fallback: treat identifier as relative path under downloads root
	return str((_downloads_root_from_env() / identifier).resolve())


def _ensure_dir(p: Path) -> None:
	try:
		p.mkdir(parents=True, exist_ok=True)
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Failed to create directory {p}: {e}")


## zip member extraction helper removed (use core.utils.archive.extract_member)


def _remove_in_mods_by_names(mods_dir: Path, names: List[str]) -> List[str]:
	"""Remove any files in mods_dir (recursively) whose basename is in names (case-insensitive)."""
	names_lower = {str(n).lower() for n in names if isinstance(n, str) and n}
	removed: List[str] = []
	try:
		for p in mods_dir.rglob("*"):
			if p.is_file() and p.name.lower() in names_lower:
				try:
					p.unlink()
					removed.append(p.name)
				except Exception:
					pass
	except Exception:
		pass
	return removed


def _remove_in_mods_by_stems(mods_dir: Path, stems: List[str]) -> List[str]:
	"""Remove any files in mods_dir (recursively) with basename matching stem + (.pak|.utoc|.ucas)."""
	targets = set()
	for st in stems:
		if not st:
			continue
		for ext in (".pak", ".utoc", ".ucas"):
			targets.add(f"{st}{ext}".lower())
	removed: List[str] = []
	try:
		for p in mods_dir.rglob("*"):
			if p.is_file() and p.name.lower() in targets:
				try:
					p.unlink()
					removed.append(p.name)
				except Exception:
					pass
	except Exception:
		pass
	return removed


@app.post("/api/local_downloads/delete")
def delete_local_downloads_endpoint(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Remove one or more local_downloads rows and cascade associated data."""
	if not isinstance(payload, dict):
		raise HTTPException(status_code=400, detail="JSON body required")
	ids_raw = payload.get("download_ids") or payload.get("ids") or []
	if ids_raw and not isinstance(ids_raw, list):
		raise HTTPException(status_code=400, detail="download_ids must be an array of integers")
	id_values: List[int] = []
	for raw in ids_raw:
		try:
			value = int(raw)
		except (TypeError, ValueError):
			continue
		if value < 0:
			continue
		if value not in id_values:
			id_values.append(value)
	mod_id_val = payload.get("mod_id")
	try:
		mod_id_int = int(mod_id_val) if mod_id_val is not None else None
	except (TypeError, ValueError):
		mod_id_int = None
	conn = get_db()
	try:
		if not id_values and mod_id_int is not None:
			cur = conn.cursor()
			rows = cur.execute(
				"SELECT id FROM local_downloads WHERE mod_id = ?",
				(mod_id_int,),
			).fetchall()
			id_values = [int(r[0]) for r in rows]
		if not id_values:
			return {"ok": True, "deleted": 0, "removed_mod_ids": []}
		deleted_count, removed_mod_ids, source_paths = delete_local_downloads(conn, id_values)
		downloads_root = _downloads_root_from_env().resolve()
		removed_files: List[str] = []
		missing_files: List[str] = []
		failed_files: List[str] = []
		seen_paths: set[str] = set()
		for raw_path in source_paths:
			if not raw_path or not isinstance(raw_path, str):
				continue
			key = raw_path.strip()
			if not key or key in seen_paths:
				continue
			seen_paths.add(key)
			try:
				absolute = Path(_resolve_download_source_path(key))
			except Exception:
				continue
			try:
				resolved = absolute.expanduser().resolve()
			except Exception:
				resolved = absolute.expanduser()
			if resolved == downloads_root:
				continue
			try:
				if not resolved.is_relative_to(downloads_root):
					continue
			except AttributeError:
				# Python < 3.9 compatibility fallback
				try:
					resolved.relative_to(downloads_root)
				except Exception:
					continue
			if not resolved.exists():
				missing_files.append(str(resolved))
				continue
			try:
				if resolved.is_dir():
					shutil.rmtree(resolved)
				else:
					resolved.unlink()
				removed_files.append(str(resolved))
			except Exception:
				failed_files.append(str(resolved))
		try:
			from scripts import build_asset_tags as _bat  # type: ignore
			from scripts import build_pak_tags as _bpt  # type: ignore
			_bat.main([])
			_bpt.main([])
		except Exception:
			pass
		_safe_rebuild_conflicts(conn, active_only=None, purpose="delete_local_downloads")
		return {
			"ok": True,
			"deleted": deleted_count,
			"removed_mod_ids": removed_mod_ids,
			"removed_files": removed_files,
			"missing_files": missing_files,
			"failed_files": failed_files,
		}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/mods/disable-all")
def disable_all_mods() -> Dict[str, Any]:
	"""Disable all active mods and clear the ~mods folder."""
	conn = get_db()
	try:
		# 1. Clear ~mods directory
		mods_dir = _mods_folder_from_env()
		if mods_dir.exists():
			import shutil
			try:
				shutil.rmtree(mods_dir)
			except Exception as e:
				logger.error(f"Failed to clear ~mods: {e}")
		try:
			mods_dir.mkdir(parents=True, exist_ok=True)
		except Exception:
			pass

		# 2. Update database
		conn.execute("UPDATE local_downloads SET active_paks = '[]'")
		conn.commit()
		_safe_rebuild_conflicts(conn, active_only=True, purpose="disable_all_mods")
		
		return {"ok": True}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/local_downloads/{download_id}/set-active")
def set_active_paks(download_id: int, payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Set the active pak list for a local_downloads row and mirror files into the game's ~mods folder.

	Body: { active_paks: ["SomePak_P.pak", ...] }
	- Copies requested paks from the local download source (.zip/.pak) into the ~mods folder.
	- Removes previously active paks for this download that are no longer requested.
	- Updates SQLite (active_paks plus last_activated/last_deactivated timestamps).
	"""
	req_active = payload.get("active_paks")
	if not isinstance(req_active, list):
		raise HTTPException(status_code=400, detail="active_paks must be an array of strings")
	desired_raw: List[str] = []
	desired_source_map: Dict[str, str] = {}  # basename.lower() → original relative path
	for x in req_active:
		if isinstance(x, str) and x.strip():
			cleaned = x.strip()
			base = os.path.basename(cleaned)
			desired_raw.append(cleaned)  # keep full relative path from frontend
			# Remember the original relative path for source file lookup
			desired_source_map[base.lower()] = cleaned
	# Load current row
	conn = get_db()
	cur = conn.cursor()
	row = cur.execute(
		"SELECT path, contents, active_paks, mod_id, name FROM local_downloads WHERE id = ?",
		(download_id,),
	).fetchone()
	if not row:
		try:
			conn.close()
		except Exception:
			pass
		raise HTTPException(status_code=404, detail="local_downloads row not found")
	src_path, contents_json, prev_active_json, row_mod_id, row_name = row
	
	# DIAGNOSTIC: Log what we read from database
	print(f"[ACTIVATE] Download ID {download_id}: name={row_name}")
	print(f"[ACTIVATE] contents_json length: {len(contents_json) if contents_json else 0}")
	print(f"[ACTIVATE] contents_json preview: {contents_json[:200] if contents_json else 'NULL'}")
	
	try:
		contents = json.loads(contents_json) if contents_json else []
		if not isinstance(contents, list):
			contents = []
	except Exception:
		contents = []
	try:
		prev_active = json.loads(prev_active_json) if prev_active_json else []
		if not isinstance(prev_active, list):
			prev_active = []
	except Exception:
		prev_active = []
	try:
		mod_id_for_download = int(row_mod_id) if row_mod_id is not None else None
	except (TypeError, ValueError):
		mod_id_for_download = None
	download_name = row_name if isinstance(row_name, str) else None
	related_contents: List[str] = []
	related_active: List[str] = []
	related_map: Dict[int, Tuple[List[str], List[str]]] = {}

	def _collect_related(rows: Iterable[Tuple[Any, Any, Any]]) -> None:
		nonlocal related_contents, related_active, related_map
		for other_id_raw, contents_raw, active_raw in rows:
			other_contents: List[str] = []
			other_active: List[str] = []
			try:
				loaded_contents = json.loads(contents_raw) if contents_raw else []
				if isinstance(loaded_contents, list):
					other_contents = [str(x) for x in loaded_contents if isinstance(x, str)]
			except Exception:
				other_contents = []
			try:
				loaded_active = json.loads(active_raw) if active_raw else []
				if isinstance(loaded_active, list):
					other_active = [str(x) for x in loaded_active if isinstance(x, str)]
			except Exception:
				other_active = []
			try:
				other_id = int(other_id_raw)
			except Exception:
				continue
			if other_id == download_id or other_id in related_map:
				continue
			related_map[other_id] = (other_contents, other_active)
			related_contents.extend(other_contents)
			related_active.extend(other_active)

	if mod_id_for_download is not None:
		try:
			related_rows = cur.execute(
				"SELECT id, contents, active_paks FROM local_downloads WHERE mod_id = ? AND id != ?",
				(mod_id_for_download, download_id),
			).fetchall()
			_collect_related(related_rows)
		except Exception:
			pass
	try:
		normalized_name = str(download_name).strip() if download_name else ""
		if normalized_name:
			name_rows = cur.execute(
				"SELECT id, contents, active_paks FROM local_downloads WHERE id != ? AND name = ? COLLATE NOCASE",
				(download_id, normalized_name),
			).fetchall()
			_collect_related(name_rows)
	except Exception:
		pass
	# Validate desired names against contents, case-insensitive, with .pak/.utoc stem fallback
	# Build lookup: basename.lower() → full relative path from contents
	valid_basenames = {os.path.basename(c).lower(): c for c in contents if isinstance(c, str) and c}
	valid_lower = set(valid_basenames.keys())
	def _alt_ext(name: str) -> List[str]:
		try:
			stem, ext = os.path.splitext(name)
			if ext.lower() == ".pak":
				return [name, f"{stem}.utoc"]
			if ext.lower() == ".utoc":
				return [name, f"{stem}.pak"]
			return [name]
		except Exception:
			return [name]
	# Resolve each incoming path to the matching contents relative path
	desired: List[str] = []  # will hold relative paths matching contents
	rel_to_basename: Dict[str, str] = {}  # relative path → basename (for file ops)
	for d in desired_raw:
		base_d = os.path.basename(d)
		dl = base_d.lower()
		# Try exact relative path match first (frontend may send full relative path)
		exact = next((c for c in contents if isinstance(c, str) and c.lower() == d.lower()), None)
		if exact:
			desired.append(exact)
			rel_to_basename[exact] = os.path.basename(exact)
			continue
		# Try basename match against contents
		if dl in valid_lower:
			rel_path = valid_basenames[dl]
			desired.append(rel_path)
			rel_to_basename[rel_path] = os.path.basename(rel_path)
			continue
		# Try alt extension
		alts = _alt_ext(base_d)
		found_rel = None
		for a in alts:
			al = a.lower()
			if al in valid_lower:
				found_rel = valid_basenames[al]
				break
		if found_rel:
			desired.append(found_rel)
			rel_to_basename[found_rel] = os.path.basename(found_rel)
			continue
		try:
			conn.close()
		except Exception:
			pass
		raise HTTPException(status_code=400, detail=f"Requested pak '{d}' is not part of this download's contents")
	# desired now contains relative paths from contents
	# Build basename list for file operations
	desired_basenames: List[str] = [os.path.basename(d) for d in desired]

	candidate_paks: Set[str] = set()
	for name in contents + prev_active + desired:
		if not isinstance(name, str):
			continue
		base = os.path.basename(name)
		if base:
			candidate_paks.add(base.lower())
	if candidate_paks:
		try:
			ordered = sorted(candidate_paks)
			placeholders = ",".join("?" for _ in ordered)
			params: List[Any] = [download_id, *ordered]
			shared_rows = cur.execute(
				f"""
				SELECT DISTINCT l.id, l.contents, l.active_paks
				FROM local_downloads l
				JOIN json_each(l.contents) AS c ON 1
				WHERE l.id != ?
				  AND LOWER(COALESCE(c.value, '')) IN ({placeholders})
				""",
				tuple(params),
			).fetchall()
			_collect_related(shared_rows)
		except Exception:
			pass

	related_downloads: List[Tuple[int, List[str], List[str]]] = [
		(other_id, data[0], data[1]) for other_id, data in related_map.items()
	]

	# Ensure ~mods exists
	mods_dir = _mods_folder_from_env()
	_ensure_dir(mods_dir)

	# Determine target subfolder by inferred character tag from DB tags/heuristics
	char_folder: Optional[Path] = None
	try:
		# candidate paks from contents desired list
		candidate_paks = [p for p in desired if isinstance(p, str)]
		tag = _infer_character_tag(cur, name=download_name, pak_candidates=candidate_paks)
		if tag:
			char_folder = mods_dir / _to_folder_name(tag)
	except Exception:
		char_folder = None
	# Fallback: reuse previous active tag mapping if current files are new
	if char_folder is None and prev_active:
		try:
			prev_candidates = [p for p in prev_active if isinstance(p, str)]
			alt_tag = _infer_character_tag(cur, name=download_name, pak_candidates=prev_candidates)
			if alt_tag:
				char_folder = mods_dir / _to_folder_name(alt_tag)
		except Exception:
			pass
	# Fallback: consider other downloads for this mod to infer a shared folder
	if char_folder is None and related_active:
		try:
			alt_tag = _infer_character_tag(cur, name=download_name, pak_candidates=related_active)
			if alt_tag:
				char_folder = mods_dir / _to_folder_name(alt_tag)
		except Exception:
			pass
	if char_folder is None and not related_active and related_contents:
		try:
			alt_tag = _infer_character_tag(cur, name=download_name, pak_candidates=related_contents)
			if alt_tag:
				char_folder = mods_dir / _to_folder_name(alt_tag)
		except Exception:
			pass
	# Last resort: locate existing destination of prior files within ~mods and reuse its parent folder
	if char_folder is None:
		extra_names = [p for p in related_active + related_contents if isinstance(p, str)]
		search_names = [p for p in desired + prev_active + extra_names if isinstance(p, str)]
		seen_lower: set[str] = set()
		for name in search_names:
			base = os.path.basename(name)
			if not base:
				continue
			lower = base.lower()
			if lower in seen_lower:
				continue
			seen_lower.add(lower)
			try:
				candidate_path: Optional[Path] = None
				for found in mods_dir.rglob(base):
					if not found.is_file():
						continue
					parent = found.parent
					try:
						parent.relative_to(mods_dir)
					except Exception:
						continue
					candidate_path = parent
					break
			except Exception:
				candidate_path = None
			if candidate_path is not None:
				char_folder = candidate_path
				break
	# Only create the char subfolder when we actually have files to copy
	if char_folder is not None and desired_basenames:
		try:
			_ensure_dir(char_folder)
		except HTTPException:
			raise
		except Exception:
			char_folder = None

	# Resolve source path: handle relative DB paths (resolve under MARVEL_RIVALS_MODS_ROOT) and names
	src_path = _resolve_download_source_path(str(src_path or ""))
	src_lower = src_path.lower()
	is_zip = src_lower.endswith('.zip')
	is_pak = src_lower.endswith('.pak')
	is_rar = src_lower.endswith('.rar')
	is_7z = src_lower.endswith('.7z')
	is_folder = os.path.isdir(src_path)

	if not os.path.exists(src_path):
		try:
			conn.close()
		except Exception:
			pass
		raise HTTPException(status_code=404, detail=f"Source archive not found: {src_path}")

	# Activate: copy requested paks and their IoStore companions (.utoc, .ucas) if present
	copied: List[str] = []
	companions: List[str] = []
	applied_set: set[str] = set()
	if is_zip or is_rar or is_7z:
		try:
			entries = list_entries(src_path)
			lookup = build_entry_lookup(entries)
			for item in desired:
				stem, _ext = os.path.splitext(item)
				# For each stem, try to extract .pak, .utoc, .ucas if present
				for ext in (".pak", ".utoc", ".ucas"):
					fname = f"{stem}{ext}"
					entry = resolve_entry(lookup, fname)
					if not entry:
						continue
					dest_base = char_folder if char_folder else mods_dir
					dest_fname = os.path.basename(fname)
					dest = dest_base / dest_fname
					if dest.exists():
						try:
							dest.unlink()
						except Exception:
							pass
					extract_member(src_path, entry, str(dest))
					applied_set.add(dest_fname)
					if dest_fname.lower() == os.path.basename(item).lower():
						copied.append(dest_fname)
					else:
						companions.append(dest_fname)
		except HTTPException:
			raise
		except Exception as e:
			raise HTTPException(status_code=500, detail=f"Archive extract failed: {e}")
	elif is_pak:
		# Single pak source; also copy sibling .utoc/.ucas if present alongside
		base = os.path.basename(src_path)
		if base in desired_basenames:
			dest_base = char_folder if char_folder else mods_dir
			dest = dest_base / base
			try:
				if dest.exists():
					dest.unlink()
				shutil.copy2(src_path, dest)
			except Exception as e:
				raise HTTPException(status_code=500, detail=f"Copy failed: {e}")
			copied.append(base)
			applied_set.add(base)
			# Try siblings for IoStore
			stem, _ = os.path.splitext(base)
			for ext in (".utoc", ".ucas"):
				cand = Path(src_path).with_suffix(ext)
				if cand.exists():
					dest_base = char_folder if char_folder else mods_dir
					d = dest_base / cand.name
					try:
						if d.exists():
							d.unlink()
						shutil.copy2(str(cand), d)
					except Exception as e:
						raise HTTPException(status_code=500, detail=f"Copy failed: {e}")
					companions.append(cand.name)
					applied_set.add(cand.name)
	elif is_folder:
		# Folder source: copy files directly from folder (using variant-aware lookup)
		src_folder = Path(src_path)
		try:
			for item in desired_basenames:
				stem, _ext = os.path.splitext(item)
				# Check if we have a relative path from the user's variant selection
				rel_path = desired_source_map.get(item.lower(), "")
				rel_stem = os.path.splitext(rel_path)[0] if rel_path else ""
				# For each stem, try to copy .pak, .utoc, .ucas if present
				for ext in (".pak", ".utoc", ".ucas"):
					fname = f"{stem}{ext}"
					src_file = None
					# 1. Try exact relative path from the selected variant
					if rel_stem:
						variant_file = src_folder / f"{rel_stem}{ext}".replace("/", os.sep)
						if variant_file.exists() and variant_file.is_file():
							src_file = variant_file
					# 2. Try direct path at folder root
					if src_file is None:
						direct = src_folder / fname
						if direct.exists() and direct.is_file():
							src_file = direct
					# 3. Fallback: search recursively by basename
					if src_file is None:
						basename = os.path.basename(fname)
						for candidate in src_folder.rglob(basename):
							if candidate.is_file():
								src_file = candidate
								break
					if src_file is None:
						continue
					dest_base = char_folder if char_folder else mods_dir
					dest = dest_base / os.path.basename(fname)
					try:
						if dest.exists():
							dest.unlink()
						shutil.copy2(str(src_file), str(dest))
					except Exception as e:
						raise HTTPException(status_code=500, detail=f"Copy failed: {e}")
					applied_set.add(os.path.basename(fname))
					if os.path.basename(fname).lower() == item.lower():
						copied.append(os.path.basename(fname))
					else:
						companions.append(os.path.basename(fname))
		except HTTPException:
			raise
		except Exception as e:
			raise HTTPException(status_code=500, detail=f"Folder copy failed: {e}")
	else:
		# For unknown sources, cannot auto-apply
		raise HTTPException(status_code=400, detail="Unsupported source type for auto-apply. Use .zip/.rar/.7z/.pak or folder containing .pak files.")

	# If nothing was newly extracted but files already existed, ensure they are considered applied
	if not applied_set and (is_zip or is_rar or is_7z):
		# Consider already-present main requested items as applied
		for item in desired_basenames:
			stem, _ = os.path.splitext(item)
			for ext in (".pak", ".utoc", ".ucas"):
				fname = f"{stem}{ext}"
				if (mods_dir / fname).exists():
					applied_set.add(fname)

	# Build the final applied list: desired relative paths + basename companions
	# Companions (IoStore files) don't have relative paths in contents, use basenames
	applied: List[str] = sorted({*desired, *applied_set})

	# Deactivate: remove files no longer desired (best-effort)
	# Compare by basename since prev_active may have basenames (legacy) or relative paths
	applied_basenames_set = {os.path.basename(p).lower() for p in applied}
	to_remove = [p for p in prev_active if os.path.basename(p).lower() not in applied_basenames_set]
	removed: List[str] = []
	# Try direct path first, then recursive search by basename (handles char subfolders)
	for pak in to_remove:
		base = os.path.basename(pak)
		deleted = False
		# 1. Try direct path
		fp = mods_dir / pak
		try:
			if fp.exists():
				fp.unlink()
				removed.append(pak)
				deleted = True
		except Exception:
			pass
		# 2. Recursive search by basename in all subfolders
		if not deleted:
			try:
				for found in mods_dir.rglob(base):
					if found.is_file():
						try:
							found.unlink()
							removed.append(pak)
						except Exception:
							pass
			except Exception:
				pass

	# Additionally, ensure IoStore companions are removed by stem when a pak gets deactivated
	def _stem_of(fname: str) -> Optional[str]:
		try:
			st, ext = os.path.splitext(os.path.basename(fname))
			if ext.lower() in (".pak", ".utoc", ".ucas"):
				return st
			return None
		except Exception:
			return None
	prev_stems = {s for s in (_stem_of(x) for x in prev_active) if s}
	applied_stems = {s for s in (_stem_of(x) for x in applied) if s}
	stems_to_remove = prev_stems - applied_stems
	# Remove companions by stems in any subfolder
	for stem in stems_to_remove:
		for ext in (".pak", ".utoc", ".ucas"):
			target_name = f"{stem}{ext}"
			try:
				for found in mods_dir.rglob(target_name):
					if found.is_file():
						try:
							found.unlink()
							removed.append(target_name)
						except Exception:
							pass
			except Exception:
				pass

	# Clean up empty subdirectories left behind after file removal
	if removed:
		try:
			for dirpath, dirnames, filenames in os.walk(str(mods_dir), topdown=False):
				dp = Path(dirpath)
				if dp == mods_dir:
					continue
				if not filenames and not dirnames:
					try:
						dp.rmdir()
					except Exception:
						pass
		except Exception:
			pass

	# Persist new active list
	try:
		update_local_download_active_paks(conn, download_id, applied)
		if related_downloads:
			applied_lower = {os.path.basename(name).lower() for name in applied}
			for other_id, _other_contents, other_active in related_downloads:
				if not other_active:
					continue
				filtered_active: List[str] = []
				changed = False
				for name in other_active:
					base = os.path.basename(name)
					if base.lower() in applied_lower:
						changed = True
						continue
					filtered_active.append(name)
				if changed:
					update_local_download_active_paks(conn, other_id, filtered_active)
	except Exception as e:
		try:
			conn.close()
		except Exception:
			pass
		raise HTTPException(status_code=500, detail=f"DB update failed: {e}")

	# Sync DB with on-disk state and refresh conflicts
	try:
		scan_active_main(_get_scan_active_args())
	except Exception:
		pass
	_safe_rebuild_conflicts(conn, active_only=True, purpose="set_active_paks")
	try:
		conn.close()
	except Exception:
		pass
	return {
		"ok": True,
		"download_id": download_id,
		"active_paks": applied,
		"copied": copied,
		"removed": removed,
		"mods_dir": str(mods_dir),
	}


@app.post("/api/local_downloads/activate-by-name")
def activate_by_name(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Activate all pak files for the given local_download name by extracting its archive to ~mods.

	Body: { name: string }
	"""
	name = payload.get("name")
	if not name or not isinstance(name, str):
		raise HTTPException(status_code=400, detail="name is required")
	conn = get_db()
	cur = conn.cursor()
	row = cur.execute(
		"SELECT id, path, contents FROM local_downloads WHERE name = ? ORDER BY id DESC LIMIT 1",
		(name,),
	).fetchone()
	if not row:
		try:
			conn.close()
		except Exception:
			pass
		raise HTTPException(status_code=404, detail="local_download not found by name")
	dl_id, rel_path, contents_json = row
	# Resolve full path using helper; supports absolute, relative, or name lookup
	full_path = _resolve_download_source_path(str(rel_path or name))
	# Ensure destination ~mods exists
	mods_dir = _mods_folder_from_env()
	_ensure_dir(mods_dir)
	# Determine target subfolder by inferred character tag
	char_folder: Optional[Path] = None
	try:
		try:
			contents = json.loads(contents_json) if contents_json else []
		except Exception:
			contents = []
		candidate_paks = [c for c in contents if isinstance(c, str) and c.lower().endswith('.pak')]
		tag = _infer_character_tag(cur, name=name, pak_candidates=candidate_paks)
		if tag:
			char_folder = mods_dir / _to_folder_name(tag)
			_ensure_dir(char_folder)
	except Exception:
		char_folder = None
	# Extract only the .pak files into ~mods
	lower = full_path.lower()
	copied: List[str] = []
	companions: List[str] = []
	applied_set: set[str] = set()
	try:
		if lower.endswith((".zip", ".rar", ".7z")):
			entries = list_entries(full_path)
			lookup = build_entry_lookup(entries)
			try:
				contents = json.loads(contents_json) if contents_json else []
			except Exception:
				contents = []
			desired = [c for c in contents if isinstance(c, str) and c.lower().endswith('.pak')]
			for pak in desired:
				stem, _ = os.path.splitext(pak)
				for ext in (".pak", ".utoc", ".ucas"):
					fname = f"{stem}{ext}"
					entry = resolve_entry(lookup, fname)
					if not entry:
						continue
					dest_base = char_folder if char_folder else mods_dir
					dest = dest_base / fname
					if dest.exists():
						applied_set.add(fname)
						continue
					extract_member(full_path, entry, str(dest))
					applied_set.add(fname)
					if ext == ".pak":
						copied.append(fname)
					else:
						companions.append(fname)
		elif lower.endswith(".pak"):
			base = os.path.basename(full_path)
			dest_base = char_folder if char_folder else mods_dir
			dest = dest_base / base
			if not dest.exists():
				shutil.copy2(full_path, dest)
				copied.append(base)
				applied_set.add(base)
			# Try siblings .utoc/.ucas
			stem, _ = os.path.splitext(base)
			for ext in (".utoc", ".ucas"):
				cand = Path(full_path).with_suffix(ext)
				if cand.exists():
					dest_base = char_folder if char_folder else mods_dir
					d = dest_base / cand.name
					if not d.exists():
						shutil.copy2(str(cand), d)
						companions.append(cand.name)
						applied_set.add(cand.name)
		else:
			raise HTTPException(status_code=400, detail="Unsupported source type for activation. Use .zip/.rar/.7z/.pak")
	except HTTPException:
		raise
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"Activation failed: {e}")
	# Update DB active_paks and rescan
	try:
		if copied or companions or applied_set:
			# Merge copied into current active for this download id
			try:
				prev = cur.execute("SELECT active_paks FROM local_downloads WHERE id=?", (dl_id,)).fetchone()
				active = []
				if prev and prev[0]:
					active = json.loads(prev[0]) if isinstance(prev[0], str) else []
				merged = list({*(active or []), *copied, *companions, *applied_set})
				update_local_download_active_paks(conn, dl_id, merged)
			except Exception:
				pass
		scan_active_main(_get_scan_active_args())
		_safe_rebuild_conflicts(conn, active_only=True, purpose="activate_by_name")
	finally:
		try:
			conn.close()
		except Exception:
			pass
	return {"ok": True, "name": name, "copied": copied, "mods_dir": str(mods_dir)}


@app.post("/api/local_downloads/deactivate-by-name")
def deactivate_by_name(payload: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
	"""Deactivate (remove) all pak files for the given local_download name from ~mods and DB.

	Body: { name: string }
	"""
	name = payload.get("name")
	if not name or not isinstance(name, str):
		raise HTTPException(status_code=400, detail="name is required")
	conn = get_db()
	cur = conn.cursor()
	row = cur.execute(
		"SELECT id, contents FROM local_downloads WHERE name = ? ORDER BY id DESC LIMIT 1",
		(name,),
	).fetchone()
	if not row:
		try:
			conn.close()
		except Exception:
			pass
		raise HTTPException(status_code=404, detail="local_download not found by name")
	dl_id, contents_json = row
	try:
		contents = json.loads(contents_json) if contents_json else []
		pak_names = [os.path.basename(c) for c in contents if isinstance(c, str) and c.lower().endswith('.pak')]
	except Exception:
		pak_names = []
	mods_dir = _mods_folder_from_env()
	removed: List[str] = []
	# Remove by stems (handles .pak/.utoc/.ucas and nested folders)
	stems = [os.path.splitext(p)[0] for p in pak_names]
	removed += _remove_in_mods_by_stems(mods_dir, stems)
	# Also attempt direct/name-based removal as a safety (in case of unusual extensions)
	removed += _remove_in_mods_by_names(mods_dir, pak_names)
	# Update DB active_paks and rescan
	try:
		update_local_download_active_paks(conn, dl_id, [])
	except Exception:
		pass
	try:
		scan_active_main(_get_scan_active_args())
		_safe_rebuild_conflicts(conn, active_only=True, purpose="deactivate_by_name")
	finally:
		try:
			conn.close()
		except Exception:
			pass
	return {"ok": True, "name": name, "removed": removed}


@app.post("/api/scan/active")
def scan_active_endpoint() -> Dict[str, Any]:
	"""Trigger a filesystem scan of ~mods and update local_downloads.active_paks accordingly."""
	# Validate configuration before scanning
	try:
		_mods_folder_from_env()
	except HTTPException as e:
		raise e
	try:
		scan_active_main(_get_scan_active_args())
	except Exception as e:
		raise HTTPException(status_code=500, detail=f"scan failed: {e}")
	return {"ok": True}


@app.get("/api/local_downloads/{download_id}")
def get_local_download(download_id: int) -> Dict[str, Any]:
	"""Return a single local_download row with parsed contents and active_paks."""
	conn = get_db()
	try:
		cur = conn.cursor()
		row = cur.execute(
			"""
			SELECT l.id, l.name, l.mod_id, l.version, l.path, l.contents, l.active_paks, l.created_at,
			   variant_latest.version AS file_version,
			   variant_latest.uploaded_at AS latest_uploaded_at,
			   variant_latest.file_id AS latest_file_id,
			   variant_latest.version_key AS latest_version_key,
			   variant_latest.name AS file_name
			FROM local_downloads l
			LEFT JOIN v_mods_with_latest_by_version overall_latest ON overall_latest.mod_id = l.mod_id
			LEFT JOIN (
				SELECT mod_id, file_id, version, uploaded_at, name, version_key,
					   ROW_NUMBER() OVER (PARTITION BY mod_id, REPLACE(REPLACE(REPLACE(LOWER(name), ' ', ''), '-', ''), '_', '') ORDER BY uploaded_at DESC, file_id DESC) as rn
				FROM mod_files
			) variant_latest ON variant_latest.mod_id = l.mod_id 
				AND variant_latest.rn = 1 
				AND REPLACE(REPLACE(REPLACE(LOWER(variant_latest.name), ' ', ''), '-', ''), '_', '') = REPLACE(REPLACE(REPLACE(LOWER(l.name), ' ', ''), '-', ''), '_', '')
			WHERE l.id = ?
			""",
			(download_id,),
		).fetchone()
		if not row:
			raise HTTPException(status_code=404, detail="local_download not found")
		
		(id_, name, mod_id, version, path, contents_raw, active_raw, created_at,
		 latest_version, latest_uploaded_at, latest_file_id, latest_version_key, latest_file_name) = row

		try:
			contents = json.loads(contents_raw) if contents_raw else []
			if not isinstance(contents, list):
				contents = []
		except Exception:
			contents = []
		try:
			active_paks = json.loads(active_raw) if active_raw else []
			if not isinstance(active_paks, list):
				active_paks = []
		except Exception:
			active_paks = []

		import logging
		logger = logging.getLogger("modmanager.api.downloads")
		actual_active_filenames = _get_actually_active_filenames(logger)
		if actual_active_filenames is not None:
			filtered_active_paks = []
			for p in active_paks:
				basename = os.path.basename(p).lower()
				if basename in actual_active_filenames:
					filtered_active_paks.append(p)
			if len(filtered_active_paks) != len(active_paks):
				logger.info(f"[get_local_download] download_id={download_id} active_paks changed from {active_paks} to {filtered_active_paks} (files not found in ~mods)")
				try:
					from core.db.db import update_local_download_active_paks
					update_local_download_active_paks(conn, download_id, filtered_active_paks)
				except Exception as update_err:
					logger.warning(f"[get_local_download] Failed to update out-of-sync active_paks: {update_err}")
				active_paks = filtered_active_paks

		local_version_key = make_version_key(version)[0]
		needs_update = False
		if versions_equivalent(version, latest_version):
			needs_update = False
		elif latest_version_key and local_version_key:
			needs_update = latest_version_key > local_version_key
		elif latest_version and (version or "").strip():
			needs_update = latest_version.strip() != (version or "").strip()

		return {
			"id": id_,
			"name": name,
			"mod_id": mod_id,
			"version": version,
			"path": path,
			"contents": contents,
			"active_paks": active_paks,
			"created_at": created_at,
			"latest_version": latest_version,
			"latest_uploaded_at": latest_uploaded_at,
			"latest_file_id": latest_file_id,
			"latest_version_key": latest_version_key,
			"latest_file_name": latest_file_name,
			"local_version_key": local_version_key,
			"needs_update": needs_update,
		}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/pak-assets")
def get_pak_assets(download_ids: Optional[str] = None) -> List[Dict[str, Any]]:
	"""Fetch pak assets from pak_assets_json table for given download IDs.
	
	Query params:
	  - download_ids: Comma-separated list of local download IDs
	
	Returns:
	  List of objects with:
	    - pak_name: str
	    - assets: list of asset paths (strings)
	"""
	conn = get_db()
	try:
		if not download_ids:
			return []
			
		# Parse download IDs
		ids: Set[int] = set()
		for token in re.split(r"[,\\s]+", str(download_ids)):
			if not token:
				continue
			try:
				value = int(token)
			except (TypeError, ValueError):
				continue
			if value >= 0:
				ids.add(value)
				
		if not ids:
			return []
			
		# First, get all pak names associated with these download IDs from mod_paks table
		cur = conn.cursor()
		placeholders = ",".join("?" for _ in ids)
		pak_rows = cur.execute(
			f"""
			SELECT DISTINCT pak_name
			FROM mod_paks
			WHERE local_download_id IN ({placeholders})
			""",
			tuple(ids),
		).fetchall()
		
		if not pak_rows:
			return []
			
		pak_names = [row[0] for row in pak_rows if row[0]]
		
		# Now fetch assets from pak_assets_json for these pak names
		pak_placeholders = ",".join("?" for _ in pak_names)
		asset_rows = cur.execute(
			f"""
			SELECT pak_name, assets_json
			FROM pak_assets_json
			WHERE pak_name IN ({pak_placeholders})
			""",
			tuple(pak_names),
		).fetchall()
		
		result: List[Dict[str, Any]] = []
		for pak_name, assets_json in asset_rows:
			try:
				assets = json.loads(assets_json) if assets_json else []
				if not isinstance(assets, list):
					assets = []
			except Exception:
				assets = []
				
			result.append({
				"pak_name": pak_name,
				"assets": assets,
			})
				
		return result
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.delete("/api/mods/{mod_id}")
def delete_mod_endpoint(mod_id: int) -> Dict[str, Any]:
	"""Delete all local downloads for a specific mod and clean up associated metadata.
	
	EXECUTION FLOW:
	1. Find all local_downloads entries for the given mod_id
	2. For each download being deleted:
	   - Check if it has active_paks (is currently activated)
	   - If active, call update_local_download_active_paks() to deactivate it first
	   - This removes the mod files from the game's ~mods folder
	3. Delete the local_downloads entries from the database
	4. Clean up associated mod metadata if no downloads remain
	5. Return success status and cleanup details
	
	This ensures that activated mods are properly deactivated before removal,
	preventing orphaned files in the game's mod directory.
	
	Returns:
		- ok: Boolean success status
		- deleted: Number of downloads actually deleted
		- removed_mod_ids: List of mod IDs that were cleaned up
		- source_paths: List of file paths that were removed from disk
		- message: Human-readable status message
	"""
	conn = get_db()
	try:
		# Get all download IDs for this mod first
		cur = conn.cursor()
		rows = cur.execute(
			"SELECT id FROM local_downloads WHERE mod_id = ?",
			(mod_id,),
		).fetchall()
		
		if not rows:
			return {"ok": True, "deleted": 0, "message": "No downloads found for this mod"}
		
		download_ids = [int(r[0]) for r in rows]
		
		# Use the existing delete_local_downloads function which now handles deactivation
		deleted_count, removed_mod_ids, source_paths = delete_local_downloads(conn, download_ids)
		
		return {
			"ok": True,
			"deleted": deleted_count,
			"removed_mod_ids": removed_mod_ids,
			"source_paths": source_paths,
			"message": f"Successfully deleted mod {mod_id} and its associated downloads"
		}
	finally:
		try:
			conn.close()
		except Exception:
			pass


# ─── Collections API ──────────────────────────────────────────────────────────

_NEXUS_COLLECTIONS_GRAPHQL = "https://api.nexusmods.com/v2/graphql"

_COLLECTION_QUERY = """
query CollectionRevision($slug: String!, $revision: Int) {
  collectionRevision(slug: $slug, revision: $revision) {
    id
    revisionNumber
    status
    modCount
    createdAt
    updatedAt
    totalSize
    collection {
      id
      slug
      name
      summary
      tileImage { url }
      user { name }
      game { domainName }
    }
    modFiles {
      id
      fileId
      optional
      version
      file {
        fileId
        modId
        name
        sizeInBytes
        version
        uri
        mod {
          name
          pictureUrl
        }
      }
    }
  }
}
"""


def _parse_collection_nxm(nxm_url: str) -> tuple[str, Optional[int]]:
	"""Extract (slug, revision_num) from an nxm://game/collections/{slug}/revisions/{N} URL."""
	import re
	m = re.search(r"collections/([^/\s?]+)(?:/revisions/(\d+))?", nxm_url, re.IGNORECASE)
	if not m:
		raise HTTPException(status_code=400, detail=f"Cannot parse collection slug from: {nxm_url!r}")
	slug = m.group(1)
	revision = int(m.group(2)) if m.group(2) else None
	return slug, revision


def _fetch_collection_from_nexus(slug: str, revision_num: Optional[int] = None) -> Dict[str, Any]:
	"""Fetch collection data from Nexus GraphQL API."""
	import requests as _req
	api_key = get_api_key()
	headers: Dict[str, str] = {"Content-Type": "application/json"}
	if api_key:
		headers["apikey"] = api_key
	variables: Dict[str, Any] = {"slug": slug}
	if revision_num is not None:
		variables["revision"] = revision_num
	try:
		resp = _req.post(
			_NEXUS_COLLECTIONS_GRAPHQL,
			json={"query": _COLLECTION_QUERY, "variables": variables},
			headers=headers,
			timeout=30,
			verify=False,
		)
		resp.raise_for_status()
		data = resp.json()
	except Exception as exc:
		raise HTTPException(status_code=502, detail=f"Nexus Collections API request failed: {exc}")
	if "errors" in data and data["errors"]:
		msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
		raise HTTPException(status_code=502, detail=f"Nexus GraphQL error: {msgs}")
	revision = (data.get("data") or {}).get("collectionRevision")
	if not revision:
		raise HTTPException(status_code=404, detail=f"Collection '{slug}' rev {revision_num} not found on Nexus")
	return revision


def _upsert_collection(conn, revision: Dict[str, Any], slug: str) -> int:
	"""Insert or replace a collection and its mod files. Returns collection DB id."""
	import json as _json
	col = revision.get("collection") or {}
	cur = conn.cursor()
	existing = cur.execute(
		"SELECT id FROM collections WHERE slug = ? AND COALESCE(revision_num, -1) = COALESCE(?, -1)",
		(slug, revision.get("revisionNumber")),
	).fetchone()
	if existing:
		cid = existing[0]
		cur.execute(
			"""UPDATE collections SET
				nexus_id=?, revision_id=?, revision_num=?, name=?, summary=?,
				picture_url=?, author=?, total_mods=?, total_size=?,
				status=?, created_at=?, updated_at=?, fetched_at=datetime('now'), raw_json=?
			WHERE id=?""",
			(
				col.get("id"), revision.get("id"), revision.get("revisionNumber"),
				col.get("name"), col.get("summary"), (col.get("tileImage") or {}).get("url"),
				(col.get("user") or {}).get("name"),
				revision.get("modCount"), int(revision.get("totalSize") or 0),
				revision.get("status"), revision.get("createdAt"), revision.get("updatedAt"),
				_json.dumps(revision), cid,
			),
		)
		# Delete old mod files to re-insert fresh
		cur.execute("DELETE FROM collection_mod_files WHERE collection_id = ?", (cid,))
	else:
		cur.execute(
			"""INSERT INTO collections
				(slug, nexus_id, revision_id, revision_num, game, name, summary,
				 picture_url, author, total_mods, total_size, status, created_at, updated_at, raw_json)
			VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
			(
				slug, col.get("id"), revision.get("id"), revision.get("revisionNumber"),
				(col.get("game") or {}).get("domainName") or "marvelrivals",
				col.get("name"), col.get("summary"), (col.get("tileImage") or {}).get("url"),
				(col.get("user") or {}).get("name"),
				revision.get("modCount"), int(revision.get("totalSize") or 0),
				revision.get("status"), revision.get("createdAt"), revision.get("updatedAt"),
				_json.dumps(revision),
			),
		)
		cid = cur.lastrowid

	# Insert mod files
	for mf in revision.get("modFiles") or []:
		f = mf.get("file") or {}
		mod = f.get("mod") or {}
		
		# Skip UTOC Signature Bypass Patch (Mod 2940) as requested by user
		# It's a tool and doesn't need to be installed via the collection flow
		parsed_mod_id = int(f.get("modId") or 0) or None
		if parsed_mod_id == 2940:
			continue
			
		cur.execute(
			"""INSERT INTO collection_mod_files
				(collection_id, entry_id, file_id, mod_id, optional, version,
				 file_name, file_uri, size_in_bytes, mod_name, picture_url)
			VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
			(
				cid,
				str(mf.get("id") or ""),
				int(f.get("fileId") or mf.get("fileId") or 0),
				parsed_mod_id,
				1 if mf.get("optional") else 0,
				str(mf.get("version") or f.get("version") or ""),
				f.get("name") or "",
				f.get("uri") or "",
				int(f.get("sizeInBytes") or 0) or None,
				mod.get("name") or f.get("name") or "",
				mod.get("pictureUrl") or "",
			),
		)
	conn.commit()
	return cid


def _serialize_collection(conn, cid: int) -> Dict[str, Any]:
	"""Return a full collection dict with mod_files list for the API response."""
	cur = conn.cursor()
	row = cur.execute(
		"SELECT id, slug, nexus_id, revision_id, revision_num, game, name, summary, "
		"picture_url, author, total_mods, total_size, status, created_at, updated_at, fetched_at "
		"FROM collections WHERE id = ?", (cid,)
	).fetchone()
	if not row:
		raise HTTPException(status_code=404, detail="Collection not found")
	keys = ["id","slug","nexus_id","revision_id","revision_num","game","name","summary",
			"picture_url","author","total_mods","total_size","status","created_at","updated_at","fetched_at"]
	result = dict(zip(keys, row))

	files = cur.execute(
		"SELECT id, entry_id, file_id, mod_id, optional, version, file_name, file_uri, "
		"size_in_bytes, mod_name, picture_url, download_state "
		"FROM collection_mod_files WHERE collection_id = ? AND (mod_id IS NULL OR mod_id != 2940) ORDER BY id",
		(cid,)
	).fetchall()
	fkeys = ["id","entry_id","file_id","mod_id","optional","version","file_name","file_uri",
			 "size_in_bytes","mod_name","picture_url","download_state"]
	result["mod_files"] = [dict(zip(fkeys, r)) for r in files]
	return result


@app.post("/api/collections/import")
def import_collection(body: Dict[str, Any]) -> Dict[str, Any]:
	"""Import a Nexus collection by NXM URL or slug+revision. Fetches from Nexus API and stores in DB."""
	nxm_url: Optional[str] = body.get("nxm_url")
	slug: Optional[str] = body.get("slug")
	revision_num: Optional[int] = body.get("revision")

	if nxm_url:
		slug, revision_num = _parse_collection_nxm(nxm_url)
	elif not slug:
		raise HTTPException(status_code=400, detail="Provide 'nxm_url' or 'slug'")

	revision = _fetch_collection_from_nexus(slug, revision_num)
	conn = get_db()
	try:
		cid = _upsert_collection(conn, revision, slug)
		return {"ok": True, "collection": _serialize_collection(conn, cid)}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/collections/import-raw")
def import_collection_raw(body: Dict[str, Any]) -> Dict[str, Any]:
	"""Import a collection from a raw GraphQL response payload (for seeding from JSON)."""
	import json as _json
	revision = body.get("revision")
	slug = body.get("slug")
	if not revision or not slug:
		raise HTTPException(status_code=400, detail="Provide 'revision' and 'slug'")
	conn = get_db()
	try:
		cid = _upsert_collection(conn, revision, slug)
		return {"ok": True, "collection": _serialize_collection(conn, cid)}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/collections")
def list_collections() -> Dict[str, Any]:
	"""List all stored collections (summary, no mod_files)."""
	conn = get_db()
	try:
		cur = conn.cursor()
		rows = cur.execute(
			"SELECT id, slug, nexus_id, revision_num, game, name, summary, picture_url, "
			"author, total_mods, total_size, status, updated_at, fetched_at "
			"FROM collections ORDER BY fetched_at DESC"
		).fetchall()
		keys = ["id","slug","nexus_id","revision_num","game","name","summary","picture_url",
				"author","total_mods","total_size","status","updated_at","fetched_at"]
		return {"ok": True, "collections": [dict(zip(keys, r)) for r in rows]}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.get("/api/collections/{collection_id}")
def get_collection(collection_id: int) -> Dict[str, Any]:
	"""Get a full collection with its mod_files list."""
	conn = get_db()
	try:
		return {"ok": True, "collection": _serialize_collection(conn, collection_id)}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.delete("/api/collections/{collection_id}")
def delete_collection(collection_id: int) -> Dict[str, Any]:
	"""Remove a collection (and its mod_files via CASCADE)."""
	conn = get_db()
	try:
		cur = conn.cursor()
		cur.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
		conn.commit()
		return {"ok": True}
	finally:
		try:
			conn.close()
		except Exception:
			pass


@app.post("/api/collections/{collection_id}/refresh")
def refresh_collection(collection_id: int) -> Dict[str, Any]:
	"""Re-fetch collection data from Nexus and update the DB."""
	conn = get_db()
	try:
		cur = conn.cursor()
		row = cur.execute(
			"SELECT slug, revision_num FROM collections WHERE id = ?", (collection_id,)
		).fetchone()
		if not row:
			raise HTTPException(status_code=404, detail="Collection not found")
		slug, revision_num = row
	finally:
		try:
			conn.close()
		except Exception:
			pass

	revision = _fetch_collection_from_nexus(slug, revision_num)
	conn2 = get_db()
	try:
		cid = _upsert_collection(conn2, revision, slug)
		return {"ok": True, "collection": _serialize_collection(conn2, cid)}
	finally:
		try:
			conn2.close()
		except Exception:
			pass


@app.patch("/api/collections/{collection_id}/mod-files/{file_id}/state")
def update_mod_file_state(collection_id: int, file_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
	"""Update the download_state of a collection mod file (pending|downloading|downloaded|failed)."""
	state: str = body.get("state", "pending")
	valid = {"pending", "downloading", "downloaded", "failed"}
	if state not in valid:
		raise HTTPException(status_code=400, detail=f"state must be one of {valid}")
	conn = get_db()
	try:
		cur = conn.cursor()
		cur.execute(
			"UPDATE collection_mod_files SET download_state = ? "
			"WHERE collection_id = ? AND file_id = ?",
			(state, collection_id, file_id),
		)
		conn.commit()
		return {"ok": True}
	finally:
		try:
			conn.close()
		except Exception:
			pass
