"""LEGACY v1 calibration (kept only for skill comparison). Calibration of the season-end hazard coefficients against observed seasons.

What is being fitted
--------------------
The intercept and four slopes of the daily termination hazard used by
`app.model.burn`:

    h(d) = sigmoid(b0 + b1*rain_7d + b2*drought_factor(KBDI) + b3*oni
                   + b4*monsoon_progress)

Data
----
* **Labels.** `app.data.loader.load_seasons()` gives a start date, an end date
  and hence a duration for each historical Kalimantan fire season.
* **Features.** The *observed* daily weather at the peat-weighted Kalimantan
  centroid, pulled once from the Open-Meteo Archive (ERA5) back to 1996 and
  cached in ``app/model/calibration_weather.json`` so the fit is reproducible
  offline.  KBDI is run continuously over the whole 30-year record rather than
  restarted each season, which is how Keetch & Byram intended it.  This matters:
  an earlier version of this module reconstructed each season's weather from
  climatology, and because that reconstruction was uncorrelated with the
  observed end dates the likelihood correctly drove the rainfall and drought
  coefficients to zero.  Fitting against real weather is what gives b1 and b2
  any meaning at all.

  If the archive cannot be reached and no cache exists, the module falls back
  to ENSO-conditioned climatology and marks the result
  ``"weather_source": "climatology"``.  A fit made that way is not worth much
  and says so.

Two-stage fit
-------------
1. **Maximum likelihood** for the discrete-time survival model.  Each season
   contributes one observation per day it was burning: censored (y=0) on every
   day before the observed end date, an event (y=1) on the end date.  The mean
   negative log-likelihood

       -LL = -(1/N) sum_i [ y_i*log h_i + (1-y_i)*log(1-h_i) ]

   is minimised with L-BFGS-B under sign bounds ``b1, b4 >= 0`` and
   ``b2, b3 <= 0``, so the fit cannot come out claiming rain prolongs fires.

2. **Duration refinement.**  MLE is unbiased for the daily hazard but not for
   the *median duration*, which is the number the site puts in 200-point type.
   Stage 2 runs Nelder-Mead over all five coefficients, minimising the RMSE of
   the model's median duration against the observed duration, where the median
   duration is read off the deterministic survival curve driven by the weather
   that actually occurred in that season:

       S(D) = prod_{d=1..D} (1 - h(d)),   duration_hat = min{D : S(D) <= 0.5}

Three error numbers are reported, and they mean different things:

* ``rmse_days`` -- in-sample, perfect-foresight.  Given the weather that
  actually happened, how well does the hazard function place the end date?
  This is the honest measure of the hazard function itself.
* ``rmse_days_loo`` -- leave-one-out: stage 1 is refit without each season,
  stage 2's offset carried over.  This is the number to judge the model by.
* ``rmse_days_forecast`` -- what the site would have said on day one of each
  season, running the full Monte-Carlo with no knowledge of the weather to
  come.  It is necessarily the largest of the three, and it is the error a
  user of the website is actually exposed to.

Sources
-------
* Cox, D.R. (1972). Regression models and life-tables (discrete-time form).
  *JRSS B* 34(2), 187-220 -- the survival formulation used here.
* Keetch & Byram (1968) -- the drought index (see `app.model.kbdi`).
* Field, R.D. et al. (2016). *PNAS* 113(33), 9204 -- ENSO-drought-fire coupling.
* Open-Meteo Historical Weather API (ERA5), https://open-meteo.com/ -- the
  observed daily weather the fit is conditioned on.
"""

from __future__ import annotations

import json
import math
import os
from datetime import date, timedelta

from app.model import kbdi as kbdi_mod
from app.model import meteo as meteo_mod
import legacy_v1_burn as burn_mod

__all__ = ["fit_hazard_coefficients", "build_training_frame",
           "observed_weather", "predicted_duration", "CAVEATS",
           "FIT_PATH", "WEATHER_CACHE_PATH"]

