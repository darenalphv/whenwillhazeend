"""Offline calibration of the v2 season-end model (MODEL_SPEC section 2.4, S1-S6).

Run:  .venv/bin/python -m app.model.calibrate        (writes hazard_fit.json)

Never imported on a request path (scipy). Inputs, all bundled or produced by
``scripts/model/season/*``:

* **Labels** (S1): fire-observed season start/end from NASA FIRMS MODIS daily
  counts in the Field et al. (2016) peat-belt box,
  ``scripts/model/season/data/firms_daily_modis.json`` (2001-2025), labelled by
  `app.model.forecast.season_labels` -- the same rule that resolves the odds.
  1997-2000 have no MODIS; they keep rainfall-rule dates in seasons.json and
  are excluded from the fit and from every skill figure.
* **Weather**: ERA5 areal mean of five peat-belt points,
  ``app/model/calibration_weather.json`` (1981-).
* **Indices**: ONI (CPC) and weekly DMI (NOAA OSMC), publication-lagged so
  every feature on day d is something a forecaster could have known on d.

Fit
---
1. Rainfall generator (S4): monthly Markov chain + gamma excess on areal-mean
   ERA5 rain 1981-2025; k_enso and k_iod by OLS of log monthly rainfall on the
   centred 3-month ONI and the monthly DMI, separately for Jun-Nov and Dec-May.
2. Hazard (S3) by discrete-time survival maximum likelihood (L-BFGS-B, sign
   bounds c1..c6 >= 0), then Nelder-Mead refinement of the median duration
   read off the deterministic survival curve on the observed weather.

Four error figures, as in v1:
* ``rmse_days``           in-sample, perfect foresight of the weather;
* ``rmse_days_loo``       leave-one-season-out;
* ``rmse_days_forecast``  Monte-Carlo from each season's start date with no
                          future weather -- the number the site publishes;
* ``anchor``              the documented-episode check (2015, 2019).
Plus: the same four for the v1 model on the same labels, an ENSO-only
ablation, the S6 positive-IOD split, and the spec's 2019 test (forecast from
1 Aug 2019 must be longer than the ENSO-only model's).
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import sys
from datetime import date, timedelta

from app.model import burn as burn_mod
from app.model import drought_code as dc_mod
from app.model import forecast as fc_mod

ROOT = pathlib.Path(__file__).resolve().parents[2]
FIT_PATH = pathlib.Path(__file__).with_name("hazard_fit.json")
WEATHER_PATH = pathlib.Path(__file__).with_name("calibration_weather.json")
FIRMS_PATH = ROOT / "scripts/model/season/data/firms_daily_modis.json"
FIRMS_VIIRS_PATH = ROOT / "scripts/model/season/data/firms_daily_viirs.json"

KEYS = burn_mod.KEYS
BOUNDS = [(-14.0, 4.0), (0.0, 6.0), (0.0, 6.0), (0.0, 6.0), (0.0, 6.0), (0.0, 4.0), (0.0, 4.0),
          (0.0, 40.0)]
ALL = (1, 1, 1, 1, 1, 1, 1, 1)
SPEC_ONLY = (1, 1, 1, 1, 1, 1, 1, 0)      # MODEL_SPEC S3 exactly: no seasonal clock
HORIZON = burn_mod.MAX_HORIZON_DAYS
#: Seasons whose end has an independent documented anchor (Fanin & van der
#: Werf 2017: 2015 fires stopped in late October; 2019 is the spec's IOD test).
ANCHOR_SEASONS = (2015, 2019)
POSITIVE_IOD_SON = 0.4   # [J] MODEL_SPEC S6; BoM +0.4 convention UNVERIFIED
GEN_FIT_YEARS = (1981, 2025)


# --------------------------------------------------------------------------- #
# labels
# --------------------------------------------------------------------------- #

def fire_labels(path=FIRMS_PATH, box="kalimantan") -> list[dict]:
    import numpy as np
    d = json.loads(pathlib.Path(path).read_text())
    years = sorted(int(y) for y in d["years"])
    raw = {}
    for y in years:
        cur = d["years"][str(y)][box]
        dates, counts = list(cur["dates"]), list(cur["count"])
        nxt = d["years"].get(str(y + 1))
        if nxt:
            dates += nxt[box]["dates"][:120]
            counts += nxt[box]["count"][:120]
        raw[y] = (dates, counts)
    maxima = [fc_mod.season_labels(*raw[y], y)["max_7d"] for y in years]
    floor = float(np.percentile(maxima, fc_mod.FLOOR_PERCENTILE))
    out = []
    for y in years:
        lab = fc_mod.season_labels(*raw[y], y, floor=floor)
        dur = None
        if lab["status"] == "season" and lab["start"]:
            dur = (date.fromisoformat(lab["end"]) - date.fromisoformat(lab["start"])).days
        out.append({"year": y, **lab, "duration": dur, "floor_7d": floor,
                    "season_total": int(sum(raw[y][1][:365 + (y % 4 == 0)])),
                    "target_source": f"firms_{d.get('sensor', 'modis')}"})
    return out


# --------------------------------------------------------------------------- #
# observed record with features and point-in-time indices
# --------------------------------------------------------------------------- #

class Record:
    def __init__(self, wx: dict):
        import numpy as np
        from app.data.loader_season import oni_known_on, dmi_known_on
        self.dates = list(wx["dates"])
        self.index = {d: i for i, d in enumerate(self.dates)}
        self.rain = np.asarray(wx["rain_mm"], float)
        self.tmax = np.asarray(wx["tmax_c"], float)
        f = burn_mod.observed_features(self.rain.tolist(), self.tmax.tolist())
        self.dc = np.asarray(f["dc"])
        self.p84 = np.asarray(f["p84"])
        self.wet10 = np.asarray(f["wet10"])
        self.nwet = np.asarray(f["n_wet"])
        self.deficit = np.maximum(0.0, burn_mod.ALPHA_P84 - self.p84)
        self.dcterm = np.maximum(0.0, self.dc - burn_mod.DC_CRIT) / 100.0
        oni = [oni_known_on(ds)[0] for ds in self.dates]
        dmi = [dmi_known_on(ds)[0] for ds in self.dates]
        self.oni = np.asarray([v if v is not None else 0.0 for v in oni], float)
        self.dmi = np.asarray([v if v is not None else 0.0 for v in dmi], float)
        self.source = wx.get("source")
        self.seasonal = None
        self.frailty_sd = 0.0
        self.mp = np.zeros(len(self.dates))

    def X(self, i0: int, i1: int, iod: bool = True):
        """Design rows for days i0..i1 inclusive: [1, wet10, nwet, -def, -dc, -oni, -dmi]."""
        import numpy as np
        sl = slice(i0, i1 + 1)
        n = i1 - i0 + 1
        return np.column_stack([np.ones(n), self.wet10[sl], self.nwet[sl], -self.deficit[sl],
                                -self.dcterm[sl], -self.oni[sl],
                                -self.dmi[sl] if iod else np.zeros(n), self.mp[sl]])

    def set_seasonal(self, params):
        import numpy as np
        self.seasonal = params
        self.mp = np.array([burn_mod.seasonal_clock(date.fromisoformat(d), params)
                            for d in self.dates]) if params else np.zeros(len(self.dates))


def _seasons(labels, rec):
    rows = []
    for lab in labels:
        if lab["status"] != "season" or not lab["start"]:
            continue
        i0 = rec.index.get(lab["start"])
        if i0 is None or i0 + HORIZON >= len(rec.dates):
            continue
        rows.append({"year": lab["year"], "start": lab["start"], "end": lab["end"],
                     "duration": lab["duration"], "i0": i0})
    return rows


def build_frame(rows, rec, iod=True):
    import numpy as np
    X, y, meta = [], [], []
    for s in rows:
        Xi = rec.X(s["i0"] + 1, s["i0"] + s["duration"], iod)
        yi = np.zeros(len(Xi))
        yi[-1] = 1.0
        X.append(Xi)
        y.append(yi)
        meta.append(np.full(len(Xi), s["year"]))
    return np.vstack(X), np.concatenate(y), np.concatenate(meta)


# --------------------------------------------------------------------------- #
# likelihood, duration, fits
# --------------------------------------------------------------------------- #

def _nll(theta, X, y):
    import numpy as np
    z = np.clip(X @ theta, -40.0, 40.0)
    ll = y * (-np.logaddexp(0.0, -z)) + (1.0 - y) * (-np.logaddexp(0.0, z))
    return float(-ll.sum() / len(y))


def _grad(theta, X, y):
    import numpy as np
    z = np.clip(X @ theta, -40.0, 40.0)
    p = 1.0 / (1.0 + np.exp(-z))
    return (X.T @ (p - y)) / len(y)


def _bounds(free):
    return [b if f else (0.0, 0.0) for b, f in zip(BOUNDS, free)]


def fit_mle(X, y, x0=None, free=ALL):
    import numpy as np
    from scipy.optimize import minimize
    free = np.asarray(free, bool)
    x0 = np.asarray(x0 if x0 is not None else [-3.5, 0.3, 0.3, 0.3, 0.3, 0.1, 0.1, 2.0], float) * free
    res = minimize(_nll, x0, args=(X, y), jac=_grad, method="L-BFGS-B", bounds=_bounds(free))
    return np.asarray(res.x, float) * free, float(res.fun)


def clip(theta, free=ALL):
    import numpy as np
    return np.array([min(max(float(v), lo), hi) if f else 0.0
                     for v, (lo, hi), f in zip(theta, BOUNDS, free)])


def predicted_duration(theta, s, rec, iod=True) -> float:
    import numpy as np
    X = rec.X(s["i0"] + 1, s["i0"] + HORIZON, iod)
    h = 1.0 / (1.0 + np.exp(-np.clip(X @ theta, -40, 40)))
    surv = np.cumprod(1.0 - h)
    hit = np.nonzero(surv <= 0.5)[0]
    return float(hit[0] + 1) if hit.size else float(HORIZON + 1)


def rmse_of(theta, rows, rec, iod=True):
    import numpy as np
    pred = np.array([predicted_duration(theta, s, rec, iod) for s in rows])
    obs = np.array([s["duration"] for s in rows], float)
    return float(np.sqrt(np.mean((pred - obs) ** 2))), pred


def fit_two_stage(X, y, rows, rec, free=ALL, iod=True, maxiter=1500):
    import numpy as np
    from scipy.optimize import minimize
    th_mle, nll = fit_mle(X, y, free=free)
    best = (rmse_of(th_mle, rows, rec, iod)[0], th_mle)
    freeb = np.asarray(free, bool)
    for x0 in (th_mle, np.array([-5.0, 0.5, 0.5, 0.5, 0.5, 0.2, 0.2, 4.0]) * freeb):
        def obj(v):
            t = np.zeros(8)
            t[freeb] = v
            return rmse_of(clip(t, free), rows, rec, iod)[0]
        r = minimize(obj, x0[freeb], method="Nelder-Mead",
                     options={"maxiter": maxiter, "xatol": 1e-4, "fatol": 1e-3})
        t = np.zeros(8)
        t[freeb] = r.x
        t = clip(t, free)
        e = rmse_of(t, rows, rec, iod)[0]
        if e < best[0]:
            best = (e, t)
    return best[1], th_mle, nll


# --------------------------------------------------------------------------- #
# rainfall generator (S4)
# --------------------------------------------------------------------------- #

def fit_rain_generator(rec: Record, iod: bool = True, wet_threshold: float = 1.0) -> dict:
    import numpy as np
    from app.data.loader_season import oni_for_season_centred, load_iod
    y0, y1 = GEN_FIT_YEARS
    yrs = np.array([int(d[:4]) for d in rec.dates])
    mos = np.array([int(d[5:7]) for d in rec.dates])
    sel = (yrs >= y0) & (yrs <= y1)
    rain, tmax = rec.rain, rec.tmax
    wet = rain >= wet_threshold
    p01, p11, shape, excess, tdry, twet, tsd = [], [], [], [], [], [], []
    for m in range(1, 13):
        idx = np.nonzero(sel & (mos == m))[0]
        idx = idx[idx > 0]
        prev = wet[idx - 1]
        cur = wet[idx]
        p01.append(float(cur[~prev].mean()))
        p11.append(float(cur[prev].mean()))
        x = rain[idx][cur] - wet_threshold + 1e-3
        mean = float(x.mean())
        A = math.log(mean) - float(np.mean(np.log(x)))
        k = (1.0 + math.sqrt(1.0 + 4.0 * A / 3.0)) / (4.0 * A)       # Thom (1958)
        shape.append(round(k, 4))
        excess.append(round(mean, 3))
        tdry.append(round(float(tmax[idx][~cur].mean()), 3))
        twet.append(round(float(tmax[idx][cur].mean()), 3))
        resid = tmax[idx] - np.where(cur, twet[-1], tdry[-1])
        tsd.append(round(float(resid.std()), 3))

    # monthly totals and indices for the ENSO/IOD regression
    tot, freq, tav, X_oni, X_dmi, mm = [], [], [], [], [], []
    monthly_dmi = load_iod()["monthly"]
    for y in range(max(y0, 1982), y1 + 1):
        for m in range(1, 13):
            idx = np.nonzero((yrs == y) & (mos == m))[0]
            o = oni_for_season_centred(y, m)
            dm = monthly_dmi.get(f"{y:04d}-{m:02d}")
            if idx.size < 28 or o is None or dm is None:
                continue
            tot.append(float(rain[idx].sum()))
            freq.append(float(wet[idx].mean()))
            tav.append(float(tmax[idx].mean()))
            X_oni.append(o)
            X_dmi.append(dm)
            mm.append(m)
    tot, freq, tav = np.array(tot), np.array(freq), np.array(tav)
    X_oni, X_dmi, mm = np.array(X_oni), np.array(X_dmi), np.array(mm)
    clim_tot = {m: tot[mm == m].mean() for m in range(1, 13)}
    clim_frq = {m: freq[mm == m].mean() for m in range(1, 13)}
    clim_t = {m: tav[mm == m].mean() for m in range(1, 13)}
    la = np.log(np.maximum(tot, 1.0) / np.array([clim_tot[m] for m in mm]))
    lf = np.log(np.maximum(freq, 0.02) / np.array([clim_frq[m] for m in mm]))
    ta = tav - np.array([clim_t[m] for m in mm])
    regress = {}
    k_e, k_i, share = [0.0] * 12, [0.0] * 12, [0.5] * 12
    for name, months in (("jun_nov", range(6, 12)), ("dec_may", (12, 1, 2, 3, 4, 5))):
        s = np.isin(mm, list(months))
        cols = [np.ones(s.sum()), X_oni[s]] + ([X_dmi[s]] if iod else [])
        A = np.column_stack(cols)
        beta, *_ = np.linalg.lstsq(A, la[s], rcond=None)
        resid = la[s] - A @ beta
        dof = max(1, s.sum() - A.shape[1])
        cov = (resid @ resid / dof) * np.linalg.inv(A.T @ A)
        ss_tot = float(((la[s] - la[s].mean()) ** 2).sum())
        r2 = 1.0 - float(resid @ resid) / ss_tot if ss_tot > 0 else 0.0
        ke = -float(beta[1])
        ki = -float(beta[2]) if iod else 0.0
        sh = float(np.clip(np.polyfit(la[s], lf[s], 1)[0], 0.0, 1.0))
        regress[name] = {
            "k_enso": round(ke, 4), "k_enso_se": round(float(math.sqrt(cov[1, 1])), 4),
            "k_iod": round(ki, 4),
            "k_iod_se": round(float(math.sqrt(cov[2, 2])), 4) if iod else None,
            "r2": round(r2, 3), "n_months": int(s.sum()),
            "freq_share": round(sh, 3),
        }
        for m in months:
            k_e[m - 1], k_i[m - 1], share[m - 1] = ke, ki, sh
    s = np.isin(mm, list(range(6, 12)))
    t_coef = float(np.polyfit(X_oni[s], ta[s], 1)[0])
    return {
        "wet_threshold_mm": wet_threshold,
        "fit_period": f"{max(y0, 1982)}-{y1} (ENSO/IOD regression), {y0}-{y1} (daily)",
        "monthly": {"p01": [round(v, 4) for v in p01], "p11": [round(v, 4) for v in p11],
                    "gamma_shape": shape, "excess_mean_mm": excess,
                    "tmax_dry_c": tdry, "tmax_wet_c": twet, "tmax_sd_c": tsd},
        "k_enso_by_month": [round(v, 4) for v in k_e],
        "k_iod_by_month": [round(v, 4) for v in k_i],
        "freq_share_by_month": [round(v, 3) for v in share],
        "tmax_oni_coeff": round(t_coef, 3),
        "regression": regress,
        "note": ("k fitted by OLS of log(monthly areal-mean ERA5 rain / monthly climatology) "
                 "on the CPC 3-month ONI centred on that month and the OSMC monthly DMI. "
                 "Replaces the unsourced v1 constant ENSO_RAIN_K = 0.35, MEAN_WET_DAY_MM = 13 "
                 "and WET_PERSISTENCE = 0.35."),
    }


# --------------------------------------------------------------------------- #
# BMKG rainy-season onset per year (S5 diagnostic)
# --------------------------------------------------------------------------- #

def bmkg_onsets(rec, after_month=8) -> dict:
    """First calendar dasarian (1-10, 11-20, 21-end) after 1 Aug that begins
    three consecutive dasarian with >= 50 mm each (Giarno et al. 2012), on the
    ERA5 areal-mean rain; summarised by burn-window ENSO phase."""
    import numpy as np
    from app.data.loader import load_enso
    phase = {}
    for r in load_enso():
        bw = r.get("oni_burn_window")
        if bw is not None:
            phase[r["year"]] = "el_nino" if bw >= 0.5 else ("la_nina" if bw <= -0.5 else "neutral")
    years = sorted({int(d[:4]) for d in rec.dates})
    out = {}
    for y in years:
        das = []
        for m in range(after_month, 13):
            for lo, hi in ((1, 10), (11, 20), (21, 31)):
                idx = [rec.index.get(f"{y}-{m:02d}-{d:02d}") for d in range(lo, hi + 1)]
                idx = [i for i in idx if i is not None]
                if len(idx) < 8:
                    das = None
                    break
                das.append((f"{y}-{m:02d}-{lo:02d}", float(rec.rain[idx].sum())))
            if das is None:
                break
        if not das:
            continue
        for k in range(len(das) - 2):
            if all(das[k + j][1] >= burn_mod.WET10_MM for j in range(3)):
                out[y] = das[k][0]
                break
    doys = {y: date.fromisoformat(v).timetuple().tm_yday for y, v in out.items()}

    def q(vals):
        if not vals:
            return None
        a = np.percentile(vals, [25, 50, 75])
        return [int(round(x)) for x in a]
    by = {ph: q([d for y, d in doys.items() if phase.get(y) == ph])
          for ph in ("el_nino", "neutral", "la_nina")}
    return {"onset_by_year": out, "quartiles_doy_all": q(list(doys.values())),
            "quartiles_doy_by_enso_phase": by,
            "definition": "BMKG: first of three consecutive calendar dasarian (10-day periods) "
                          "each with >= 50 mm, searched from 1 Aug; ERA5 areal mean of five "
                          "peat-belt points (Giarno et al. 2012)",
            "comparison": "Moron et al. 2009 southern-Kalimantan onset quartiles 17 Sep / 22 Sep "
                          "/ 25 Oct (their own definition)"}


# --------------------------------------------------------------------------- #
# seasonal clock (onset day, width) by profile likelihood
# --------------------------------------------------------------------------- #

def profile_seasonal(rows, rec):
    """Fit the seasonal clock's onset DOY and width by maximum likelihood.

    For each (onset, width) on a grid the hazard's other coefficients are
    fitted by MLE; the pair with the lowest negative log-likelihood wins.
    Likelihood, not duration RMSE, is the selection criterion, so the
    leave-one-out and forecast errors reported later are not tuned on it.
    """
    grid = []
    best = None
    for onset in range(270, 336, 5):
        for width in (8.0, 14.0, 20.0, 30.0):
            params = {"onset_doy": onset, "width_days": width,
                      "retreat_doy": 110, "retreat_width_days": 28.0}
            rec.set_seasonal(params)
            X, y, _ = build_frame(rows, rec)
            _, nll = fit_mle(X, y)
            grid.append({"onset_doy": onset, "width_days": width, "nll": round(nll, 6)})
            if best is None or nll < best[0]:
                best = (nll, params)
    params = dict(best[1])
    params["fitted_by"] = ("profile maximum likelihood over onset 270-335 (step 5) x width "
                           "{8,14,20,30} days; retreat fixed at v1's DOY 110 / 28 d (irrelevant "
                           "inside a fire season)")
    return params, sorted(grid, key=lambda g: g["nll"])[:8]


# --------------------------------------------------------------------------- #
# probability calibration: per-season frailty
# --------------------------------------------------------------------------- #

def hindcast_pit(theta, gen, rows, rec, sd, step=10, n_sims=800, iod=True):
    """PIT of observed ends for leak-free hindcasts issued every ``step`` days
    from each season's start until its end."""
    import numpy as np
    coeffs = dict(zip(KEYS, [float(v) for v in theta]))
    pits = []
    for s_ in rows:
        for off in range(0, s_["duration"], step):
            ed = forecast_from(rec, coeffs, gen, s_["i0"] + off, n_sims, 300 + s_["year"] + off,
                               iod, frailty_sd=sd)[1]
            true = s_["duration"] - off
            pits.append(float((ed < true).mean() + 0.5 * (ed == true).mean()))
    p = np.array(pits)
    return {"frailty_sd": sd, "n_forecasts": int(p.size),
            "coverage_p10_p90": round(float(((p > 0.1) & (p < 0.9)).mean()), 3),
            "below_p10": round(float((p <= 0.1).mean()), 3),
            "above_p90": round(float((p >= 0.9).mean()), 3),
            "pit_histogram_deciles": np.histogram(p, bins=10, range=(0, 1))[0].tolist()}


