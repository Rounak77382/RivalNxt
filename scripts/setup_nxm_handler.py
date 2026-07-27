#!/usr/bin/env python3
"""Register the nxm:// handler and verify DNS resolution for Nexus hosts.

This script is intended to run on Windows. It registers the nxm protocol
handler using the existing register_nxm_handler module, then performs a DNS
lookup to confirm the required Nexus host is reachable. Results are printed to
stdout for easy verification.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

try:
    import winreg  # type: ignore[attr-defined]
except ImportError as exc:  # pragma: no cover
    raise SystemExit("This script can only run on Windows") from exc

from register_nxm_handler import HANDLER_KEY, register

REQUIRED_HOSTS = ("nxm.nexusmods.com",)


def _read_handler_command() -> str | None:
    command_key = HANDLER_KEY + r"\shell\open\command"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, command_key) as key:
            value, _ = winreg.QueryValueEx(key, None)
            return value
    except FileNotFoundError:
        return None


def _register_handler() -> Path:
    script_path = Path(__file__).resolve().parent / "nxm_handler.py"
    if not script_path.exists():
        raise SystemExit(f"nxm handler script not found: {script_path}")
    python_exe = Path(sys.executable).resolve()
    print(f"Registering nxm handler -> python={python_exe} script={script_path}")
    register(script_path, python_exe=python_exe)
    current = _read_handler_command()
    print(f"Current handler command: {current}")
    return script_path


def _check_dns_hosts() -> bool:
    print("Verifying DNS resolution for required Nexus hosts...")
    all_ok = True
    for host in REQUIRED_HOSTS:
        try:
            results = socket.getaddrinfo(host, None)
            addresses = sorted({res[4][0] for res in results})
            print(f"  ✔ {host} -> {', '.join(addresses)}")
        except socket.gaierror as exc:
            all_ok = False
            print(f"  ✖ {host} lookup failed: {exc}")
    return all_ok


def main() -> int:
    _register_handler()
    dns_ok = _check_dns_hosts()
    if dns_ok:
        print("DNS looks good. You can now use Mod Manager Download.")
        return 0
    print("DNS checks failed. Update your DNS resolver or network settings.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
