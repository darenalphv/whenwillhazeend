# whenwillhazeend.com — CONTRACT V2 (authoritative for the rebuild)

`CONTRACT.md` (v1) still holds for everything not changed here. Where they
conflict, this file wins.

## What changed and why

1. **Light mode.** The whole site, both pages. Tokens live in
   `app/static/css/tokens.css` — values may be refined, names never removed.
2. **Hosting: Cloudflare Python Worker.** FastAPI runs inside a Python Worker
   (Pyodide). Static pages and assets are served by **Workers Static Assets**,
   not by FastAPI. The Worker only handles `/api/*`.
3. **Research-grounded model.** `research/MODEL_SPEC.md` (being written now)
   is the scientific basis. Model changes implement it.
4. **Detailed 3D simulator.** Real terrain, GPU particle smoke advected by a
   wind field, fire particle systems, smooth surface concentration.

## Golden rules (unchanged, still binding)

- Do not edit files you don't own. Ownership table below.
- Every number from literature carries a source; estimates are flagged.
  **Never present a guess as a measurement.**
- The site must work with no API keys. Live data (Open-Meteo) is optional with
  a labelled fallback.
- Every page renders real loading and error states. No blank panels.

## Ownership

| Agent | Owns |
|---|---|
| RESEARCH | `research/**` |
| PLATFORM | `wrangler.jsonc`, `package.json`, `package-lock.json`, `pyproject.toml`, `worker/**`, `scripts/build_static.py`, `public/` (build output, gitignored), `research/PLATFORM.md` |
| MAIN-WEB | `app/templates/index.html`, `app/static/css/main.css`, `app/static/css/tokens.css`, `app/static/js/{app,map,charts,sim,util,fire}.js`, `app/static/js/__mocks__/**` |
| SIM3D-WEB | `app/templates/simulator.html`, `app/static/css/sim3d.css`, `app/static/js/sim3d.js`, `app/static/js/sim3d/**` |
| MODEL | `app/model/**`, `app/data/**`, `tests/**` |
| Lead (integrator) | `app/main.py`, `app/api/**`, `app/config.py`, `requirements.txt`, `README.md`, `CONTRACT*.md`, `.gitignore`, git, deployment |

Need a change in a file you don't own? Say so in your report. Don't make it.

## Static asset URLs

Reference assets with plain absolute paths (`/static/css/main.css`). The
PLATFORM build step rewrites them to content-hashed URLs for cache busting —
do not hand-add `?v=` strings.

Pages are served at `/` (index) and `/simulator`. Treat both templates as
static HTML: **no Jinja variables**, because Workers Static Assets serves them
pre-rendered.

## Fonts and libraries

Google Fonts are allowed: Newsreader (display), Public Sans (UI), IBM Plex
Mono (data). Libraries only from `cdn.jsdelivr.net`, `unpkg.com` or
`cdnjs.cloudflare.com`. Map tiles must need **no API key**:

- light vector basemap: OpenFreeMap — `https://tiles.openfreemap.org/styles/positron`
- terrain: AWS Terrain Tiles (terrarium) —
  `https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
- fallback raster: Esri World Light Gray Canvas

CARTO tiles watermark without a key — do not use them.

## API changes

All v1 routes stay. Additions are **optional fields** — the frontend must
render correctly whether they are present or not, because the model work
lands after the frontend work starts.

### Ranges instead of single figures

Where the literature disagrees, the API may return a range object alongside
(never instead of) the v1 scalar:

```json
"excess_deaths_est": 100300,
"excess_deaths_range": {
  "low": 11880, "high": 100300, "central": null,
  "estimates": [
    {"value": 100300, "region": "ID+MY+SG", "source": "Koplitz et al. 2016", "doi": "10.1088/1748-9326/11/9/094023"},
    {"value": 44040,  "region": "ID+MY+SG", "source": "Kiely et al. 2020",   "doi": "..."}
  ],
  "note": "Estimates differ mainly in the concentration-response function and the modelled exposure."
}
```

Same pattern for `burned_area_mha_kalimantan_range` and
`burned_area_mha_indonesia_range`. **When a range is present, the UI shows the
range and the disagreement, not the scalar alone.** The numbers above are
illustrative of shape only; the real values come from the MODEL agent.

### Season model

`drivers[]` may gain `{"id": "iod", ...}` (Indian Ocean Dipole). Render any
driver by its `id`/`name`/`explain`; don't hardcode the list of four.

### `POST /api/simulate/haze3d` — v2 response (superset of v1)

```json
{
  "version": 2,
  "grid":   { ...v1... },
  "frames": [{
    ...v1 frame keys...,
    "values": [...],               // surface layer PM2.5, ints, row-major S->N
    "values_aloft": [...] | null,  // residual / elevated layer, same grid
    "aloft_base_m": 1200, "aloft_top_m": 3000
  }],
  "wind": {
    "nx": 16, "ny": 16,                 // coarse grid over the same bbox as `grid`
    "levels_m": [10, 850, 1500],        // heights of the wind levels, metres AGL
    "frames": [{ "u": [...], "v": [...] }]   // m/s; index = level*nx*ny + row*nx + col
  },
  "sources": [{
    "lat": -2.3, "lon": 113.9, "area_ha": 1240, "pool": "peat_drained",
    "pm25_kg": 123456.7,
    "emission_g_s": 95.2,           // PM2.5 emission rate while burning
    "injection_m": [0, 600],        // plume injection height range
    "flaming_fraction": 0.15,       // 0 = pure smoulder, 1 = pure flaming
    "region": "kalimantan"          // or "sumatra"
  }],
  "receptor_series": [...v1, plus "arrival_hours", "downwind"...],
  "validation": {...}, "caveats": [...], "conditions": {...}
}
```

**Until v2 is live the simulator must adapt v1 responses**: if `wind` is
absent, build a uniform wind field from `conditions.wind_speed_ms` /
`frames[].wind_dir_deg`; if `emission_g_s` is absent, derive it from `pm25_kg`
over a 14-day episode; if `injection_m` is absent, use `[0, 500]` for peat
pools and `[0, 1500]` otherwise. Keep that adapter after v2 lands — it is the
fallback if the model layer is ever unavailable for the extended fields.

## Visual truth rules for the 3D view

The particles are a visualisation of the model's wind field and emissions.
They must be advected by the `wind` field the API returns, released at rates
proportional to `emission_g_s`, and injected inside `injection_m`. The
**numbers** a user reads (receptor values, peak PM2.5) always come from
`frames[].values`, never from counting particles. A straight edge in the
concentration field is the edge of the model grid — say so in the legend.