def calibrate_frailty(theta, gen, rows, rec, iod=True):
    """Pick the frailty sd whose hindcast p10-p90 coverage is closest to 80%."""
    table = [hindcast_pit(theta, gen, rows, rec, sd, iod=iod)
             for sd in (0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5)]
    best = min(table, key=lambda t: abs(t["coverage_p10_p90"] - 0.8))
    return {"sd": best["frailty_sd"], "selected": best, "grid": table,
            "method": ("per-simulated-season Normal(0, sd) offset on the hazard log-odds; sd "
                       "chosen so that p10-p90 intervals of leak-free hindcasts, issued every "
                       "10 days through each 2001-2025 season, contain the observed end ~80% "
                       "of the time. In-sample, so mildly optimistic.")}


# --------------------------------------------------------------------------- #
# forecast mode
# --------------------------------------------------------------------------- #

def init_state(rec: Record, i: int) -> dict:
    lo = max(0, i - burn_mod.P84_DAYS + 1)
    return {"dc": float(rec.dc[i]), "rain_hist": rec.rain[lo:i + 1].tolist()}


def forecast_from(rec, coeffs, gen, i, n_sims=2000, seed=1, iod=True, frailty_sd=None):
    """Median days-to-end of a Monte-Carlo started on record day i (no future weather)."""
    import numpy as np
    d = date.fromisoformat(rec.dates[i])
    end_day, _, _ = burn_mod.run_hazard_simulation(
        d, d, init_state(rec, i), coeffs, gen, float(rec.oni[i]),
        float(rec.dmi[i]) if iod else 0.0, n_sims=n_sims, seed=seed, horizon=HORIZON,
        seasonal=rec.seasonal, frailty_sd=rec.frailty_sd if frailty_sd is None else frailty_sd)
    return float(np.median(end_day)), end_day


