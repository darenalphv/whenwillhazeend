"""Weather input layer: Open-Meteo client, climatological fallback, monsoon phase.

This module is the only place the model touches the network, and it is written
so that the network is strictly optional.  `fetch_weather()` never raises: on
timeout, HTTP error, malformed payload, a missing `httpx`, or no network at
all it synthesises a physically plausible series from the bundled monthly
normals in `app.data.loader.load_climatology()` and reports
``{"live": false, "fallback_reason": "..."}``.

Equations / method
------------------
**Stochastic daily rainfall** (used beyond the 16-day forecast horizon and in
the offline fallback).  Daily rain is generated with the classic two-state
first-order Markov chain plus gamma intensity of Richardson (1981) and Wilks
(1999):

    P(wet_t | wet_{t-1}) = p_m + rho*(1 - p_m)
    P(wet_t | dry_{t-1}) = p_m * (1 - rho)
    R_t | wet ~ Gamma(k = 0.8, theta = mu_m / 0.8)

where ``p_m`` is the wet-day frequency of calendar month *m*, recovered from
the monthly rainfall normal ``R_m`` and a mean wet-day intensity
``mu = 13 mm/day`` typical of equatorial convective rainfall
(``p_m = R_m / (D_m * mu)``), and ``rho = 0.35`` is the lag-1 wet/dry
persistence.  The gamma shape of 0.8 reproduces the heavy right tail of
tropical convective daily totals.

**Two generators live in the code.** `RainSampler` below is the v1 single-point
generator, now used only for the offline fallback of ``fetch_weather`` (the
``/api/meteo`` endpoint). Its constants MEAN_WET_DAY_MM, WET_PERSISTENCE and
ENSO_RAIN_K have no traceable source (MODEL_SPEC Appendix A). The season model
uses `app.model.burn.Generator`, fitted to ERA5 areal-mean rainfall with
k_enso / k_iod estimated by OLS (``hazard_fit.json -> rain_generator``), and
its weather comes from `fetch_areal_weather` (peat-belt areal mean).

**ENSO conditioning (v1 fallback only).**  Borneo dry-season rainfall responds
near-exponentially to ONI, and the response is concentrated in JJASON:

    R_m(oni) = R_m * exp(-k_enso * w_m * oni),  k_enso = 0.35, w_m = 1.0 in
    Jun-Nov, 0.4 in Dec-May

i.e. a +2 sigma El Nino removes roughly half of dry-season rainfall.  This is
the Field et al. (2016, PNAS) nonlinear drought-fire relationship expressed on
the rainfall side; the 4 mm/day seasonal-mean threshold below which severe fire
becomes likely falls out of it.

**Monsoon progress.**  The Asian-Australian monsoon over Borneo is represented
by a difference of two logistics on a "monsoon year" that begins at the
climatological dry minimum (15 August, DOY 227):

    x        = (DOY - 227) mod 365
    progress = sigma((x - x_onset)/w_on) * (1 - sigma((x - x_retreat)/w_off))

with ``w_on = 18 d``, ``w_off = 28 d``.  Onset is latitude dependent: the wet
onset reaches northern Borneo (~+2 N) around 15 October and southern
Kalimantan (~-3.5 S) around 1 November, and retreat is around 20 April.  A
value near 0 means "deep dry season, nothing is going to put a peat fire out";
a value near 1 means "full monsoon, the episode is over".

Sources
-------
* Open-Meteo Forecast & Historical Weather APIs (https://open-meteo.com/),
  ERA5 / ECMWF IFS backed, no API key, CC-BY 4.0.
* Richardson, C.W. (1981). Stochastic simulation of daily precipitation,
  temperature and solar radiation. *Water Resources Research* 17(1), 182-190.
* Wilks, D.S. (1999). Interannual variability and extreme-value characteristics
  of several stochastic daily precipitation models. *Agric. For. Meteorol.* 93.
* Field, R.D. et al. (2016). *PNAS* 113(33), 9204-9209 - nonlinear sensitivity
  of Indonesian fire to El Nino drought; the ~4 mm/day seasonal rainfall
  threshold for severe fire.
* Aldrian, E. & Susanto, R.D. (2003). Identification of three dominant rainfall
  regions within Indonesia. *Int. J. Climatology* 23, 1435-1452 - the monsoonal
  (region A) annual cycle used for the onset/retreat dates.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

__all__ = [
    "fetch_weather", "monsoon_progress", "synthesize_weather", "oni_value",
    "enso_phase_of",
    "climatology_for", "annual_rain_mm_for", "RainSampler",
    "REQUEST_TIMEOUT_S", "FORECAST_MAX_LEAD_DAYS", "CAVEATS",
]

REQUEST_TIMEOUT_S = 6.0
#: Open-Meteo forecast API serves today+15 days and up to 92 days of recent past.
FORECAST_MAX_LEAD_DAYS = 14
FORECAST_MAX_PAST_DAYS = 80
ARCHIVE_LAG_DAYS = 6
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_DAILY_VARS = (
    "temperature_2m_max,temperature_2m_min,precipitation_sum,"
    "relative_humidity_2m_mean,wind_speed_10m_max,wind_direction_10m_dominant"
)

# --- stochastic rainfall parameters (model parameters, not measurements) ----
MEAN_WET_DAY_MM = 13.0     # mean intensity of a wet day, equatorial Borneo
WET_PERSISTENCE = 0.35     # lag-1 wet/dry Markov persistence
GAMMA_SHAPE = 0.8          # heavy-tailed convective daily totals
ENSO_RAIN_K = 0.35         # d ln(rain) / d ONI in the dry season

# --- monsoon phase parameters ----------------------------------------------
DRY_MINIMUM_DOY = 227      # 15 August
ONSET_DOY_SOUTH = 305      # ~1 November at 3.5 S
ONSET_DOY_NORTH = 288      # ~15 October at 2.0 N
RETREAT_DOY = 110          # ~20 April
ONSET_WIDTH_D = 18.0
RETREAT_WIDTH_D = 28.0

#: Last-resort monthly normals for Central Kalimantan (Palangka Raya), used
#: only if `app.data.loader.load_climatology()` is unavailable or unparseable.
#: These are order-of-magnitude values for a station in the Aldrian & Susanto
#: monsoonal region; the bundled data file is authoritative when present.
_EMERGENCY_CLIMATOLOGY = {
    "name": "Kalimantan Tengah (emergency default)",
    "lat": -2.21, "lon": 113.92,
    "rain_mm": [320, 290, 330, 300, 230, 160, 140, 150, 180, 270, 330, 330],
    "tmax_c": [31.4, 31.6, 32.0, 32.4, 32.6, 32.4, 32.2, 32.6, 32.8, 32.6, 32.0, 31.6],
    "tmin_c": [23.0, 23.0, 23.2, 23.4, 23.4, 23.0, 22.6, 22.6, 22.8, 23.2, 23.4, 23.2],
    "estimated": True,
}


# ---------------------------------------------------------------------------
# date helpers
# ---------------------------------------------------------------------------

def _d(s: str | date) -> date:
    if isinstance(s, date):
        return s
    return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))


def _iso(d: date) -> str:
    return d.isoformat()


def _daterange(start: date, end: date) -> list[date]:
    n = (end - start).days
    return [start + timedelta(days=i) for i in range(max(0, n + 1))]


def _doy(d: date) -> int:
    return d.timetuple().tm_yday


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


# ---------------------------------------------------------------------------
# climatology adapter -- tolerant of the exact shape the DATA layer ships
# ---------------------------------------------------------------------------

def _looks_like_monthly(v) -> bool:
    return (isinstance(v, (list, tuple)) and len(v) == 12
            and all(isinstance(x, (int, float)) for x in v))


def _harvest(node, name_hint: str, out: list[dict]) -> None:
    """Recursively pull {name, lat, lon, rain_mm[12], tmax_c[12]} records."""
    if isinstance(node, dict):
        rain = tmax = tmin = None
        for k, v in node.items():
            lk = k.lower()
            if _looks_like_monthly(v):
                if "rain" in lk or "precip" in lk or "pr_" in lk:
                    rain = [float(x) for x in v]
                elif "tmax" in lk or ("max" in lk and "t" in lk):
                    tmax = [float(x) for x in v]
                elif "tmin" in lk or ("min" in lk and "t" in lk):
                    tmin = [float(x) for x in v]
                elif "temp" in lk and tmax is None:
                    tmax = [float(x) for x in v]
        if rain is not None:
            nm = node.get("name") or node.get("province") or node.get("id") or name_hint
            lat = node.get("lat", node.get("latitude", node.get("capital_lat")))
            lon = node.get("lon", node.get("longitude", node.get("capital_lon")))
            if tmax is None:
                tmax = list(_EMERGENCY_CLIMATOLOGY["tmax_c"])
            if tmin is None:
                tmin = [t - 9.0 for t in tmax]
            out.append({
                "name": str(nm), "lat": _f(lat), "lon": _f(lon),
                "rain_mm": rain, "tmax_c": tmax, "tmin_c": tmin,
            })
            return
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                _harvest(v, str(k), out)
    elif isinstance(node, list):
        for v in node:
            _harvest(v, name_hint, out)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


_CLIM_CACHE: list[dict] | None = None


def _all_climatologies() -> list[dict]:
    global _CLIM_CACHE
    if _CLIM_CACHE is not None:
        return _CLIM_CACHE
    recs: list[dict] = []
    try:
        from app.data.loader import load_climatology
        _harvest(load_climatology(), "climatology", recs)
    except Exception:
        recs = []
    # fill missing coordinates from the region file if we can
    if recs and any(r["lat"] is None for r in recs):
        try:
            from app.data.loader import load_regions
            coords = {}
            for feat in (load_regions() or {}).get("features", []):
                p = feat.get("properties", {})
                nm = p.get("name") or p.get("name_en")
                if nm and p.get("capital_lat") is not None:
                    coords[str(nm).lower()] = (_f(p["capital_lat"]), _f(p.get("capital_lon")))
            for r in recs:
                if r["lat"] is None:
                    hit = coords.get(r["name"].lower())
                    if hit:
                        r["lat"], r["lon"] = hit
        except Exception:
            pass
    if not recs:
        recs = [dict(_EMERGENCY_CLIMATOLOGY)]
    for r in recs:
        if r["lat"] is None:
            r["lat"], r["lon"] = _EMERGENCY_CLIMATOLOGY["lat"], _EMERGENCY_CLIMATOLOGY["lon"]
    _CLIM_CACHE = recs
    return recs


def climatology_for(lat: float, lon: float) -> dict:
    """Nearest bundled monthly-normal record to (lat, lon)."""
    recs = _all_climatologies()
    best, bd = recs[0], float("inf")
    for r in recs:
        dd = (r["lat"] - lat) ** 2 + ((r["lon"] - lon) * math.cos(math.radians(lat))) ** 2
        if dd < bd:
            best, bd = r, dd
    return best


def annual_rain_mm_for(lat: float, lon: float) -> float:
    """Mean annual precipitation at a point -- the KBDI `R` parameter."""
    return float(sum(climatology_for(lat, lon)["rain_mm"]))


# ---------------------------------------------------------------------------
# monsoon phase
# ---------------------------------------------------------------------------

def monsoon_progress(date_: str, lat: float, lon: float) -> float:
    """0..1 -- how far the wet-monsoon onset has advanced at this place/date.

    Difference of two logistics on the monsoon year beginning 15 August.
    0 = deep dry season; 1 = full monsoon.  See module docstring.
    """
    d = _d(date_)
    x = (_doy(d) - DRY_MINIMUM_DOY) % 365
    # latitude interpolation of onset: earlier in the north
    f = (lat - (-3.5)) / (2.0 - (-3.5))
    f = min(max(f, -0.6), 1.6)
    onset_doy = ONSET_DOY_SOUTH + f * (ONSET_DOY_NORTH - ONSET_DOY_SOUTH)
    x_onset = (onset_doy - DRY_MINIMUM_DOY) % 365
    x_retreat = (RETREAT_DOY - DRY_MINIMUM_DOY) % 365
    rise = _sigmoid((x - x_onset) / ONSET_WIDTH_D)
    fall = 1.0 - _sigmoid((x - x_retreat) / RETREAT_WIDTH_D)
    return min(max(rise * fall, 0.0), 1.0)


def monsoon_progress_shifted(date_: str, lat: float, lon: float,
                             shift_days: float = 0.0) -> float:
    """`monsoon_progress` with the onset moved `shift_days` later (+) / earlier (-)."""
    d = _d(date_) - timedelta(days=int(round(shift_days)))
    return monsoon_progress(_iso(d), lat, lon)


# ---------------------------------------------------------------------------
# stochastic weather
# ---------------------------------------------------------------------------

def _enso_month_weight(month: int) -> float:
    """ENSO's grip on Borneo rainfall is a dry-season phenomenon."""
    return 1.0 if 6 <= month <= 11 else 0.4


