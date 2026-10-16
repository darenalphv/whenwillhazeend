# whenwillhazeend

A model of two questions about the Kalimantan haze, one urgent and one final:

1. **How many more days will the current fire season burn?**
2. **How many years until there is no peat or forest left in Kalimantan to burn?**

Both answers are *modelled*, not observed. Every API response carries its own
`caveats` array, and the web page is required to render them. Read
["What this is not"](#what-this-is-not) before quoting any number from it.

---

## Running it

No API keys. No network required. No build step.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
./run.sh                      # http://127.0.0.1:8000
```

Tests:

```bash
.venv/bin/pytest
```

Interactive API docs at `/api/docs`. The whole app works offline; live weather
is an optimisation, and when it is unavailable the response says
`"live": false` and gives a `fallback_reason` rather than pretending.

Environment variables (all optional):

| Variable | Default | Effect |
|---|---|---|
| `WWHE_OFFLINE` | `false` | Skip the live-weather probe entirely. |
| `WWHE_PROBE_LIVE_WEATHER` | `true` | Whether `/api/health` probes Open-Meteo. |
| `WWHE_LIVE_PROBE_TTL_S` | `300` | How long a probe result is trusted. |
| `PORT` | `8000` | Port for `run.sh`. |

---

## What the model actually does

### 1. The season-end model — a daily survival model

The fire season is treated as an episode that terminates on some future day.
For each day *d* after the forecast date, the termination hazard is a logistic
function of four drivers:

```
h(d) = sigmoid( b0
              + b1 * rain_7d(d)              # recent rain ends fires
              + b2 * drought_factor(KBDI(d)) # dry peat prolongs them
              + b3 * ONI                     # El Nino prolongs them
              + b4 * monsoon_progress(d) )   # the NE monsoon ends them
```

`KBDI` is the Keetch–Byram Drought Index (Keetch & Byram 1968), on the standard
0–800 imperial scale, driven by daily maximum temperature and rainfall.
`monsoon_progress` is a 0–1 measure of how far the north-east monsoon rain onset
has advanced at that location and date.

The season-end date is then simulated by Monte Carlo: thousands of daily
trajectories, each drawing weather from the Open-Meteo forecast where one exists
and from bundled climatology beyond the forecast horizon. The reported answer is
the distribution of termination days — a **P50 with an 80% interval**, never a
single point estimate presented as certainty.

The `b` coefficients are fitted against observed historical season durations;
the fit and its RMSE on those durations are recorded in `app/model/calibrate.py`
and in a comment on the coefficient constant in `app/model/burn.py`.

### 2. The exhaustion model — stock and flow per fuel pool

Kalimantan's burnable land is divided into pools (intact peat, drained peat,
primary forest, secondary forest, scrub). Each pool's stock evolves annually:

```
S[p, t+1] = S[p, t] - burned[p, t] - converted[p, t] + restored[p, t]

burned[p, t] = A_base
             * enso_multiplier(state_t)
             * susceptibility[p]
             * (S[p, t] / S[p, 0]) ** theta
```

The last term is the important one: as a pool shrinks, the area that burns each
year shrinks with it. Fire does not stop on a fixed date — it tails off as the
fuel runs out. ENSO evolves as a three-state Markov chain (La Niña / neutral /
El Niño) with transition probabilities fitted from the historical ONI record, so
the model reproduces the clustering of catastrophic years rather than averaging
them away.

"Exhausted" is defined as burnable stock (peat plus forest pools) falling below
**5% of its 2025 value**. That threshold is a modelling choice, not a physical
constant. A meaningful share of trajectories never reach it — that fraction is
reported as `never_pct`, and it is not rounded away.

### 3. The dispersion model — Gaussian plume

Emissions per burning cluster are `area_ha × fuel_load_t_ha × EF_g_kg`, using
per-pool fuel loads and PM2.5 emission factors. The smoke is advected downwind
and concentrations at receptor cities come from a Gaussian plume with
Pasquill–Gifford stability class D/E σ<sub>y</sub>/σ<sub>z</sub> power laws, with
wet deposition scaling on rainfall.

This is a **screening model**. A Gaussian plume assumes flat terrain, steady
uniform wind and no chemistry. Real regional haze transport over 500+ km needs a
chemical transport model (GEOS-Chem, CAMx, WRF-Chem). The numbers here are the
right order of magnitude and the wrong number of significant figures.

---

## Where the numbers come from

The full provenance table is served by `GET /api/provenance` (and documented in
`app/data/README.md`), and is rendered in the "Methods & caveats" section of the
site. Every row carries a `status` of `measured`, `estimated`, `synthesised` or
`modelled`, one row per *number* rather than per file — so, for example, the
Kalimantan burned-area figures are split into the years that were measured
directly and the years apportioned from national totals. In summary:

**Measured / published.** Historical burned area, hotspot counts, season start
and end dates, ONI index values, peak PM2.5 readings at monitoring stations,
province areas and populations, and the published excess-mortality and economic
loss estimates for major fire years. Each carries a `source` string naming the
publication.

**Estimated, and flagged as such.** Every record and parameter that is not a
direct measurement carries `"estimated": true`. This includes:

- **`burn_susceptibility`** — the relative annual probability that a hectare of
  a given pool burns in a drought year. This is a fitted model parameter, not an
  observation.
- **The fitted hazard coefficients `b0..b4`** and the exhaustion model's
  `A_base` and `theta`.
- **Fire-cluster geometries.** The per-year fire clusters drawn on the map are
  **synthesised from published aggregate statistics** — provincial burned-area
  totals, hotspot counts and peat-fraction shares — not per-fire satellite
  records, unless a year's file states otherwise in `app/data/README.md`. They
  reproduce the right totals and the right spatial distribution. **An individual
  dot on the map is not a fire that was observed at that location.** Do not use
  them for anything that requires real fire locations.
- Any year where a published figure does not exist is `null`. It is never
  back-filled with an interpolation dressed up as a measurement.

### About the mortality and economic figures

The site publishes excess-death and economic-loss figures, most visibly the
~100,000 excess deaths attributed to the 2015 event across Indonesia, Malaysia
and Singapore (Koplitz et al. 2016, *Environmental Research Letters*) and the
World Bank's ~US$16bn loss estimate for the same season.

These are **epidemiological and economic model outputs, not body counts**. The
mortality figure comes from exposure–response functions applied to modelled
PM2.5 fields; it is a central estimate with a wide published uncertainty range,
and other studies using different methods produce materially different numbers.
The economic figure depends on which losses are counted. Both are reproduced
here as published, with their sources, because they are the best available
estimates of a real and severe harm — not because they are precise.

The app does **not** produce its own mortality forecasts. It reports historical
published estimates and models smoke concentrations. It does not convert its own
modelled PM2.5 into a predicted death toll.

---

## API

| Route | Returns |
|---|---|
| `GET /` | The single-page frontend. |
| `GET /api/health` | `{status, version, live_weather_available}` |
| `GET /api/history` | All historical seasons plus aggregate summary. |
| `GET /api/history/{year}` | One season's record plus that year's fire clusters. |
| `GET /api/history/compare?years=` | Side-by-side comparison of named years. |
| `GET /api/provenance` | Per-number provenance table: source, coverage and `status`. |
| `GET /api/regions` | Kalimantan provinces as GeoJSON. |
| `GET /api/fuel` | Current burnable stock by fuel pool. |
| `GET /api/receptors` | Downwind receptor cities. |
| `POST /api/simulate/season` | Days until this season ends. |
| `POST /api/simulate/exhaustion` | Years until nothing is left to burn. |
| `POST /api/dispersion` | Modelled PM2.5 downwind of burning clusters. |
| `GET /api/meteo?lat&lon&start&end` | Daily weather (live, or climatology). |

Request bounds are enforced, not clamped: `n_sims` ≤ 20000, `horizon_years` ≤
300, `burn_reduction_pct` ∈ [0, 100]. An out-of-range request gets **422** with
the offending field named.

**If a model or data layer fails, the endpoint returns 503** with a `detail`
naming the component that failed. There is no fallback forecast and no
last-known-good value. An outage is reported as an outage.

---

## What this is not

- **This is not a forecast you should plan your health around.** For deciding
  whether to go outside today, use measured air quality: BMKG, Indonesia's
  ISPU stations, Singapore's NEA, Malaysia's APIMS, or a local PM2.5 monitor.
  A measurement from an instrument beats a model output every time.

- **It is not a validated operational fire model.** The hazard coefficients are
  fitted to a few dozen historical seasons. That is a very small sample for a
  five-parameter model, the historical record is itself uncertain, and the
  drivers are correlated with each other. The 80% interval reflects simulated
  weather variability — it does **not** include parameter uncertainty, structural
  uncertainty, or the possibility that the model is simply wrong about how fire
  seasons end.

- **The exhaustion year is an extrapolation, not a prediction.** Projecting two
  or three centuries forward requires assuming that today's drivers keep
  behaving like today's drivers. Policy changes, plantation economics, El Niño
  frequency under a warming climate, the peatland restoration programme's actual
  delivery rate, fire-free land clearing, and enforcement all move it by decades.
  Read any exhaustion year as "on current trends, eventually", not as a date.

- **`never_pct` is horizon-relative.** "Never" in the exhaustion response means
  "not before the end of the requested `horizon_years`", not "never". At the
  default 200-year horizon the baseline run reports no median exhaustion year
  at all; extend the horizon and one appears. Whether the headline reads as a
  year or as "not this side of 2225" is a function of a knob, and the site
  should not let that read as a physical finding.

- **It does not model climate feedbacks.** Drier future dry seasons, or wetter
  ones, are not in the ENSO chain. Nor is the carbon released feeding back into
  the climate that dries the peat.

- **The dispersion model is a screening tool**, not a regional air quality
  forecast. See above.

- **The fire-cluster map is illustrative.** The geometries are synthesised from
  aggregates. The totals are real; the individual dots are not.

- **It is not a substitute for the people doing this work.** BMKG, KLHK, the
  Peatland and Mangrove Restoration Agency (BRGM), Global Forest Watch, the
  GFED and FIRMS teams, and the epidemiologists who did the mortality studies
  produced everything this app is built on. This is a visualisation of their
  work with a model on top.

- **It is not neutral about the subject.** Peat that burns does not grow back on
  a human timescale. The "permanent end of the haze" this model projects is not
  good news; it is the date the fuel runs out. That is the joke, and it is not
  a funny one.

## Licence and data

Code in this repository is provided as-is. The underlying datasets belong to
their publishers and are cited individually in `app/data/README.md`; check their
terms before redistributing.