def forecast_rmse(theta, gen, rows, rec, iod=True, n_sims=2000, seed=11):
    import numpy as np
    coeffs = dict(zip(KEYS, [float(v) for v in theta]))
    pred = np.array([forecast_from(rec, coeffs, gen, s["i0"], n_sims, seed + s["year"], iod)[0]
                     for s in rows])
    obs = np.array([s["duration"] for s in rows], float)
    return float(np.sqrt(np.mean((pred - obs) ** 2))), pred


def fixed_date_rmse(theta, gen, rows, rec, mmdd="09-01", iod=True, n_sims=2000, seed=5):
    """End-date error of forecasts issued on a fixed calendar date inside the season."""
    import numpy as np
    coeffs = dict(zip(KEYS, [float(v) for v in theta]))
    errs = []
    for s in rows:
        d = f"{s['year']}-{mmdd}"
        if not (s["start"] <= d < s["end"]):
            continue
        i = rec.index[d]
        med, _ = forecast_from(rec, coeffs, gen, i, n_sims, seed + s["year"], iod)
        true_days = (date.fromisoformat(s["end"]) - date.fromisoformat(d)).days
        errs.append({"year": s["year"], "predicted_days": int(round(med)),
                     "observed_days": true_days, "residual_days": int(round(med - true_days))})
    r = float(np.sqrt(np.mean([e["residual_days"] ** 2 for e in errs]))) if errs else None
    return r, errs


