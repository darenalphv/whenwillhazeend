"""LEGACY v1 season-end model (kept only for skill comparison; not served). When does the *current* Kalimantan burn episode stop?

The question "how many more days of haze?" is a survival question, not a
regression question, so the model is a **discrete-time survival (hazard)
model**, Monte-Carlo'd forward day by day.

Equations
---------
For each day *d* after ``as_of``, the probability that the episode terminates
on that day, given that it is still burning, is

    h(d) = sigmoid( b0
                  + b1 * rain_7d(d)                    [mm, trailing 7-day sum]
                  + b2 * drought_factor(KBDI(d))       [0..1]
                  + b3 * oni                           [degC, ONI]
                  + b4 * monsoon_progress(d) )         [0..1]

with b1, b4 >= 0 (rain and the monsoon end fires) and b2, b3 <= 0 (drought and
El Nino prolong them).  The survival function is the usual discrete product

    S(D) = prod_{d=1..D} (1 - h(d))
    P(end on day D) = h(D) * S(D-1)

and the reported p10/p50/p90 are quantiles of the simulated termination day.

**Why the driver is rain_7d and not rain today.**  This is the crux of tropical
peat fire.  A smouldering front sits 10-50 cm below the surface, is
self-sustaining at very low oxygen supply, and is insulated from a shower by
the overlying peat.  Rewetting a peat dome enough to quench it requires the
water table to be raised, which takes sustained rain, not a storm.  With the
fitted coefficients below, a single 5 mm shower moves the daily termination
hazard by less than a percentage point, while seven consecutive days of >10 mm
(rain_7d ~ 70-100 mm) raises it by well over an order of magnitude -- and
simultaneously collapses KBDI, which removes the b2 term as well.  That
asymmetry is the whole physical content of the model.

**Weather forward-propagation.**  Days inside the 15-day Open-Meteo forecast horizon
use the forecast, with a multiplicative log-normal perturbation whose spread
grows with lead time (0 at day 1 to sigma=0.6 at day 15) to represent forecast
error.  Beyond the horizon each simulation draws its own rainfall from the
Markov-gamma sampler in `app.model.meteo`, conditioned on ONI, and carries its
own KBDI trajectory.  So the ensemble spread is genuine weather uncertainty,
not a variance parameter someone chose.

Scenarios
---------
``baseline``            as observed / forecast.
``no_enforcement``      burning continues to be used for land clearing and
                        suppression is nominal: hazard log-odds shifted down
                        (episodes run longer).
``strict_moratorium``   canal blocking, enforced no-burn, active suppression:
                        hazard log-odds shifted up.
``monsoon_early`` /
``monsoon_late``        monsoon onset moved 14 days earlier / later, which
                        shifts both the b4 term and the rainfall climatology.
The enforcement offsets are *policy assumptions*, not fitted quantities; they
are marked as such in ``SCENARIOS``.

Sources
-------
* Keetch & Byram (1968) - drought index (see `app.model.kbdi`).
* Field, R.D. et al. (2016). *PNAS* 113(33) 9204 - nonlinear ENSO-drought-fire
  sensitivity in Indonesia; the dry-season rainfall threshold for severe fire.
* Page, S.E. et al. (2002). The amount of carbon released from peat and forest
  fires in Indonesia during 1997. *Nature* 420, 61-65 - peat smouldering depth
  and persistence.
* Turetsky, M.R. et al. (2015). Global vulnerability of peatlands to fire and
  carbon loss. *Nature Geoscience* 8, 11-14 - why smouldering peat survives
  rain that would end a flaming fire.
* Wooster, M.J. et al. (2012). Field determination of biomass burning emission
  ratios and factors via open-path FTIR spectroscopy ... Indonesian peatland
  fires. *Atmos. Chem. Phys.* 11, 11700 - peat fire persistence and emissions.
* Huijnen, V. et al. (2016). Fire carbon emissions over maritime southeast Asia
  in 2015 largest since 1997. *Scientific Reports* 6, 26886.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.model import kbdi as kbdi_mod
from app.model import meteo as meteo_mod

__all__ = ["simulate_season_end", "HAZARD_COEFFS", "SCENARIOS", "CAVEATS",
           "hazard_logit", "region_centroid"]

# ---------------------------------------------------------------------------
# Fitted hazard coefficients.
#
# Fitted by `app.model.calibrate.fit_hazard_coefficients()` -- discrete-time
# survival maximum likelihood over every historical season returned by
# `app.data.loader.load_seasons()` that has both a start_date and an end_date,
# with weather reconstructed from ENSO-conditioned climatology (see
# calibrate.py), followed by a coarse grid refinement of b0 against observed
# durations.  Sign bounds enforced: b1,b4 >= 0; b2,b3 <= 0.
#
# Fitted 2026-09-18 against 27 historical seasons (1997-2025, the two years
# with no burn window excluded) using OBSERVED ERA5 daily weather at Palangka
# Raya, 1996-2026, cached in app/model/calibration_weather.json.
#
#   RMSE  14.18 days  in-sample, perfect foresight (weather known)
#   RMSE  17.97 days  leave-one-out
#   RMSE  25.48 days  forecast mode (Monte-Carlo from day 1, no future weather)
#   RMSE   8.19 days  on 1997 / 2015 / 2019, the three seasons whose dates come
#                     from documented fire episodes rather than a rainfall rule
#   RMSE  41.25 days  for a naive "always predict the mean duration" model
#
# CIRCULARITY WARNING. The start and end dates in app/data/seasons.json are
# themselves derived from an ERA5 rainfall rule, not from observed fire dates,
# so fitting a hazard with a rainfall term against them is partly self-
# referential and the in-sample number flatters the model. Ablations
# (app/model/hazard_fit.json): removing BOTH rainfall terms (b1 = b2 = 0) still
# gives 24.55 days; removing the climate terms (b3 = b4 = 0) gives 18.34 days;
# monsoon climatology alone gives 29.63 days. So roughly a third of the skill
# survives with no rainfall information at all, and the honest headline error is
# the 25 days of forecast mode, not the 14 of hindcast.
HAZARD_COEFFS = {
    "b0":  -3.60073,   # intercept: log-odds of ending on a dry, El-Nino-free day
    "b1":   0.00843,   # per mm of trailing 7-day rain     (>= 0: rain ends fires)
    "b2":  -5.54060,   # per unit drought factor 0..1      (<= 0: drought prolongs)
    "b3":  -0.15913,   # per degC of ONI                   (<= 0: El Nino prolongs)
    "b4":   5.98514,   # per unit monsoon progress 0..1    (>= 0: the rains end it)
}
HAZARD_FIT = {
    "fitted": True,
    "fitted_at": "2026-09-18",
    "n_seasons": 27,
    "rmse_days": 14.18,
    "rmse_days_loo": 17.97,
    "rmse_days_forecast": 25.48,
    "rmse_days_anchor_seasons": 8.19,
    "rmse_days_naive_mean": 41.25,
    "weather_source": "open-meteo archive (ERA5) 1996-2026 at Palangka Raya",
    "target_is_rainfall_derived": True,
    "detail": "app/model/hazard_fit.json",
}

#: Policy scenarios.  `logit_offset` is an *assumption*, not a fitted value.
SCENARIOS = {
    "baseline":          {"logit_offset": 0.0,  "monsoon_shift_days": 0,   "rain_multiplier": 1.0,
                          "estimated": False,
                          "label": "Business as usual"},
    "no_enforcement":    {"logit_offset": -0.55, "monsoon_shift_days": 0,  "rain_multiplier": 1.0,
                          "estimated": True,
                          "label": "No enforcement: burning continues for land clearing"},
    "strict_moratorium": {"logit_offset": 0.70, "monsoon_shift_days": 0,   "rain_multiplier": 1.0,
                          "estimated": True,
                          "label": "Enforced moratorium, canal blocking and active suppression"},
    "monsoon_early":     {"logit_offset": 0.0,  "monsoon_shift_days": -14, "rain_multiplier": 1.15,
                          "estimated": False,
                          "label": "Monsoon onset 14 days early"},
    "monsoon_late":      {"logit_offset": 0.0,  "monsoon_shift_days": 14,  "rain_multiplier": 0.85,
                          "estimated": False,
                          "label": "Monsoon onset 14 days late"},
}

MAX_HORIZON_DAYS = 270
DEFAULT_CENTROID = (-2.21, 113.92)   # Palangka Raya, Central Kalimantan
KBDI_SPINUP_DAYS = 150
#: Below this KBDI, or above this monsoon progress, there is no episode to end.
#: 350 is not a guess: over the ERA5-driven KBDI record 1997-2025 it separates
#: the 27 years that had a burn window from the two that did not (2010 peaked at
#: KBDI 329, 2022 at 276; every season year exceeded 465) with no
#: misclassifications. It is nevertheless a threshold fitted against exactly two
#: negative examples, so do not read much into its precision.
ACTIVE_KBDI_MIN = 350.0
ACTIVE_MONSOON_MAX = 0.80


def _d(s):
    return meteo_mod._d(s)


def _iso(d: date) -> str:
    return d.isoformat()


def hazard_logit(coeffs, rain_7d, drought_f, oni, monsoon, offset=0.0):
    """b0 + b1*rain7 + b2*df + b3*oni + b4*monsoon (+ scenario offset)."""
    return (coeffs["b0"] + offset
            + coeffs["b1"] * rain_7d
            + coeffs["b2"] * drought_f
            + coeffs["b3"] * oni
            + coeffs["b4"] * monsoon)


def region_centroid(region: str = "kalimantan") -> tuple[float, float, str]:
    """(lat, lon, resolved_name) for a region key or province name."""
    key = (region or "kalimantan").strip().lower()
    try:
        from app.data.loader import load_regions
        feats = (load_regions() or {}).get("features", [])
        if key not in ("kalimantan", "all", ""):
            for f in feats:
                p = f.get("properties", {})
                names = [str(p.get("name", "")), str(p.get("name_en", ""))]
                if any(key in n.lower() or n.lower() in key for n in names if n):
                    lat = p.get("capital_lat")
                    lon = p.get("capital_lon")
                    if lat is not None and lon is not None:
                        return float(lat), float(lon), str(p.get("name") or region)
        # Whole-island: use the reference station of the province holding the
        # most peat (Kalimantan Tengah / Palangka Raya). A geometric centroid
        # would fall between stations, where the bundled monthly normals and the
        # ERA5 record the model is calibrated on disagree by ~900 mm a year --
        # and a wet/dry bias in the reference climate translates directly into a
        # biased end date.
        best, best_peat = None, -1.0
        for f in feats:
            p = f.get("properties", {})
            lat, lon = p.get("capital_lat"), p.get("capital_lon")
            if lat is None or lon is None:
                continue
            peat = float(p.get("peat_mha") or 0.0)
            if peat > best_peat:
                best, best_peat = (float(lat), float(lon)), peat
        if best is not None:
            return best[0], best[1], "kalimantan"
    except Exception:
        pass
    return DEFAULT_CENTROID[0], DEFAULT_CENTROID[1], (region or "kalimantan")


def _latest_oni(as_of: str) -> float:
    try:
        from app.data.loader import load_enso
        rows = [r for r in (load_enso() or [])
                if meteo_mod.oni_value(r) is not None]
        rows.sort(key=lambda r: r.get("year", 0))
        yr = _d(as_of).year
        for r in reversed(rows):
            if int(r.get("year", 0)) <= yr:
                return float(meteo_mod.oni_value(r))
        return float(meteo_mod.oni_value(rows[-1])) if rows else 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# vectorised KBDI + rainfall propagation
# ---------------------------------------------------------------------------

def _kbdi_step_vec(np, q_prev, tmax_c, net_rain_mm, annual_rain_in):
    q = np.clip(q_prev - 100.0 * (net_rain_mm / 25.4), 0.0, 800.0)
    tf = np.clip(tmax_c * 9.0 / 5.0 + 32.0, -60.0, 160.0)
    dq = ((800.0 - q) * (0.968 * np.exp(0.0486 * tf) - 8.30)
          / (1.0 + 10.88 * math.exp(-0.0441 * annual_rain_in)) * 1e-3)
    return np.clip(q + np.maximum(dq, 0.0), 0.0, 800.0)


def _net_rain_vec(np, rain, event_total):
    """Vectorised Keetch-Byram net rainfall; mutates and returns event_total."""
    wet = rain > 0.0
    event_total = np.where(wet, event_total, 0.0)
    deductible = np.maximum(0.0, kbdi_mod.RAIN_INTERCEPTION_MM
                            - np.minimum(event_total, kbdi_mod.RAIN_INTERCEPTION_MM))
    net = np.where(wet, np.maximum(0.0, rain - deductible), 0.0)
    event_total = event_total + np.where(wet, rain, 0.0)
    return net, event_total


def _sample_rain_block(np, rng, months, p_wet_by_month, mean_by_month, wet_state):
    """One day of Markov-gamma rain for all sims (months is a scalar month)."""
    p = p_wet_by_month[months - 1]
    p_trans = np.where(wet_state,
                       p + meteo_mod.WET_PERSISTENCE * (1.0 - p),
                       p * (1.0 - meteo_mod.WET_PERSISTENCE))
    wet = rng.random(wet_state.shape) < np.clip(p_trans, 0.0, 1.0)
    mu = mean_by_month[months - 1] / max(1e-6, p * 30.4)
    draw = rng.gamma(meteo_mod.GAMMA_SHAPE, mu / meteo_mod.GAMMA_SHAPE, size=wet_state.shape)
    return np.where(wet, draw, 0.0), wet


# ---------------------------------------------------------------------------
# core simulation (shared with calibrate.py)
# ---------------------------------------------------------------------------

def run_hazard_simulation(as_of: str, lat: float, lon: float, oni: float,
                          coeffs: dict, n_sims: int = 5000,
                          seed: int | None = None, horizon: int = MAX_HORIZON_DAYS,
                          forecast: dict | None = None,
                          kbdi_start: float | None = None,
                          logit_offset: float = 0.0,
                          monsoon_shift_days: float = 0.0,
                          rain_multiplier: float = 1.0):
    """Monte-Carlo the daily-hazard survival model.

    Returns ``(end_day_array, p_end_per_day, p_still_per_day)`` where
    ``end_day`` is 1-based days after ``as_of`` (``horizon + 1`` = censored).
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    start = _d(as_of)

    sampler = meteo_mod.RainSampler(lat, lon, oni=oni, rng=rng,
                                    rain_multiplier=rain_multiplier)
    p_wet_by_month = np.array([sampler.p_wet(m) for m in range(1, 13)])
    mean_by_month = np.array([sampler.month_rain_mm(m) for m in range(1, 13)])
    tmax_by_month = np.array(sampler.clim["tmax_c"])
    annual_rain_in = max(float(sum(sampler.clim["rain_mm"])) / 25.4, 1.0)

    n = int(max(1, n_sims))
    q = np.full(n, float(kbdi_start if kbdi_start is not None else 400.0))
    event_total = np.zeros(n)
    wet_state = np.zeros(n, dtype=bool)
    rain_hist = np.zeros((7, n))
    if forecast and forecast.get("_rain_hist"):
        hist = [float(v) for v in forecast["_rain_hist"][-7:]]
        # align to the END of the buffer so the most recent day is row -1
        for i, v in enumerate(hist):
            rain_hist[7 - len(hist) + i, :] = v

    fc_dates = {}
    if forecast:
        for i, dt in enumerate(forecast.get("dates", [])):
            fc_dates[dt] = (forecast["rain_mm"][i], forecast["tmax_c"][i])

    ended = np.zeros(n, dtype=bool)
    end_day = np.full(n, horizon + 1, dtype=np.int32)
    p_end = np.zeros(horizon)
    p_still = np.zeros(horizon)

    for k in range(1, horizon + 1):
        cur = start + timedelta(days=k)
        key = _iso(cur)
        if key in fc_dates:
            base_rain, base_tmax = fc_dates[key]
            # forecast error grows with lead time
            sig = min(0.6, 0.04 * k)
            rain = np.asarray(base_rain, dtype=float) * rng.lognormal(-0.5 * sig * sig, sig, n)
            # a dry forecast can still verify wet; allow occasional busts
            bust = rng.random(n) < min(0.25, 0.015 * k)
            extra, _ = _sample_rain_block(np, rng, cur.month, p_wet_by_month,
                                          mean_by_month, np.zeros(n, dtype=bool))
            rain = np.where(bust, np.maximum(rain, extra), rain)
            tmax = float(base_tmax) + rng.normal(0.0, 0.5 + 0.05 * k, n)
            wet_state = rain > 0.0
        else:
            rain, wet_state = _sample_rain_block(np, rng, cur.month, p_wet_by_month,
                                                 mean_by_month, wet_state)
            tmax = (tmax_by_month[cur.month - 1] + np.where(wet_state, -1.2, 1.4)
                    + 0.35 * oni + rng.normal(0.0, 0.9, n))

        net, event_total = _net_rain_vec(np, rain, event_total)
        q = _kbdi_step_vec(np, q, tmax, net, annual_rain_in)

        rain_hist = np.roll(rain_hist, -1, axis=0)
        rain_hist[-1, :] = rain
        rain7 = rain_hist.sum(axis=0)

        df = _drought_factor_vec(np, q)
        mp = meteo_mod.monsoon_progress_shifted(key, lat, lon, monsoon_shift_days)

        z = (coeffs["b0"] + logit_offset + coeffs["b1"] * rain7
             + coeffs["b2"] * df + coeffs["b3"] * oni + coeffs["b4"] * mp)
        h = 1.0 / (1.0 + np.exp(-np.clip(z, -40.0, 40.0)))

        fires = (~ended) & (rng.random(n) < h)
        end_day = np.where(fires, k, end_day)
        ended = ended | fires
        p_end[k - 1] = float(fires.mean())
        p_still[k - 1] = float((~ended).mean())

    return end_day, p_end, p_still


