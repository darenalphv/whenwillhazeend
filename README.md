# whenwillhazeend

**[whenwillhazeend.com](https://whenwillhazeend.com)** — the Southeast Asian haze,
for the people breathing it in Kuala Lumpur, Selangor and Singapore.

It answers three questions, and is careful about which answers are measured and
which are modelled:

1. **What has the haze done to us this week?** Official Singapore readings (NEA
   PSI and PM2.5) and NASA satellite fire detections across Sumatra and
   Kalimantan — measured, live, attributed.
2. **When does the Kalimantan fire season end?** A daily-hazard model trained on
   fire end-dates observed by satellite, shown as a median, an interval and
   prediction-market-style *model* odds. This is not the date the sky over KL or
   Singapore clears: Sumatra burns too, and the wind decides.
3. **When does Kalimantan run out of peat?** A stock-and-flow model in which
   drained peat sinks by drainage as well as burning.

Plus a 3D simulator of how the smoke travels to the peninsula.

Built and run by [ALPHV Technologies](https://alphv.com/?utm_source=whenwillhazeend&utm_medium=referral&utm_campaign=haze2026&utm_content=readme).

Read ["What this is not"](#what-this-is-not) before quoting any number from it.

---

## Running it locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
./run.sh                      # http://127.0.0.1:8000
.venv/bin/pytest              # never touches the network
```

`requirements.txt` is the production runtime; `requirements-dev.txt` adds scipy
(offline calibration only) and pytest. Interactive API docs are at `/api/docs`.

With no configuration the app makes **no Open-Meteo calls** (see Licensing), so
the season forecast runs on bundled ERA5 weather plus climatology and the
modelled KL/Selangor readings are off. To use Open-Meteo's free tier for local,
non-commercial development, set `WWHE_OPEN_METEO_ALLOW_FREE=1`.

### Configuration (all optional)

| Variable | Effect |
|---|---|
| `OPEN_METEO_API_KEY` | Commercial Open-Meteo key. Enables live weather, CAMS readings for KL/Selangor and the 3D haze field. Sent in POST bodies, never URLs or logs. |
| `WWHE_OPEN_METEO_ALLOW_FREE` | `1` = use Open-Meteo's free tier. Local development only; never in production. |
| `DATA_GOV_SG_API_KEY` | Raises data.gov.sg's rate limit (6 → 30 calls per 10 s). |
| `FIRMS_MAP_KEY` | NASA FIRMS key, read by the offline `scripts/model/season/fetch_firms_nrt.py` to resolve forecast odds automatically. |
| `WWHE_LIVE_WARM` | `0` = don't prefetch the live feeds at startup. |
| `WWHE_LIVE_CACHE_DIR` | Shared on-disk cache for the live feeds (default `$TMPDIR/whenwillhazeend-live`; empty disables). |
| `SERVE_BUILT` | `1` = serve the content-hashed build in `public/` (set in the Dockerfile). |
| `PORT`, `WEB_CONCURRENCY` | uvicorn port and worker count. |

## Deployment

One container (`Dockerfile`) on **DigitalOcean App Platform**, Singapore region,
1 vCPU / 1 GiB. The image builds the static site with
`scripts/build_static.py --check`, which content-hashes every asset URL —
including ES-module imports — and fails the build on any unhashed reference, so
returning visitors never run stale JavaScript. Hashed `/static` assets cache for a
year; pages revalidate. `/healthz` is the no-I/O health check.

Measured locally: ~170 MB across both workers after the heaviest 3D run; gzip
takes the 3D payload from ~0.9–1.7 MB to a few hundred KB.

## The models

The scientific basis for every model is [`research/MODEL_SPEC.md`](research/MODEL_SPEC.md):
eight components, each with the literature, the gap in the previous version and
the specified change, every figure tagged by verification status, 114 references
in `research/references.bib` and 201 parameters in `research/PARAMETERS.json`.

**Season end** (`app/model/burn.py`, `drought_code.py`, `forecast.py`). Season
dates for 2001–2025 come from NASA FIRMS fire counts, not a rainfall rule. The
daily hazard uses the Drought Code (verified against cffdrs), 12-week and 10-day
rainfall, ONI and DMI as *published on the forecast date*, and a fitted seasonal
clock, with per-season frailty so the intervals are calibrated (p10–p90 covers
84% of outcomes). No information after the forecast date is used. Forecast-mode
error is about 14 days. Honest accounting: that is not a skill gain over a fairly
refitted older model, and the Indian Ocean Dipole adds nothing measurable — the
gain is honest targets and calibrated uncertainty.

**Peat and forest** (`stock.py`). Peat is a thickness column that loses depth to
drainage subsidence (Hooijer 2012; Evans 2019) as well as to fire; burned peat
never returns. On current trends drained peat is half gone around the 2070s, and
by 2050 drainage removes roughly four times as much peat carbon as fire. Peat and
forest get separate answers.

**Smoke transport** (`transport.py`, `haze3d.py`, `emissions.py`). LPM-3L, a
reduced-form three-layer Lagrangian particle model on ERA5 winds, with Sumatra
and Kalimantan both as sources and observed daily emissions where bundled. 2015
validation meets all three spec targets (regional emissions, Singapore's season
mean, Kuching's Borneo share). Its known misses are stated in its caveats.

**This week** (`app/live/`). NEA PSI and PM2.5 by region, NASA FIRMS 7-day VIIRS
detections and, with a commercial key, the CAMS PM2.5 field and winds. Every value
carries its index (PSI, API/IPU, or modelled US AQI) and whether it is measured
or modelled, and nothing about "this week" is hardcoded.

Every API response carries a `caveats` array, and the site renders all of them.

## Licensing

- **Open-Meteo** (weather, CAMS): the free API is licensed for non-commercial use
  only. This site promotes a company, so **by default it makes no Open-Meteo
  calls**. Setting `OPEN_METEO_API_KEY` from a commercial plan turns them on.
  ERA5 and CAMS data are CC BY 4.0.
- **NEA / data.gov.sg**: Singapore Open Data Licence v1.0 — commercial use
  allowed with a notice naming the dataset, access date and licence, which the
  site shows. No endorsement by NEA is implied.
- **NASA FIRMS**: released without restriction; acknowledgement given, LANCE
  near-real-time disclaimer applies.
- **Malaysia's API (IPU)**: DOE publishes it on APIMS but offers no documented
  public feed or reuse licence, so this site does not copy it and links to
  [APIMS](https://eqms.doe.gov.my/APIMS/main) instead.
- **GFED4.1s**: its download pages state no licence; derived emissions ship
  with attribution.

Every dataset is listed with its status in `app/data/provenance.json` and
described in `app/data/README.md`.

## What this is not

- **Not a forecast to plan your health around.** For whether to go outside
  today, use measured air quality: Singapore's NEA, Malaysia's APIMS, Indonesia's
  ISPU stations, or a local PM2.5 monitor. An instrument beats a model.

- **Not a forecast of clear skies over KL or Singapore.** The countdown is the
  Kalimantan fire season. Haze over the peninsula depends on Sumatra's fires and
  on the wind, and can clear before that date or linger after it.

- **Not a validated operational fire model.** The season model is fitted to
  22 fire-labelled seasons — a small sample — with correlated drivers. Its
  intervals are calibrated against those seasons, not against every way the model
  could be structurally wrong.

- **The exhaustion years are extrapolations.** They assume today's drainage,
  burning and land-use trends continue. Restoration, rewetting, enforcement and
  plantation economics can move them by decades. Several inputs (notably the
  drained fraction of peat) are marked unverified. Read them as "on current
  trends", not as dates.

- **`never_pct` is horizon-relative.** "Never" means "not before the end of the
  requested horizon".

- **The transport model is a reduced-form model, not a regional air-quality
  forecast.** It runs 2–5× high near burning peat, misses some days entirely,
  and runs on a monthly wind composite for years without bundled reanalysis —
  including this year. Its this-week runs are indicative only.

- **The season-replay map is illustrative.** Historical fire-cluster geometries
  are synthesised from published aggregates: the totals are real, the individual
  dots are not. This week's fires are real satellite detections.

- **Mortality figures are model outputs, not body counts**, and the published
  estimates disagree several-fold. The site shows the range.

- **Not a substitute for the people doing this work.** NEA, DOE Malaysia, BMKG,
  KLHK, BRGM, Global Forest Watch, the GFED and FIRMS teams, ECMWF/Copernicus and
  the epidemiologists produced everything this is built on.

- **Not neutral about the subject.** Peat that burns or drains does not come back
  on a human timescale. The day the fuel runs out is not good news.

## Repository layout

```
app/
  api/        FastAPI routes and request schemas
  model/      season, odds, peat/forest stock, emissions, transport, 3D field
  live/       this-week feeds: NEA, FIRMS, CAMS (key-gated)
  data/       bundled datasets, provenance.json
  templates/  index.html, simulator.html
  static/     CSS, JS (sim3d/ is the 3D engine), ALPHV brand assets
research/     model specification, parameters, references
scripts/      static build; offline model pipelines (scripts/model/)
tests/        pytest suite (offline)
```