SPINUP_START = "1996-01-01"   # KBDI spin-up before the first fitted season
BULK_FETCH_TIMEOUT_S = 120.0  # one-off 30-year archive pull, not a page load
BOUNDS = [(-9.0, 2.0), (0.0, 0.5), (-9.0, 0.0), (-3.0, 0.0), (0.0, 12.0)]
KEYS = ("b0", "b1", "b2", "b3", "b4")
#: The three seasons whose start and end dates are anchored in documented fire
#: episodes rather than derived from a rainfall rule. Skill against these three
#: is worth more than in-sample fit against all twenty-seven.
ANCHOR_SEASONS = (1997, 2015, 2019)
_HERE = os.path.dirname(__file__)
FIT_PATH = os.devnull
WEATHER_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "calibration_weather_v1_palangkaraya.json")


# ---------------------------------------------------------------------------
# observed weather
# ---------------------------------------------------------------------------

def observed_weather(lat: float, lon: float, start: str = SPINUP_START,
                     end: str | None = None, refresh: bool = False) -> dict:
    """Daily tmax/rain at (lat, lon), from cache, then ERA5, then climatology."""
    end = end or date.today().isoformat()
    if not refresh and os.path.exists(WEATHER_CACHE_PATH):
        try:
            with open(WEATHER_CACHE_PATH) as fh:
                cached = json.load(fh)
            if (cached.get("dates") and cached["dates"][0] <= start
                    and cached["dates"][-1] >= end[:10]):
                return cached
        except (OSError, ValueError, KeyError, IndexError):
            pass

    out = None
    try:
        # A 30-year bulk pull is not a page load: allow it far longer than the
        # 6 s the live forecast path is held to.
        rows = meteo_mod._pull(meteo_mod.ARCHIVE_URL, lat, lon,
                               meteo_mod._d(start), meteo_mod._d(end),
                               timeout=BULK_FETCH_TIMEOUT_S)
        if len(rows) > 3000:
            keys = sorted(rows)
            out = {
                "source": "open-meteo archive (ERA5)",
                "weather_source": "observed",
                "lat": float(lat), "lon": float(lon),
                "retrieved": date.today().isoformat(),
                "dates": keys,
                "tmax_c": [round(rows[k]["tmax_c"], 2) for k in keys],
                "rain_mm": [round(rows[k]["rain_mm"], 2) for k in keys],
            }
    except Exception:
        out = None

    if out is None:
        synth = meteo_mod.synthesize_weather(lat, lon, start, end, oni=0.0, seed=1996)
        return {
            "source": synth["source"], "weather_source": "climatology",
            "lat": float(lat), "lon": float(lon),
            "retrieved": date.today().isoformat(),
            "dates": synth["dates"], "tmax_c": synth["tmax_c"],
            "rain_mm": synth["rain_mm"],
        }

    try:
        with open(WEATHER_CACHE_PATH, "w") as fh:
            json.dump(out, fh)
    except OSError:
        pass
    return out


# ---------------------------------------------------------------------------
# seasons + features
# ---------------------------------------------------------------------------

def _load_raw_seasons():
    try:
        from app.data.loader import load_seasons
        return load_seasons() or []
    except Exception:
        return []


def _seasons_with_dates(seasons=None):
    if seasons is None:
        from app.data.loader import load_seasons
        seasons = load_seasons()
    out = []
    for s in seasons or []:
        if not s.get("start_date") or not s.get("end_date"):
            continue
        st, en = meteo_mod._d(s["start_date"]), meteo_mod._d(s["end_date"])
        dur = s.get("duration_days")
        dur = int(dur) if dur else (en - st).days
        if dur <= 0 or dur > burn_mod.MAX_HORIZON_DAYS:
            continue
        oni = meteo_mod.oni_value(s)
        if oni is None:
            oni = {"el_nino": 1.2, "la_nina": -1.0}.get(
                (s.get("enso_phase") or "neutral").lower(), 0.0)
        out.append({"year": int(s["year"]), "start": st, "end": en,
                    "duration": dur, "oni": float(oni),
                    "phase": meteo_mod.enso_phase_of(s)})
    out.sort(key=lambda r: r["year"])
    return out