_DF_L0 = 1.0 / (1.0 + math.exp(kbdi_mod._DF_CENTRE / kbdi_mod._DF_SCALE))
_DF_L800 = 1.0 / (1.0 + math.exp(-(800.0 - kbdi_mod._DF_CENTRE) / kbdi_mod._DF_SCALE))


def _drought_factor_vec(np, q):
    raw = 1.0 / (1.0 + np.exp(-(q - kbdi_mod._DF_CENTRE) / kbdi_mod._DF_SCALE))
    return np.clip((raw - _DF_L0) / (_DF_L800 - _DF_L0), 0.0, 1.0)


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def simulate_season_end(as_of: str, region: str = "kalimantan",
                        oni: float | None = None, n_sims: int = 5000,
                        seed: int | None = None, scenario: str = "baseline") -> dict:
    """How many more days until the current Kalimantan burn episode ends.

    See the module docstring for the hazard equation and its provenance.
    Deterministic for a given ``seed``.
    """
    import numpy as np

    scen_key = scenario if scenario in SCENARIOS else "baseline"
    scen = SCENARIOS[scen_key]
    n_sims = int(max(1, min(int(n_sims), 50000)))
    lat, lon, region_name = region_centroid(region)
    as_of_d = _d(as_of)
    if oni is None:
        oni = _latest_oni(as_of)
    oni = float(oni)

    # --- observed / forecast weather -------------------------------------
    wx = meteo_mod.fetch_weather(lat, lon,
                                 _iso(as_of_d - timedelta(days=KBDI_SPINUP_DAYS)),
                                 _iso(as_of_d + timedelta(days=meteo_mod.FORECAST_MAX_LEAD_DAYS)))
    dates = wx["dates"]
    try:
        cut = dates.index(_iso(as_of_d))
    except ValueError:
        cut = max(0, len(dates) - 17)

    annual_rain = meteo_mod.annual_rain_mm_for(lat, lon)
    hist_dates = dates[:cut + 1]
    hist_rain = [r * scen["rain_multiplier"] for r in wx["rain_mm"][:cut + 1]]
    series = kbdi_mod.kbdi_series(hist_dates, wx["tmax_c"][:cut + 1], hist_rain,
                                  annual_rain, kbdi_0=0.0)
    kbdi_now = series[-1] if series else 400.0
    rain7_now = float(sum(hist_rain[-7:]))
    mp_now = meteo_mod.monsoon_progress_shifted(_iso(as_of_d), lat, lon,
                                                scen["monsoon_shift_days"])
    df_now = kbdi_mod.drought_factor(kbdi_now)

    forecast = {
        "dates": dates[cut + 1:],
        "rain_mm": [r * scen["rain_multiplier"] for r in wx["rain_mm"][cut + 1:]],
        "tmax_c": wx["tmax_c"][cut + 1:],
        "_rain_hist": hist_rain[-7:],
    }

    active = bool(kbdi_now >= ACTIVE_KBDI_MIN and mp_now < ACTIVE_MONSOON_MAX)

    end_day, p_end, p_still = run_hazard_simulation(
        _iso(as_of_d), lat, lon, oni, HAZARD_COEFFS, n_sims=n_sims, seed=seed,
        horizon=MAX_HORIZON_DAYS, forecast=forecast, kbdi_start=kbdi_now,
        logit_offset=scen["logit_offset"],
        monsoon_shift_days=scen["monsoon_shift_days"],
        rain_multiplier=scen["rain_multiplier"])

    mean_is_trimmed = False
    if not active:
        p10 = p50 = p90 = 0
        mean_days = 0.0
    else:
        q10, q50, q90 = (float(x) for x in np.percentile(end_day, [10, 50, 90]))
        p10, p50, p90 = int(round(q10)), int(round(q50)), int(round(q90))
        p10, p50, p90 = sorted((p10, p50, p90))          # guarantee ordering
        mean_days = float(np.mean(np.minimum(end_day, MAX_HORIZON_DAYS + 1)))
        # A long censored tail can drag the mean past p90. Report the mean of
        # the central 80% in that case and say so, rather than publishing a
        # summary whose own numbers are out of order.
        mean_is_trimmed = not (p10 <= mean_days <= p90)
        if mean_is_trimmed:
            central = end_day[(end_day >= p10) & (end_day <= p90)]
            if central.size:
                mean_days = float(np.mean(central))

    def _date_at(nd: int) -> str:
        return _iso(as_of_d + timedelta(days=int(nd)))

    # --- driver attribution (share of |log-odds| excluding the intercept) --
    terms = [
        ("kbdi", "KBDI", kbdi_now, "index", HAZARD_COEFFS["b2"] * df_now,
         "Peat is dry to roughly {:.1f} m depth; drought this deep keeps the "
         "smouldering front alive.".format(0.1 + 0.9 * df_now)),
        ("rain_7d", "7-day rainfall", rain7_now, "mm", HAZARD_COEFFS["b1"] * rain7_now,
         "Rain over the last week. Peat needs sustained wetting, not a shower: "
         "about 70 mm over seven days is what actually puts it out."),
        ("oni", "ONI", oni, "degC", HAZARD_COEFFS["b3"] * oni,
         "El Nino (positive ONI) suppresses Borneo dry-season rainfall and "
         "pushes the monsoon break later."),
        ("monsoon", "Monsoon progress", mp_now, "0-1", HAZARD_COEFFS["b4"] * mp_now,
         "How far the wet monsoon has advanced over this location. The season "
         "ends when this crosses about 0.5, not before."),
    ]
    total = sum(abs(t[4]) for t in terms) or 1.0
    drivers = [{
        "id": did,
        "name": nm,
        "value": round(float(val), 3),
        "unit": unit,
        "contribution": round(abs(term) / total, 4),
        "direction": ("neutral" if abs(term) < 1e-9
                      else ("prolongs" if term < 0 else "shortens")),
        "explain": expl,
    } for did, nm, val, unit, term, expl in terms]

    spread = p90 - p10
    if wx["live"] and spread <= 30:
        confidence = "high"
    elif spread <= 75:
        confidence = "moderate"
    else:
        confidence = "low"
    if not HAZARD_FIT.get("fitted"):
        confidence = "low"
    if mean_is_trimmed:
        caveats_extra = ("More than 10% of simulations were still burning at the "
                         f"{MAX_HORIZON_DAYS}-day horizon, so the reported mean is "
                         "the mean of the central 80% of trajectories; the true "
                         "mean is longer.")
    else:
        caveats_extra = None

    horizon_days = min(MAX_HORIZON_DAYS, 180)
    daily = [{
        "date": _date_at(k + 1),
        "p_end": round(float(p_end[k]), 6),
        "p_still_burning": round(float(p_still[k]), 6),
    } for k in range(horizon_days)]

    caveats = list(CAVEATS)
    if caveats_extra:
        caveats.insert(0, caveats_extra)
    if not wx["live"]:
        caveats.insert(0, "No live weather was available for this run ("
                       + str(wx.get("fallback_reason")) + "), so the forecast "
                       "window is climatology rather than a real forecast. "
                       "Treat the interval as wider than shown.")
    if not active:
        caveats.insert(0, "No burn episode is detected as active at this date "
                       "(KBDI {:.0f}, monsoon progress {:.2f}), so the countdown "
                       "reads zero. This is a statement about drought and season, "
                       "not a satellite observation of zero fires."
                       .format(kbdi_now, mp_now))

    return {
        "as_of": _iso(as_of_d),
        "region": region_name,
        "oni": round(float(oni), 3),
        "active": active,
        "days_remaining": {"p10": p10, "p50": p50, "p90": p90,
                           "mean": round(mean_days, 1)},
        "end_date": {"p10": _date_at(p10), "p50": _date_at(p50),
                     "p90": _date_at(p90)},
        "daily_end_probability": daily,
        "drivers": drivers,
        "confidence": confidence,
        "live_weather": bool(wx["live"]),
        "scenario": scen_key,
        "caveats": caveats,
    }


