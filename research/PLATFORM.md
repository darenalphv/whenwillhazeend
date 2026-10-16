> **Superseded (2026-10-16).** The owner moved hosting to DigitalOcean App Platform
> (a plain CPython container; see `Dockerfile`). This document is kept as the record
> of the Cloudflare Python Worker evaluation -- the architecture worked, but needed
> the Workers Paid plan. `scripts/build_static.py` (content-hashed assets) carried
> over; the `worker/` directory and wrangler config were removed.

# PLATFORM — whenwillhazeend.com on Cloudflare (Python Worker + Static Assets)

Owner of this file: PLATFORM agent. Written 2026-10-16. Every number in the
"measured" tables was measured on this machine (Apple Silicon, local `workerd`
via `pywrangler dev`) unless it is marked *docs* (from developers.cloudflare.com,
read on 2026-10-16) or *estimate*.

## Verdict

**The architecture works, with changes.** FastAPI, pydantic v2 and numpy all
run unmodified on Python Workers today. Every endpoint returns the same result
it returns under uvicorn: seeded `/api/simulate/exhaustion` and
`/api/simulate/haze3d` output is byte-identical between native numpy 2.5.3 and
Pyodide numpy 2.4.6. The bundle fits easily.

**It needs the Workers Paid plan ($5/month). The Free plan cannot run this site.**
Free allows 10 ms of CPU per request. The simulation endpoints use 120–1,600 ms,
which is 12–160× that. Even the cached static-data GETs come close to 10 ms in
Pyodide. On Free, the main page's simulations would fail with error 1102.

The changes it needed:

| # | Change | Owner | Status |
|---|---|---|---|
| 1 | `app/main.py`: import `StaticFiles` / `Jinja2Templates` lazily. starlette 1.x raises `ImportError` at *import* when jinja2 is absent, which killed the Worker at startup. | PLATFORM (minimal edit, allowed) | done; the 82 native tests still pass |
| 2 | `app/model/meteo.py` `_get_json`: send `Accept-Encoding: identity`. Without it the Workers fetch hands httpx the still-compressed body, `r.json()` raises `UnicodeDecodeError`, and every request silently falls back to climatology (`live: false`). | **MODEL** | needed; see "Requests to other owners" |
| 3 | `.gitignore` additions | **Lead** | needed |
| 4 | `GET /api/forecast/odds` should accept `?as_of=YYYY-MM-DD` and (optionally) return `p50_end_date` | **MODEL** | requested |

## Architecture

```
                 whenwillhazeend.com  (Custom Domain on the Worker)
                               │
              ┌────────────────┴─────────────────┐
   path matches /api/*  (run_worker_first)   everything else
              │                                  │
  Python Worker (Pyodide, Python 3.14)    Workers Static Assets  (free, unlimited)
  worker/src/entry.py                     public/  ← scripts/build_static.py
   ├─ edge cache (Cache API)                /                 index.html
   ├─ asgi.fetch → app.main:app (FastAPI)   /simulator        simulator/index.html
   ├─ /api/forecast/history: merges KV      /static/*?v=hash  immutable, 1 year
   └─ scheduled(): daily odds → KV          _headers          cache + security headers
              │
     Workers KV  FORECAST_KV  (odds history)
     Open-Meteo  (outbound fetch via httpx → Workers fetch API)
```

* Pages and assets never run Python, so they cost nothing and have no cold start.
* `/api/*` runs the **unchanged** FastAPI app through the Workers ASGI adapter
  (`workers.asgi.fetch`). Sync route handlers run inline: the SDK patches
  `anyio.to_thread` because Workers have no threads.

### Files (all PLATFORM-owned)

