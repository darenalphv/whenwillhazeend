# whenwillhazeend.com — INTERFACE CONTRACT (authoritative)

Python 3.14 (macOS, homebrew). Stack: **FastAPI + uvicorn + Jinja2 + numpy + httpx**.
Frontend: **vanilla JS + Leaflet 1.9 + Chart.js 4** via CDN. No build step. No npm.

Repo root: `/Users/daren/code/whenwillhazeend`

## Golden rules
1. **Do not create or edit files outside the paths you own.** Another agent owns them.
2. Every module must be importable standalone: `python3 -c "import app.model.burn"` from repo root.
3. Model/data layers return **plain JSON-serializable dicts** exactly matching the shapes below.
   No pydantic in `app/model/` or `app/data/`.
4. Every number sourced from literature carries a `source` string. Anything you estimated or
   interpolated must be marked `"estimated": true`. **Never present a guess as a measurement.**
5. All dates are ISO `YYYY-MM-DD` strings. All areas in **Mha** (million hectares) unless the
   key name ends in `_ha` or `_km2`. PM2.5 in µg/m³.
6. Live network calls (Open-Meteo, NASA FIRMS) must be optional: wrap in try/except, fall back
   to bundled data, and set `"live": false` in the response when falling back.
   The app must fully work with no network and no API keys.

## File ownership
| Agent | Owns |
|---|---|
| DATA  | `app/data/**` |
| MODEL | `app/model/**` |
| API   | `app/main.py`, `app/api/**`, `app/config.py`, `requirements.txt`, `run.sh`, `tests/**` |
| WEB   | `app/templates/**`, `app/static/**` |

---

## Layer 1 — `app/data/` (DATA agent)

### `app/data/loader.py`
```python
def load_seasons() -> list[dict]      # historical fire seasons, ascending by year
def load_fuel_stock() -> dict         # current burnable stock in Kalimantan
def load_regions() -> dict            # GeoJSON FeatureCollection, Kalimantan provinces
def load_fire_clusters(year: int) -> dict   # GeoJSON FeatureCollection of that year's fire clusters
def load_receptors() -> list[dict]    # downwind cities that receive haze
def load_climatology() -> dict        # monthly rainfall/temp normals per province
def load_enso() -> list[dict]         # yearly ONI index
```
All read from JSON files in `app/data/*.json`. Cache with `functools.lru_cache`.

### Season object
```json
{
  "year": 2015,
  "enso_phase": "el_nino",           
  "oni_peak": 2.6,
  "start_date": "2015-06-24",
  "peak_date": "2015-10-14",
  "end_date": "2015-11-09",
  "duration_days": 138,
  "burned_area_mha_indonesia": 2.61,
  "burned_area_mha_kalimantan": 0.77,
  "peat_fraction": 0.33,
  "hotspot_count": 129813,
  "pm25_peak_ugm3": {"Palangkaraya": 3741, "Pontianak": 850, "Singapore": 341},
  "co2e_gt": 1.75,
  "economic_loss_usd_bn": 16.1,
  "excess_deaths_est": 100300,
  "source": "KLHK 2016; Koplitz et al. 2016 ERL; World Bank 2016",
  "estimated": false,
  "notes": "..."
}
```
Cover **at minimum** 1997, 2002, 2004, 2006, 2009, 2014, 2015, 2016, 2017, 2018, 2019,
2020, 2021, 2022, 2023, 2024, 2025. Low-fire La Niña years must be present (they anchor
the model's floor). If a field is unknown for a year, use `null` — never invent it.

### Fuel stock object
```json
{
  "as_of": "2025-12-31",
  "pools": [
    {"id":"peat_intact","label":"Intact peatland","area_mha":2.1,"fuel_load_t_ha":320,
     "pm25_ef_g_kg":17.3,"burn_susceptibility":0.35,"source":"...","estimated":false},
    {"id":"peat_drained","label":"Drained/degraded peatland","area_mha":2.7,"fuel_load_t_ha":300, ...},
    {"id":"primary_forest","label":"Primary forest", ...},
    {"id":"secondary_forest","label":"Secondary forest / logged-over", ...},
    {"id":"scrub_grass","label":"Scrub, grass & abandoned land", ...}
  ],
  "deforestation_mha_per_yr": 0.13,
  "restoration_mha_per_yr": 0.05,
  "total_land_mha": 54.4,
  "sources": ["..."]
}
```
`burn_susceptibility` = relative annual probability a hectare of that pool burns given a
drought year, normalised so drained peat ≈ 1.0. Mark `"estimated": true` on it — it is a
model parameter, not a measurement.

### Fire cluster feature properties
`{"date": "2015-09-14", "area_ha": 1240, "duration_days": 22, "pool": "peat_drained",
  "frp_mw": 880, "province": "Kalimantan Tengah", "estimated": true}`
Keep each year's file under ~400 features (cluster/aggregate, don't dump raw hotspots).
Geometry: `Point` with `area_ha` for sizing, or `Polygon` if you have real burn scars.