class RainSampler:
    """Markov-gamma daily rainfall / temperature sampler for one location.

    Shared by the offline meteo fallback, the season Monte-Carlo beyond the
    forecast horizon, and the calibration harness, so that all three see the
    same rainfall climate.
    """

    def __init__(self, lat: float, lon: float, oni: float = 0.0, rng=None,
                 rain_multiplier: float = 1.0):
        import numpy as np
        self.clim = climatology_for(lat, lon)
        self.lat, self.lon = float(lat), float(lon)
        self.oni = float(oni)
        self.rain_multiplier = float(rain_multiplier)
        self.rng = rng if rng is not None else np.random.default_rng()
        self._wet = False
        self._np = np

    def month_rain_mm(self, month: int) -> float:
        base = self.clim["rain_mm"][month - 1]
        w = _enso_month_weight(month)
        return float(base * math.exp(-ENSO_RAIN_K * w * self.oni) * self.rain_multiplier)

    def p_wet(self, month: int) -> float:
        dim = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        p = self.month_rain_mm(month) / (dim * MEAN_WET_DAY_MM)
        return min(max(p, 0.02), 0.92)

    def step(self, d: date) -> tuple[float, float, float]:
        """Return (rain_mm, tmax_c, tmin_c) for one day, advancing the chain."""
        m = d.month
        p = self.p_wet(m)
        p_trans = p + WET_PERSISTENCE * (1.0 - p) if self._wet else p * (1.0 - WET_PERSISTENCE)
        wet = bool(self.rng.random() < min(max(p_trans, 0.0), 1.0))
        self._wet = wet
        if wet:
            mu = self.month_rain_mm(m) / max(1e-6, p * 30.4)
            rain = float(self.rng.gamma(GAMMA_SHAPE, mu / GAMMA_SHAPE))
        else:
            rain = 0.0
        tmax = self.clim["tmax_c"][m - 1] + (0.0 if wet else 1.4) - (1.2 if wet else 0.0) \
            + 0.35 * self.oni + float(self.rng.normal(0.0, 0.9))
        tmin = self.clim["tmin_c"][m - 1] + (0.6 if wet else -0.4) + float(self.rng.normal(0.0, 0.7))
        return rain, tmax, tmin