| Path | What |
|---|---|
| `package.json`, `package-lock.json` | wrangler 4.151.0 as a devDependency, plus `build` / `dev` / `deploy` scripts |
| `worker/pyproject.toml` | Worker runtime deps: fastapi, pydantic, numpy, httpx. **This lives in `worker/`, not the repo root.** pywrangler treats the folder that holds `pyproject.toml` as the project root, and it refuses to run if a `requirements.txt` is in the same folder. The repo root keeps `requirements.txt` for uvicorn/pytest. |
| `worker/pylock.toml` | Pyodide-resolved pins (commit it) |
| `worker/uv.lock` | uv lock for the dev group (commit it) |
| `worker/wrangler.jsonc` | Worker config: assets, routing, KV, cron, custom domain, limits |
| `worker/stage_app.py` | Copies `app/**/*.py` and `app/**/*.json` into `worker/src/app/`, then writes `worker/src/build_info.py` (`APP_HASH`). Skips `static/`, `templates/` and `model/calibrate.py` (scipy is build-time only). It syncs incrementally, so it is safe to run while `dev` is watching. |
| `worker/src/entry.py` | Worker entry: snapshot warm-up, edge cache, KV history, cron |
| `scripts/build_static.py` | Builds `public/` with content-hashed URLs |
| `worker/src/app/`, `worker/src/build_info.py`, `public/`, `worker/python_modules/`, `worker/.venv*` | **generated, never edit** |

## How to run locally

Prerequisites: Node ≥ 20 (24 is installed), `uv` (0.12 is installed), and Python ≥ 3.9 for the build scripts.

```bash
npm install                 # wrangler (local devDependency)
npm run build               # public/ + worker/src/app/
npm run dev                 # build, then pywrangler dev → http://localhost:8787
# pass wrangler flags through:  npm run dev -- --port 8787 --ip 127.0.0.1
```

The first `dev` run takes about 1 minute. It downloads the Pyodide 3.14 interpreter and resolves
packages from `https://index.pyodide.org/314.0.7` into `worker/python_modules/`.
After that, startup takes about 5 s. After you edit `app/**`, run `npm run build` again while `dev` is running. The
watcher reloads the Python, and the static pages are rebuilt.

Trigger the daily cron locally:

```bash
curl "http://localhost:8787/cdn-cgi/local/scheduled?cron=30+0+*+*+*"
```

Locally, KV and the Cache API are simulated by Miniflare and persist in `worker/.wrangler/state`.
Delete `worker/.wrangler/state/v3/cache` to empty the edge cache.

The native workflow (`./run.sh`, uvicorn on :8000, pytest) is unchanged.

## How to deploy (commands only, for the lead, after the owner has logged in)

```bash
# 0. once: the account must be on Workers Paid (dashboard → Workers & Pages → Plans).
#    `limits.cpu_ms` in wrangler.jsonc is rejected on the Free plan.
cd whenwillhazeend   # the repository root
npm install
npx wrangler login                      # owner, interactive
npx wrangler whoami                     # confirm the right account

# 1. DNS: the Custom Domain cannot be created over an existing apex record.
#    Dashboard → whenwillhazeend.com → DNS: delete any A/AAAA/CNAME on "@"
#    (keep MX/TXT). Nothing else to create; deploy makes the record + cert.

# 2. Recommended: create the KV namespace explicitly. (Deploy can auto-provision a
#    binding without an id, but wrangler still labels that feature experimental.)
cd worker && npx wrangler kv namespace create FORECAST_KV && cd ..
#    → paste the printed id into worker/wrangler.jsonc:
#      "kv_namespaces": [{ "binding": "FORECAST_KV", "id": "<id>" }]

# 3. Deploy (build → pywrangler sync → wrangler deploy).
npm run deploy
#    Record "Total Upload … / gzip …" and "Worker Startup Time: … ms" from the output.

# 4. Smoke test.
curl -s https://whenwillhazeend.com/api/health
curl -sI https://whenwillhazeend.com/ | grep -i cache-control
curl -s -D- -o /dev/null -H 'content-type: application/json' \
     -d '{"n_sims":2000,"horizon_years":300,"seed":7}' \
     https://whenwillhazeend.com/api/simulate/exhaustion | grep -i x-wwhe-cache   # MISS, then HIT
npx wrangler tail whenwillhazeend      # live logs; cron prints "forecast-odds cron: {...}"
```

**Custom domain.** It is in config (`routes: [{pattern: "whenwillhazeend.com", custom_domain: true}]`), so `npm run deploy` attaches it.
The equivalent manual route is Dashboard → Workers & Pages → whenwillhazeend → Settings → Domains & Routes → Add → Custom domain.

