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

---

## AMENDMENTS — 2026-10-16: marketing funnel, forecast odds, 3D hero

The site's purpose is now explicit: **a top-of-funnel asset for ALPHV
Technologies** (alphv.com). The hooks are the 3D burning map and a
prediction-market-style forecast. The funnel works *because* the site is
credible — nothing below may compromise the honesty rules above.

### M1. The 3D burning map is the hero (MAIN-WEB + SIM3D-WEB)

SIM3D-WEB exposes an embeddable mode:

```js
import { mountBurningMap } from '/static/js/sim3d/embed.js';
const handle = await mountBurningMap(containerEl, {
  year: 2015, date: '2015-10-14',   // or omit for the current season
  autoplay: true, orbit: true,      // slow cinematic camera
  interactive: false,               // hero: no panels, gestures off
  budget: 'low' | 'med',            // particle budget
});
handle.destroy();
```

It loads MapLibre / deck.gl **dynamically** (never blocking first paint),
pauses offscreen and on tab hide, and resolves/rejects its promise so the
caller can fall back. Under `prefers-reduced-motion` it renders one still
frame.

MAIN-WEB places it full-bleed behind the hero. **The answer renders first**,
as text, on a paper panel; the haze veil is the instant fallback; the 3D scene
fades in over it when ready and stays the veil on no-WebGL, reduced motion,
`saveData`, or a failed load. LCP must be the answer, not the canvas.

### M2. Forecast odds — prediction-market style (MAIN-WEB; data from MODEL)

Prediction-market *presentation* of the model's own probabilities. Visual
style only:

- **Never use the name, logo or colours of Polymarket** or any other market.
- **No money, no trading.** No "buy", "bet", "wager", "trade", "position" or
  "shares" anywhere, no order book. This is a Malaysian business; it must not
  read as gambling.
- Label it plainly: *Model odds. Not a market — no money changes hands. Each
  percentage is the share of N simulated seasons.*

UI: binary cards ("Will the haze season end by 30 Nov 2026?" — YES % / NO %
bar), a multi-outcome "Which week does it end?" distribution, an
**odds-over-time chart** (the signature element), and a **resolution
criteria** box per market stating exactly what "ended" means and what source
resolves it. A share action per market (Web Share API, copy-link fallback).

New endpoints (MODEL implements; may add the routes):

```
GET /api/forecast/odds    -> {
  "as_of", "season_active", "n_sims",
  "markets": [{"id", "question", "threshold_date", "yes", "no"}],
  "distribution": {"bucket": "week", "outcomes": [{"label", "start", "end", "p"}]},
  "resolution": {"definition", "source"},
  "model_error_days": 25.5,
  "caveats": [...]
}
GET /api/forecast/history -> {
  "series": [{"as_of", "p50_end_date", "markets": {"<market id>": p, ...}}],
  "method": "...", "computed_at": "...", "caveats": [...]
}
```

**History must not leak the future.** A point "as of D" may only use weather
observed up to D; beyond D it uses climatology/forecast as the live model
would have. Using archived actual weather after D would make the chart look
prescient and would be a fabrication of skill.

### M3. ALPHV attribution and funnel (MAIN-WEB and SIM3D-WEB, each on their page)

Brand rules (from the ALPHV brand system — these override the site's own
look *inside the ALPHV elements only*): blue `#0100FE`, black, white; **zero
radius; no shadows; flat fills; borders separate**; headline Archivo
Expanded Medium uppercase (Google Fonts `Archivo` at `wdth` 125, weight 500),
body Inter, sentence case; Lucide icons if any; no emoji.

- **Logo:** the real full-colour lockup is at `/static/brand/alphv-logo.png`
  (160×40 — display at most 120×30, prefer 96×24). Full-colour lockup sits **on
  `#FFFFFF` only** — not on the warm paper. Never redraw, recolour, crop,
  shadow or approximate the mark.
- **Placements:** small "Powered by ALPHV" in the header (logo on a white
  chip); a contextual CTA block after the odds section (main page) and after
  the arrivals panel (simulator); a white footer band on both pages.
- **Copy — use only what alphv.com itself claims:** "AI-native systems
  integrator in Kuala Lumpur, Malaysia, building AI, agents and automation into
  the core systems businesses run"; "From business case to production, with
  one team"; "AI pays off after the pilot. Not in it." ALPHV voice: flat,
  declarative, no marketing adjectives, no exclamation marks. Do not invent
  clients, numbers or capabilities. The honest pitch: this site is itself a
  production system — live weather, a calibrated simulation, a published error
  rate — and ALPHV builds systems like it.
- **Links:** `https://alphv.com/?utm_source=whenwillhazeend&utm_medium=referral&utm_campaign=haze2026&utm_content=<placement>`
  with placement ∈ `header | cta-odds | cta-simulator | footer | methods`.
  `rel="noopener"`; open in the same tab.
- **No dark patterns:** no popups, interstitials, exit-intent, email gates or
  content held hostage. The forecast is free and complete without clicking.
