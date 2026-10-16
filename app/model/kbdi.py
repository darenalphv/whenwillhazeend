"""Keetch-Byram Drought Index (KBDI) and a fire-spread readiness mapping.

**No longer used by the season model** (MODEL_SPEC S2): the hazard now uses
the Canadian Drought Code (`app.model.drought_code`), the index BMKG and the
published Indonesian haze thresholds use. KBDI stays computable for
continuity and for anything that still reads it.

Equations
---------
The KBDI (Keetch & Byram 1968, USDA Forest Service Research Paper SE-38) models
the cumulative moisture deficiency of the upper soil / duff layer as a closed
system holding 8 inches (203.2 mm) of water.  The index is expressed in
hundredths of an inch of soil-moisture deficit, so it runs 0 (saturated) to
800 (8 inches of deficit).

1.  Rain removes deficit, but only *net* rainfall counts.  The first 0.20 in
    (5.08 mm) of a rain **event** is intercepted by the canopy and litter and
    never reaches the soil store:

        P_net(t) = max(0, P(t) - max(0, 5.08 mm - sum of P over the preceding
                                     consecutive wet days of the same event))

    A "wet day" is any day with P > 0; the running event total resets on the
    first dry day.  Practically: an isolated 5 mm shower moves the index by
    nothing at all, which is exactly why a single shower does not end a peat
    fire.

2.  After subtracting net rain, the index is advanced by the *drought factor*
    dQ, the evapotranspiration demand of that day (imperial form, T in degF,
    R in inches of mean **annual** rainfall):

        Q       = KBDI(t-1) - 100 * P_net(t)        [units of 0.01 in]
        dQ      = (800 - Q) * (0.968 * exp(0.0486*T) - 8.30) * dt
                  / (1 + 10.88 * exp(-0.0441*R)) * 1e-3
        KBDI(t) = clip(Q + dQ, 0, 800)

    The numerator bracket goes negative below T ~ 44.2 degF (6.8 degC); dQ is
    clamped at 0 there, as in the standard operational implementation.

    The equivalent SI form (Crane 1982; Janis et al. 2002) is
        dQ_SI = (203.2 - Q_SI)*(0.968*exp(0.0875*T_C + 1.5552) - 8.30)*dt
                / (1 + 10.88*exp(-0.001736*R_mm)) * 1e-3
    which is the same expression after unit substitution.  This module
    computes in imperial units and returns the 0-800 scale the contract asks
    for; inputs are accepted in degC and mm because that is what Open-Meteo
    serves.

3.  `drought_factor()` maps KBDI onto a 0..1 "fire-spread readiness".  The
    relationship between KBDI and actual burning in Indonesian peatland is
    strongly non-linear: below roughly KBDI 300 the water table is high enough
    that ignitions self-extinguish, between 400 and 600 surface fires escape
    into the peat, and above ~600 the peat is dry well below the surface and
    fires become effectively unsuppressable (Field et al. 2016 PNAS;
    Dymond et al. 2005 on KBDI thresholds in Kalimantan).  We therefore use a
    logistic in KBDI centred at 400 with a 110-unit scale, renormalised so that
    drought_factor(0) == 0 and drought_factor(800) == 1.

Sources
-------
* Keetch, J.J. & Byram, G.M. (1968). *A Drought Index for Forest Fire Control.*
  USDA Forest Service Research Paper SE-38.
* Crane, W.J.B. (1982). Computing grassland and forest fire behaviour, relative
  humidity and drought index by pocket calculator. *Australian Forestry* 45(2).
* Janis, M.J., Johnson, M.B. & Forthun, G. (2002). Near-real time mapping of
  Keetch-Byram drought index. *Int. J. Wildland Fire* 11, 281-289.
* Field, R.D. et al. (2016). Indonesian fire activity and smoke pollution in
  2015 show persistent nonlinear sensitivity to El Nino-induced drought.
  *PNAS* 113(33), 9204-9209.
"""

from __future__ import annotations

import math

__all__ = ["kbdi_series", "kbdi_step", "drought_factor", "CAVEATS",
           "RAIN_INTERCEPTION_MM", "KBDI_MAX"]

KBDI_MAX = 800.0
#: Canopy/litter interception that must be satisfied before rain reaches the
#: soil moisture store.  0.20 inch in Keetch & Byram (1968).
RAIN_INTERCEPTION_MM = 5.08

#: Logistic parameters for drought_factor(). Centre/scale in KBDI units.
_DF_CENTRE = 400.0
_DF_SCALE = 110.0


def _mm_to_in(mm: float) -> float:
    return mm / 25.4


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def kbdi_step(prev_kbdi: float, tmax_c: float, net_rain_mm: float,
              annual_rain_mm: float, dt_days: float = 1.0) -> float:
    """Advance the index one day.  See module docstring for the equation."""
    annual_rain_in = max(_mm_to_in(annual_rain_mm), 1.0)  # guard log-blowup
    q = prev_kbdi - 100.0 * _mm_to_in(net_rain_mm)
    q = min(max(q, 0.0), KBDI_MAX)

    tmax_f = _c_to_f(tmax_c)
    # exp overflow guard: 0.0486 * 200degF is still tiny, but clamp anyway.
    numer = (KBDI_MAX - q) * (0.968 * math.exp(0.0486 * min(tmax_f, 160.0)) - 8.30) * dt_days
    denom = 1.0 + 10.88 * math.exp(-0.0441 * annual_rain_in)
    dq = numer / denom * 1e-3
    if dq < 0.0:      # too cold for net evapotranspiration
        dq = 0.0
    return min(max(q + dq, 0.0), KBDI_MAX)


