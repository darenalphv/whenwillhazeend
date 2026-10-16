"""Model layer for whenwillhazeend.com.

Two questions, two models:

* **How many more days of haze?** -- `simulate_season_end` (`burn.py`), a
  discrete-time survival model whose daily termination hazard is a logistic in
  trailing rainfall, the Keetch-Byram drought index, ONI and monsoon progress,
  Monte-Carlo'd forward on Open-Meteo forecasts and ENSO-conditioned
  climatology.
* **How many years until there is nothing left to burn?** --
  `simulate_exhaustion` (`stock.py`), a stock-and-flow model over five land
  pools driven by a three-state ENSO Markov chain, in which burned peat is
  removed permanently.

Supporting modules: `kbdi` (Keetch & Byram 1968 drought index), `meteo`
(Open-Meteo client with a never-raising climatological fallback and the
monsoon phase function), `dispersion` (Gaussian-plume smoke transport to the
downwind cities), `history` (the observed season record), and `calibrate`
(the fit of the hazard coefficients, imported lazily because it needs scipy).

Every module carries its equations and citations in its docstring and a
``CAVEATS`` list of specific, honest limitations, which the website renders in
its Methods section.  ``ALL_CAVEATS`` collects them.
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

#: caveats keyed by module, for the site's mandatory Methods section
MODULE_CAVEATS = {
    "kbdi": list(kbdi.CAVEATS),
    "meteo": list(meteo.CAVEATS),
    "burn": list(burn.CAVEATS),
    "stock": list(stock.CAVEATS),
    "dispersion": list(dispersion.CAVEATS),
    "history": list(history.CAVEATS),
}

ALL_CAVEATS = [c for group in MODULE_CAVEATS.values() for c in group]


def __getattr__(name):
    """Lazily expose the calibration entry point (it imports scipy)."""
    if name in ("fit_hazard_coefficients", "calibrate"):
        import importlib
        _calibrate = importlib.import_module("app.model.calibrate")
        return _calibrate if name == "calibrate" else _calibrate.fit_hazard_coefficients
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