### `app/data/regions.json`
Simplified GeoJSON, 5 provinces: Kalimantan Barat, Tengah, Selatan, Timur, Utara.
Properties: `{"name","name_en","area_mha","peat_mha","population","capital","capital_lat","capital_lon"}`.
Simplify to <200 coordinate pairs per province — this ships over the wire.

### `app/data/README.md`
A provenance table: every dataset, its real-world source, URL, and what is estimated.
Be explicit that fire-cluster geometries are **synthesised from published aggregate
statistics** unless you sourced genuine per-fire records.

---

## Layer 2 — `app/model/` (MODEL agent)

### `app/model/kbdi.py`
```python
def kbdi_series(dates: list[str], tmax_c: list[float], rain_mm: list[float],
                annual_rain_mm: float, kbdi_0: float = 0.0) -> list[float]
```
Keetch–Byram Drought Index, standard formulation, returned in the 0–800 (imperial) scale.
Docstring must give the equation and cite Keetch & Byram 1968.

```python
def drought_factor(kbdi: float) -> float   # 0..1, maps KBDI to fire-spread readiness
```

### `app/model/meteo.py`
```python
def fetch_weather(lat: float, lon: float, start: str, end: str) -> dict
```
Open-Meteo. Forecast API for future dates, Archive API for past. **No API key.**
Returns:
```json
{"live": true, "lat":-2.2,"lon":113.9,"dates":[...],"tmax_c":[...],"tmin_c":[...],
 "rain_mm":[...],"rh_pct":[...],"wind_speed_ms":[...],"wind_dir_deg":[...],
 "source":"open-meteo","fallback_reason": null}
```
On any failure: synthesise from `load_climatology()`, set `"live": false` and a
human-readable `fallback_reason`. Timeout 6s. Never raise to the caller.

```python
def monsoon_progress(date: str, lat: float, lon: float) -> float
```
0..1 — how far the NE monsoon rain onset has advanced at that location/date.

### `app/model/burn.py` — the season model (the headline number)
```python
def simulate_season_end(as_of: str, region: str = "kalimantan",
                        oni: float | None = None, n_sims: int = 5000,
                        seed: int | None = None, scenario: str = "baseline") -> dict
```
Discrete-time daily survival model. For each day `d` after `as_of`, the episode's
termination hazard is

    h(d) = sigmoid(b0 + b1*rain_7d(d) + b2*drought_factor(KBDI(d)) + b3*oni + b4*monsoon_progress(d))

with `b2`, `b3` negative-signed appropriately (drought and El Niño *prolong*). Monte-Carlo
`n_sims` trajectories using the meteo forecast where available and climatological draws
beyond the forecast horizon. Calibrate `b*` against `load_seasons()` durations — put the
calibration in `app/model/calibrate.py` and **write the fitted coefficients into a module
constant with a comment recording the fit's RMSE on historical durations.**

`scenario` ∈ `{"baseline","no_enforcement","strict_moratorium","monsoon_early","monsoon_late"}`.

Returns:
```json
{
  "as_of": "2026-09-18",
  "region": "kalimantan",
  "active": true,
  "days_remaining": {"p10": 9, "p50": 27, "p90": 61, "mean": 31.4},
  "end_date": {"p10":"2026-09-27","p50":"2026-10-15","p90":"2026-11-18"},
  "daily_end_probability": [{"date":"2026-09-19","p_end":0.004,"p_still_burning":0.996}, ...],
  "drivers": [{"name":"KBDI","value":612,"unit":"index","contribution":0.41,
               "direction":"prolongs","explain":"Peat is dry to ~0.6 m depth."}, ...],
  "confidence": "moderate",
  "live_weather": true,
  "scenario": "baseline",
  "caveats": ["..."]
}
```
`contribution` values across drivers must sum to ~1.0 (share of log-odds magnitude).