**www.** Dashboard → whenwillhazeend.com → Rules → Redirect Rules → "Redirect from WWW to root" template (301, preserve path and query).
It also needs a proxied DNS record for `www`, for example `AAAA www 100::`.
Do not add `www` as a second Custom Domain, because that would serve duplicate content.

**Rollback.** `cd worker && npx wrangler rollback` (versions are kept).

## Plan required: Workers Paid, $5/month

| Limit (*docs*, 2026-10-16) | Free | Paid | This app |
|---|---|---|---|
| CPU per HTTP request | 10 ms | 30 s default, up to 5 min (`limits.cpu_ms`) | 2–1,600 ms measured. **Needs Paid.** We set `cpu_ms: 10000`. |
| CPU per Cron Trigger | 10 ms | 30 s (<1 h interval), 15 min (≥1 h) | one odds run takes ~0.2–0.6 s (estimate, see below) |
| Worker size | 64 MiB uncompressed (no compressed limit any more) | 64 MiB | **20.2 MiB** uncompressed, 5.05 MiB gzip |
| Memory per isolate | 128 MB | 128 MB | not measurable locally. See known gaps. |
| Startup (global scope) | 1 s | 1 s | measured at deploy. See cold start. |
| Static assets | 20,000 files, 25 MiB each | 100,000 files | 28 files, 366 KiB |
| Subrequests per invocation | 50 | 10,000 | ≤ 2 (Open-Meteo archive + forecast) |
| Requests included | 100k/day | 10M/month, then $0.30/M | static asset requests are free and unlimited |
| CPU included | n/a | 30M CPU-ms/month, then $0.02/M CPU-ms | see the cost note |
| KV | 1k writes/day | 1M writes/month | 2 writes/day |

**Cost note.** Normal traffic is close to $5/month flat. The main page's
simulations are seeded, so they are edge-cached and repeat visitors cost a cache
lookup. The risk is someone hammering unseeded `n_sims=20000` requests (1.6 s
each). 30M included CPU-ms covers about 18,000 of those a month, and beyond that
it is about $0.03 per 1,000 requests. **Recommendation:** add a WAF rate-limiting
rule on `POST /api/simulate/*` and `/api/dispersion` (for example 30 per minute
per IP) in the dashboard.

## Measured numbers

The runtime is local `workerd` (wrangler 4.151.0), with Python 3.14.2 and Pyodide 314 for compat date 2026-10-01.
Each request runs pure-CPU Python with no I/O (`live_weather` off unless noted). In that case workerd's wall time
is a close proxy for CPU time. Workers freeze timers during execution, so CPU
cannot be read from inside the Worker. Production CPUs may differ; treat these as
±50 % until `wrangler tail` / Workers Observability show real CPU time.

| Endpoint | Status | Response | 1st call ms | Warm median ms | Edge-cache hit ms | Native uvicorn CPU ms |
|---|---|---|---|---|---|---|
| GET /api/health | 200 | 64 B | 103 (includes an Open-Meteo probe; I/O) | – | not cached (`no-store`) | – |
| GET /api/history | 200 | 38 KB | 59 | ~3–48 | 3 | 46 / 1 |
| GET /api/history/2015 | 200 | 102 KB | 6 | 6 | 2 | 2 |
| GET /api/regions | 200 | 19 KB | 3 | 3 | 9 | – |
| GET /api/fuel | 200 | 5.7 KB | 3 | 3 | 3 | – |
| GET /api/receptors | 200 | 2.9 KB | 5 | 4 | 2 | – |
| GET /api/provenance | 200 | 20 KB | 3 | 3 | 2 | – |
| GET /api/meteo (15 days, climatology) | 200 | 0.9 KB | 6–10 | – | 2 | – |
| POST /api/simulate/season n=3000 | 200 | 15 KB | 134 | 116 | 3 | – |
| POST /api/simulate/season n=5000 (page default) | 200 | 15 KB | 170 | 169 | 2 | 122 |
| POST /api/simulate/season n=20000 (max) | 200 | 15 KB | 584 | 581 | 3 | 315 |
| …same, with live Open-Meteo | 200 | 15 KB | 1,795 wall | 541 wall | – | CPU unchanged; the extra time is I/O wait |
| POST /api/simulate/exhaustion n=1500 | 200 | 14 KB | 161 | 159 | 2 | – |
| POST /api/simulate/exhaustion n=2000 (page default) | 200 | 14 KB | 178 | 177 | 4 | 93 |
| POST /api/simulate/exhaustion n=20000 (max) | 200 | 14 KB | 1,599 | 1,462 | 2 | 828 |
| POST /api/dispersion, 2 sources | 200 | 19 KB | 24 | 19 | 2 | – |
| POST /api/dispersion, 150 sources (what map.js sends) | 200 | 58 KB | 1,061 | 1,060 | 3 | 450 |
| POST /api/simulate/haze3d 24 h | 200 | 265 KB | 209 | 209 | 3 | 91 |
| POST /api/simulate/haze3d 96 h, step 2 h, release_from_start | 200 | 489 KB | 563 | 578 | 3 | 252 |

