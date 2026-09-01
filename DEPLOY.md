# Deploying Weitblick to Netlify

## The short version

`serve.py` cannot run on Netlify. Netlify serves static files and runs serverless
functions in JavaScript, Go or Rust, not a Python web server. Its three proxy
endpoints have therefore been rewritten as Netlify Functions in
`netlify/functions/`. The dashboard needs no changes: it already calls
`/api/fred`, `/api/gscpi` and `/api/usitc`, and each function claims exactly that
path.

`serve.py` stays for local use. The two are now equivalent implementations of the
same three endpoints.

## What gets published, and what must not

```
Weitblick/
├── site/                    <- the ONLY folder Netlify uploads
│   └── quantiva.html
├── netlify/functions/       <- runs as serverless functions
│   ├── fred.mjs
│   ├── gscpi.mjs
│   └── usitc.mjs
├── netlify.toml             publish = "site"
├── serve.py                 local only. reads the key from the environment
├── Evidence-Sources/        local only. publisher PDFs
└── DEPLOY.md
```

The `publish = "site"` line in `netlify.toml` is the safety catch. Setting it to
`"."` would serve `serve.py` and the whole repository at
`https://your-site.netlify.app/`, including the copyrighted publisher material
under `Evidence-Sources/`. Leave it as it is. (`Evidence-Sources/` is also
git-ignored, so it is not in the repository in the first place.)

## Step by step

1. **Put the folder in a Git repository** (GitHub, GitLab or Bitbucket). Netlify
   can also take a drag-and-drop upload, but that route does not deploy functions
   reliably, so Git is the better path here.

2. **Create the site.** In Netlify: Add new site, Import an existing project, pick
   the repository. It reads `netlify.toml`, so leave the build settings empty.
   Build command: none. Publish directory: `site`. Functions directory:
   `netlify/functions`.

3. **Set the API key as an environment variable.** Site configuration →
   Environment variables → Add a variable:

   ```
   Key:   FRED_API_KEY
   Value: <your FRED key>
   ```

   Scope it to Functions. This is the piece that makes the deployment work, and
   it is also better practice than the local setup: the key lives in Netlify's
   settings rather than in a file, so it is never in the repository.

4. **Deploy.** The dashboard is then at the site root, because `netlify.toml`
   rewrites `/` to `/quantiva.html`.

## Checking it worked

Open these directly in a browser after deploying:

- `https://your-site.netlify.app/api/fred?series_id=WPU101&limit=2` should return
  JSON with two observations.
- `https://your-site.netlify.app/api/gscpi` should return a long series of
  `{date, value}` pairs ending in the current year.
- `https://your-site.netlify.app/api/usitc?from=8481&to=8482` should return
  tariff rows.

If `/api/fred` answers `FRED_API_KEY is not configured`, step 3 was missed or the
variable was not scoped to functions.

Eurostat, OECD, the ECB and the World Bank need no proxy at all. They send CORS
headers, so the page calls them directly and they will keep working regardless.

## Two things to decide before going public

**The proxy is open once deployed.** Anyone who finds the URL can use
`/api/fred` and consume your key's rate limit. The function restricts the
parameters it forwards, validates the series id format and caps the row count, so
it cannot be turned into a general-purpose relay, but it does not authenticate
callers. FRED keys are free and rate-limited rather than billed, so the practical
consequence is a throttled dashboard, not a bill. If the site should not be
public at all, put Netlify password protection or Netlify Identity in front of it.

**Caching is per instance.** Each function keeps a short in-memory cache, which
helps while an instance stays warm but is lost on cold starts. If FRED throttling
becomes noticeable, the next step is Netlify Blobs or a scheduled function that
refreshes a cached snapshot.

## Keeping the local and deployed versions in step

The dashboard file is now `site/quantiva.html` in both cases. `serve.py` detects
the `site/` folder and serves from it, so local development is unchanged:

```
python3 ~/Desktop/Weitblick/serve.py
```

If you change a proxy endpoint, remember it exists twice: in `serve.py` for local
use and in `netlify/functions/` for the deployment.