class FeatureRecord:
    """The continuous daily feature record the fit and the hindcast both read."""

    __slots__ = ("dates", "index", "rain7", "df", "monsoon", "kbdi",
                 "weather_source", "source", "lat", "lon")

    def __init__(self, wx, lat, lon):
        self.dates = list(wx["dates"])
        self.index = {d: i for i, d in enumerate(self.dates)}
        self.weather_source = wx.get("weather_source", "observed")
        self.source = wx.get("source", "")
        self.lat, self.lon = float(lat), float(lon)
        annual = meteo_mod.annual_rain_mm_for(lat, lon)
        rain = [float(r or 0.0) for r in wx["rain_mm"]]
        self.kbdi = kbdi_mod.kbdi_series(self.dates, wx["tmax_c"], rain, annual, 0.0)
        self.df = [kbdi_mod.drought_factor(k) for k in self.kbdi]
        run, r7 = 0.0, []
        for i, v in enumerate(rain):
            run += v
            if i >= 7:
                run -= rain[i - 7]
            r7.append(run)
        self.rain7 = r7
        cache: dict[tuple[int, int], float] = {}
        mp = []
        for d in self.dates:
            key = (int(d[5:7]), int(d[8:10]))
            if key not in cache:
                cache[key] = meteo_mod.monsoon_progress(d, lat, lon)
            mp.append(cache[key])
        self.monsoon = mp

    def row(self, iso_date: str, oni: float):
        i = self.index.get(iso_date)
        if i is None:
            return None
        return [1.0, self.rain7[i], self.df[i], float(oni), self.monsoon[i]]


def build_training_frame(seasons=None, lat=None, lon=None, weather=None):
    """Design matrix X (n_days x 5), labels y, season-year tags, and the record."""
    import numpy as np
    if lat is None or lon is None:
        lat, lon, _ = burn_mod.region_centroid("kalimantan")
    rows = _seasons_with_dates(seasons)
    if not rows:
        raise RuntimeError("no historical seasons with usable start/end dates")
    last_needed = max(r["start"] for r in rows) + timedelta(
        days=burn_mod.MAX_HORIZON_DAYS + 2)
    end = min(last_needed, date.today()).isoformat()
    wx = weather or observed_weather(lat, lon, SPINUP_START, end)
    rec = FeatureRecord(wx, lat, lon)

    X, y, meta, usable = [], [], [], []
    for s in rows:
        days = [s["start"] + timedelta(days=k) for k in range(s["duration"] + 1)]
        feats = [rec.row(d.isoformat(), s["oni"]) for d in days]
        if any(f is None for f in feats):
            continue                     # season predates the weather record
        usable.append(s)
        for d, f in zip(days, feats):
            X.append(f)
            y.append(1.0 if d == s["end"] else 0.0)
            meta.append(s["year"])
    if not usable:
        raise RuntimeError("no season falls inside the available weather record")
    return (np.asarray(X, dtype=float), np.asarray(y, dtype=float),
            np.asarray(meta), usable, rec)


# ---------------------------------------------------------------------------
# likelihood and duration
# ---------------------------------------------------------------------------

def _nll(theta, X, y):
    import numpy as np
    z = np.clip(X @ theta, -40.0, 40.0)
    ll = y * (-np.logaddexp(0.0, -z)) + (1.0 - y) * (-np.logaddexp(0.0, z))
    return float(-ll.sum() / len(y))


def _fit_mle(X, y, x0=None):
    import numpy as np
    from scipy.optimize import minimize
    if x0 is None:
        x0 = np.array([-4.6, 0.03, -3.2, -0.45, 3.0])
    res = minimize(_nll, np.asarray(x0, dtype=float), args=(X, y),
                   method="L-BFGS-B", bounds=BOUNDS)
    return np.asarray(res.x, dtype=float), float(res.fun)


def predicted_duration(theta, season, rec, horizon=None) -> float:
    """Median duration from the deterministic survival curve on real weather.

    ``S(D) = prod(1 - h(d))``; the median is the first D with ``S(D) <= 0.5``.
    """
    horizon = horizon or burn_mod.MAX_HORIZON_DAYS
    surv = 1.0
    for k in range(1, horizon + 1):
        f = rec.row((season["start"] + timedelta(days=k)).isoformat(), season["oni"])
        if f is None:
            break
        z = sum(a * b for a, b in zip(theta, f))
        h = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
        surv *= (1.0 - h)
        if surv <= 0.5:
            return float(k)
    return float(horizon + 1)


