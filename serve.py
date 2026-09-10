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
import re
import time as _time
import threading
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
# No key is stored in this file: the repository is public, so a hardcoded key
# would be published on GitHub. The local key lives in ~/.fred_api_key.
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



# ---------------------------------------------------------------------------
# GDELT news lookup.
# GDELT needs no API key, but it throttles hard (one request every five seconds)
# and its throttle response carries no CORS header, so a browser calling it
# directly sees a CORS failure rather than a rate limit. Routing it through here
# fixes both: the server paces the calls and caches the answers for an hour.
# ---------------------------------------------------------------------------
_NEWS_CACHE = {}          # company -> (timestamp, payload)
_NEWS_TTL = 3600
_NEWS_LAST = [0.0]
_NEWS_GAP = 7.0          # GDELT throttles below this and its 429 carries no CORS header
_NEWS_LOCK = threading.Lock()
_NEWS_JUNK = re.compile(
    r"dailypolitical|marketbeat|pr-inside|247wallst|stocknews|zacks|simplywall"
    r"|tipranks|insidertrades|etfdailynews|newsheater|themarketsdaily|americanbankingnews",
    re.I)


_OECD_CACHE = {}
_OECD_TTL = 1800
_OECD_HOST = "sdmx.oecd.org"

def fetch_oecd(flow: str, key: str, last_n: str) -> bytes:
    """OECD SDMX, proxied.

    The browser called this host directly and it is the only upstream that does
    not reliably send CORS headers: during testing all four calls failed at once
    and the affected cards silently lost their non-European series. Going through
    the proxy removes the browser from that decision entirely.
    """
    cache_key = f"{flow}|{key}|{last_n}"
    hit = _OECD_CACHE.get(cache_key)
    if hit and _time.time() - hit[0] < _OECD_TTL:
        return hit[1]
    url = (f"https://{_OECD_HOST}/public/rest/data/{urllib.parse.quote(flow, safe=',@:')}"
           f"/{urllib.parse.quote(key, safe='+.:')}"
           f"?format=jsondata&lastNObservations={last_n}")
    # No Accept header on purpose: asking for the SDMX media type makes OECD reply
    # with the 1.0 schema, which nests the structure differently from the 2.0 shape
    # the browser used to receive. Sending none keeps the payload identical to
    # what the page parsed before the proxy existed.
    req = urllib.request.Request(url, headers={"User-Agent": "Quantiva/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    _OECD_CACHE[cache_key] = (_time.time(), raw)
    return raw


def fetch_news(company: str) -> bytes:
    now = _time.time()
    hit = _NEWS_CACHE.get(company)
    if hit and now - hit[0] < _NEWS_TTL:
        return hit[1]

    with _NEWS_LOCK:                      # GDELT counts requests, not callers
        wait = _NEWS_GAP - (_time.time() - _NEWS_LAST[0])
        if wait > 0:
            _time.sleep(wait)
        _NEWS_LAST[0] = _time.time()

        # A bare company name pulls in anything that shares a token: a search for
        # TRUMPF returned an article about Trump. The query is narrowed to the
        # industry, and every candidate must still carry the name in its title.
        q = urllib.parse.urlencode({
            # sourcelang:eng because the dashboard is read in English: without it a
            # Danish or Romanian rewrite of the same release wins on recency.
            "query": f'"{company}" sourcelang:eng '
                     f'(machinery OR manufacturing OR industrial '
                     f'OR earnings OR revenue OR results OR orders OR factory)',
            "mode": "artlist", "maxrecords": "20",
            "sort": "datedesc", "format": "json",
        })
        url = f"https://api.gdeltproject.org/api/v2/doc/doc?{q}"
        req = urllib.request.Request(url, headers={"User-Agent": "Quantiva/1.0"})
        raw = b""
        for attempt in range(2):          # one retry: a throttle is common, not fatal
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    raw = r.read()
                break
            except Exception:
                if attempt == 0:
                    _time.sleep(_NEWS_GAP)
                    _NEWS_LAST[0] = _time.time()

    try:
        arts = json.loads(raw.decode("utf-8", "replace")).get("articles") or []
    except json.JSONDecodeError:
        arts = []                          # a throttle page is not JSON

    # The name has to appear in the headline, otherwise the article is about
    # something else that merely mentions the company somewhere in the body.
    needle = company.lower().split(" & ")[0].split(" corporation")[0].strip()
    pick = None
    for a in arts:
        title = a.get("title") or ""
        if not title or not a.get("url"):
            continue
        if _NEWS_JUNK.search(a.get("domain", "")):
            continue
        if needle not in title.lower():
            continue
        pick = a
        break

    if pick:
        sd = str(pick.get("seendate", ""))
        payload = json.dumps({
            "company": company,
            "date": f"{sd[0:4]}-{sd[4:6]}-{sd[6:8]}" if len(sd) >= 8 else "",
            "title": " ".join(pick["title"].split()),
            "url": pick["url"],
            "domain": pick.get("domain", ""),
        }).encode()
    else:
        payload = json.dumps({"company": company, "empty": True}).encode()

    _NEWS_CACHE[company] = (_time.time(), payload)
    return payload

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

        if parsed.path == "/api/news":
            company = (query.get("company") or "").strip()
            if not company or len(company) > 80:
                return self._error(400, "Missing or oversized parameter: company")
            try:
                return self._send_json(fetch_news(company))
            except Exception:
                # The card carries a stored headline, so a failed lookup is a
                # non-event rather than an error worth surfacing.
                return self._send_json(json.dumps({"company": company, "empty": True}).encode())

        if parsed.path == "/api/oecd":
            flow = (query.get("flow") or "").strip()
            key = (query.get("key") or "").strip()
            last_n = (query.get("n") or "").strip()
            if not flow or not key or not last_n.isdigit():
                return self._error(400, "Missing or invalid parameter: flow, key, n")
            if not re.fullmatch(r"[A-Za-z0-9_,.@\-]{1,120}", flow) \
               or not re.fullmatch(r"[A-Za-z0-9_+.:\-]{1,200}", key):
                return self._error(400, "flow or key contains characters that are not allowed")
            try:
                return self._send_json(fetch_oecd(flow, key, last_n))
            except Exception as exc:
                return self._error(502, f"Could not reach OECD: {exc}")

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