Pyodide is about **2.0–2.4× slower than native CPython+numpy** on these
workloads. The heaviest legitimate request, `exhaustion` at the n_sims ceiling,
uses about 1.6 s. That is 6 % of the Paid default and 0.5 % of the Paid maximum.
**Nothing comes close to the Paid CPU limit, so no endpoint needs porting or n_sims reduction.**

**Bundle size.** `wrangler deploy --dry-run` gives `Total Upload: 20,670 KiB / gzip: 5,173 KiB`.
Of that, about 19.4 MiB is vendored packages: numpy ≈ 9 MiB after excludes, pydantic-core
4.7 MiB, pydantic 2 MiB, fastapi/starlette 1.3 MiB. About 2.1 MiB is the app
(1.7 MiB of it is `app/data` JSON plus 277 KiB of `calibration_weather.json`).
`python_modules.exclude` drops tests, `.pyi` and `numpy/f2py`, which saves 1.6 MiB.
The current limit is 64 MiB uncompressed on both plans, with no compressed limit.
Under the older 3 MiB (Free) / 10 MiB (Paid) gzip limits it would also have fit on Paid.

**Data files.** All `app/data/**/*.json` and `app/model/*.json` ship as `Data`
modules (wrangler rule `**/*.json`). The Python Worker mounts every bundled
module into its filesystem at `/session/metadata/`, so `app/data/loader.py`'s
`Path(__file__).parent / ...` `open()` works unchanged. This is verified by every
data endpoint and by `haze3d` loading `fires_2015.json`.

**Cold start.** This cannot be measured meaningfully on local `workerd`: there is no
deploy-time memory snapshot, and timers are frozen during startup.
`wrangler check startup` does not support Python Workers yet (it errors). In
production, Cloudflare snapshots the Wasm heap after the entry module's
top-level code runs at deploy time. Cloudflare's published benchmark for a
fastapi+pydantic+httpx import set is about **1.0 s mean cold start with snapshots**
(about 10 s without; Cloudflare blog "Python Workers advancements" and changelog 2025-12-08).
`entry.py` imports numpy, every model module and the small datasets at top level,
inside a guard, so all of that is in the snapshot instead of on the first request.
**Lead:** record `Worker Startup Time` from the `npm run deploy` output. If it is
near the 1 s startup limit, remove the dataset preload from `WARM_LOADERS` first.
Cold starts only affect `/api/*`. The answer text, page and assets come from Static Assets.

**Native vs Worker versions.** The local `.venv` has Python 3.14.7, numpy 2.5.3, pydantic 2.13.5 and fastapi 0.141.1.
The Worker has Python 3.14.2 (Pyodide 314), numpy 2.4.6, pydantic 2.14.1, fastapi 0.143.2 and starlette 1.8.0.
Both are 3.14, so no syntax or stdlib gap. Seeded results are identical (checked for exhaustion and haze3d).
The numpy minor-version gap matters only if MODEL starts using a numpy 2.5-only API.
`pywrangler` picks the Python version from `compatibility_date`: on or after 2026-09-08 gives 3.14, on or after 2025-09-29 gives 3.13.