def synthesize_weather(lat: float, lon: float, start: str, end: str,
                       oni: float = 0.0, seed: int | None = 20260918) -> dict:
    """Climatology-driven pseudo-observations for [start, end].  Never fails."""
    import numpy as np
    rng = np.random.default_rng(seed)
    sampler = RainSampler(lat, lon, oni=oni, rng=rng)
    days = _daterange(_d(start), _d(end))
    dates, tmax, tmin, rain, rh, ws, wd = [], [], [], [], [], [], []
    for d in days:
        r, tx, tn = sampler.step(d)
        dates.append(_iso(d))
        rain.append(round(r, 2))
        tmax.append(round(tx, 2))
        tmin.append(round(tn, 2))
        rh.append(round(_rh_from_t(tn, tx), 1))
        # Dry-season low-level flow over Kalimantan is the SE monsoon
        # (from ~135 deg); in the wet season it swings to the NW (~315 deg).
        mp = monsoon_progress(_iso(d), lat, lon)
        wd.append(round((135.0 + 180.0 * mp) % 360.0, 1))
        ws.append(round(float(abs(rng.normal(3.2, 1.0))) + 0.4, 2))
    return {
        "live": False, "lat": float(lat), "lon": float(lon), "dates": dates,
        "tmax_c": tmax, "tmin_c": tmin, "rain_mm": rain, "rh_pct": rh,
        "wind_speed_ms": ws, "wind_dir_deg": wd,
        "source": "climatology:" + sampler.clim["name"],
        "fallback_reason": "synthesised from bundled monthly normals",
    }


