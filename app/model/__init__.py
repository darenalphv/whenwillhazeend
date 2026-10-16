"""Model layer for whenwillhazeend.com.

The scientific basis for every module is ``research/MODEL_SPEC.md``.

* **When does the Kalimantan fire season end?** -- `simulate_season_end`
  (`burn.py`): a daily-hazard survival model trained on fire end-dates observed
  by NASA FIRMS (2001-2025), driven by the Drought Code (`drought_code.py`,
  verified against cffdrs), 12-week and 10-day rainfall, ONI and DMI as
  published on the forecast date, and a fitted seasonal clock, with per-season
  frailty for calibrated intervals. No information after the forecast date is
  used. `forecast.py` turns it into prediction-market-style model odds and a
  leak-free odds history.
* **When does Kalimantan run out of peat and forest?** --
  `simulate_exhaustion` (`stock.py`): peat as a thickness column that loses
  depth to drainage subsidence as well as fire, plus forest pools, with an
  ENSO Markov chain and a satellite-consistent burn rate. Peat and forest get
  separate headlines.
* **Where does the smoke go?** -- `haze3d.py` over `transport.py` (LPM-3L, a
  reduced-form 3-layer Lagrangian particle model on ERA5 winds with Sumatra
  and Kalimantan as sources), `emissions.py` (per-hectare and observed daily
  emissions) and `dispersion.py` (the snapshot plume). `health.py` publishes
  the literature's mortality ranges.

Supporting: `meteo` (weather client that makes no Open-Meteo call unless a
commercial key is configured, with bundled-ERA5 and climatology fallbacks),
`kbdi` (legacy, unused), `history`, and `calibrate` (offline; imports scipy
lazily).

Every module carries its equations and citations in its docstring and a
``CAVEATS`` list, which the site renders in its Methods section.
"""

from __future__ import annotations

from app.model import kbdi as kbdi
from app.model import meteo as meteo
from app.model import burn as burn
from app.model import stock as stock
from app.model import dispersion as dispersion
from app.model import history as history

from app.model.kbdi import kbdi_series, drought_factor
from app.model.meteo import fetch_weather, monsoon_progress
from app.model.burn import (simulate_season_end, HAZARD_COEFFS, HAZARD_FIT,
                            SCENARIOS)
from app.model.stock import simulate_exhaustion
from app.model.dispersion import haze_dispersion, pm25_to_aqi, aqi_category
from app.model.history import season_summary, compare_years

__all__ = [
    "kbdi_series", "drought_factor",
    "fetch_weather", "monsoon_progress",
    "simulate_season_end", "HAZARD_COEFFS", "HAZARD_FIT", "SCENARIOS",
    "simulate_exhaustion",
    "haze_dispersion", "pm25_to_aqi", "aqi_category",
    "season_summary", "compare_years",
    "fit_hazard_coefficients",
    "ALL_CAVEATS", "MODULE_CAVEATS",
    "kbdi", "meteo", "burn", "stock", "dispersion", "history",
]

def _caveats_of(name: str) -> list:
    """Caveats from a newer module, imported defensively: one broken module
    must not take the whole model package down with it."""
    import importlib
    try:
        return list(getattr(importlib.import_module(f"app.model.{name}"), "CAVEATS", []))
    except Exception:  # noqa: BLE001
        return []


#: caveats keyed by module, for the site's mandatory Methods section
MODULE_CAVEATS = {
    "kbdi": list(kbdi.CAVEATS),
    "meteo": list(meteo.CAVEATS),
    "burn": list(burn.CAVEATS),
    "stock": list(stock.CAVEATS),
    "dispersion": list(dispersion.CAVEATS),
    "history": list(history.CAVEATS),
}

for _name in ("drought_code", "forecast", "emissions", "transport", "haze3d", "health"):
    MODULE_CAVEATS[_name] = _caveats_of(_name)

ALL_CAVEATS = [c for group in MODULE_CAVEATS.values() for c in group]


def __getattr__(name):
    """Lazily expose the calibration entry point (it imports scipy)."""
    if name in ("fit_hazard_coefficients", "calibrate"):
        import importlib
        _calibrate = importlib.import_module("app.model.calibrate")
        return _calibrate if name == "calibrate" else _calibrate.fit_hazard_coefficients
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