### `app/model/stock.py` — the fuel-exhaustion model
```python
def simulate_exhaustion(n_sims: int = 2000, horizon_years: int = 200,
                        restoration_mha_per_yr: float | None = None,
                        burn_reduction_pct: float = 0.0,
                        deforestation_mha_per_yr: float | None = None,
                        seed: int | None = None) -> dict
```
Stock-and-flow per pool:

    S_{p,t+1} = S_{p,t} - burned_{p,t} - converted_{p,t} + restored_{p,t}
    burned_{p,t} = A_base * enso_multiplier(state_t) * susceptibility_p * (S_{p,t}/S_{p,0})**theta

ENSO evolves as a 3-state Markov chain (la_nina / neutral / el_nino) with a transition
matrix fitted from `load_enso()`. "Exhausted" = burnable stock (peat + forest pools) falls
below 5% of its 2025 value.

Returns:
```json
{
  "exhaustion_year": {"p10": 2071, "p50": 2094, "p90": 2138, "never_pct": 4.2},
  "trajectories": {"years":[2026,...], "p10":[...], "p50":[...], "p90":[...]},
  "per_pool": [{"id":"peat_drained","exhaustion_year_p50":2068,"remaining_mha_2050":1.1}, ...],
  "cumulative_burned_mha_by_2050": 6.4,
  "cumulative_pm25_kt": 4120,
  "assumptions": {...echo of inputs...},
  "caveats": ["Peat that burns does not grow back on a human timescale. ..."]
}
```

### `app/model/dispersion.py`
```python
def haze_dispersion(sources: list[dict], wind_speed_ms: float, wind_dir_deg: float,
                    mixing_height_m: float = 800.0, rain_mm: float = 0.0) -> dict
```
`sources` = `[{"lat":..,"lon":..,"area_ha":..,"pool":"peat_drained"}]`.
Emission: `PM2.5_kg = area_ha * fuel_load_t_ha * EF_g_kg` (pool values from `load_fuel_stock()`).
Advect downwind; concentration at a receptor from a Gaussian plume (Pasquill–Gifford
stability class D/E, sigma_y/sigma_z power laws) with wet deposition scaling on `rain_mm`.
Returns per-receptor `{"name","lat","lon","pm25_ugm3","aqi","category","arrival_hours","distance_km"}`
plus a `"plume"` GeoJSON FeatureCollection of concentration contour polygons
(bands at 35 / 55 / 150 / 250 µg/m³) for the map.
AQI category strings: `"Good","Moderate","Unhealthy for Sensitive Groups","Unhealthy","Very Unhealthy","Hazardous"`.

### `app/model/history.py`
```python
def season_summary() -> dict      # aggregate stats + per-year rows for charts
def compare_years(years: list[int]) -> dict
```

Add `app/model/__init__.py` re-exporting the public functions. Every model file gets a
module docstring stating the equations and their citations, and a `CAVEATS` list constant.

---

## Layer 3 — `app/main.py` + `app/api/` (API agent)

FastAPI app, `app/main.py` exposing `app` and mounting `/static`, serving `index.html`
at `/` via Jinja2 (`app/templates/index.html`).

Routes (all JSON unless noted):
```
GET  /                        -> HTML page
GET  /api/health              -> {"status":"ok","version":...,"live_weather_available":bool}
GET  /api/history             -> {"seasons":[...], "summary":{...}}
GET  /api/history/{year}      -> {"season":{...}, "clusters":<geojson>}
GET  /api/regions             -> geojson
GET  /api/fuel                -> load_fuel_stock()
GET  /api/receptors           -> [...]
POST /api/simulate/season     -> simulate_season_end(...)
POST /api/simulate/exhaustion -> simulate_exhaustion(...)
POST /api/dispersion          -> haze_dispersion(...)
GET  /api/meteo?lat&lon&start&end -> fetch_weather(...)
```
Pydantic v2 request models in `app/api/schemas.py`, with sane bounds
(`n_sims` ≤ 20000, `horizon_years` ≤ 300, `burn_reduction_pct` 0–100).
Response caching via `functools.lru_cache` on deterministic (seeded) simulations.
Return **HTTP 503 with a clear JSON `detail`** if a model layer raises — never a bare 500.

