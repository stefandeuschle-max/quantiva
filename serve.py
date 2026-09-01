#!/usr/bin/env python3
"""
Local server + FRED API proxy for the macroeconomic dashboard.

Why this exists
---------------
The FRED API does not send CORS headers, so a browser cannot call it directly
from a static HTML page. This server does two things:

  1. Serves the dashboard files.
  2. Proxies /api/fred -> api.stlouisfed.org, injecting the API key server-side.

That keeps the API key out of the browser entirely: it is injected into outgoing
FRED calls server-side and never appears in any response.

Usage
-----
    python3 serve.py

Provide a FRED key via the FRED_API_KEY environment variable or by writing it to
~/.fred_api_key. Without one, FRED requests fail and the dashboard falls back to
sample data. A free key: https://fred.stlouisfed.org/docs/api/api_key.html

Then open http://localhost:8000/quantiva.html

Optional:
    PORT=9000 python3 serve.py
"""

import csv
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime
from time import time

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
_HERE = Path(__file__).resolve().parent
# The deployable page lives in site/ so that Netlify publishes only that folder
# and never this file, which carries the key.
ROOT = _HERE / "site" if (_HERE / "site").is_dir() else _HERE
PORT = int(os.environ.get("PORT", "8000"))

# Key resolution order:
#   1. FRED_API_KEY environment variable
#   2. ~/.fred_api_key
# No key is stored in this file, so it is safe to commit to a public repository.
KEY_FILE = Path.home() / ".fred_api_key"


def _load_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if key:
        return key
    try:
        return KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


API_KEY = _load_key()

# Only these query params are forwarded upstream. Anything else a client sends
# is ignored, so the proxy cannot be repurposed into an open relay.
ALLOWED_PARAMS = {"series_id", "limit", "sort_order", "observation_start", "observation_end", "frequency", "units"}

# Simple in-process cache so repeated loads and range switches do not hammer FRED.
CACHE_TTL_SECONDS = 600
_cache: dict[str, tuple[float, bytes]] = {}


def fetch_fred(query: dict[str, str]) -> bytes:
    """Call FRED with the server-side key and return the raw JSON body."""
    params = {k: v for k, v in query.items() if k in ALLOWED_PARAMS}
    params.update({"api_key": API_KEY, "file_type": "json"})
    params.setdefault("sort_order", "desc")

    cache_key = urllib.parse.urlencode(sorted(params.items()))
    hit = _cache.get(cache_key)
    if hit and (time() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]

    url = f"{FRED_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "macro-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()

    _cache[cache_key] = (time(), body)
    return body


GSCPI_CSV = ("https://www.newyorkfed.org/medialibrary/research/interactives/"
             "data/gscpi/gscpi_interactive_data.csv")
USITC_URL = "https://hts.usitc.gov/reststop/exportList"


def fetch_gscpi() -> bytes:
    """NY Fed GSCPI. The CSV is a vintage matrix: rows are observation dates,
    columns are release vintages. The current estimate is the LAST populated
    column, not the first. Returns a flat {date, value} series as JSON."""
    hit = _cache.get("gscpi")
    if hit and (time() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]

    req = urllib.request.Request(GSCPI_CSV, headers={"User-Agent": "macro-dashboard/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8-sig", errors="replace")

    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for r in rows[1:]:
        if not r or not r[0].strip():
            continue
        value = None
        for cell in reversed(r[1:]):          # newest vintage first
            cell = cell.strip()
            if cell and cell != "#N/A":
                try:
                    value = float(cell)
                except ValueError:
                    continue
                break
        if value is None:
            continue
        try:
            d = datetime.strptime(r[0].strip(), "%d-%b-%Y")
        except ValueError:
            continue
        out.append({"date": d.strftime("%Y-%m-%d"), "value": value})

    body = json.dumps({"observations": out}).encode()
    _cache["gscpi"] = (time(), body)
    return body


def fetch_usitc(hts_from: str, hts_to: str) -> bytes:
    """Current US HTS duty rates. `styles=false` is required or the endpoint 400s."""
    key = f"usitc:{hts_from}:{hts_to}"
    hit = _cache.get(key)
    if hit and (time() - hit[0]) < CACHE_TTL_SECONDS:
        return hit[1]

    qs = urllib.parse.urlencode({"from": hts_from, "to": hts_to,
                                 "format": "JSON", "styles": "false"})
    req = urllib.request.Request(f"{USITC_URL}?{qs}",
                                 headers={"User-Agent": "macro-dashboard/1.0",
                                          "Referer": "https://hts.usitc.gov/"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    _cache[key] = (time(), body)
    return body


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _send_json(self, payload: bytes, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _error(self, status: int, message: str) -> None:
        self._send_json(json.dumps({"error": message}).encode(), status)

    def do_GET(self):  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        parsed = urllib.parse.urlparse(self.path)
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if parsed.path == "/api/gscpi":
            try:
                return self._send_json(fetch_gscpi())
            except Exception as exc:
                return self._error(502, f"Could not reach NY Fed: {exc}")

        if parsed.path == "/api/usitc":
            hts_from = query.get("from", "")
            hts_to = query.get("to", "")
            if not (hts_from.isdigit() and hts_to.isdigit()):
                return self._error(400, "from/to must be numeric HTS codes")
            try:
                return self._send_json(fetch_usitc(hts_from, hts_to))
            except Exception as exc:
                return self._error(502, f"Could not reach USITC: {exc}")

        if parsed.path != "/api/fred":
            return super().do_GET()

        if not API_KEY:
            return self._error(500, "FRED_API_KEY is not set on the server.")

        if not query.get("series_id"):
            return self._error(400, "Missing required parameter: series_id")

        try:
            self._send_json(fetch_fred(query))
        except urllib.error.HTTPError as exc:
            # Never surface the upstream URL: it contains the API key.
            self._error(exc.code, f"FRED request failed with status {exc.code}")
        except (urllib.error.URLError, TimeoutError) as exc:
            self._error(502, f"Could not reach FRED: {exc.reason if hasattr(exc, 'reason') else exc}")

    def log_message(self, fmt, *args):
        # Suppress per-request noise; keep startup output readable.
        pass


def main() -> int:
    if not API_KEY:
        print("WARNING: no FRED key found. FRED requests will fail and the dashboard", file=sys.stderr)
        print("         will fall back to sample data.", file=sys.stderr)
        print(f"         Fix: export FRED_API_KEY=... , or write it to {KEY_FILE}\n", file=sys.stderr)

    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard:  http://localhost:{PORT}/quantiva.html")
    print(f"FRED proxy: http://localhost:{PORT}/api/fred?series_id=...")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
