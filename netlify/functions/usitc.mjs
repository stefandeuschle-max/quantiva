// USITC Harmonized Tariff Schedule: current US duty rates.
// Two things this endpoint requires and will otherwise reject:
//   - styles=false, or it answers HTTP 400
//   - a hts.usitc.gov Referer, since it enforces an origin allowlist

const URL_BASE = 'https://hts.usitc.gov/reststop/exportList';

const cache = new Map();
const TTL_MS = 24 * 60 * 60 * 1000;      // duty rates change rarely

export default async (req) => {
  const q = new URL(req.url).searchParams;
  const from = q.get('from') || '';
  const to   = q.get('to')   || '';
  if (!/^\d{2,10}$/.test(from) || !/^\d{2,10}$/.test(to)) {
    return json({ error: 'from and to must be numeric HTS codes' }, 400);
  }

  const key = `${from}:${to}`;
  const hit = cache.get(key);
  if (hit && Date.now() - hit.t < TTL_MS) return json(hit.body);

  try {
    const url = `${URL_BASE}?from=${from}&to=${to}&format=JSON&styles=false`;
    const r = await fetch(url, {
      headers: { 'User-Agent': 'weitblick/1.0', 'Referer': 'https://hts.usitc.gov/' },
    });
    if (!r.ok) throw new Error(String(r.status));
    const body = await r.json();
    cache.set(key, { t: Date.now(), body });
    return json(body);
  } catch (e) {
    return json({ error: 'Could not reach USITC' }, 502);
  }
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

export const config = { path: '/api/usitc' };