def _es_hpa(t_c: float) -> float:
    """Saturation vapour pressure, Tetens (1930)."""
    return 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))


def _rh_from_t(tmin_c: float, tmax_c: float) -> float:
    """Daily-mean RH when only tmin/tmax are known.

    Uses the standard assumption that the dewpoint equals the daily minimum
    temperature (valid over wet tropical land), evaluated at the daily mean
    temperature (Allen et al. 1998, FAO-56).
    """
    tmean = 0.5 * (tmin_c + tmax_c)
    return min(100.0, max(5.0, 100.0 * _es_hpa(tmin_c) / max(1e-6, _es_hpa(tmean))))


# ---------------------------------------------------------------------------
# Open-Meteo client
# ---------------------------------------------------------------------------

def _get_json(url: str, params: dict, timeout: float | None = None) -> dict:
    import httpx  # imported lazily so the module imports without httpx present
    # Accept-Encoding: identity -- inside a Cloudflare Python Worker the fetch
    # bridge hands httpx a still-compressed body for the default encodings and
    # r.json() raises (research/PLATFORM.md). Harmless under CPython.
    with httpx.Client(timeout=timeout or REQUEST_TIMEOUT_S,
                      headers={"Accept-Encoding": "identity"}) as client:
        r = client.get(url, params=params)
        r.raise_for_status()
        return r.json()


