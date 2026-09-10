// OECD SDMX proxy — the serverless twin of fetch_oecd() in serve.py.
//
// sdmx.oecd.org is the one upstream this dashboard uses that does not reliably
// send CORS headers; when it refuses the browser, the affected cards silently
// lose their non-European series. Going through this function takes the browser
// out of that decision.

const OECD_BASE = 'https://sdmx.oecd.org/public/rest/data';

const cache = new Map();                 // survives while the instance stays warm
const TTL_MS = 30 * 60 * 1000;

// SDMX ids carry ',' '@' ':' (flow) and '+' '.' ':' (key) as meaningful
// separators, so they must reach OECD unescaped. The character classes below are
// the validation *and* the reason no encoding is needed: nothing that survives
// them changes meaning in a URL path.
const FLOW_RE = /^[A-Za-z0-9_,.@-]{1,120}$/;
const KEY_RE = /^[A-Za-z0-9_+.:-]{1,200}$/;

export default async (req) => {
  const q = new URL(req.url).searchParams;
  const flow = (q.get('flow') || '').trim();
  const key = (q.get('key') || '').trim();
  const n = (q.get('n') || '').trim();

  if (!FLOW_RE.test(flow) || !KEY_RE.test(key) || !/^\d{1,4}$/.test(n)) {
    return json({ error: 'Missing or invalid parameter: flow, key, n' }, 400);
  }

  const cacheKey = `${flow}|${key}|${n}`;
  const hit = cache.get(cacheKey);
  if (hit && Date.now() - hit.t < TTL_MS) return raw(hit.body);

  // No Accept header on purpose: asking for the SDMX media type makes OECD reply
  // with the 1.0 schema, which nests the structure differently from the 2.0 shape
  // the page parses.
  const url = `${OECD_BASE}/${flow}/${key}?format=jsondata&lastNObservations=${n}`;

  try {
    const r = await fetch(url, { headers: { 'User-Agent': 'Quantiva/1.0' } });
    if (!r.ok) return json({ error: `OECD request failed with status ${r.status}` }, r.status);
    const body = await r.text();
    cache.set(cacheKey, { t: Date.now(), body });
    return raw(body);
  } catch {
    return json({ error: 'Could not reach OECD' }, 502);
  }
};

function raw(body, status = 200) {
  return new Response(body, {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

function json(body, status = 200) {
  return raw(JSON.stringify(body), status);
}

export const config = { path: '/api/oecd' };