def net_rainfall(rain_mm: list[float]) -> list[float]:
    """Per-day net rainfall using the consecutive-wet-day event rule."""
    out: list[float] = []
    event_total = 0.0
    for r in rain_mm:
        r = float(r or 0.0)
        if r <= 0.0:
            event_total = 0.0
            out.append(0.0)
            continue
        already = min(event_total, RAIN_INTERCEPTION_MM)
        deductible = RAIN_INTERCEPTION_MM - already
        out.append(max(0.0, r - deductible))
        event_total += r
    return out


def kbdi_series(dates: list[str], tmax_c: list[float], rain_mm: list[float],
                annual_rain_mm: float, kbdi_0: float = 0.0) -> list[float]:
    """Keetch-Byram Drought Index time series on the 0-800 imperial scale.

    Parameters
    ----------
    dates : ISO ``YYYY-MM-DD`` strings, ascending.  Used only for length and
        for spacing (gaps are treated as multi-day steps).
    tmax_c : daily maximum air temperature, degrees Celsius.
    rain_mm : daily precipitation total, millimetres.
    annual_rain_mm : mean *annual* precipitation at the site, millimetres.
        For Kalimantan this is typically 2500-3500 mm.
    kbdi_0 : index value on the day before ``dates[0]``.  Keetch & Byram
        recommend starting from a saturated soil (0) after prolonged rain.

    Returns
    -------
    list[float] of the same length as ``dates``.
    """
    n = len(dates)
    if not (len(tmax_c) == len(rain_mm) == n):
        raise ValueError("dates, tmax_c and rain_mm must be the same length")
    if n == 0:
        return []

    net = net_rainfall([float(r or 0.0) for r in rain_mm])
    series: list[float] = []
    q = float(kbdi_0)
    prev_date = None
    for i in range(n):
        dt = 1.0
        if prev_date is not None:
            dt = max(1.0, float(_days_between(prev_date, dates[i])))
        q = kbdi_step(q, float(tmax_c[i]), net[i], annual_rain_mm, dt_days=dt)
        series.append(q)
        prev_date = dates[i]
    return series


def _days_between(a: str, b: str) -> int:
    from datetime import date
    ya, ma, da = (int(x) for x in a[:10].split("-"))
    yb, mb, db = (int(x) for x in b[:10].split("-"))
    return (date(yb, mb, db) - date(ya, ma, da)).days


_L0 = 1.0 / (1.0 + math.exp(_DF_CENTRE / _DF_SCALE))
_L800 = 1.0 / (1.0 + math.exp(-(KBDI_MAX - _DF_CENTRE) / _DF_SCALE))


def drought_factor(kbdi: float) -> float:
    """Map KBDI (0-800) onto a 0..1 fire-spread readiness.

    Logistic centred at KBDI 400 with scale 110, renormalised to hit exactly
    0 at KBDI 0 and 1 at KBDI 800.  The steep part (roughly 300-550) is where
    Indonesian peatland flips from "ignitions self-extinguish" to "surface
    fire escapes underground".
    """
    k = min(max(float(kbdi), 0.0), KBDI_MAX)
    raw = 1.0 / (1.0 + math.exp(-(k - _DF_CENTRE) / _DF_SCALE))
    return min(max((raw - _L0) / (_L800 - _L0), 0.0), 1.0)


CAVEATS = [
    "KBDI was developed for temperate mineral soils in the southeastern United "
    "States, not for tropical peat domes several metres deep. It saturates at "
    "an 8-inch (203 mm) deficit, which a Kalimantan peat dome can exceed; once "
    "the index pins at 800 it stops distinguishing 'dry' from 'catastrophically "
    "dry', so the model under-separates 2015 from an ordinary El Nino year.",
    "The index has no memory of the water table. Drained peatland with a water "
    "table 0.8 m below the surface and intact peatland with a water table at "
    "0.1 m produce identical KBDI values from identical weather, yet only the "
    "first one burns. Drainage is handled elsewhere in this model (pool "
    "susceptibility), not here.",
    "Daily maximum temperature is a weak proxy for evapotranspiration under "
    "thick smoke. During a severe haze episode incoming solar radiation can "
    "fall by more than half, which both cools the surface and suppresses "
    "evaporation, so KBDI probably over-estimates drying at the height of the "
    "very events it is being used to forecast.",
    "The 0.20 inch (5.08 mm) interception constant is a temperate-forest value. "
    "Interception over burned-over scrub and oil-palm is different and is not "
    "differentiated here.",
    "Gridded or point rainfall in Kalimantan is sparse and convective rainfall "
    "is extremely patchy: a gauge or model cell can record 30 mm while peat "
    "5 km away receives nothing. Single-point KBDI is therefore a noisy "
    "estimate of the condition of a landscape.",
]