def _pull(url: str, lat: float, lon: float, s: date, e: date,
          timeout: float | None = None) -> dict[str, dict]:
    payload = _get_json(url, {
        "latitude": lat, "longitude": lon,
        "start_date": _iso(s), "end_date": _iso(e),
        "daily": _DAILY_VARS, "timezone": "UTC",
    }, timeout=timeout)
    daily = payload.get("daily") or {}
    times = daily.get("time") or []
    if not times:
        raise ValueError("open-meteo returned no daily.time array")
    out: dict[str, dict] = {}
    for i, t in enumerate(times):
        def g(key):
            arr = daily.get(key)
            if not arr or i >= len(arr):
                return None
            return arr[i]
        tx, tn = g("temperature_2m_max"), g("temperature_2m_min")
        if tx is None or tn is None:
            continue
        rh = g("relative_humidity_2m_mean")
        out[str(t)[:10]] = {
            "tmax_c": float(tx), "tmin_c": float(tn),
            "rain_mm": float(g("precipitation_sum") or 0.0),
            "rh_pct": float(rh) if rh is not None else _rh_from_t(float(tn), float(tx)),
            "wind_speed_ms": float(g("wind_speed_10m_max") or 3.0) / 3.6,
            "wind_dir_deg": float(g("wind_direction_10m_dominant") or 135.0),
        }
    return out


#: Reference points for the season model's areal-mean weather. All lie inside
#: the Field et al. (2016) south-central Kalimantan peat-belt box (4S-0,
#: 110-117E) in which the fire-observed season labels are counted. Chosen [J]
#: to spread across the main Central/South Kalimantan peat areas; one
#: multi-location Open-Meteo request serves all five.
PEAT_BELT_POINTS = (
    ("Palangka Raya", -2.21, 113.92),
    ("Kapuas (ex-Mega Rice Project)", -2.60, 114.50),
    ("Sampit", -2.53, 112.95),
    ("Pangkalan Bun", -2.68, 111.62),
    ("Banjarmasin", -3.32, 114.59),
)