# --------------------------------------------------------------------------- #
# active-season rule
# --------------------------------------------------------------------------- #

def fit_active_rule(labels, rec) -> dict:
    """Grid-search `dc >= dc_min and n_wet < n_wet_max` against fire-observed
    in-season days (1 Jul - 31 Dec, 2001-2025), maximising balanced accuracy."""
    import numpy as np
    truth, idxs = [], []
    by_year = {l["year"]: l for l in labels}
    for y, lab in by_year.items():
        d = date(y, 7, 1)
        while d <= date(y, 12, 31):
            i = rec.index.get(d.isoformat())
            if i is not None:
                ins = (lab["status"] == "season" and lab["start"] and
                       lab["start"] <= d.isoformat() < lab["end"])
                truth.append(bool(ins))
                idxs.append(i)
            d += timedelta(days=1)
    truth = np.array(truth)
    dc = rec.dc[idxs]
    nw = rec.nwet[idxs]
    best = None
    for t in range(0, 501, 10):
        for nmax in (1, 2, 3, 4):
            pred = (dc >= t) & (nw < nmax)
            tpr = (pred & truth).sum() / max(1, truth.sum())
            tnr = (~pred & ~truth).sum() / max(1, (~truth).sum())
            ba = 0.5 * (tpr + tnr)
            if best is None or ba > best[0]:
                best = (ba, t, nmax, tpr, tnr)
    ba, t, nmax, tpr, tnr = best
    return {"dc_min": float(t), "n_wet_max": int(nmax),
            "balanced_accuracy": round(float(ba), 3),
            "true_positive_rate": round(float(tpr), 3),
            "true_negative_rate": round(float(tnr), 3),
            "n_days": int(len(truth)),
            "method": "grid search over 1 Jul-31 Dec 2001-2025 against fire-observed "
                      "in-season days; in-sample, so optimistic"}


