#!/usr/bin/env python3
"""Register or unregister the nxm:// protocol handler on Windows.

The script writes HKCU\Software\Classes\nxm entries pointing to the bundled
nxm_handler.py script. It requires a user shell with registry write access.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

try:
	import winreg  # type: ignore[attr-defined]
except ImportError as exc:  # pragma: no cover - only raised on non-Windows
	raise SystemExit("This script can only run on Windows") from exc


HANDLER_KEY = r"Software\Classes\nxm"
COMMAND_KEY = HANDLER_KEY + r"\shell\open\command"
DEFAULT_DESCRIPTION = "URL:Nexus Mods Protocol"
DEFAULT_ICON = ""  # Optional: populate with an .ico path later if desired


def _handler_command(python_exe: Path, handler_script: Path) -> str:
	return f'"{python_exe}" "{handler_script}" "%1"'


def register(handler_script: Path, *, python_exe: Optional[Path] = None) -> None:
	if python_exe is None:
		python_exe = Path(sys.executable)
	handler_script = handler_script.resolve()
	python_exe = python_exe.resolve()

	with winreg.CreateKey(winreg.HKEY_CURRENT_USER, HANDLER_KEY) as key:
		winreg.SetValueEx(key, None, 0, winreg.REG_SZ, DEFAULT_DESCRIPTION)
		winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
		if DEFAULT_ICON:
			winreg.SetValueEx(key, "DefaultIcon", 0, winreg.REG_SZ, DEFAULT_ICON)
	with winreg.CreateKey(winreg.HKEY_CURRENT_USER, COMMAND_KEY) as cmd_key:
		winreg.SetValueEx(cmd_key, None, 0, winreg.REG_SZ, _handler_command(python_exe, handler_script))



def unregister() -> None:
	"""Remove the nxm handler keys. Missing keys are ignored."""
	for key in (COMMAND_KEY, HANDLER_KEY):
		try:
			winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
		except FileNotFoundError:
			continue


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Register nxm:// handler for Mod Manager")
	sub = parser.add_subparsers(dest="action", required=True)

	register_parser = sub.add_parser("register", help="Register the nxm handler")
	register_parser.add_argument(
		"--python-exe",
		type=Path,
		help="Path to python executable to use (defaults to current interpreter)",
	)
	register_parser.add_argument(
		"--handler-script",
		type=Path,
		help="Path to nxm_handler.py (defaults to scripts/nxm_handler.py relative to this script)",
	)

	sub.add_parser("unregister", help="Remove the nxm handler")

	args = parser.parse_args(argv)

	if args.action == "register":
		handler_script = args.handler_script
		if handler_script is None:
			handler_script = Path(__file__).resolve().parent / "nxm_handler.py"
		if not handler_script.exists():
			print(f"Handler script not found: {handler_script}", file=sys.stderr)
			return 1
		register(handler_script, python_exe=args.python_exe)
		print("Registered nxm:// handler pointing to", handler_script)
	else:
		unregister()
		print("Removed nxm:// handler registration")
	return 0


if __name__ == "__main__":
	sys.exit(main())