## What broke, and what didn't

| Suspect | Finding |
|---|---|
| `StaticFiles` / `Jinja2Templates` | **Broke at startup.** starlette 1.x raises on import without jinja2. Fixed in `app/main.py` with lazy imports. Neither directory ships to the Worker, so the routes are skipped there; native behaviour is unchanged. |
| `httpx` for Open-Meteo | The **sync** `httpx.Client` works inside a request (Pyodide uses JSPI, `can_run_sync() == True`, over the Workers fetch API). Async httpx and `pyodide.ffi.run_sync(js.fetch(...))` also work. **Bug:** with httpx's default `Accept-Encoding`, the body arrives still deflate-compressed, so `r.json()` raises `UnicodeDecodeError` and meteo falls back to climatology. This was verified both ways. Fix (MODEL): `httpx.Client(..., headers={"Accept-Encoding": "identity"})`. With the fix, `live_weather: true` end to end. Outbound fetch time is I/O, not CPU. |
| scipy | Only `app/model/calibrate.py` uses it, lazily. It is not on any request path and is not shipped (`stage_app.py` skips that file). |
| `functools.lru_cache` | Works; it is per isolate. Each isolate warms its own cache, and an isolate's cache dies with it. The edge cache in `entry.py` is what makes seeded results shared across isolates (per data centre). |
| numpy threads/BLAS | Pyodide numpy is single-threaded with a bundled BLAS. No issues, and results are identical. |
| `threading`, `anyio.to_thread` | No threads in Workers. The SDK runs sync handlers inline. No change needed. |
| Entropy at import | The SDK patches numpy.random for the snapshot. The model creates RNGs only inside requests. OK. |
| `time.monotonic()` (health probe TTL) | Workers clocks advance only across I/O and requests. The 300 s probe TTL still works between requests. |
| `date.today()` | Real UTC date at request time. OK. |

## Caching (implemented)

**Static assets** (`public/_headers`, from `build_static.py`):

* `/static/*` → `Cache-Control: public, max-age=31536000, immutable`. This is safe
  because every reference is content-hashed (next section).
* HTML keeps the Workers default `public, max-age=0, must-revalidate` + ETag, so a
  deploy shows up on the next page load (a 304 if unchanged).

**API** (`worker/src/entry.py`). Responses produced by a Worker are **not**
CDN-cached by Cloudflare, so `Cache-Control` on them only reaches browsers. Edge
caching therefore uses the Cache API (`caches.default`). It is per data centre and
works only on the custom domain (it is a no-op on `*.workers.dev`). Keys include
`APP_HASH`, a digest of all staged app code and data, so a deploy that changes the
model never serves pre-deploy results.

| Route | Edge TTL | Browser | Notes |
|---|---|---|---|
| GET history, history/{y}, regions, fuel, receptors, provenance | 1 day | 5 min | deterministic per deploy |
| GET /api/meteo | 15 min (2 min if `live:false`) | 5 min | |
| GET /api/forecast/odds, /api/forecast/history | 1 h | 5 min | |
| POST /api/simulate/season **with seed** | 1 h, keyed by UTC day + canonical body (2 min if `live_weather:false`) | no-store | `as_of` defaults to today |
| POST /api/simulate/exhaustion **with seed** | 1 day | no-store | |
| POST /api/dispersion, /api/simulate/haze3d | 1 day | no-store | no RNG, deterministic |
| Unseeded simulations, /api/health, anything else | never | no-store | an unseeded run asks for a fresh draw |

Responses carry `X-WWHE-Cache: HIT|MISS`. A hit costs about 2–3 ms instead of 170–1,600 ms.

**Precomputing the seeded simulations at build time: not worth it.** The page's
default season run depends on today's date and live weather, so a build-time
result would be stale by the next morning. The exhaustion default costs 178 ms once
per data centre per day before the edge cache takes over. All of this fits easily
in the Paid CPU limit. Precomputing would only be necessary on the Free plan, and the
Free plan is ruled out anyway: the static-data GETs alone are near 10 ms.

