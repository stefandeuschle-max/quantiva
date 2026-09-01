# Weitblick

Macroeconomic early-warning dashboard for machinery manufacturers.

## Starting it

```
python3 ~/Desktop/Weitblick/serve.py
```

Then open http://localhost:8000/quantiva.html

The server figures out its own folder, so it does not matter which directory you
start it from. On a different port:

```
PORT=9000 python3 ~/Desktop/Weitblick/serve.py
```

Stop it with Ctrl-C.

## Why a server is needed

Opening the HTML file directly will not work properly. The FRED API sends no CORS
headers, so the browser cannot call it from a `file://` page. `serve.py` does two
things: it serves the dashboard, and it proxies FRED, the NY Fed GSCPI CSV and the
USITC tariff endpoint, injecting the API key server-side so it never reaches the
browser.

Eurostat, OECD, the ECB and the World Bank do send CORS headers and are called
directly from the page.

## API key

No key is stored in the repository. `serve.py` reads a FRED key from the
`FRED_API_KEY` environment variable, or from `~/.fred_api_key` if that is not
set. Without a key, FRED requests fail and the dashboard falls back to sample
data. A free key can be requested at
https://fred.stlouisfed.org/docs/api/api_key.html

The proxy keeps the key server-side: it injects it into outgoing FRED calls and
it never appears in a response, so no browser ever receives it.

On Netlify the same key is set as the `FRED_API_KEY` environment variable
(scoped to Functions) — see `DEPLOY.md`.

## Sections

- Cost Development: producer prices, energy, wages, unit labour costs, composite index
- Economic Cycle: GDP, inflation, industrial production
- Supply Chains: GSCPI, delivery times, freight costs and volumes
- Early Warning: yield curve, recession probability, credit spreads, lending
  standards, delinquencies, business loans, jobless claims, financial conditions,
  euro-area systemic stress
- Packaging / Metal Working / Agricultural Machinery: one page per segment, each
  with shared industry cards plus indicators you assign yourself
- Data Atlas: every indicator available from a free API, with a toggle to add one
  to a machinery segment
- Evidence Review: what the research says about each indicator, including how
  recent that research is

## Data sources

FRED, Eurostat, OECD, World Bank, ECB Data Portal, NY Fed, USITC, Cass
Information Systems, ifo (via OECD).

All 49 charts use real data. Two cards carry a provenance note: the ifo card uses
the OECD-harmonised version of the survey, and the machinery order intake card is
a proxy because VDMA's own series is members-only.