def _rmse(theta, rows, rec):
    import numpy as np
    pred = np.array([predicted_duration(theta, s, rec) for s in rows])
    obs = np.array([float(s["duration"]) for s in rows])
    return float(np.sqrt(np.mean((pred - obs) ** 2))), pred, obs


def _fit_masked(X, y, rows, rec, free, x0=None, maxiter=400):
    """Fit with some coefficients pinned to zero (an ablation)."""
    import numpy as np
    from scipy.optimize import minimize
    free = np.asarray(free, dtype=bool)
    bounds = [b if f else (0.0, 0.0) for b, f in zip(BOUNDS, free)]
    start = np.asarray(x0 if x0 is not None else [-4.6, 0.03, -3.2, -0.45, 3.0],
                       dtype=float) * free
    res = minimize(_nll, start, args=(X, y), method="L-BFGS-B", bounds=bounds)
    theta = np.asarray(res.x, dtype=float) * free

    def obj(v):
        t = np.zeros(5)
        t[free] = v
        return _rmse(_clip_bounds(t) * free, rows, rec)[0]

    r2 = minimize(obj, theta[free], method="Nelder-Mead",
                  options={"maxiter": maxiter, "xatol": 1e-4, "fatol": 1e-4})
    cand = np.zeros(5)
    cand[free] = r2.x
    cand = _clip_bounds(cand) * free
    best = min((_rmse(theta, rows, rec)[0], theta),
               (_rmse(cand, rows, rec)[0], cand), key=lambda p: p[0])
    return best[1], float(best[0])


def _clip_bounds(theta):
    import numpy as np
    return np.array([min(max(float(v), lo), hi) for v, (lo, hi) in zip(theta, BOUNDS)])


def _forecast_rmse(theta, rows, rec, n_sims=1200, seed=11):
    """RMSE of the live Monte-Carlo run from each season's first day."""
    import numpy as np
    coeffs = dict(zip(KEYS, [float(v) for v in theta]))
    pred, obs = [], []
    for s in rows:
        i = rec.index.get(s["start"].isoformat())
        k0 = rec.kbdi[i] if i is not None else 400.0
        end_day, _, _ = burn_mod.run_hazard_simulation(
            s["start"].isoformat(), rec.lat, rec.lon, s["oni"], coeffs,
            n_sims=n_sims, seed=seed + s["year"],
            horizon=burn_mod.MAX_HORIZON_DAYS, forecast=None, kbdi_start=k0)
        pred.append(float(np.median(end_day)))
        obs.append(float(s["duration"]))
    pred, obs = np.asarray(pred), np.asarray(obs)
    return float(np.sqrt(np.mean((pred - obs) ** 2))), pred