def pull_multi(url: str, points, s: date, e: date,
               timeout: float | None = None) -> list[dict[str, dict]]:
    """One Open-Meteo request for several points; one {date: row} dict per point."""
    payload = _get_json(url, {
        "latitude": ",".join(f"{p[1]:.3f}" for p in points),
        "longitude": ",".join(f"{p[2]:.3f}" for p in points),
        "start_date": _iso(s), "end_date": _iso(e),
        "daily": "temperature_2m_max,precipitation_sum", "timezone": "UTC",
    }, timeout=timeout)
    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(points):
        raise ValueError("open-meteo returned a different number of locations")
    out = []
    for it in items:
        daily = (it or {}).get("daily") or {}
        times = daily.get("time") or []
        tx = daily.get("temperature_2m_max") or []
        pr = daily.get("precipitation_sum") or []
        rows = {}
        for i, t in enumerate(times):
            if i < len(tx) and tx[i] is not None and i < len(pr) and pr[i] is not None:
                rows[str(t)[:10]] = {"tmax_c": float(tx[i]), "rain_mm": float(pr[i])}
        out.append(rows)
    return out


def _areal(rows_per_point) -> dict[str, dict]:
    if not rows_per_point:
        return {}
    common = set.intersection(*(set(r) for r in rows_per_point))
    n = len(rows_per_point)
    return {k: {"tmax_c": sum(r[k]["tmax_c"] for r in rows_per_point) / n,
                "rain_mm": sum(r[k]["rain_mm"] for r in rows_per_point) / n}
            for k in common}


def fetch_areal_weather(start: str, end: str, as_of: str,
                        allow_forecast: bool = True) -> dict:
    """Peat-belt areal-mean daily rain/tmax for [start, end].  Never raises.

    * Days up to ``as_of`` are observed: the bundled ERA5 record
      (`calibration_weather.json`), then the Open-Meteo archive, then the
      forecast API's recent past (analysis) for the last few days.
    * Days after ``as_of`` come from the Open-Meteo forecast **only** when
      ``allow_forecast`` is true (the live model, as_of = today). A hindcast
      sets it false, so nothing observed after ``as_of`` can leak in.
    * Anything not obtainable is ``None``; the season model simulates those
      days from its stochastic generator rather than inventing values here.
    """
    pts = PEAT_BELT_POINTS
    s, e, cut = _d(start), _d(end), _d(as_of)
    today = date.today()
    obs_end = min(e, cut, today)
    got: dict[str, dict] = {}
    src: dict[str, str] = {}
    reasons: list[str] = []
    net_live = False

    try:
        from app.data.loader_season import calibration_weather_index
        cw, idx = calibration_weather_index()
        for d in _daterange(s, obs_end):
            i = idx.get(_iso(d))
            if i is not None:
                got[_iso(d)] = {"tmax_c": cw["tmax_c"][i], "rain_mm": cw["rain_mm"][i]}
                src[_iso(d)] = "bundled ERA5"
    except Exception:
        pass

    missing = [d for d in _daterange(s, obs_end) if _iso(d) not in got]
    archive_end = min(obs_end, today - timedelta(days=ARCHIVE_LAG_DAYS))
    if missing and missing[0] <= archive_end:
        try:
            for k, v in _areal(pull_multi(ARCHIVE_URL, pts, missing[0], archive_end)).items():
                if k not in got:
                    got[k], src[k] = v, "ERA5 archive"
            net_live = True
        except Exception as exc:
            reasons.append(f"archive API unavailable ({type(exc).__name__})")

    fc_lo = max(s, today - timedelta(days=FORECAST_MAX_PAST_DAYS))
    fc_hi = min(e, today + timedelta(days=FORECAST_MAX_LEAD_DAYS))
    if not allow_forecast:
        fc_hi = min(fc_hi, obs_end)
    want = [d for d in _daterange(fc_lo, fc_hi) if _iso(d) not in got]
    if want:
        try:
            rows = _areal(pull_multi(FORECAST_URL, pts, want[0], want[-1]))
            for k, v in rows.items():
                if k not in got and _d(k) >= want[0]:
                    got[k] = v
                    src[k] = "forecast" if _d(k) > cut else "forecast-API analysis"
            net_live = True
        except Exception as exc:
            reasons.append(f"forecast API unavailable ({type(exc).__name__})")

    dates = [_iso(d) for d in _daterange(s, e)]
    obs_missing = sum(1 for k in dates if _d(k) <= obs_end and k not in got)
    n_fc = sum(1 for k in dates if src.get(k) == "forecast")
    return {
        "dates": dates,
        "rain_mm": [got[k]["rain_mm"] if k in got else None for k in dates],
        "tmax_c": [got[k]["tmax_c"] if k in got else None for k in dates],
        "live": bool(net_live and n_fc > 0),
        "network": bool(net_live),
        "n_observed_missing": obs_missing,
        "n_forecast_days": n_fc,
        "points": [{"name": n, "lat": la, "lon": lo} for n, la, lo in pts],
        "source": f"Open-Meteo (ERA5 archive + ECMWF IFS forecast), mean of {len(pts)} "
                  "peat-belt points",
        "fallback_reason": "; ".join(reasons) if reasons else None,
    }


