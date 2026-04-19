#!/usr/bin/env python3
"""Minimal Windows nxm:// protocol handler for the Mod Manager backend.

The handler receives the nxm link from the shell, ensures the backend is
reachable, and POSTs the raw link to the /api/nxm/handoff endpoint. The
backend is expected to perform the heavy lifting (validation, download, etc.).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_ENDPOINT = f"http://127.0.0.1:{os.environ.get('MM_BACKEND_PORT', '8000')}/api/nxm/handoff"


def post_nxm(nxm_uri: str, endpoint: str) -> None:
	log_path = Path(__file__).resolve().parent / "nxm_last_uri.txt"
	try:
		log_path.write_text(nxm_uri + "\n", encoding="utf-8")
	except Exception:
		pass
	payload = json.dumps({"nxm": nxm_uri}).encode("utf-8")
	req = urllib.request.Request(
		endpoint,
		data=payload,
		headers={"Content-Type": "application/json", "User-Agent": "ModManagerNXM/0.1"},
		method="POST",
	)
	with urllib.request.urlopen(req, timeout=10) as resp:
		body = resp.read().decode("utf-8", errors="replace")
		try:
			parsed = json.loads(body)
			if isinstance(parsed, dict):
				handoff = parsed.get("handoff")
				if isinstance(handoff, dict):
					identifier = handoff.get("id")
					mod_id = handoff.get("request", {}).get("mod_id") if isinstance(handoff.get("request"), dict) else None
					print(
						json.dumps(
							{
								"ok": parsed.get("ok", True),
								"handoff_id": identifier,
								"mod_id": mod_id,
								"message": "Nexus link forwarded to Mod Manager",
							},
							indent=2,
						)
					)
					return
			print(body)
		except json.JSONDecodeError:
			print(body)


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(description="Forward nxm links to the Mod Manager backend")
	parser.add_argument("nxm", help="Full nxm:// URI supplied by the browser")
	parser.add_argument(
		"--endpoint",
		help="Override the backend endpoint (default: %(default)s)",
		default=os.environ.get("MODMANAGER_API_NXM_ENDPOINT", DEFAULT_ENDPOINT),
	)
	args = parser.parse_args(argv)

	nxm_uri = args.nxm.strip()
	if not nxm_uri.lower().startswith("nxm://"):
		print("Expected an nxm:// URI, got:", nxm_uri, file=sys.stderr)
		return 2

	try:
		post_nxm(nxm_uri, args.endpoint)
		return 0
	except urllib.error.HTTPError as exc:
		body = exc.read().decode("utf-8", errors="replace")
		print(f"Backend rejected nxm handoff: {exc} -> {body}", file=sys.stderr)
		return 1
	except urllib.error.URLError as exc:
		print(f"Failed to contact backend at {args.endpoint}: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	sys.exit(main())