`requirements.txt` pinned. `run.sh` = `uvicorn app.main:app --reload --port 8000`.
`tests/` = pytest, covering: every endpoint returns 200 with the contracted keys, the
season model's percentiles are ordered p10≤p50≤p90, exhaustion respects
`burn_reduction_pct=100` (⇒ never exhausts by fire), and the app imports with no network
(monkeypatch httpx to raise).

Also write the root `README.md` (what the model does, how to run, and an honest
"what this is not" section) and `.gitignore`.

---

## Layer 4 — `app/templates/` + `app/static/` (WEB agent)

Single page, dark, editorial, a touch of gallows humour but **never flippant about the
health toll** — the 2015 event is linked to ~100k excess deaths. Sections:

1. **Hero countdown** — the P50 answer, huge: "≈ 27 more days" with the 80% interval and
   the end date, plus a one-line plain-language reason. Below it, the darker second answer:
   "Haze ends permanently in ≈ 2094, when there is nothing left to burn."
2. **Map** (Leaflet, dark basemap, CARTO dark_all tiles) — Kalimantan provinces choropleth
   by peat area; a **year slider (1997→2025)** replaying that year's fire clusters sized by
   `area_ha` and coloured by pool; a dispersion-plume overlay toggle with receptor-city
   markers showing modelled PM2.5.
3. **History** — Chart.js: burned area per year as bars coloured by ENSO phase, with the ONI
   index as a line on a second axis; season start→end **Gantt-style timeline** showing each
   year's burn window across the calendar.
4. **Model playground** — sliders for ONI, burn reduction %, restoration rate, deforestation
   rate, and a scenario select; re-POSTs to `/api/simulate/*` and animates the numbers.
5. **Drivers panel** — horizontal bars of `contribution` per driver with the `explain` text.
6. **Methods & caveats** — renders every `caveats` array and the data provenance table.
   This section is mandatory and must not be collapsed by default.

Files: `templates/index.html`, `static/css/main.css`, `static/js/{app,map,charts,sim}.js`.
Use CSS custom properties on `:root` for the palette. Responsive to 375px.
Handle loading + error states — never leave a blank panel if an endpoint fails.
`fetch` all data from the API; **hardcode nothing that the API serves.**

---

## AMENDMENTS (2026-09-18, after frontend integration review)

These are binding additions. The frontend already consumes them.

**A1. `simulate_season_end()` must echo the resolved ONI** at the top level:
`"oni": 1.8` (the value actually used, whether passed in or defaulted). Without it the
playground has to regex-match a driver name to recover the baseline — fragile.

**A2. Every driver in `drivers[]` gets a stable `"id"`** (snake_case, e.g. `"kbdi"`,
`"oni"`, `"rain_7d"`, `"monsoon"`). The UI keys off `id`, not the display `name`.

**A3. `haze_dispersion()` must return a `"caveats": [...]` array** alongside `receptors`
and `plume`, plus `"total_pm25_kg"` and `"live"`. The Methods section renders caveats from
every endpoint; an endpoint with no caveats silently drops out of it.

**A4. `season_summary()["summary"]` must include:** `n_seasons`, `first_year`, `last_year`,
`mean_duration_days`, `mean_duration_el_nino`, `mean_duration_la_nina`,
`total_burned_mha_kalimantan`, and `caveats`.

**A5. `per_pool[]` entries in `simulate_exhaustion()` need a `"label"`** matching the
corresponding `fuel.pools[].label`.

**A6. New data file + endpoint — `app/data/provenance.json`** (DATA agent) and
`GET /api/provenance` (API agent), serving it directly:
```json
{"datasets": [
  {"id":"seasons","label":"Historical fire seasons","source":"KLHK annual burned-area statistics",
   "url":"https://...","coverage":"1997-2025","status":"measured",
   "notes":"Burned area is national; Kalimantan split apportioned from provincial tables."},
  {"id":"fire_clusters","label":"Per-year fire clusters","source":"synthesised from published
   aggregates","url":null,"coverage":"...","status":"synthesised","notes":"Geometries are
   modelled, not observed. Totals reconcile to KLHK annual burned area."}
]}
```
`status` ∈ `{"measured","estimated","synthesised","modelled"}`. This is what the site shows
users when it tells them where a mortality or burned-area figure came from — it must be
accurate, and `synthesised` must be labelled `synthesised`.

**A7. Basemap:** CARTO `dark_all` now watermarks unkeyed tiles ("API KEY REQUIRED"). The
frontend defaults to Esri Dark Gray Canvas (no key). Do not reintroduce CARTO without a key.