## Cache busting (implemented): how ES-module imports are handled

`scripts/build_static.py` appends `?v=<first 10 hex of sha256>` to every local
static reference. Covered references:

* HTML: quoted `/static/...` URLs, including inline `<script type="module">`
  imports such as `import { mountBurningMap } from '/static/js/sim3d/embed.js'`.
* JS: `import ... from`, `export ... from`, side-effect `import '...'`, literal
  `import('...')`, `new URL('...', import.meta.url)`, and any `'/static/...'` string literal.
* CSS: `url(...)` and `@import`. Inline `data:` URIs are skipped.

**Approach: Merkle hashing of the module graph.** JS and CSS files are rewritten
in dependency order. Each file's version is the hash of its **rewritten** bytes,
and those bytes already contain the versions of its imports. Editing `util.js`
therefore changes the import line in `app.js`, which changes `app.js`'s hash,
which changes the `<script>` tag in `index.html`. Every URL the browser fetches
under `/static/` carries a hash that changes whenever anything it transitively loads
changes. That is what makes `immutable` safe. Import cycles are handled with
Tarjan SCCs: every module in a cycle gets one version derived from the whole cycle.
This was tested with a fixture that has a cycle, a dynamic import, a CSS `@import`
and a CSS data-URI.

Why not an import map? It would leave module bytes unhashed. Why not hashed filenames?
Workers Static Assets match on pathname only, so with `?v=` an old cached HTML page still
loads the current files after a deploy instead of 404-ing.

**Rule for the web agents:** keep import specifiers literal. A computed
`import(\`./${x}.js\`)` cannot be hashed. The build prints a WARNING for it, and
`python3 scripts/build_static.py --check` fails on it. Under `immutable` such a
file could stay stale in a browser for a year.

## Security headers (static responses)

`public/_headers` sets the following on every static response:

* `X-Content-Type-Options: nosniff`
* `Referrer-Policy: strict-origin-when-cross-origin`
* `Permissions-Policy` (camera, mic, geolocation and payment off)
* **`Content-Security-Policy-Report-Only`**

The CSP is report-only on purpose. It logs violations to the browser console and
blocks nothing, so a missed origin cannot break the hero map. It allows:

* scripts and styles from self, jsdelivr, unpkg and cdnjs, plus `'unsafe-inline'`
  (the pages use inline styles and may use an inline module) and `'wasm-unsafe-eval'`
* Google Fonts (`fonts.googleapis.com`, `fonts.gstatic.com`)
* tiles from `tiles.openfreemap.org`, `s3.amazonaws.com`, `services.arcgisonline.com` and `server.arcgisonline.com`
* Open-Meteo `api.` and `archive-api.`
* `blob:` workers (MapLibre / deck.gl)
* `frame-ancestors 'none'`

To enforce it: load both pages, confirm the console has no CSP reports, then
rename the header to `Content-Security-Policy` in `build_static.py`. API responses
get no CSP (they are JSON).

## Forecast odds history (CONTRACT_V2 M2): daily snapshot design (implemented in `worker/**`)

**Confirmed by test (local workerd):**

* Python Workers support `async def scheduled(self, controller, env, ctx)` on
  `WorkerEntrypoint`.
* KV bindings work from Python (`await env.FORECAST_KV.get/put`).
* The cron handler can call the FastAPI app in-process (`asgi.fetch(app, js.Request.new(url), env)`).

The whole path was exercised with a stub odds endpoint: the cron wrote two days,
and `/api/forecast/history` merged them after the stub's hindcast points.

**Flow:**

1. **Cron** `30 0 * * *`, daily at 00:30 UTC (07:30 WIB), runs `Default.scheduled`.
   It calls the app's own `GET /api/forecast/odds?as_of=<UTC date>`, so it uses exactly
   the code the site serves, with live weather observed up to that moment. That is
   no hindsight by construction.
2. The cron writes two things to the `FORECAST_KV` namespace:
   * `odds/<YYYY-MM-DD>`: the full response, for audit.
   * `history/v1`: the series. It appends `{as_of, p50_end_date, markets{id: yes}, n_sims, season_active, recorded:"live", recorded_at}`, one per day, idempotent per day.
