"""Canadian Drought Code (DC), the drought index BMKG uses operationally.

Replaces KBDI in the season hazard (research/MODEL_SPEC.md section 2.4, S2).
KBDI (`app.model.kbdi`) stays computable for continuity but is not used.

Equations (Van Wagner 1987, Canadian Forestry Service FTR-35, eqs. 18-23 as
implemented in the R package cffdrs `drought_code()`):

    rain:  if P > 2.8 mm:   r_w  = 0.83 P - 1.27                        (eq. 18)
                            SMI  = 800 exp(-DC_{t-1} / 400)              (eq. 19)
                            DC_r = DC_{t-1} - 400 ln(1 + 3.937 r_w / SMI)
                                 = 400 ln(800 / (SMI + 3.937 r_w))       (eqs. 20-21)
                            DC_r = max(DC_r, 0)
           else             DC_r = DC_{t-1}
    dry:   PE = (0.36 (T + 2.8) + L_f) / 2,  T floored at -2.8 C, PE >= 0  (eq. 22)
           DC_t = max(DC_r + PE, 0)                                      (eq. 23)

L_f is the day-length adjustment: cffdrs uses 1.4 in every month for
-20 < lat <= 20 (the tropical setting used here), and Van Wagner's monthly
tables north of 20N / south of 20S. T is the noon temperature; this model has
only the daily maximum, which runs ~1-2 C warmer than noon, so DC here dries a
little faster than a station DC would (PE rises 0.18 per degC) [J].

Verification: `scripts/model/season/verify_dc.py` runs this implementation
against the cffdrs reference table `tests/testthat/data/DroughtCode.csv`
(4,392 cases) and a multi-day FWI reference series; `tests/test_season_v2.py`
holds an excerpt. Result is recorded in `app/model/hazard_fit.json`.

Thresholds (Indonesia), all DC units -- see research/PARAMETERS.json:
  * BMKG classes: 0-140 wet, 141-260 moist, 261-350 dry, >350 very dry [V, grey]
  * Field et al. 2004 (Atmos. Env. 38:1869): severe haze likely above 388.2 [V]
  * DC_crit = 350 in the hazard (BMKG "very dry"; range 300-400)
"""

from __future__ import annotations

import math

__all__ = ["dc_step", "dc_series", "dc_step_vec", "drought_readiness",
           "DC_CRIT", "DC_WET_MAX", "DC_SEVERE_HAZE", "DC_START", "CAVEATS"]

DC_RAIN_THRESHOLD_MM = 2.8     # Van Wagner 1987 / cffdrs
DC_EFF_RAIN_SLOPE = 0.83
DC_EFF_RAIN_OFFSET = 1.27
DC_PET_COEFF = 0.36
DC_TEMP_OFFSET = 2.8
LF_TROPICS = 1.4               # cffdrs, |lat| <= 20
_LF_NORTH = (-1.6, -1.6, -1.6, 0.9, 3.8, 5.8, 6.4, 5.0, 2.4, 0.4, -1.6, -1.6)
_LF_SOUTH = (6.4, 5.0, 2.4, 0.4, -1.6, -1.6, -1.6, -1.6, -1.6, 0.9, 3.8, 5.8)

DC_CRIT = 350.0                # BMKG "very dry" lower bound (range 300-400)
DC_WET_MAX = 140.0             # BMKG "wet" class upper bound
DC_SEVERE_HAZE = 388.2         # Field et al. 2004
DC_START = 15.0                # cffdrs default start value


def _lf(lat: float, month: int, lat_adjust: bool = True) -> float:
    if not lat_adjust:
        return _LF_NORTH[month - 1]
    if lat <= -20.0:
        return _LF_SOUTH[month - 1]
    if lat <= 20.0:
        return LF_TROPICS
    return _LF_NORTH[month - 1]


def dc_step(dc_yda: float, temp_c: float, prec_mm: float, lat: float = -2.5,
            month: int = 8, lat_adjust: bool = True) -> float:
    """One day of the Drought Code, exactly as cffdrs `drought_code()`."""
    t = max(float(temp_c), -DC_TEMP_OFFSET)
    pe = (DC_PET_COEFF * (t + DC_TEMP_OFFSET) + _lf(lat, month, lat_adjust)) / 2.0
    pe = max(pe, 0.0)
    if prec_mm > DC_RAIN_THRESHOLD_MM:
        rw = DC_EFF_RAIN_SLOPE * prec_mm - DC_EFF_RAIN_OFFSET
        smi = 800.0 * math.exp(-dc_yda / 400.0)
        dr = max(dc_yda - 400.0 * math.log(1.0 + 3.937 * rw / smi), 0.0)
    else:
        dr = dc_yda
    return max(dr + pe, 0.0)


def dc_series(tmax_c, rain_mm, months=None, lat: float = -2.5,
              dc0: float = DC_START) -> list[float]:
    """DC for consecutive days. ``months`` defaults to August (irrelevant in
    the tropics, where L_f is constant)."""
    out = []
    dc = float(dc0)
    for i, (t, p) in enumerate(zip(tmax_c, rain_mm)):
        m = months[i] if months is not None else 8
        dc = dc_step(dc, float(t), float(p or 0.0), lat, m)
        out.append(dc)
    return out


def dc_step_vec(np, dc_yda, temp_c, prec_mm, lf: float = LF_TROPICS):
    """Vectorised `dc_step` for the Monte-Carlo (tropical L_f)."""
    t = np.maximum(temp_c, -DC_TEMP_OFFSET)
    pe = np.maximum((DC_PET_COEFF * (t + DC_TEMP_OFFSET) + lf) / 2.0, 0.0)
    wet = prec_mm > DC_RAIN_THRESHOLD_MM
    rw = np.where(wet, DC_EFF_RAIN_SLOPE * prec_mm - DC_EFF_RAIN_OFFSET, 0.0)
    smi = 800.0 * np.exp(-dc_yda / 400.0)
    dr = np.where(wet, np.maximum(dc_yda - 400.0 * np.log1p(3.937 * rw / smi), 0.0), dc_yda)
    return np.maximum(dr + pe, 0.0)


def drought_readiness(dc: float) -> float:
    """0..1 drought state for the site's visuals (CONTRACT_V2 M4).

    Linear in DC from the top of BMKG's "wet" class (140 -> 0) to Field et
    al.'s 2004 severe-haze threshold (388.2 -> 1), clipped. A presentation
    mapping [J], not a probability.
    """
    x = (float(dc) - DC_WET_MAX) / (DC_SEVERE_HAZE - DC_WET_MAX)
    return min(max(x, 0.0), 1.0)


CAVEATS = [
    "The Drought Code was built for Canadian forest duff, not tropical peat. "
    "It is used here because Indonesia's met agency (BMKG) and the Global Fire "
    "WEather Database use it, and because published Indonesian haze thresholds "
    "(DC 350-400) are in its units. It is a proxy for the peat water table, "
    "which is what actually decides whether peat burns and which is not "
    "measured anywhere regionally.",
    "It is computed here from the average weather of five peat-belt points "
    "(ERA5 reanalysis), using the daily maximum temperature in place of the "
    "noon temperature the index specifies. Averaging rain over points gives "
    "fewer completely dry days than a single gauge, so this DC runs lower than "
    "a station DC in the same weather; the haze thresholds quoted from the "
    "literature are station or grid-cell values.",
]
