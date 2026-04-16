
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

# Reload settings from disk to ensure we have the latest configuration
from core.config.settings import reload_settings
reload_settings()

from core.db import (
	get_connection,
	init_schema,
	rebuild_conflicts,
	replace_local_downloads,
	replace_mod_changelogs,
	replace_mod_files,
	run_migrations,
	upsert_api_cache,
	upsert_mod_info,
)
from core.ingestion.scan_active_mods import main as scan_active_main
from core.ingestion.scan_mod_downloads import (
	build_download_row,
	list_files_level_one_including_root,
)
from core.nexus.nexus_api import DEFAULT_GAME, collect_all_for_mod, get_api_key
from core.utils.nexus_metadata import (
	derive_changelogs_from_files,
	extract_description_text,
)
from field_prefs import filter_aggregate_payload, load_prefs
from scripts import ingest_download_assets, rebuild_tags


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Rebuild mods.db and the related materialized tables from canonical sources.",
	)
	parser.add_argument("--db", dest="db_path", default=None, help="Path to mods.db (default: project root).")
	parser.add_argument(
		"--downloads-json",
		dest="downloads_json",
		help="Optional downloads_list.json export to seed local_downloads.",
	)
	parser.add_argument(
		"--downloads-root",
		dest="downloads_root",
		help="Optional root directory to rescan for local downloads (fallback when no JSON provided).",
	)
	parser.add_argument(
		"--downloads-limit",
		type=int,
		default=None,
		help="Optional cap on local download entries processed (useful for smoke tests).",
	)
	parser.add_argument("--skip-downloads", action="store_true", help="Skip rebuilding local_downloads.")
	parser.add_argument("--skip-sync", action="store_true", help="Skip Nexus API metadata sync.")
	parser.add_argument("--skip-ingest", action="store_true", help="Skip rebuilding pak assets via extraction.")
	parser.add_argument("--skip-tags", action="store_true", help="Skip rebuilding asset_tags and pak_tags_json.")
	parser.add_argument("--skip-conflicts", action="store_true", help="Skip rebuilding conflict materialized tables.")
	parser.add_argument(
		"--skip-active-scan",
		action="store_true",
		help="Skip scanning installed ~mods directory to populate local_downloads.active_paks.",
	)
	parser.add_argument("--no-extract", action="store_true", help="Do not extract archives when ingesting pak assets.")
	parser.add_argument(
		"--force-reset",
		action="store_true",
		help="Remove the existing DB before rebuilding (makes a timestamped backup when --keep-backup is set).",
	)
	parser.add_argument(
		"--keep-backup",
		action="store_true",
		help="Preserve a timestamped copy of the previous DB when resetting.",
	)
	parser.add_argument("--map", dest="map_path", help="Optional character_ids.json mapping for tagging.")
	parser.add_argument(
		"--game-root",
		dest="game_root",
		help="Path to Marvel Rivals installation root (overrides MARVEL_RIVALS_ROOT for the active scan).",
	)
	parser.add_argument("--game", default=DEFAULT_GAME, help="Nexus game slug (default: %(default)s).")
	parser.add_argument(
		"--rate-delay",
		type=float,
		default=0.6,
		help="Sleep seconds between Nexus API calls (default: %(default)s).",
	)
	parser.add_argument(
		"--log-level",
		default="INFO",
		choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
		help="Logging level (default: %(default)s).",
	)
	return parser.parse_args(argv)


def _load_env(dotenv_path: Optional[Path] = None) -> None:
	path = dotenv_path or (ROOT / ".env")
	try:
		if not path.exists():
			return
		for raw in path.read_text(encoding="utf-8").splitlines():
			line = raw.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			key, value = line.split("=", 1)
			k = key.strip()
			if not k or k in os.environ:
				continue
			v = value.strip()
			if (v.startswith("\"") and v.endswith("\"")) or (v.startswith("'") and v.endswith("'")):
				v = v[1:-1]
			os.environ[k] = v
	except Exception:
		return


def _resolve_db_path(raw: Optional[str]) -> Path:
	if raw:
		return Path(raw).expanduser().resolve()
	return ROOT / "mods.db"


