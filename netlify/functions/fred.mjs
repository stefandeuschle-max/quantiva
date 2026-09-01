// FRED proxy. FRED sends no CORS headers, so the browser cannot call it directly.
// This function injects the API key server-side; the key never reaches the client.
// Netlify Functions v2: the path is declared in `config` at the bottom.

const FRED_URL = 'https://api.stlouisfed.org/fred/series/observations';

// Only these params are forwarded upstream, so the proxy cannot be turned into
// an open relay for arbitrary FRED calls.
const ALLOWED = new Set(['series_id', 'limit', 'sort_order', 'observation_start',
                         'observation_end', 'frequency', 'units']);

const cache = new Map();                 // survives while the instance stays warm
const TTL_MS = 10 * 60 * 1000;

export default async (req) => {
  const key = process.env.FRED_API_KEY;
  if (!key) return json({ error: 'FRED_API_KEY is not configured on the site' }, 500);

  const inUrl = new URL(req.url);
  const seriesId = inUrl.searchParams.get('series_id') || '';
  // FRED ids are uppercase alphanumeric; reject anything else before calling out
  if (!/^[A-Z0-9_]{1,40}$/.test(seriesId)) {
    return json({ error: 'Missing or malformed series_id' }, 400);
  }

  const params = new URLSearchParams();
  for (const [k, v] of inUrl.searchParams) if (ALLOWED.has(k)) params.set(k, v);
  const limit = Number(params.get('limit') || 12);
  params.set('limit', String(Math.min(Math.max(limit, 1), 2000)));   // cap the request size
  params.set('api_key', key);
  params.set('file_type', 'json');
  if (!params.get('sort_order')) params.set('sort_order', 'desc');

  const cacheKey = [...params].filter(([k]) => k !== 'api_key').sort().join('&');
  const hit = cache.get(cacheKey);
  if (hit && Date.now() - hit.t < TTL_MS) return json(hit.body);

  try {
    const r = await fetch(`${FRED_URL}?${params}`, {
      headers: { 'User-Agent': 'weitblick/1.0' },
    });
    if (!r.ok) return json({ error: `FRED request failed with status ${r.status}` }, r.status);
    const body = await r.json();
    cache.set(cacheKey, { t: Date.now(), body });
    return json(body);
  } catch (e) {
    // Never echo the upstream URL: it contains the key
    return json({ error: 'Could not reach FRED' }, 502);
  }
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

export const config = { path: '/api/fred' };