# --------------------------------------------------------------------------- #
# v1 on the new labels
# --------------------------------------------------------------------------- #

def legacy_v1_on_new_labels(labels) -> dict:
    sys.path.insert(0, str(ROOT / "scripts/model/season"))
    import numpy as np
    import legacy_v1_calibrate as v1c  # noqa: E402
    import legacy_v1_burn as v1b       # noqa: E402
    from app.data.loader import load_seasons, load_enso
    enso = {r["year"]: r for r in load_enso()}
    seasons = []
    for lab in labels:
        if lab["status"] != "season":
            continue
        e = enso.get(lab["year"], {})
        seasons.append({"year": lab["year"], "start_date": lab["start"], "end_date": lab["end"],
                        "duration_days": lab["duration"],
                        "oni_peak_aug_nov": e.get("oni_peak_aug_nov"),
                        "enso_phase": e.get("phase")})
    X, y, meta, rows, rec = v1c.build_training_frame(seasons)
    shipped = np.array([v1b.HAZARD_COEFFS[k] for k in v1c.KEYS])
    r_in, pred_in, obs = v1c._rmse(shipped, rows, rec)
    r_fc, pred_fc = v1c._forecast_rmse(shipped, rows, rec, n_sims=1500, seed=7)
    anchors = [{"year": s["year"], "observed_days": s["duration"],
                "predicted_days": int(round(p))} for s, p in zip(rows, pred_in)
               if s["year"] in ANCHOR_SEASONS]
    refit = v1c.fit_hazard_coefficients(seasons=seasons, write=False, n_sims=1500)
    ref_anch = [r for r in refit["residuals"] if r["year"] in ANCHOR_SEASONS]
    # the same refit, but with the ONI a forecaster could have known at season
    # start instead of v1's post-season Aug-Nov peak (which leaks the future)
    from app.data.loader_season import oni_known_on
    honest = [dict(s_, oni=oni_known_on(s_["start_date"])[0]) for s_ in seasons]
    for h in honest:
        h.pop("oni_peak_aug_nov", None)
    refit_h = v1c.fit_hazard_coefficients(seasons=honest, write=False, n_sims=1500)
    return {
        "note": ("v1 = KBDI + 7-day rain + fixed-DOY monsoon logistic + post-season ONI "
                 "(oni_peak_aug_nov), single point (Palangka Raya). Evaluated on the same "
                 "fire-observed labels as v2. 'as_shipped' uses the v1 coefficients fitted "
                 "on rainfall-rule labels, so on these labels every figure is out-of-sample "
                 "for it; 'refit' refits the v1 structure on the fire-observed labels."),
        "as_shipped": {
            "coefficients": dict(zip(v1c.KEYS, shipped.round(5).tolist())),
            "rmse_days": round(r_in, 2), "rmse_days_forecast": round(r_fc, 2),
            "anchor_seasons": anchors,
            "anchor_rmse_days": round(float(np.sqrt(np.mean(
                [(a["predicted_days"] - a["observed_days"]) ** 2 for a in anchors]))), 2),
        },
        "refit": {k: refit[k] for k in ("coefficients", "rmse_days", "rmse_days_loo",
                                         "rmse_days_forecast", "anchor_rmse_days",
                                         "n_seasons", "naive_rmse_days_mean_predictor")},
        "refit_anchor_seasons": ref_anch,
        "refit_known_oni": {k: refit_h[k] for k in ("coefficients", "rmse_days", "rmse_days_loo",
                                                     "rmse_days_forecast", "anchor_rmse_days")},
        "refit_known_oni_note": ("v1 structure refitted with the ONI known at each season's start "
                                 "(no post-season information): the fair comparison for v2."),
        "n_seasons": len(rows),
    }