def fetch_weather(lat: float, lon: float, start: str, end: str) -> dict:
    """Daily weather for [start, end] at (lat, lon).  Never raises.

    Open-Meteo Archive (ERA5) for dates older than 6 days, Open-Meteo Forecast
    (which also carries up to 92 days of recent past) for recent and future
    dates out to +16 days.  Anything neither API can cover -- and everything,
    if the network is down -- is filled from the bundled monthly normals with
    ``"live": false`` and a human-readable ``fallback_reason``.
    """
    try:
        s, e = _d(start), _d(end)
    except Exception:
        return {**synthesize_weather(lat, lon, "2026-01-01", "2026-01-02"),
                "fallback_reason": "unparseable start/end date"}
    if e < s:
        s, e = e, s
    want = _daterange(s, e)
    if len(want) > 4000:
        want = want[:4000]
        e = want[-1]

    today = date.today()
    got: dict[str, dict] = {}
    reasons: list[str] = []
    any_live = False

    archive_end = min(e, today - timedelta(days=ARCHIVE_LAG_DAYS))
    if s <= archive_end:
        try:
            got.update(_pull(ARCHIVE_URL, lat, lon, s, archive_end))
            any_live = True
        except Exception as exc:
            reasons.append(f"archive API unavailable ({type(exc).__name__})")

    fc_start = max(s, today - timedelta(days=FORECAST_MAX_PAST_DAYS))
    fc_end = min(e, today + timedelta(days=FORECAST_MAX_LEAD_DAYS))
    if fc_start <= fc_end:
        try:
            got.update(_pull(FORECAST_URL, lat, lon, fc_start, fc_end))
            any_live = True
        except Exception as exc:
            reasons.append(f"forecast API unavailable ({type(exc).__name__})")

    missing = [d for d in want if _iso(d) not in got]
    if missing:
        try:
            oni = _latest_oni()
        except Exception:
            oni = 0.0
        synth = synthesize_weather(lat, lon, _iso(want[0]), _iso(want[-1]), oni=oni,
                                   seed=abs(hash((round(lat, 2), round(lon, 2),
                                                  _iso(want[0])))) % (2**31))
        idx = {d: i for i, d in enumerate(synth["dates"])}
        for d in missing:
            k = _iso(d)
            i = idx.get(k)
            if i is None:
                continue
            got[k] = {
                "tmax_c": synth["tmax_c"][i], "tmin_c": synth["tmin_c"][i],
                "rain_mm": synth["rain_mm"][i], "rh_pct": synth["rh_pct"][i],
                "wind_speed_ms": synth["wind_speed_ms"][i],
                "wind_dir_deg": synth["wind_dir_deg"][i],
            }
        if any_live:
            reasons.append(
                f"{len(missing)} of {len(want)} days beyond the +{FORECAST_MAX_LEAD_DAYS} d forecast / "
                "pre-archive window filled from monthly normals")
        else:
            reasons.append("all days filled from bundled monthly normals")

    dates = [_iso(d) for d in want]
    rows = [got[k] for k in dates]
    live = bool(any_live and not missing)
    return {
        "live": live,
        "lat": float(lat), "lon": float(lon), "dates": dates,
        "tmax_c": [r["tmax_c"] for r in rows],
        "tmin_c": [r["tmin_c"] for r in rows],
        "rain_mm": [r["rain_mm"] for r in rows],
        "rh_pct": [r["rh_pct"] for r in rows],
        "wind_speed_ms": [r["wind_speed_ms"] for r in rows],
        "wind_dir_deg": [r["wind_dir_deg"] for r in rows],
        "source": "open-meteo" if any_live else "climatology",
        "fallback_reason": "; ".join(reasons) if reasons else None,
    }