def _resolve_downloads_root(override: Optional[str]) -> Path:
	"""Resolve downloads root from override, SETTINGS, or environment (in that order)."""
	from core.config.settings import SETTINGS
	
	candidate = override or (str(SETTINGS.marvel_rivals_local_downloads_root) if SETTINGS.marvel_rivals_local_downloads_root else None) or os.environ.get("MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT")
	if not candidate:
		raise RuntimeError(
			"Marvel Rivals local downloads root is not configured. "
			"Please configure it in Settings or pass --downloads-root."
		)
	root = Path(candidate).expanduser().resolve()
	if not root.exists():
		raise FileNotFoundError(f"Downloads root not found: {root}")
	return root


def _resolve_game_root(override: Optional[str]) -> Path:
	"""Resolve game root from override, SETTINGS, or environment (in that order)."""
	from core.config.settings import SETTINGS
	
	candidate = override or (str(SETTINGS.marvel_rivals_root) if SETTINGS.marvel_rivals_root else None) or os.environ.get("MARVEL_RIVALS_ROOT")
	if not candidate:
		raise RuntimeError(
			"Marvel Rivals game root is not configured. "
			"Please configure it in Settings or pass --game-root."
		)
	root = Path(candidate).expanduser().resolve()
	if not root.exists():
		raise FileNotFoundError(f"Game root not found: {root}")
	return root


def _maybe_reset_db(db_path: Path, *, force_reset: bool, keep_backup: bool, log: logging.Logger) -> None:
	if not db_path.exists():
		return
	if not force_reset:
		log.info("Reusing existing DB at %s", db_path)
		return
	if keep_backup:
		stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
		backup_path = db_path.with_suffix(db_path.suffix + f".bak.{stamp}")
		try:
			shutil.copy2(db_path, backup_path)
			log.info("Backed up existing DB to %s", backup_path)
		except Exception as exc:
			log.warning("Failed to create DB backup: %s", exc)
	try:
		db_path.unlink()
		log.info("Removed existing DB %s", db_path)
	except FileNotFoundError:
		return


def _load_download_rows_from_json(json_path: Path, limit: Optional[int], log: logging.Logger) -> List[Dict[str, Any]]:
	data = json.loads(json_path.read_text(encoding="utf-8"))
	rows = data.get("rows")
	if not isinstance(rows, list):
		raise ValueError(f"Expected 'rows' list in {json_path}")
	if limit is not None:
		rows = rows[:limit]
	log.info("Loaded %s local download row(s) from %s", len(rows), json_path)
	return rows


def _scan_download_rows(downloads_root: Path, limit: Optional[int], log: logging.Logger) -> List[Dict[str, Any]]:
	pairs = list_files_level_one_including_root(str(downloads_root))
	rows: List[Dict[str, Any]] = []
	for filename, rel in sorted(pairs, key=lambda item: item[0].casefold()):
		if limit is not None and len(rows) >= limit:
			break
		base = downloads_root / Path(rel) if rel else downloads_root
		full_path = base / filename
		try:
			row = build_download_row(full_path, relative_to=downloads_root)
		except (FileNotFoundError, ValueError) as exc:
			log.debug("Skipping %s: %s", full_path, exc)
			continue
		rows.append(row)
	log.info("Scanned %s local download row(s) from %s", len(rows), downloads_root)
	return rows


def _store_local_downloads(db_path: Path, rows: List[Dict[str, Any]], log: logging.Logger) -> None:
	if not rows:
		log.info("No local downloads to store; skipping replace_local_downloads step.")
		return
	conn = get_connection(str(db_path))
	try:
		init_schema(conn)
		inserted = replace_local_downloads(conn, rows)
		log.info("Upserted %s local download row(s).", inserted)
	finally:
		conn.close()