# --------------------------------------------------------------------------- #
# main fit
# --------------------------------------------------------------------------- #

def fit_all(n_sims: int = 2000, legacy: bool = True, write: bool = True) -> dict:
    import numpy as np
    wx = json.loads(WEATHER_PATH.read_text())
    rec = Record(wx)
    labels = fire_labels()
    rows = _seasons(labels, rec)
    obs = np.array([s["duration"] for s in rows], float)
    years = [s["year"] for s in rows]
    from app.data.loader_season import load_iod
    iod_seasons = load_iod()["seasons"]

    out = {"fitted_at": date.today().isoformat(), "model_version": 2}

    # --- generators ------------------------------------------------------
    gen = fit_rain_generator(rec, iod=True)
    gen_enso = fit_rain_generator(rec, iod=False)

    # --- seasonal clock: onset/width by profile likelihood ------------------
    seasonal, profile = profile_seasonal(rows, rec)
    rec.set_seasonal(seasonal)

    def variant(free, generator, iod=True):
        rec.frailty_sd = 0.0
        X, y, meta = build_frame(rows, rec, iod=iod)
        th, th_mle, nll = fit_two_stage(X, y, rows, rec, free=free, iod=iod)
        r_in, pred_in = rmse_of(th, rows, rec, iod)
        loo = []
        for s_ in rows:
            keep = meta != s_["year"]
            t_loo, _ = fit_mle(X[keep], y[keep], x0=th_mle, free=free)
            t_loo = clip(t_loo + (th - th_mle), free)
            loo.append(predicted_duration(t_loo, s_, rec, iod) - s_["duration"])
        frail = calibrate_frailty(th, generator, rows, rec, iod=iod)
        rec.frailty_sd = frail["sd"]
        r_fc, pred_fc = forecast_rmse(th, generator, rows, rec, iod=iod, n_sims=n_sims)
        r_sep, sep_rows = fixed_date_rmse(th, generator, rows, rec, "09-01", iod=iod,
                                          n_sims=n_sims)
        anchors = [{"year": s_["year"], "observed_days": s_["duration"],
                    "predicted_days": int(round(p)), "residual_days": int(round(p - s_["duration"])),
                    "forecast_days": int(round(pf))}
                   for s_, p, pf in zip(rows, pred_in, pred_fc) if s_["year"] in ANCHOR_SEASONS]
        return {
            "theta": th, "theta_mle": th_mle, "nll": nll, "n_obs": int(len(y)),
            "pred_in": pred_in, "loo": loo, "pred_fc": pred_fc, "sep_rows": sep_rows,
            "frailty": frail,
            "summary": {
                "coefficients": {k: round(float(v), 5) for k, v in zip(KEYS, th)},
                "rmse_days": round(r_in, 2),
                "rmse_days_loo": round(float(np.sqrt(np.mean(np.square(loo)))), 2),
                "rmse_days_forecast": round(r_fc, 2),
                "frailty_sd": frail["sd"],
                "hindcast_interval_coverage": frail["selected"]["coverage_p10_p90"],
                "anchor_rmse_days": round(float(np.sqrt(np.mean(
                    [a["residual_days"] ** 2 for a in anchors]))), 2),
                "anchor_seasons": anchors,
                "rmse_days_forecast_from_1_sep": round(r_sep, 2) if r_sep is not None else None,
            },
        }

    main = variant(ALL, gen)
    spec = variant(SPEC_ONLY, gen)
    enso = variant((1, 1, 1, 1, 1, 1, 0, 1), gen_enso, iod=False)
    theta, th_mle, nll = main["theta"], main["theta_mle"], main["nll"]
    th_e = enso["theta"]
    pred_in, loo, pred_fc, pred_fc_e = main["pred_in"], main["loo"], main["pred_fc"], enso["pred_fc"]
    r_in = main["summary"]["rmse_days"]
    r_loo = main["summary"]["rmse_days_loo"]
    r_fc = main["summary"]["rmse_days_forecast"]
    r_sep, sep_rows = main["summary"]["rmse_days_forecast_from_1_sep"], main["sep_rows"]

    # --- other ablations (in-sample only) ---------------------------------
    X, yy, _ = build_frame(rows, rec)
    abl = {}
    for name, free in (("no_rain_terms (c1=c2=c3=0)", (1, 0, 0, 0, 1, 1, 1, 1)),
                       ("no_drought_code (c4=0)", (1, 1, 1, 1, 0, 1, 1, 1)),
                       ("no_climate_indices (c5=c6=0)", (1, 1, 1, 1, 1, 0, 0, 1)),
                       ("seasonal_clock_only (c0, c7)", (1, 0, 0, 0, 0, 0, 0, 1)),
                       ("intercept_only", (1, 0, 0, 0, 0, 0, 0, 0))):
        th_a, _, _ = fit_two_stage(X, yy, rows, rec, free=free, maxiter=800)
        abl[name] = {"rmse_days": round(rmse_of(th_a, rows, rec)[0], 2),
                     "coefficients": dict(zip(KEYS, th_a.round(5).tolist()))}

    # --- 2019 test: forecast from 1 Aug 2019 -----------------------------
    i19 = rec.index["2019-08-01"]
    rec.frailty_sd = main["frailty"]["sd"]
    c_full = dict(zip(KEYS, theta.tolist()))
    c_enso = dict(zip(KEYS, th_e.tolist()))
    m_full, ed_full = forecast_from(rec, c_full, gen, i19, 5000, 2019)
    m_enso, ed_enso = forecast_from(rec, c_enso, gen_enso, i19, 5000, 2019, iod=False,
                                    frailty_sd=enso["frailty"]["sd"])
    seeds = [forecast_from(rec, c_full, gen, i19, 5000, sd)[0]
             - forecast_from(rec, c_enso, gen_enso, i19, 5000, sd, iod=False,
                             frailty_sd=enso["frailty"]["sd"])[0] for sd in range(10)]
    c_spec = dict(zip(KEYS, spec["theta"].tolist()))
    m_spec, _ = forecast_from(rec, c_spec, gen, i19, 5000, 2019, frailty_sd=spec["frailty"]["sd"])
    lab19 = next(l for l in labels if l["year"] == 2019)
    d0 = date(2019, 8, 1)
    test_2019 = {
        "as_of": "2019-08-01",
        "oni_known": round(float(rec.oni[i19]), 2), "dmi_known": round(float(rec.dmi[i19]), 2),
        "full_model_p50_days": m_full,
        "full_model_p50_end": (d0 + timedelta(days=int(m_full))).isoformat(),
        "full_model_p10_p90": [float(np.percentile(ed_full, 10)), float(np.percentile(ed_full, 90))],
        "enso_only_p50_days": m_enso,
        "enso_only_p50_end": (d0 + timedelta(days=int(m_enso))).isoformat(),
        "enso_only_p10_p90": [float(np.percentile(ed_enso, 10)), float(np.percentile(ed_enso, 90))],
        "spec_s3_model_p50_days": m_spec,
        "full_minus_enso_only_p50_days_over_10_seeds": seeds,
        "observed_end": lab19["end"],
        "observed_days_from_as_of": (date.fromisoformat(lab19["end"]) - d0).days,
        "passes": bool(m_full > m_enso),
    }

    # --- positive-IOD split (S6) ----------------------------------------
    def _split(pred):
        pos, oth = [], []
        for s, p in zip(rows, pred):
            son = (iod_seasons.get(str(s["year"])) or {}).get("dmi_son")
            (pos if son is not None and son > POSITIVE_IOD_SON else oth).append(p - s["duration"])
        f = lambda e: round(float(np.sqrt(np.mean(np.square(e)))), 2) if e else None
        return {"positive_iod": f(pos), "n_positive_iod": len(pos),
                "other": f(oth), "n_other": len(oth)}

    # --- anchors -----------------------------------------------------------
    anchors = [{"year": s["year"], "observed_days": s["duration"],
                "predicted_days": int(round(p)), "residual_days": int(round(p - s["duration"])),
                "forecast_days": int(round(pf))}
               for s, p, pf in zip(rows, pred_in, pred_fc) if s["year"] in ANCHOR_SEASONS]
    anchor_rmse = round(float(np.sqrt(np.mean([a["residual_days"] ** 2 for a in anchors]))), 2)

    naive = float(np.sqrt(np.mean((obs - obs.mean()) ** 2)))
    active_rule = fit_active_rule(labels, rec)

    # --- VIIRS cross-check of the labels ----------------------------------
    viirs = []
    if FIRMS_VIIRS_PATH.exists():
        try:
            vl = {l["year"]: l for l in fire_labels(FIRMS_VIIRS_PATH)}
            for l in labels:
                v = vl.get(l["year"])
                if v:
                    viirs.append({"year": l["year"], "modis_start": l["start"], "viirs_start": v["start"],
                                  "modis_end": l["end"], "viirs_end": v["end"]})
        except Exception as exc:  # pragma: no cover
            viirs = [{"error": repr(exc)}]

    try:
        sys.path.insert(0, str(ROOT / "scripts/model/season"))
        import verify_dc
        cache = os.environ.get("CFFDRS_CACHE")
        dc_check = verify_dc.verify(pathlib.Path(cache) if cache else None)
    except Exception as exc:  # pragma: no cover
        dc_check = {"error": repr(exc)}

    out.update({
        "coefficients": {k: round(float(v), 5) for k, v in zip(KEYS, theta)},
        "coefficients_mle": {k: round(float(v), 5) for k, v in zip(KEYS, th_mle)},
        "coefficient_signs": "h = sigmoid(c0 + c1*wet10 + c2*n_wet - c3*deficit - c4*dc_term "
                             "- c5*ONI - c6*DMI); c1..c6 >= 0",
        "fixed_parameters": {
            "alpha_p84_mm_per_day": burn_mod.ALPHA_P84,
            "alpha_source": "Field et al. 2016, PNAS 113:9204 (Kalimantan haze 3.7; range 3.7-5.5)",
            "dc_crit": burn_mod.DC_CRIT,
            "dc_crit_source": "BMKG 'very dry' class >350 (range 300-400; Field et al. 2004 388.2)",
            "wet10_mm": burn_mod.WET10_MM,
            "wet10_source": "BMKG dasarian criterion, Giarno et al. 2012 (3 x 10 days >= 50 mm = onset)",
        },
        "rain_generator": gen,
        "rain_generator_enso_only": gen_enso,
        "active_rule": active_rule,
        "season_definition": fc_mod.SEASON_END_DEFINITION,
        "labels": [{k: l[k] for k in ("year", "status", "start", "peak", "end", "duration",
                                       "max_7d", "target_source")} for l in labels],
        "label_floor_7d": labels[0]["floor_7d"] if labels else None,
        "skill": {
            "n_seasons": len(rows),
            "seasons": years,
            "rmse_days": round(r_in, 2),
            "rmse_days_loo": round(r_loo, 2),
            "rmse_days_forecast": round(r_fc, 2),
            "anchor_rmse_days": anchor_rmse,
            "anchor_seasons": anchors,
            "rmse_days_forecast_from_1_sep": round(r_sep, 2) if r_sep is not None else None,
            "forecast_from_1_sep": sep_rows,
            "naive_rmse_days_mean_predictor": round(naive, 2),
            "mean_observed_duration_days": round(float(obs.mean()), 1),
            "sd_observed_duration_days": round(float(obs.std()), 1),
            "forecast_rmse_by_iod": _split(pred_fc),
            "n_daily_observations": int(main["n_obs"]),
            "negative_log_likelihood": round(nll, 6),
        },
        "enso_only_ablation": {**enso["summary"], "forecast_rmse_by_iod": _split(pred_fc_e),
                               "note": "c6 = 0 and a rainfall generator fitted without DMI"},
        "spec_s3_without_seasonal_clock": {
            **spec["summary"], "forecast_rmse_by_iod": _split(spec["pred_fc"]),
            "note": ("The hazard exactly as written in MODEL_SPEC S3 (no seasonal-clock term). "
                     "Reported because it is what the spec asked for; it is not shipped "
                     "because it is worse than both the shipped v2 model and the v1 structure "
                     "refitted on the same labels.")},
        "seasonal": seasonal,
        "frailty": main["frailty"],
        "monsoon_onset": bmkg_onsets(rec),
        "seasonal_profile": profile,
        "ablations_in_sample": abl,
        "test_2019_forecast_from_1_aug": test_2019,
        "residuals": [{"year": s["year"], "start": s["start"], "end": s["end"],
                       "observed_days": s["duration"], "predicted_days": int(round(p)),
                       "loo_residual_days": int(round(lr)),
                       "forecast_days": int(round(pf)), "enso_only_forecast_days": int(round(pe)),
                       "oni_at_start": round(float(rec.oni[s["i0"]]), 2),
                       "dmi_at_start": round(float(rec.dmi[s["i0"]]), 2)}
                      for s, p, lr, pf, pe in zip(rows, pred_in, loo, pred_fc, pred_fc_e)],
        "viirs_label_check": viirs,
        "drought_code_verification": dc_check,
        "weather_source": "observed",
        "weather_provenance": rec.source,
        "weather_record": [rec.dates[0], rec.dates[-1]],
        "target_is_rainfall_derived": False,
        "target_note": ("Season dates are fire-observed (NASA FIRMS MODIS counts, site rule in "
                        "season_definition). The v1 rainfall-rule target, and its circularity, "
                        "are gone; in-sample error is therefore expected to rise (MODEL_SPEC S6)."),
        "caveats": CAVEATS,
    })
    if legacy:
        out["v1_on_fire_labels"] = legacy_v1_on_new_labels(labels)
    if write:
        FIT_PATH.write_text(json.dumps(out, indent=1))
    return out


