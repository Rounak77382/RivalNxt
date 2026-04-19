"""Wrapper entry point used by Tauri to start the FastAPI backend."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# CRITICAL: Add repo root to sys.path BEFORE any local imports
_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# For PyInstaller frozen builds, we need additional path handling
if getattr(sys, 'frozen', False):
    # When running as PyInstaller executable, the executable is in a temp directory
    # We need to find the actual script directory or use a different approach
    import sys
    if hasattr(sys, '_MEIPASS'):
        # We're running from PyInstaller bundle
        # The core module should be in the same directory as the executable
        exe_dir = Path(sys.executable).parent
        if str(exe_dir) not in sys.path:
            sys.path.insert(0, str(exe_dir))
        
        # Also check if core is relative to the executable
        core_dir = exe_dir / "core"
        if core_dir.exists() and str(core_dir) not in sys.path:
            sys.path.insert(0, str(core_dir))
        
        # Alternative: check if core is in the same location as the script would be
        script_dir = exe_dir.parent
        core_dir2 = script_dir / "core"
        if core_dir2.exists() and str(core_dir2) not in sys.path:
            sys.path.insert(0, str(core_dir2))

import uvicorn

try:
    from core.config.settings import SETTINGS, configure
except ImportError as e:
    print(f"Failed to import core module: {e}")
    print(f"Current working directory: {os.getcwd()}")
    print(f"Python path: {sys.path}")
    print(f"sys.executable: {sys.executable}")
    print(f"sys.frozen: {getattr(sys, 'frozen', False)}")
    if hasattr(sys, '_MEIPASS'):
        print(f"sys._MEIPASS: {sys._MEIPASS}")
    print(f"Repository root calculated as: {_repo_root}")
    raise


def _ensure_repo_on_path() -> Path:
    """Return the repository root (already added to sys.path at module load)."""
    return _repo_root


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments for backend configuration."""
    parser = argparse.ArgumentParser(
        description="Marvel Rivals Mod Manager Backend Server"
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        help="Directory for database and app data storage (overrides environment variables)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Host address to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        default="INFO",
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


def main() -> None:
    repo_root = _ensure_repo_on_path()
    os.environ.setdefault("PROJECT_MODMANAGER_ROOT", str(repo_root))

    # Parse command-line arguments
    args = _parse_args()
    
    # For production builds, also log to a file for debugging
    # Use a custom StreamHandler that ignores OSError on flush (Windows issue)
    class SafeStreamHandler(logging.StreamHandler):
        """StreamHandler that ignores OSError during flush (Windows console issues)."""
        def flush(self):
            try:
                super().flush()
            except (OSError, ValueError):
                # Ignore errors when stream is not available (common on Windows)
                pass
    
    stream_handler = SafeStreamHandler()
    stream_handler.setLevel(getattr(logging, args.log_level))
    log_handlers = [stream_handler]
    if getattr(sys, "frozen", False):
        # Running as PyInstaller executable
        try:
            log_dir = (
                Path.home()
                / "AppData"
                / "Roaming"
                / "com.rivalnxt.modmanager"
                / "logs"
            )
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "backend.log"
            file_handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            file_handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            file_handler.setLevel(logging.WARNING)
            log_handlers.append(file_handler)
        except Exception:
            pass  # If log file creation fails, just use console
    
    # Configure logging early
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=log_handlers,
    )
    logger = logging.getLogger("modmanager.startup")

    # Log environment variables for debugging
    logger.info("=" * 70)
    logger.info("ENVIRONMENT DIAGNOSTICS")
    logger.info("=" * 70)
    logger.info(f"Python executable: {sys.executable}")
    logger.info(f"Working directory: {os.getcwd()}")
    logger.info(f"MODMANAGER_DATA_DIR env: {os.environ.get('MODMANAGER_DATA_DIR', 'NOT SET')}")
    logger.info(f"MM_DATA_DIR env: {os.environ.get('MM_DATA_DIR', 'NOT SET')}")
    logger.info(f"MM_BACKEND_HOST env: {os.environ.get('MM_BACKEND_HOST', 'NOT SET')}")
    logger.info(f"MM_BACKEND_PORT env: {os.environ.get('MM_BACKEND_PORT', 'NOT SET')}")
    logger.info(f"Frozen (PyInstaller): {getattr(sys, 'frozen', False)}")
    logger.info("=" * 70)

    # Determine data directory from multiple sources (priority order):
    # 1. CLI argument --data-dir
    # 2. Environment variable MODMANAGER_DATA_DIR or MM_DATA_DIR
    # 3. Default from settings
    data_dir_override = (
        args.data_dir
        or os.environ.get("MODMANAGER_DATA_DIR")
        or os.environ.get("MM_DATA_DIR")
    )
    
    host_override = args.host or os.environ.get("MM_BACKEND_HOST")
    port_override = args.port or (
        int(os.environ["MM_BACKEND_PORT"]) if "MM_BACKEND_PORT" in os.environ else None
    )

    # Configure settings
    SETTINGS = configure(
        data_dir=data_dir_override or SETTINGS.data_dir,
        backend_host=host_override or SETTINGS.backend_host,
        backend_port=port_override if port_override is not None else SETTINGS.backend_port,
    )

    # Log startup information
    logger.info("=" * 70)
    logger.info("Marvel Rivals Mod Manager - Backend Server")
    logger.info("=" * 70)
    logger.info(f"Mode: {'Tauri Desktop' if data_dir_override else 'Web Development'}")
    logger.info(f"Data Directory: {SETTINGS.data_dir}")
    logger.info(f"Database Path: {SETTINGS.data_dir / 'mods.db'}")
    logger.info(f"Server Host: {SETTINGS.backend_host}")
    logger.info(f"Server Port: {SETTINGS.backend_port}")
    logger.info(f"Repository Root: {repo_root}")
    logger.info("=" * 70)

    # Ensure data directory exists
    try:
        SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"✓ Data directory ready: {SETTINGS.data_dir}")
    except Exception as e:
        logger.error(f"✗ Failed to create data directory: {e}")
        sys.exit(1)

    # Import and initialize the app
    from core.api.server import app  # import after path injection and configuration
    
    # Check database status
    db_path = SETTINGS.data_dir / "mods.db"
    if db_path.exists():
        logger.info(f"✓ Database found: {db_path}")
    else:
        logger.warning(f"⚠ Database not found - will be created on first use: {db_path}")
        logger.warning("⚠ Frontend may show 'needs bootstrap' - this is expected for new installations")

    host = SETTINGS.backend_host
    port = SETTINGS.backend_port

    logger.info(f"Starting server on {host}:{port}...")
    
    # Suppress spammy polling logs from backend console
    # Show the first one so we know it's working, but hide the rest
    class EndpointFilter(logging.Filter):
        def __init__(self, path_substring: str):
            super().__init__()
            self.path_substring = path_substring
            self.has_logged = False
            
        def filter(self, record: logging.LogRecord) -> bool:
            if self.path_substring in record.getMessage():
                if not self.has_logged:
                    self.has_logged = True
                    return True
                return False
            return True
            
    # Add filter to uvicorn access logger
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter("GET /api/nxm/handoffs"))
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    main()
