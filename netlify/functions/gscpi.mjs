// NY Fed Global Supply Chain Pressure Index.
// The published CSV is a vintage matrix: rows are observation dates, columns are
// release vintages. The current estimate is the LAST populated column, not the
// first, which is easy to get wrong. Returns a flat {date, value} series.

const CSV = 'https://www.newyorkfed.org/medialibrary/research/interactives/'
          + 'data/gscpi/gscpi_interactive_data.csv';

const MONTHS = { Jan:0, Feb:1, Mar:2, Apr:3, May:4, Jun:5,
                 Jul:6, Aug:7, Sep:8, Oct:9, Nov:10, Dec:11 };

let cache = null;
const TTL_MS = 60 * 60 * 1000;

export default async () => {
  if (cache && Date.now() - cache.t < TTL_MS) return json(cache.body);

  try {
    const r = await fetch(CSV, { headers: { 'User-Agent': 'weitblick/1.0' } });
    if (!r.ok) throw new Error(String(r.status));
    const text = await r.text();

    const observations = [];
    for (const line of text.split(/\r?\n/).slice(1)) {
      const cells = splitCsv(line);
      if (!cells.length || !cells[0].trim()) continue;

      let value = null;
      for (let i = cells.length - 1; i >= 1; i--) {      // newest vintage first
        const c = cells[i].trim();
        if (c && c !== '#N/A' && !Number.isNaN(Number(c))) { value = Number(c); break; }
      }
      if (value === null) continue;

      const d = parseDate(cells[0].trim());              // e.g. 31-Jul-2026
      if (d) observations.push({ date: d, value });
    }
    const body = { observations };
    cache = { t: Date.now(), body };
    return json(body);
  } catch (e) {
    return json({ error: 'Could not reach the NY Fed' }, 502);
  }
};

function parseDate(s) {
  const m = /^(\d{1,2})-([A-Za-z]{3})-(\d{4})$/.exec(s);
  if (!m || !(m[2] in MONTHS)) return null;
  const dt = new Date(Date.UTC(+m[3], MONTHS[m[2]], +m[1]));
  return dt.toISOString().slice(0, 10);
}

function splitCsv(line) {
  const out = []; let cur = '', q = false;
  for (const ch of line) {
    if (ch === '"') q = !q;
    else if (ch === ',' && !q) { out.push(cur); cur = ''; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

export const config = { path: '/api/gscpi' };