def fit_hazard_coefficients(seasons=None, seed: int = 7, n_sims: int = 1200,
                            refine: bool = True, maxiter: int = 600,
                            write: bool = True, weather=None) -> dict:
    """Two-stage fit.  Returns coefficients, three RMSEs and per-season residuals."""
    import numpy as np
    from scipy.optimize import minimize

    X, y, meta, rows, rec = build_training_frame(seasons, weather=weather)
    if len(rows) < 6:
        raise RuntimeError(f"only {len(rows)} usable historical seasons; refusing to fit")

    theta_mle, nll = _fit_mle(X, y)
    rmse_mle, _, _ = _rmse(theta_mle, rows, rec)

    theta, rmse = theta_mle.copy(), rmse_mle
    if refine:
        def obj(t):
            r, _, _ = _rmse(_clip_bounds(t), rows, rec)
            return r
        best = None
        for start in (theta_mle, np.array([-5.5, 0.02, -2.0, -0.6, 5.0])):
            res = minimize(obj, start, method="Nelder-Mead",
                           options={"maxiter": maxiter, "xatol": 1e-4, "fatol": 1e-4})
            cand = _clip_bounds(np.asarray(res.x, dtype=float))
            r, _, _ = _rmse(cand, rows, rec)
            if best is None or r < best[0]:
                best = (r, cand)
        if best and best[0] < rmse:
            rmse, theta = best

    rmse_final, pred, obs = _rmse(theta, rows, rec)

    loo_err = []
    for s in rows:
        keep = meta != s["year"]
        try:
            t_loo, _ = _fit_mle(X[keep], y[keep], x0=theta)
            t_loo = _clip_bounds(t_loo + (theta - theta_mle))
        except Exception:
            t_loo = theta
        loo_err.append(predicted_duration(t_loo, s, rec) - float(s["duration"]))
    rmse_loo = float(np.sqrt(np.mean(np.square(loo_err)))) if loo_err else None

    rmse_fc, pred_fc = _forecast_rmse(theta, rows, rec, n_sims=n_sims, seed=seed)

    # --- ablations: how much of the skill needs rainfall at all? ------------
    ablations = {}
    for name, free in (("no_rainfall_terms (b1=b2=0)", (1, 0, 0, 1, 1)),
                       ("no_climate_terms (b3=b4=0)", (1, 1, 1, 0, 0)),
                       ("monsoon_climatology_only (b1=b2=b3=0)", (1, 0, 0, 0, 1))):
        try:
            th_a, r_a = _fit_masked(X, y, rows, rec, free, x0=theta)
            ablations[name] = {
                "rmse_days": round(float(r_a), 2),
                "coefficients": {k: round(float(v), 5) for k, v in zip(KEYS, th_a)},
            }
        except Exception as exc:                       # pragma: no cover
            ablations[name] = {"error": type(exc).__name__}

    # --- skill on the three documented-episode seasons ----------------------
    anchors = []
    for s_ in rows:
        if s_["year"] not in ANCHOR_SEASONS:
            continue
        p_ = predicted_duration(theta, s_, rec)
        anchors.append({"year": s_["year"], "observed_days": s_["duration"],
                        "predicted_days": int(round(p_)),
                        "residual_days": int(round(p_ - s_["duration"]))})
    anchor_rmse = (round(float(np.sqrt(np.mean([a["residual_days"] ** 2
                                                for a in anchors]))), 2)
                   if anchors else None)

    # --- the floor: years in which no season happened at all ----------------
    zero_check = []
    for raw in (seasons if seasons is not None else _load_raw_seasons()):
        if raw.get("duration_days") not in (0, 0.0):
            continue
        yr = int(raw["year"])
        probe = f"{yr}-09-01"
        i = rec.index.get(probe)
        if i is None:
            continue
        oni_y = meteo_mod.oni_value(raw) or 0.0
        zero_check.append({
            "year": yr,
            "observed_days": 0,
            "kbdi_1_sep": round(rec.kbdi[i], 1),
            "monsoon_progress_1_sep": round(rec.monsoon[i], 3),
            "model_says_active": bool(rec.kbdi[i] >= burn_mod.ACTIVE_KBDI_MIN
                                      and rec.monsoon[i] < burn_mod.ACTIVE_MONSOON_MAX),
            "oni": oni_y,
        })

    out = {
        "coefficients": {k: round(float(v), 5) for k, v in zip(KEYS, theta)},
        "coefficients_mle": {k: round(float(v), 5) for k, v in zip(KEYS, theta_mle)},
        "rmse_days": round(float(rmse_final), 2),
        "rmse_days_mle_only": round(float(rmse_mle), 2),
        "rmse_days_loo": round(rmse_loo, 2) if rmse_loo is not None else None,
        "rmse_days_forecast": round(float(rmse_fc), 2),
        "mean_observed_duration_days": round(float(np.mean(obs)), 1),
        "sd_observed_duration_days": round(float(np.std(obs)), 1),
        "naive_rmse_days_mean_predictor": round(
            float(np.sqrt(np.mean((obs - obs.mean()) ** 2))), 2),
        "n_seasons": len(rows),
        "n_daily_observations": int(len(y)),
        "negative_log_likelihood": round(float(nll), 6),
        "weather_source": rec.weather_source,
        "weather_provenance": rec.source,
        "weather_record": [rec.dates[0], rec.dates[-1]],
        "centroid": {"lat": rec.lat, "lon": rec.lon},
        "fitted_at": date.today().isoformat(),
        "target_is_rainfall_derived": True,
        "target_note": (
            "Season start/end dates in app/data/seasons.json are derived from an "
            "ERA5 rainfall rule, not from observed fire-episode dates. Fitting a "
            "hazard model that contains a rainfall term against those durations "
            "is partly circular, so the in-sample RMSE overstates real skill. "
            "See `ablations` for how much of the skill survives removing every "
            "rainfall term, and `anchor_seasons` for the three seasons whose "
            "dates come from documented episodes."),
        "ablations": ablations,
        "anchor_seasons": anchors,
        "anchor_rmse_days": anchor_rmse,
        "zero_season_check": zero_check,
        "residuals": [
            {"year": s["year"], "observed_days": s["duration"],
             "predicted_days": int(round(float(p))),
             "residual_days": int(round(float(p) - s["duration"])),
             "forecast_days": int(round(float(pf))),
             "oni": s["oni"], "phase": s["phase"]}
            for s, p, pf in zip(rows, pred, pred_fc)],
        "caveats": CAVEATS,
    }
    if write:
        try:
            with open(FIT_PATH, "w") as fh:
                json.dump(out, fh, indent=2)
        except OSError:
            pass
    return out


