"""Season-end model v2: when does the current Kalimantan fire season stop?

Implements research/MODEL_SPEC.md section 2.4 (S1-S6). It is still a
**discrete-time survival (hazard) model**, Monte-Carlo'd forward day by day,
but every input changed from v1:

    h(d) = sigmoid( c0 + c1*wet10(d) + c2*n_wet(d)
                    - c3*deficit(d) - c4*dc_term(d)
                    - c5*ONI_3mo - c6*DMI_3mo  [+ scenario offset] )

    deficit(d) = max(0, alpha - P84(d))       P84 = mean daily rain over the
                                              previous 84 days, mm/d (S3);
                                              alpha = 3.7 mm/d (Field et al. 2016)
    dc_term(d) = max(0, DC(d) - 350) / 100    Drought Code (S2; Van Wagner 1987;
                                              350 = BMKG "very dry")
    wet10(d)   = 1 if the last 10 days had >= 50 mm (BMKG dasarian unit)
    n_wet(d)   = consecutive trailing 10-day blocks with >= 50 mm, 0..3; three
                 is the BMKG rainy-season onset criterion (Giarno et al. 2012)
    ONI_3mo, DMI_3mo = the latest 3-month ONI / ~13-week DMI a forecaster could
                 have read on the forecast date (publication-lagged) and held
                 fixed over the simulation -- never a post-season value (S3).

Sign constraints c1..c6 >= 0. Coefficients, skill and the fitted rainfall
generator live in ``app/model/hazard_fit.json``, written offline by
``app/model/calibrate.py`` (scipy never runs on a request).

**Target.** The hazard is fitted to *fire-observed* season ends (S1), see
`app.model.forecast.SEASON_END_DEFINITION`. That is the site's own definition
[J]; no published one exists.

**Weather.** Areal mean of five peat-belt points (`meteo.PEAT_BELT_POINTS`):
observed up to the forecast date; the Open-Meteo forecast for up to 14 days
after it (live runs only); then a Markov-gamma generator fitted to ERA5 and
conditioned on ONI and DMI (S4):

    R_m(ONI, DMI) = R_m * exp(-k_enso,m * ONI - k_iod,m * DMI)

**No hindsight.** For a forecast date in the past the model sees weather only
up to that date and simulates everything after it; archived weather after the
forecast date is never used as a "forecast".

The v1 model (KBDI, 7-day rain, fixed-DOY monsoon logistic) is preserved for
comparison in ``scripts/model/season/legacy_v1.py``.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.model import drought_code as dc_mod
from app.model import meteo as meteo_mod

__all__ = ["simulate_season_end", "SCENARIOS", "CAVEATS", "hazard_logit",
           "region_centroid", "observed_features", "run_hazard_simulation",
           "prepare_state", "load_model", "KEYS", "ALPHA_P84", "DC_CRIT"]

KEYS = ("c0", "c1", "c2", "c3", "c4", "c5", "c6", "c7")
ALPHA_P84 = 3.7           # mm/d, Field et al. 2016 Kalimantan haze threshold
DC_CRIT = dc_mod.DC_CRIT  # 350, BMKG "very dry"
WET10_MM = 50.0           # BMKG dasarian rain criterion (Giarno et al. 2012)
P84_DAYS = 84
SPINUP_DAYS = 240         # DC spin-up before the forecast date
MAX_HORIZON_DAYS = 270
MAX_PRE_DAYS = 300        # cap on unobserved days simulated before as_of
DEFAULT_CENTROID = (-2.21, 113.92)

#: Policy scenarios. `logit_offset` is an *assumption*, not a fitted value.
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


def _d(s):
    return meteo_mod._d(s)


def _iso(d: date) -> str:
    return d.isoformat()


def load_model() -> dict:
    """The fitted model: coefficients, rainfall generator, active rule, skill."""
    from app.data.loader_season import load_hazard_fit
    return load_hazard_fit()


def hazard_logit(coeffs, wet10, n_wet, deficit, dc_term, oni, dmi, clock=0.0, offset=0.0):
    return (coeffs["c0"] + offset + coeffs["c1"] * wet10 + coeffs["c2"] * n_wet
            - coeffs["c3"] * deficit - coeffs["c4"] * dc_term
            - coeffs["c5"] * oni - coeffs["c6"] * dmi + coeffs.get("c7", 0.0) * clock)


def seasonal_clock(d: date, params: dict | None) -> float:
    """Climatological monsoon progress 0..1 on calendar date ``d``.

    Difference of two logistics on a year starting 15 Aug (the v1 form), but
    with the onset day and width **fitted by maximum likelihood** to the
    fire-observed season ends (calibrate.profile_seasonal) instead of v1's
    hand-set DOY 305. It is the climatological expectation of the monsoon,
    the part of the season's end that rainfall alone does not explain in
    this data; the observed onset enters separately through n_wet.
    """
    if not params:
        return 0.0
    doy = d.timetuple().tm_yday
    x = (doy - 227) % 365
    x_on = (params["onset_doy"] - 227) % 365
    x_off = (params.get("retreat_doy", 110) - 227) % 365
    rise = 1.0 / (1.0 + math.exp(-(x - x_on) / params["width_days"]))
    fall = 1.0 - 1.0 / (1.0 + math.exp(-(x - x_off) / params.get("retreat_width_days", 28.0)))
    return min(max(rise * fall, 0.0), 1.0)


def region_centroid(region: str = "kalimantan") -> tuple[float, float, str]:
    """v1 compatibility. The v2 model always uses the peat-belt areal mean;
    a province name changes only the label."""
    return DEFAULT_CENTROID[0], DEFAULT_CENTROID[1], (region or "kalimantan")


def feature_terms(dc: float, p84: float) -> tuple[float, float]:
    return max(0.0, ALPHA_P84 - p84), max(0.0, dc - DC_CRIT) / 100.0


# ---------------------------------------------------------------------------
# features on an observed series
# ---------------------------------------------------------------------------

def observed_features(rain, tmax, dc0: float = dc_mod.DC_START) -> dict:
    """DC, P84 and the BMKG onset features for a contiguous daily series.

    Day i uses rain up to and including day i. Partial P84 windows at the very
    start of a series are scaled to a daily mean.
    """
    n = len(rain)
    csum = [0.0]
    for r in rain:
        csum.append(csum[-1] + float(r))

    def blk(a, b):
        return csum[max(0, b)] - csum[max(0, a)]

    dc, p84, wet10, nwet = [], [], [], []
    cur = float(dc0)
    for i in range(n):
        cur = dc_mod.dc_step(cur, float(tmax[i]), float(rain[i]))
        dc.append(cur)
        lo = max(0, i + 1 - P84_DAYS)
        p84.append((csum[i + 1] - csum[lo]) / (i + 1 - lo))
        a = blk(i - 9, i + 1) >= WET10_MM
        b = blk(i - 19, i - 9) >= WET10_MM
        c = blk(i - 29, i - 19) >= WET10_MM
        wet10.append(1.0 if a else 0.0)
        nwet.append(float(a) * (1.0 + float(b) * (1.0 + float(c))))
    return {"dc": dc, "p84": p84, "wet10": wet10, "n_wet": nwet}


# ---------------------------------------------------------------------------
# stochastic weather generator (fitted by calibrate.fit_rain_generator)
# ---------------------------------------------------------------------------

class Generator:
    """Monthly two-state Markov chain + gamma excess amounts for areal-mean
    rain, and a wet/dry-conditioned normal tmax, conditioned on ONI and DMI.

    A wet day is >= ``wet_threshold_mm``; its rain is threshold + Gamma excess.
    The ENSO/IOD factor f = exp(-k_enso*ONI - k_iod*DMI) multiplies the
    monthly mean; a fitted share of log f goes to wet-day frequency and the
    rest to intensity.
    """

    def __init__(self, gen: dict, oni: float, dmi: float, rain_multiplier: float = 1.0):
        import numpy as np
        m = gen["monthly"]
        ke = np.array(gen["k_enso_by_month"], dtype=float)
        ki = np.array(gen["k_iod_by_month"], dtype=float)
        s = np.array(gen["freq_share_by_month"], dtype=float)
        logf = -(ke * oni + ki * dmi) + math.log(max(rain_multiplier, 1e-6))
        f_freq, f_int = np.exp(s * logf), np.exp((1.0 - s) * logf)
        self.thr = float(gen["wet_threshold_mm"])
        self.p01 = np.clip(np.array(m["p01"], float) * f_freq, 0.002, 0.98)
        self.p11 = np.clip(np.array(m["p11"], float) * f_freq, 0.002, 0.98)
        self.shape = np.array(m["gamma_shape"], float)
        mean_wet = (self.thr + np.array(m["excess_mean_mm"], float)) * f_int
        self.excess = np.maximum(mean_wet - self.thr, 0.1)
        self.t_dry = np.array(m["tmax_dry_c"], float) + gen.get("tmax_oni_coeff", 0.0) * oni
        self.t_wet = np.array(m["tmax_wet_c"], float) + gen.get("tmax_oni_coeff", 0.0) * oni
        self.t_sd = np.array(m["tmax_sd_c"], float)

    def climatological_mean_rain(self, month: int) -> float:
        i = month - 1
        p = self.p01[i] / max(1e-6, 1.0 - self.p11[i] + self.p01[i])
        return float(p * (self.thr + self.excess[i]))

    def step(self, np, rng, month: int, wet_prev):
        i = month - 1
        p = np.where(wet_prev, self.p11[i], self.p01[i])
        wet = rng.random(wet_prev.shape) < p
        k = self.shape[i]
        amt = self.thr + rng.gamma(k, self.excess[i] / k, size=wet_prev.shape)
        rain = np.where(wet, amt, 0.0)
        tmax = np.where(wet, self.t_wet[i], self.t_dry[i]) + rng.normal(
            0.0, self.t_sd[i], wet_prev.shape)
        return rain, tmax, wet


# ---------------------------------------------------------------------------
# core Monte-Carlo (shared with calibrate.py and forecast.py)
# ---------------------------------------------------------------------------

def run_hazard_simulation(start: date, as_of: date, init: dict, coeffs: dict,
                          gen: dict, oni: float, dmi: float, n_sims: int = 5000,
                          seed: int | None = None, horizon: int = MAX_HORIZON_DAYS,
                          forecast: dict | None = None, logit_offset: float = 0.0,
                          monsoon_shift_days: float = 0.0,
                          rain_multiplier: float = 1.0, seasonal: dict | None = None,
                          frailty_sd: float = 0.0):
    """Monte-Carlo the hazard from the observed state at ``start``.

    ``init`` = {"dc": float, "rain_hist": [up to 84 days of rain, oldest
    first]}. Days ``start+1 .. as_of`` are simulated without hazard (days
    before the forecast date with no observed weather); days ``as_of+1 ..
    as_of+horizon`` carry the hazard. ``forecast`` maps ISO date ->
    (rain_mm, tmax_c) and is used where present.

    Returns ``(end_day, p_end, p_still)``; ``end_day`` is 1-based days after
    ``as_of`` (``horizon + 1`` = still burning at the horizon).
    """
    import numpy as np
    rng = np.random.default_rng(seed)
    n = int(max(1, n_sims))
    G = Generator(gen, oni, dmi, rain_multiplier)
    hist = [float(x) for x in init["rain_hist"]][-P84_DAYS:]
    if len(hist) < P84_DAYS:      # only when spinning up without observations
        hist = [G.climatological_mean_rain(start.month)] * (P84_DAYS - len(hist)) + hist
    buf = np.tile(np.asarray(hist, dtype=float)[:, None], (1, n))   # ring, oldest at pos
    pos = 0
    s84 = buf.sum(axis=0)
    s10a = buf[-10:].sum(axis=0)
    s10b = buf[-20:-10].sum(axis=0)
    s10c = buf[-30:-20].sum(axis=0)
    dc = np.full(n, float(init["dc"]))
    wet_prev = buf[-1] >= G.thr
    fc = forecast or {}
    c = coeffs
    lin_const = c["c0"] + float(logit_offset) - c["c5"] * oni - c["c6"] * dmi
    if frailty_sd > 0:
        # per-season random log-odds offset ("frailty"): everything the model
        # does not see -- ignitions, enforcement, local hydrology. Its size is
        # set so hindcast p10-p90 intervals cover ~80% of outcomes.
        lin_const = lin_const + rng.normal(0.0, float(frailty_sd), n)
    shift = timedelta(days=int(round(monsoon_shift_days)))
    c7 = float(c.get("c7", 0.0))

    pre = max(0, (as_of - start).days)
    ended = np.zeros(n, dtype=bool)
    end_day = np.full(n, horizon + 1, dtype=np.int32)
    p_end = np.zeros(horizon)
    p_still = np.zeros(horizon)

    for k in range(1, pre + horizon + 1):
        cur = start + timedelta(days=k)
        key = _iso(cur)
        if key in fc:
            base_rain, base_tmax = fc[key]
            lead = max(1, (cur - as_of).days)
            sig = min(0.6, 0.04 * lead)
            rain = float(base_rain) * rng.lognormal(-0.5 * sig * sig, sig, n)
            bust = rng.random(n) < min(0.25, 0.015 * lead)
            extra, _, _ = G.step(np, rng, cur.month, np.zeros(n, dtype=bool))
            rain = np.where(bust, np.maximum(rain, extra), rain) * rain_multiplier
            tmax = float(base_tmax) + rng.normal(0.0, 0.5 + 0.05 * lead, n)
            wet_prev = rain >= G.thr
        else:
            rain, tmax, wet_prev = G.step(np, rng, (cur - shift).month, wet_prev)

        # rolling sums: P84 and the three trailing 10-day blocks
        r10 = buf[(pos - 10) % P84_DAYS]
        r20 = buf[(pos - 20) % P84_DAYS]
        r30 = buf[(pos - 30) % P84_DAYS]
        s84 += rain - buf[pos]
        s10c += r20 - r30
        s10b += r10 - r20
        s10a += rain - r10
        buf[pos] = rain
        pos = (pos + 1) % P84_DAYS
        dc = dc_mod.dc_step_vec(np, dc, tmax, rain)
        if k <= pre:
            continue

        j = k - pre
        a = s10a >= WET10_MM
        nwet = a * (1.0 + (s10b >= WET10_MM) * (1.0 + (s10c >= WET10_MM)))
        z = (lin_const + c7 * seasonal_clock(cur - shift, seasonal)
             + c["c1"] * a + c["c2"] * nwet
             - c["c3"] * np.maximum(0.0, ALPHA_P84 - s84 / P84_DAYS)
             - c["c4"] * np.maximum(0.0, dc - DC_CRIT) / 100.0)
        h = 1.0 / (1.0 + np.exp(-np.clip(z, -40.0, 40.0)))
        fires = (~ended) & (rng.random(n) < h)
        end_day[fires] = j
        ended |= fires
        p_end[j - 1] = fires.mean()
        p_still[j - 1] = 1.0 - ended.mean()
    return end_day, p_end, p_still


# ---------------------------------------------------------------------------
# observed state at the forecast date
# ---------------------------------------------------------------------------

def indices_known_on(as_of: date, oni_override=None, dmi_override=None) -> dict:
    """ONI / DMI a forecaster could have read on ``as_of`` (or overrides)."""
    from app.data.loader_season import oni_known_on, dmi_known_on
    oni_v, oni_lbl = oni_known_on(as_of)
    dmi_v, dmi_wk = dmi_known_on(as_of)
    return {
        "oni": float(oni_override) if oni_override is not None else float(oni_v or 0.0),
        "oni_season": "override" if oni_override is not None else oni_lbl,
        "dmi": float(dmi_override) if dmi_override is not None else float(dmi_v or 0.0),
        "dmi_through": "override" if dmi_override is not None else dmi_wk,
    }


_WX_CACHE: dict[tuple, tuple[float, dict]] = {}
_WX_TTL_S = 1800.0


def _weather_cached(start: str, end: str, as_of: str, allow_forecast: bool) -> dict:
    """Areal weather, memoised for 30 min so repeated requests (other seeds,
    scenarios, the odds endpoint) do not each wait on Open-Meteo."""
    import time
    key = (start, end, as_of, bool(allow_forecast), date.today().isoformat())
    now = time.monotonic()
    hit = _WX_CACHE.get(key)
    if hit and now - hit[0] < _WX_TTL_S:
        return hit[1]
    wx = meteo_mod.fetch_areal_weather(start, end, as_of, allow_forecast=allow_forecast)
    if wx.get("network") or not wx.get("fallback_reason"):
        _WX_CACHE[key] = (now, wx)
        while len(_WX_CACHE) > 32:
            _WX_CACHE.pop(next(iter(_WX_CACHE)))
    return wx


def prepare_state(as_of: date, allow_forecast: bool, gen: dict | None = None) -> dict:
    """Observed areal weather, features and the simulation start state."""
    gen = gen or load_model()["rain_generator"]
    start_req = as_of - timedelta(days=SPINUP_DAYS + P84_DAYS)
    end_req = as_of + timedelta(days=meteo_mod.FORECAST_MAX_LEAD_DAYS if allow_forecast else 0)
    wx = _weather_cached(_iso(start_req), _iso(end_req), _iso(as_of), allow_forecast)
    dates = wx["dates"]
    cut = dates.index(_iso(as_of))
    rain_o, tmax_o = wx["rain_mm"][:cut + 1], wx["tmax_c"][:cut + 1]
    obs = [i for i, r in enumerate(rain_o) if r is not None and tmax_o[i] is not None]
    filled = 0
    feats = None
    if obs:
        gm = gen["monthly"]
        rain, tmax = [], []
        for i in range(obs[0], obs[-1] + 1):
            if rain_o[i] is None or tmax_o[i] is None:   # interior gap: monthly mean
                m = _d(dates[i]).month - 1
                p = gm["p01"][m] / max(1e-6, 1 - gm["p11"][m] + gm["p01"][m])
                rain.append(p * (gen["wet_threshold_mm"] + gm["excess_mean_mm"][m]))
                tmax.append(0.5 * (gm["tmax_dry_c"][m] + gm["tmax_wet_c"][m]))
                filled += 1
            else:
                rain.append(float(rain_o[i]))
                tmax.append(float(tmax_o[i]))
        feats = observed_features(rain, tmax)
        start = _d(dates[obs[-1]])
        init = {"dc": feats["dc"][-1], "rain_hist": rain[-P84_DAYS:]}
        obs_until = dates[obs[-1]]
    else:
        start = as_of - timedelta(days=SPINUP_DAYS)
        init = {"dc": dc_mod.DC_START, "rain_hist": []}
        obs_until = None
    if (as_of - start).days > MAX_PRE_DAYS:
        start = as_of - timedelta(days=MAX_PRE_DAYS)
        init = {"dc": dc_mod.DC_START, "rain_hist": []}
        feats = None
    forecast = {dates[i]: (wx["rain_mm"][i], wx["tmax_c"][i])
                for i in range(cut + 1, len(dates))
                if wx["rain_mm"][i] is not None and wx["tmax_c"][i] is not None}
    return {"wx": wx, "feats": feats, "start": start, "init": init,
            "forecast": forecast, "obs_until": obs_until, "filled_days": filled,
            "pre_days": max(0, (as_of - start).days)}


def _ensemble_state(st: dict, gen: dict, oni_v: float, dmi_v: float, seed) -> tuple:
    """Median DC / P84 / onset state at as_of when the last days are unobserved."""
    import numpy as np
    rng = np.random.default_rng(None if seed is None else seed + 1)
    G = Generator(gen, oni_v, dmi_v)
    n = 400
    hist = list(st["init"]["rain_hist"])[-P84_DAYS:]
    if len(hist) < P84_DAYS:
        hist = [G.climatological_mean_rain(st["start"].month)] * (P84_DAYS - len(hist)) + hist
    buf = np.tile(np.asarray(hist, float)[:, None], (1, n))
    dc = np.full(n, float(st["init"]["dc"]))
    wet = buf[-1] >= G.thr
    for k in range(1, st["pre_days"] + 1):
        cur = st["start"] + timedelta(days=k)
        rain, tmax, wet = G.step(np, rng, cur.month, wet)
        buf = np.vstack([buf[1:], rain[None, :]])
        dc = dc_mod.dc_step_vec(np, dc, tmax, rain)
    a = buf[-10:].sum(0) >= WET10_MM
    b = buf[-20:-10].sum(0) >= WET10_MM
    cc = buf[-30:-20].sum(0) >= WET10_MM
    return (float(np.median(dc)), float(np.median(buf.mean(axis=0))),
            float(np.median(a)), float(np.median(a * (1.0 + b * (1.0 + cc)))))


def is_active(dc: float, n_wet: float, rule: dict) -> bool:
    """Season-in-progress test, fitted by calibrate.fit_active_rule."""
    return bool(dc >= rule["dc_min"] and n_wet < rule["n_wet_max"])


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def simulate_season_end(as_of: str, region: str = "kalimantan",
                        oni: float | None = None, n_sims: int = 5000,
                        seed: int | None = None, scenario: str = "baseline",
                        dmi: float | None = None, allow_forecast: bool | None = None,
                        _keep_end_days: bool = False) -> dict:
    """How many more days until the current Kalimantan fire season ends.

    ``allow_forecast`` defaults to True only when ``as_of`` is today or
    yesterday (UTC); for any earlier date the run is a leak-free hindcast.
    Deterministic for a given ``seed`` and weather input.
    """
    import numpy as np

    model = load_model()
    coeffs = model["coefficients"]
    gen = model["rain_generator"]
    scen_key = scenario if scenario in SCENARIOS else "baseline"
    scen = SCENARIOS[scen_key]
    n_sims = int(max(1, min(int(n_sims), 50000)))
    as_of_d = _d(as_of)
    if allow_forecast is None:
        allow_forecast = as_of_d >= date.today() - timedelta(days=1)
    idx = indices_known_on(as_of_d, oni, dmi)
    oni_v, dmi_v = idx["oni"], idx["dmi"]

    st = prepare_state(as_of_d, allow_forecast, gen)
    wx = st["wx"]
    end_day, p_end, p_still = run_hazard_simulation(
        st["start"], as_of_d, st["init"], coeffs, gen, oni_v, dmi_v,
        n_sims=n_sims, seed=seed, horizon=MAX_HORIZON_DAYS,
        forecast=st["forecast"], logit_offset=scen["logit_offset"],
        monsoon_shift_days=scen["monsoon_shift_days"],
        rain_multiplier=scen["rain_multiplier"], seasonal=model.get("seasonal"),
        frailty_sd=float(model.get("frailty", {}).get("sd", 0.0)))

    if st["feats"] is not None and st["pre_days"] == 0:
        f = st["feats"]
        dc_now, p84_now = f["dc"][-1], f["p84"][-1]
        wet10_now, nwet_now = f["wet10"][-1], f["n_wet"][-1]
        state_source = "observed"
    else:
        dc_now, p84_now, wet10_now, nwet_now = _ensemble_state(st, gen, oni_v, dmi_v, seed)
        state_source = (f"simulated: the {st['pre_days']} days before the forecast date "
                        "have no observed weather")
    active = is_active(dc_now, nwet_now, model["active_rule"])
    readiness = dc_mod.drought_readiness(dc_now)

    mean_is_trimmed = False
    if not active:
        p10 = p50 = p90 = 0
        mean_days = 0.0
    else:
        q10, q50, q90 = (float(x) for x in np.percentile(end_day, [10, 50, 90]))
        p10, p50, p90 = sorted((int(round(q10)), int(round(q50)), int(round(q90))))
        mean_days = float(np.mean(np.minimum(end_day, MAX_HORIZON_DAYS + 1)))
        mean_is_trimmed = not (p10 <= mean_days <= p90)
        if mean_is_trimmed:
            central = end_day[(end_day >= p10) & (end_day <= p90)]
            if central.size:
                mean_days = float(np.mean(central))

    def _date_at(nd: int) -> str:
        return _iso(as_of_d + timedelta(days=int(nd)))

    deficit_now, dcterm_now = feature_terms(dc_now, p84_now)
    clock_now = seasonal_clock(as_of_d - timedelta(days=int(scen["monsoon_shift_days"])),
                               model.get("seasonal"))
    c = coeffs
    terms = [
        ("dc", "Drought Code", dc_now, "DC units", -c["c4"] * dcterm_now,
         "Canadian Drought Code, the index Indonesia's met agency (BMKG) uses for "
         "fire danger. Above 350 ('very dry') peat is dry deep enough for "
         "smouldering to outlast showers; the model counts only the excess over 350."),
        ("rain_12wk", "12-week rainfall", p84_now, "mm/day", -c["c3"] * deficit_now,
         "Average daily rain over the last 84 days. Kalimantan fire and haze rise "
         "steeply below about 3.7 mm a day (Field et al. 2016); above that this "
         "term adds nothing."),
        ("rain_10d", "Rainy-season onset", nwet_now, "wet 10-day periods (0-3)",
         c["c1"] * wet10_now + c["c2"] * nwet_now,
         "How many consecutive 10-day periods have had at least 50 mm of rain. "
         "Three in a row is BMKG's definition of the start of the rainy season; "
         "even one wet 10-day period often pauses the fires."),
        ("monsoon", "Time of year", clock_now, "0-1", c.get("c7", 0.0) * clock_now,
         "Where the calendar sits relative to the usual arrival of the monsoon "
         "over the peat belt, fitted to past fire-season ends. It is what makes "
         "an end in late October far likelier than one in August even in "
         "identical weather."),
        ("oni", "ONI (El Nino)", oni_v, "degC", -c["c5"] * oni_v,
         "Latest published 3-month Oceanic Nino Index. El Nino (positive) dries "
         "Borneo and lengthens the season."),
        ("iod", "Indian Ocean Dipole", dmi_v, "degC", -c["c6"] * dmi_v,
         "Dipole Mode Index averaged over the last ~13 weeks. A positive IOD dries "
         "Indonesia independently of El Nino: 2019 burned under a neutral ENSO and "
         "an extreme positive IOD."),
    ]
    total = sum(abs(t[4]) for t in terms) or 1.0
    drivers = [{
        "id": did, "name": nm, "value": round(float(val), 3), "unit": unit,
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

    daily = [{"date": _date_at(k + 1), "p_end": round(float(p_end[k]), 6),
              "p_still_burning": round(float(p_still[k]), 6)}
             for k in range(min(MAX_HORIZON_DAYS, 180))]

    caveats = list(CAVEATS)
    if mean_is_trimmed:
        caveats.insert(0, "More than 10% of simulations were still burning at the "
                       f"{MAX_HORIZON_DAYS}-day horizon, so the reported mean is the "
                       "mean of the central 80% of trajectories; the true mean is longer.")
    if not allow_forecast:
        caveats.insert(0, "Hindcast: the model was given weather observed only up to "
                       f"{_iso(as_of_d)} and simulated everything after it. It did not "
                       "get the 14-day weather forecast the live site uses, because "
                       "past forecasts are not archived.")
    elif not wx["live"]:
        caveats.insert(0, "No live weather forecast was available for this run ("
                       + str(wx.get("fallback_reason") or "offline") + "), so every "
                       "day after the forecast date is simulated from climatology. "
                       "Treat the interval as wider than shown.")
    if st["pre_days"] > 0:
        caveats.insert(0, f"Observed weather ends {st['obs_until'] or 'outside the window'}; "
                       f"the {st['pre_days']} days up to the forecast date were simulated, "
                       "so the current drought state is itself uncertain.")
    if not active:
        caveats.insert(0, "No fire season is detected as active at this date (Drought "
                       f"Code {dc_now:.0f}, {int(nwet_now)} consecutive wet 10-day "
                       "periods), so the countdown reads zero. This is a statement "
                       "about drought and rain, not a satellite count of zero fires.")

    skill = model.get("skill", {})
    out = {
        "as_of": _iso(as_of_d),
        "region": region or "kalimantan",
        "oni": round(float(oni_v), 3),
        "dmi": round(float(dmi_v), 3),
        "indices": {"oni": round(float(oni_v), 3), "oni_season": idx["oni_season"],
                    "dmi_3mo": round(float(dmi_v), 3), "dmi_through_week": idx["dmi_through"]},
        "active": active,
        "days_remaining": {"p10": p10, "p50": p50, "p90": p90, "mean": round(mean_days, 1)},
        "end_date": {"p10": _date_at(p10), "p50": _date_at(p50), "p90": _date_at(p90)},
        "daily_end_probability": daily,
        "drivers": drivers,
        "drought_readiness": round(readiness, 3),
        "drought_code": round(float(dc_now), 1),
        "confidence": confidence,
        "live_weather": bool(wx["live"]),
        "scenario": scen_key,
        "model_version": 2,
        "season_definition": model.get("season_definition"),
        "model_error_days": skill.get("rmse_days_forecast"),
        "state_source": state_source,
        # The last day of observed (not simulated) weather behind this run, and
        # why live weather was not used -- explicit, so clients never have to
        # parse state_source prose.
        "weather_through": ((as_of_d - timedelta(days=st["pre_days"])).isoformat()
                            if st["feats"] is not None else None),
        "fallback_reason": wx.get("fallback_reason"),
        "monsoon_onset_climatology": _onset_climatology(model, as_of_d),
        "caveats": caveats,
    }
    if _keep_end_days:
        out["_end_days"] = end_day
    return out


def _onset_climatology(model: dict, as_of_d: date) -> dict | None:
    """BMKG onset quartiles (ERA5 1981-2025) as dates in the forecast's year (S5)."""
    mo = model.get("monsoon_onset") or {}
    q = mo.get("quartiles_doy_all")
    if not q:
        return None
    y = as_of_d.year

    def _dt(doy):
        return _iso(date(y, 1, 1) + timedelta(days=int(doy) - 1))
    by = {ph: ([_dt(v) for v in qq] if qq else None)
          for ph, qq in (mo.get("quartiles_doy_by_enso_phase") or {}).items()}
    return {"p25": _dt(q[0]), "p50": _dt(q[1]), "p75": _dt(q[2]),
            "by_enso_phase": by, "definition": mo.get("definition")}