3. **`GET /api/forecast/history`** first gets the app's response, which is
   MODEL's build-time hindcast JSON for days before launch. It then overlays the
   KV-recorded points; a recorded point beats a reconstruction of the same day.
   It adds `recorded_points: n`. If KV is unbound or empty, the app's response
   passes through untouched. If the app route 404s, a KV-only series is returned.
4. **CPU.** One odds computation is a season Monte-Carlo run: 170 ms at n=5000 and
   580 ms at n=20000 (measured, Pyodide), plus Open-Meteo I/O. The cron limit
   is `cpu_ms` (10 s here), so it fits about 17× over at n=20000. On Free it would not (10 ms).

**Why KV, not D1.** There is one writer, once a day, and readers want the whole
series. One small JSON value (about 100 B per day, about 15 KB per season) is the simplest
fit, and it costs 2 writes a day. D1 becomes worth it only if the series needs querying by
range or the history spans many seasons with per-market rows.

**Binding.** `FORECAST_KV`. `npm run deploy` auto-provisions it if no id is set (wrangler's `--x-provision`, on by default but labelled experimental). Recommended instead:
`cd worker && npx wrangler kv namespace create FORECAST_KV`, then paste the id into `wrangler.jsonc`.
To inspect it: `npx wrangler kv key get --binding FORECAST_KV history/v1 --remote`.

**Requests to MODEL** (in "Requests to other owners"):

* accept `as_of` as a query param on `/api/forecast/odds`
* optionally include `p50_end_date`. Otherwise the cron derives it from the cumulative weekly distribution, to week resolution.

## Known gaps / risks

* **Production CPU and cold start are not yet measured.** The numbers above come from
  local workerd. After the first deploy, read the CPU time per invocation in
  Workers Observability, and the startup time from the deploy output.
* **Memory (128 MB/isolate) was not measured.** Pyodide, numpy, FastAPI and the
  preloaded data are probably around 60–90 MB (*estimate*). The largest working set,
  haze3d 96 h on a 56×56 grid, is small. Watch for `Exceeded Memory Limit` in tail.
* **Cache API is per data centre.** The first visitor in each colo pays the
  computation. That is fine at these CPU costs.
* **Rate limiting** is not configured. Add a WAF rule (cost note above).
* The CSP is **report-only** until checked on both pages.
* Static-page **404**: `public/404.html` is a plain placeholder. MAIN-WEB may
  restyle it if the build is taught to take it from a template.
* `worker/.wrangler/` local state can grow. It is safe to delete.

## Requests to other owners

**MODEL** (`app/model/meteo.py`, `_get_json`):

```python
with httpx.Client(timeout=timeout or REQUEST_TIMEOUT_S,
                  headers={"Accept-Encoding": "identity"}) as client:
```

Without this, there is no live weather on Cloudflare: the body arrives compressed
and `UnicodeDecodeError` triggers the fallback. It is harmless natively.

**MODEL** (`/api/forecast/odds`, `/api/forecast/history`):

* accept `?as_of=YYYY-MM-DD` on odds, which the cron passes
* optionally add `p50_end_date` to the odds response
* serve the build-time hindcast from a JSON file under `app/data/`; it ships automatically
* keep series points shaped `{as_of, p50_end_date, markets: {id: p}}` so the KV merge lines up

**Lead** (`.gitignore`): add

```
public/
worker/src/app/
worker/src/build_info.py
```

These are already covered: `worker/python_modules/`, `worker/.venv-workers/`, `.venv/` (which matches `worker/.venv`), `.wrangler/` and `node_modules/`.
Commit `package.json`, `package-lock.json`, `worker/pyproject.toml`, `worker/pylock.toml`, `worker/uv.lock`, `worker/wrangler.jsonc`, `worker/stage_app.py`, `worker/src/entry.py` and `scripts/build_static.py`.

**Web agents:** use literal import specifiers (see "Cache busting"). Reference
assets as plain `/static/...` paths; the build adds the hashes.