CAVEATS = [
    "About 22 fitted seasons and seven coefficients: enough to fit, barely enough to "
    "validate. Judge the model by the leave-one-out and forecast-mode errors.",
    "The season labels are this site's own rule on MODIS fire counts. A different "
    "threshold (5% or 20% of the season maximum; 7 or 21 quiet days) moves end dates "
    "by days to weeks, and the coefficients move with them.",
    "MODIS Terra/Aqua orbits have drifted since about 2020-2022, changing overpass times "
    "and fire counts. Relative thresholds absorb a constant change, not a trend within "
    "a season.",
    "The rainfall generator's climatology includes the season being forecast (it is "
    "fitted on 1981-2025), a small leak that flatters forecast-mode error slightly.",
    "Parameter uncertainty is not propagated into the published interval; the p10-p90 "
    "band is weather uncertainty only.",
    "Land use changed across 2001-2025 (drainage, the 2011 moratorium, the 2016 peat "
    "restoration agency); one stationary coefficient set papers over that.",
]


def main():  # pragma: no cover
    out = fit_all()
    show = {k: out[k] for k in ("coefficients", "skill", "enso_only_ablation",
                                "test_2019_forecast_from_1_aug", "active_rule")}
    show["v1"] = out.get("v1_on_fire_labels")
    print(json.dumps(show, indent=1, default=str))


if __name__ == "__main__":  # pragma: no cover
    main()


#: v1 name, kept because ``app.model.__init__`` exposes it lazily.
fit_hazard_coefficients = fit_all