CAVEATS = [
    "The historical season lengths this model is calibrated against are not "
    "observations of fire. Published start and end dates for individual "
    "Indonesian fire episodes barely exist, so the bundled dataset derives them "
    "from a rainfall rule applied to ERA5 reanalysis. Because this model also "
    "takes rainfall as an input, it is partly being fitted to its own input, "
    "and its hindcast accuracy is flattered as a result. Stripping every "
    "rainfall term out still leaves about a third of the skill, and the "
    "forecast-mode error of roughly 25 days is the number to hold it to. Three "
    "seasons - 1997, 2015 and 2019 - do have dates anchored in documented "
    "episodes, and the model is within about eight days on those.",
    "This predicts when the *burning episode* ends, not when the air is clean. "
    "Coarse particulate and the smell linger for days after the last flame, and "
    "re-ignition from a still-hot peat layer after a dry spell is common.",
    "The model has no ignitions in it. Almost every Kalimantan fire is lit by "
    "someone, for land clearing, and the end of a season is partly a decision "
    "about when people stop lighting them. That human term is folded into the "
    "fitted intercept and into the scenario offsets, which are assumptions "
    "rather than measurements.",
    "It is fitted to fewer than twenty observed seasons, several of which have "
    "start and end dates that are themselves estimates from hotspot time series "
    "rather than agreed observations. Any statement of precision finer than "
    "about a week is spurious.",
    "'The season' is treated as one island-wide episode with one end date. In "
    "reality Central Kalimantan, West Kalimantan and the Mahakam delta can end "
    "weeks apart, and a province-level query only shifts the location, not the "
    "single-episode assumption.",
    "The hazard function is a logistic in four drivers. It cannot represent the "
    "threshold behaviour that peat scientists actually describe, where the "
    "episode ends abruptly once the water table recovers past a critical depth. "
    "The real distribution of end dates is probably more sharply peaked than "
    "the simulated one.",
    "Groundwater, canal density and drainage state -- the strongest predictors "
    "of whether a given hectare of peat can burn at all -- are not inputs. Two "
    "landscapes with identical weather and very different canal networks get "
    "the same answer here.",
    "Coefficients were fitted on weather reconstructed from ENSO-conditioned "
    "climatology, not on the actual daily weather of each historical season, "
    "because a consistent daily record for every year back to 1997 is not "
    "bundled with this app. That makes the fit a relationship between season-"
    "scale drought and season length, not a daily-scale causal estimate.",
]
