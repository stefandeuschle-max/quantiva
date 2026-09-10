// GDELT news lookup — the serverless twin of fetch_news() in serve.py.
//
// GDELT needs no API key but throttles hard, and its throttle response carries
// no CORS header, so a browser calling it directly sees a CORS failure rather
// than a rate limit. This function caches for an hour and, unlike serve.py,
// cannot pace requests globally: serverless instances are ephemeral and run
// concurrently, so a throttle is simply treated as "no headline", which is what
// the card already falls back to.

const GDELT = 'https://api.gdeltproject.org/api/v2/doc/doc';

const cache = new Map();
const TTL_MS = 60 * 60 * 1000;

const JUNK = /dailypolitical|marketbeat|pr-inside|247wallst|stocknews|zacks|simplywall|tipranks|insidertrades|etfdailynews|newsheater|themarketsdaily|americanbankingnews/i;

export default async (req) => {
  const company = (new URL(req.url).searchParams.get('company') || '').trim();
  if (!company || company.length > 80) {
    return json({ error: 'Missing or oversized parameter: company' }, 400);
  }

  const hit = cache.get(company);
  if (hit && Date.now() - hit.t < TTL_MS) return json(hit.body);

  // A bare company name pulls in anything that shares a token: a search for
  // TRUMPF returned an article about Trump. The query is narrowed to the
  // industry, and every candidate must still carry the name in its title.
  // sourcelang:eng because the dashboard is read in English: without it a Danish
  // or Romanian rewrite of the same release wins on recency.
  const params = new URLSearchParams({
    query: `"${company}" sourcelang:eng (machinery OR manufacturing OR industrial `
         + `OR earnings OR revenue OR results OR orders OR factory)`,
    mode: 'artlist',
    maxrecords: '20',
    sort: 'datedesc',
    format: 'json',
  });

  let articles = [];
  try {
    const r = await fetch(`${GDELT}?${params}`, { headers: { 'User-Agent': 'Quantiva/1.0' } });
    if (r.ok) {
      const text = await r.text();
      try {
        articles = JSON.parse(text).articles || [];   // a throttle page is not JSON
      } catch {
        articles = [];
      }
    }
  } catch {
    articles = [];
  }

  // The name has to appear in the headline, otherwise the article is about
  // something else that merely mentions the company somewhere in the body.
  const needle = company.toLowerCase().split(' & ')[0].split(' corporation')[0].trim();
  const pick = articles.find(a => {
    const title = a && a.title;
    if (!title || !a.url) return false;
    if (JUNK.test(a.domain || '')) return false;
    return title.toLowerCase().includes(needle);
  });

  let payload;
  if (pick) {
    const sd = String(pick.seendate || '');
    payload = {
      company,
      date: sd.length >= 8 ? `${sd.slice(0, 4)}-${sd.slice(4, 6)}-${sd.slice(6, 8)}` : '',
      title: pick.title.split(/\s+/).join(' ').trim(),
      url: pick.url,
      domain: pick.domain || '',
    };
  } else {
    // The card carries a stored headline, so a failed lookup is a non-event
    // rather than an error worth surfacing.
    payload = { company, empty: true };
  }

  cache.set(company, { t: Date.now(), body: payload });
  return json(payload);
};

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
  });
}

export const config = { path: '/api/news' };