def main():  # pragma: no cover - developer entry point
    fit = fit_hazard_coefficients()
    print(json.dumps({k: v for k, v in fit.items() if k != "caveats"}, indent=2))
    c = fit["coefficients"]
    print("\n# paste into app/model/burn.py")
    print("HAZARD_COEFFS = {")
    for k in KEYS:
        print(f'    "{k}": {c[k]:>10.5f},')
    print("}")
    print(f'# RMSE {fit["rmse_days"]} d in-sample (perfect foresight), '
          f'{fit["rmse_days_loo"]} d leave-one-out, '
          f'{fit["rmse_days_forecast"]} d forecast-mode; n={fit["n_seasons"]}')
    print(f'# naive mean-predictor RMSE {fit["naive_rmse_days_mean_predictor"]} d; '
          f'anchor-season RMSE {fit["anchor_rmse_days"]} d '
          f'(1997/2015/2019, documented episodes)')
    print("# ablations: " + "; ".join(
        f'{k} -> {v.get("rmse_days")} d' for k, v in fit["ablations"].items()))


CAVEATS = [
    "The season start and end dates this model is fitted against are not "
    "observations of fire. Published per-season episode dates barely exist, so "
    "the bundled dataset derives them from an ERA5 rainfall rule. A hazard "
    "model containing a rainfall term is therefore fitted partly to its own "
    "input, and the in-sample RMSE flatters it. The ablation that removes every "
    "rainfall term is the number to look at, together with the three seasons "
    "(1997, 2015, 2019) whose dates come from documented episodes.",
    "There are at most about thirty observed Kalimantan fire seasons on record "
    "and five free parameters. That is enough to fit and barely enough to "
    "validate. Judge the model by the leave-one-out and forecast-mode errors, "
    "not by the in-sample one.",
    "The published start and end dates of historical seasons are themselves "
    "modelled quantities, usually a threshold on a satellite hotspot count. "
    "Move the threshold and every observed duration moves by days to weeks, and "
    "the fitted coefficients move with them.",
    "The fit uses ERA5 reanalysis rainfall at one point for the whole of "
    "Kalimantan. ERA5 has a known wet bias over tropical land and cannot see "
    "the patchiness of convective rain, so the fitted rainfall coefficient is "
    "an effective, island-scale one, not the true local sensitivity of a "
    "smouldering front to being rained on.",
    "Land use changed enormously across the fitted period. Peatland drainage, "
    "the 2011 moratorium, the 2016 peat restoration agency and the spread of "
    "oil palm all mean a 1997 season and a 2023 season are not draws from the "
    "same process. A single stationary coefficient set papers over that.",
    "The fit is leveraged by a handful of extreme El Nino years, because those "
    "are the long seasons. The uncertainty on the ONI coefficient is much "
    "larger than its point estimate suggests.",
    "Parameter uncertainty is not propagated into the published forecast "
    "interval. The p10-p90 band on the site is weather uncertainty only; "
    "including coefficient uncertainty would widen it materially.",
    "Perfect-foresight RMSE flatters the model: it is the error when the "
    "weather for the rest of the season is already known. What a visitor to "
    "the site is exposed to is the forecast-mode error, which is roughly twice "
    "as large.",
]


if __name__ == "__main__":  # pragma: no cover
    main()