#: Keys the ENSO record may carry, in order of preference. The fire season runs
#: Aug-Nov, so an Aug-Nov ONI peak is the right summary statistic for it; a bare
#: "oni" or a DJF peak is accepted as a fallback.
_ONI_KEYS = ("oni", "oni_peak_aug_nov", "oni_peak", "oni_annual_mean_aug_nov",
             "oni_son", "oni_aso", "oni_jas", "oni_ond", "oni_mean", "value",
             # last resort: the most recent published season for a year whose
             # Aug-Nov ONI is not out yet (the current season)
             "latest_available_oni")


def oni_value(row: dict) -> float | None:
    """Pull an ONI value out of an ENSO or season record, whatever it calls it."""
    if not isinstance(row, dict):
        return None
    for k in _ONI_KEYS:
        v = row.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def enso_phase_of(row: dict) -> str:
    """la_nina / neutral / el_nino, preferring an explicit phase field."""
    ph = str(row.get("phase") or row.get("enso_phase") or "").strip().lower()
    if ph in ("la_nina", "neutral", "el_nino"):
        return ph
    oni = oni_value(row)
    if oni is None:
        return "neutral"
    return "el_nino" if oni >= 0.5 else ("la_nina" if oni <= -0.5 else "neutral")


def _latest_oni() -> float:
    from app.data.loader import load_enso
    rows = [r for r in (load_enso() or []) if oni_value(r) is not None]
    if not rows:
        return 0.0
    rows.sort(key=lambda r: r.get("year", 0))
    return float(oni_value(rows[-1]))


CAVEATS = [
    "Weather beyond about 10 days is not forecastable. Everything past the "
    "16-day Open-Meteo horizon is a draw from the monthly normals conditioned "
    "on ENSO, not a forecast: it carries the right average and the right "
    "spread, but it cannot tell you that the rains will break on a particular "
    "week.",
    "The season model represents the monsoon twice: as BMKG's observed onset "
    "test (three wet 10-day periods) and as a smooth climatological clock whose "
    "timing is fitted to past fire-season ends. Real onset is abrupt, patchy and "
    "varies by weeks between years; the Madden-Julian Oscillation can advance or "
    "delay the effective break by a fortnight and is not represented.",
    "Five points in the Central/South Kalimantan peat belt stand in for the "
    "whole fire region. Rainfall on the day the fires die can differ by an order "
    "of magnitude between neighbouring districts, so an areal mean blurs a real "
    "spatial story.",
    "Open-Meteo's tropical precipitation comes from ERA5 and the ECMWF IFS. "
    "Reanalysis rainfall over Borneo has known wet biases over land and is "
    "poorly constrained by gauges; a wet bias in the input makes this model "
    "call the end of the season too early.",
    "The Indian Ocean Dipole (1997 and 2019 were both strong positive-IOD years) "
    "enters the season model through its rainfall generator, fitted on ERA5: "
    "each +1 C of DMI cuts Jun-Nov rainfall by about 19% (k_iod ~0.21 +/- 0.09). "
    "Fitted on 22 seasons, it did not measurably improve the model's end-date "
    "error over ENSO alone.",
    "Smoke feedback is ignored. Dense haze suppresses convection and rainfall, "
    "so a severe fire season partly sustains the dry conditions that keep it "
    "going. This model treats weather as exogenous.",
]