CAVEATS = [
    "'The season has ended' is the site's own definition, made from satellite "
    "fire counts: the first day after the seasonal peak from which the 7-day "
    "count of NASA MODIS active-fire detections over the Central/South "
    "Kalimantan peat belt stays below 10% of that season's maximum for 14 days. "
    "No published definition exists. It is not the day the air is clean: smoke "
    "and smell linger, and smouldering peat can flare again after a dry spell.",
    "The model is fitted to the fire-count end dates of the 2001-2025 seasons. "
    "Its honest error is the forecast-mode figure -- what it would have said on "
    "the day each season began, without knowing the weather to come -- not the "
    "smaller in-sample error.",
    "Satellites miss fires under cloud and thick smoke, and MODIS fire counts "
    "have drifted since about 2020 as both satellites' orbits decayed. A season "
    "can look over in the fire counts a few days before it is, especially when "
    "the rains arrive with heavy cloud.",
    "There are no ignitions in the model. Almost every Kalimantan fire is lit by "
    "someone, and the end of a season is partly a decision to stop lighting "
    "them. The enforcement scenarios are assumptions, not measurements.",
    "Weather beyond about two weeks is not forecastable. After the 14-day "
    "forecast each simulated season draws its own rain from a generator fitted "
    "to ERA5 and nudged by the latest El Nino (ONI) and Indian Ocean Dipole (DMI) "
    "values, which are held fixed at today's. Both indices can move a lot within "
    "a season; persisting them, the DMI especially, is a weak assumption.",
    "The peat water table, which decides whether peat burns, is not measured "
    "regionally. The Drought Code is a proxy for it, and canal density and "
    "drainage -- why two landscapes in identical weather burn differently -- are "
    "not inputs.",
    "One season with one end date for the whole peat belt is a simplification: "
    "West, Central and South Kalimantan can end weeks apart.",
]


def __getattr__(name):
    """v1 compatibility: ``HAZARD_COEFFS`` / ``HAZARD_FIT`` (imported by
    ``app.model.__init__``) are read lazily from ``hazard_fit.json``."""
    if name == "HAZARD_COEFFS":
        return dict(load_model().get("coefficients", {}))
    if name == "HAZARD_FIT":
        m = load_model()
        sk = m.get("skill", {})
        return {"fitted": "coefficients" in m, "fitted_at": m.get("fitted_at"),
                "n_seasons": sk.get("n_seasons"), "rmse_days": sk.get("rmse_days"),
                "rmse_days_loo": sk.get("rmse_days_loo"),
                "rmse_days_forecast": sk.get("rmse_days_forecast"),
                "rmse_days_anchor_seasons": sk.get("anchor_rmse_days"),
                "rmse_days_naive_mean": sk.get("naive_rmse_days_mean_predictor"),
                "weather_source": m.get("weather_source"),
                "target_is_rainfall_derived": m.get("target_is_rainfall_derived"),
                "detail": "app/model/hazard_fit.json"}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