def _sync_mod_metadata(
	conn,
	mod_ids: List[int],
	*,
	game: str,
	rate_delay: float,
	log: logging.Logger,
) -> int:
	if not mod_ids:
		log.info("No mod IDs to sync from Nexus.")
		return 0
	key = get_api_key()
	if not key:
		log.warning("Nexus API key not configured - skipping Nexus metadata sync.")
		log.warning("To enable Nexus metadata sync, configure your API key in Settings.")
		return 0
	prefs = load_prefs()
	processed = 0
	for idx, mod_id in enumerate(mod_ids, start=1):
		try:
			payload = collect_all_for_mod(key, game, mod_id)
		except Exception as exc:
			log.error("Failed to fetch Nexus payload for mod %s: %s", mod_id, exc)
			continue
		filtered = filter_aggregate_payload(payload, prefs)
		mod_info_payload = dict(filtered.get("mod_info") or {})
		desc_text = extract_description_text(filtered.get("description"))
		if desc_text:
			mod_info_payload["description"] = desc_text
		upsert_api_cache(conn, mod_id, filtered)
		mod_info_status = int(payload.get("mod_info_status", 0))
		files_status = int(payload.get("files_status", 0))
		changelog_status = int(payload.get("changelogs_status", 0))
		upsert_mod_info(conn, game, mod_id, mod_info_status, mod_info_payload)
		replace_mod_files(conn, mod_id, filtered.get("files"))
		changelog_payload = filtered.get("changelogs") or {}
		if not changelog_payload or (isinstance(changelog_payload, dict) and not changelog_payload.get("changelogs")):
			changelog_payload = derive_changelogs_from_files(filtered.get("files"))
		replace_mod_changelogs(conn, mod_id, changelog_payload)
		log.info(
			"Synced mod %s (info=%s files=%s changelogs=%s)",
			mod_id,
			mod_info_status,
			files_status,
			changelog_status,
		)
		processed += 1
		if idx < len(mod_ids):
			time.sleep(max(0.0, rate_delay))
	return processed


def _run_ingest(
	db_path: Path,
	args: argparse.Namespace,
	log: logging.Logger,
	downloads_root: Optional[Path],
) -> None:
	ingest_args: List[str] = ["--log-level", args.log_level]
	if args.db_path:
		ingest_args.extend(["--db", str(db_path)])
	if downloads_root:
		ingest_args.extend(["--downloads-root", str(downloads_root)])
	if not args.no_extract:
		ingest_args.append("--extract")
	try:
		rc = ingest_download_assets.main(ingest_args)
		if rc != 0:
			log.warning("ingest_download_assets returned exit code %d — some downloads may not have been processed", rc)
		else:
			log.info("Ingested pak metadata successfully.")
	except Exception as exc:
		log.exception("ingest_download_assets crashed — continuing with remaining rebuild steps: %s", exc)


def _run_tag_rebuild(db_path: Path, map_path: Optional[str], log_level: str, log: logging.Logger) -> None:
	tag_args: List[str] = ["--log-level", log_level]
	if str(db_path) != str(ROOT / "mods.db"):
		tag_args.extend(["--db", str(db_path)])
	if map_path:
		tag_args.extend(["--map", map_path])
	rc = rebuild_tags.main(tag_args)
	if rc != 0:
		raise RuntimeError(f"rebuild_tags failed with exit code {rc}")
	log.info("Tag artifacts rebuilt.")


def _rebuild_conflict_tables(db_path: Path, log: logging.Logger) -> None:
	conn = get_connection(str(db_path))
	try:
		init_schema(conn)
		run_migrations(conn)
		results = rebuild_conflicts(conn, active_only=None)
		log.info("Conflict tables rebuilt: %s", results)
	finally:
		conn.close()


def _run_active_scan(db_path: Path, log: logging.Logger, *, game_root: Optional[Path]) -> None:
	scan_args: List[str] = []
	if str(db_path) != str(ROOT / "mods.db"):
		scan_args.extend(["--db", str(db_path)])
	if game_root is not None:
		scan_args.extend(["--game-root", str(game_root)])
	log.info("Scanning installed mods directory to update active pak snapshot...")
	try:
		rc = scan_active_main(scan_args)
	except SystemExit as exc:
		rc = int(exc.code or 0)
	if rc != 0:
		raise RuntimeError(f"scan_active_mods failed with exit code {rc}")
	log.info("Active pak snapshot updated.")


def main(argv: Optional[Iterable[str]] = None) -> int:
	current_settings = reload_settings()
	try:
		key_preview = len(current_settings.nexus_api_key.strip()) if current_settings.nexus_api_key else 0
		if key_preview:
			print(f"[Settings] Active Nexus API key detected (length={key_preview})")
		else:
			print("[Settings] No Nexus API key detected at task start")
	except Exception:
		pass

	args = parse_args(argv)
	logging.basicConfig(
		level=getattr(logging, args.log_level.upper(), logging.INFO),
		format="%(asctime)s %(levelname)s [rebuild_sqlite] %(message)s",
	)
	log = logging.getLogger("rebuild_sqlite")

	_load_env()

	db_path = _resolve_db_path(args.db_path)
	db_path.parent.mkdir(parents=True, exist_ok=True)
	_maybe_reset_db(db_path, force_reset=args.force_reset, keep_backup=args.keep_backup, log=log)

	conn = get_connection(str(db_path))
	try:
		init_schema(conn)
		run_migrations(conn)
	finally:
		conn.close()

	downloads_root_path: Optional[Path] = None
	needs_download_root = (not args.skip_ingest) or (not args.skip_downloads)
	if needs_download_root:
		try:
			downloads_root_path = _resolve_downloads_root(args.downloads_root)
			os.environ["MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT"] = str(downloads_root_path)
		except Exception as exc:
			log.error("Failed to resolve downloads root: %s", exc)
			return 1

	game_root_path: Optional[Path] = None
	if not args.skip_active_scan:
		try:
			game_root_path = _resolve_game_root(args.game_root)
			os.environ.setdefault("MARVEL_RIVALS_ROOT", str(game_root_path))
		except Exception as exc:
			log.warning("Active scan will be skipped: %s", exc)
			args.skip_active_scan = True

	try:
		if not args.skip_downloads:
			rows: List[Dict[str, Any]] = []
			if args.downloads_json:
				rows = _load_download_rows_from_json(Path(args.downloads_json), args.downloads_limit, log)
			else:
				if downloads_root_path is None:
					raise RuntimeError(
						"Downloads root could not be resolved. "
						"Please configure Marvel Rivals local downloads root in Settings."
					)
				rows = _scan_download_rows(downloads_root_path, args.downloads_limit, log)
			_store_local_downloads(db_path, rows, log)
		else:
			log.info("Skipping local_downloads rebuild (--skip-downloads).")

		if not args.skip_sync:
			conn = get_connection(str(db_path))
			try:
				init_schema(conn)
				mod_rows = conn.execute(
					"SELECT DISTINCT mod_id FROM local_downloads WHERE mod_id IS NOT NULL ORDER BY mod_id;"
				).fetchall()
				mod_ids = [int(row[0]) for row in mod_rows if row and row[0] is not None]
				if args.downloads_limit is not None:
					mod_ids = mod_ids[: args.downloads_limit]
				synced = _sync_mod_metadata(conn, mod_ids, game=args.game, rate_delay=args.rate_delay, log=log)
				log.info("Synced %s mod(s) from Nexus.", synced)
			finally:
				conn.close()
		else:
			log.info("Skipping Nexus API sync (--skip-sync).")

		if not args.skip_ingest:
			_run_ingest(db_path, args, log, downloads_root_path)
		else:
			log.info("Skipping pak asset ingest (--skip-ingest).")

		if not args.skip_tags:
			_run_tag_rebuild(db_path, args.map_path, args.log_level, log)
		else:
			log.info("Skipping tag rebuild (--skip-tags).")

		if not args.skip_active_scan:
			_run_active_scan(db_path, log, game_root=game_root_path)
		else:
			log.info("Skipping active pak scan (--skip-active-scan).")

		if not args.skip_conflicts:
			_rebuild_conflict_tables(db_path, log)
		else:
			log.info("Skipping conflict rebuild (--skip-conflicts).")
	except Exception as exc:
		log.exception("Rebuild failed: %s", exc)
		return 1

	log.info("Rebuild completed successfully.")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

