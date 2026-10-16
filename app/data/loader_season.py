"""Loaders for the season model's bundled data (MODEL-SEASON owned).

Same rules as `app.data.loader`: plain JSON, paths from ``__file__``, results
``lru_cache``d and therefore **read-only** for callers.

* ``iod.json``                 weekly Dipole Mode Index (NOAA OSMC, OISST)
* ``enso.json``                via `app.data.loader.load_enso` (yearly rows,
                               each now carrying every 3-month ONI season)
* ``forecast_history.json``    build-time, leak-free hindcast of the forecast
                               odds for the current season
* ``../model/calibration_weather.json``  ERA5 areal-mean daily weather
* ``../model/hazard_fit.json``           the fitted season model
"""

from __future__ import annotations

import bisect
import functools
import json
import pathlib
from datetime import date, timedelta

DATA_DIR = pathlib.Path(__file__).parent
MODEL_DIR = DATA_DIR.parent / "model"

#: 3-month ONI seasons in CPC order; index i is centred on calendar month i+1.
ONI_SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
               "JJA", "JAS", "ASO", "SON", "OND", "NDJ")
#: CPC publishes a 3-month ONI about this many days after its last month ends
#: (ERSSTv5 monthly update, typically in the first ~10 days of the month).
ONI_PUBLICATION_LAG_DAYS = 10
#: OSMC weekly DMI: assume a week is usable this many days after its centre date.
DMI_PUBLICATION_LAG_DAYS = 7


def _read(path: pathlib.Path):
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def load_iod() -> dict:
    """``{"weekly": {"dates": [...], "dmi": [...]}, "monthly": {...}, "seasons": {...}}``."""
    return _read(DATA_DIR / "iod.json")


@functools.lru_cache(maxsize=1)
def load_hazard_fit() -> dict:
    return _read(MODEL_DIR / "hazard_fit.json")


@functools.lru_cache(maxsize=1)
def load_calibration_weather() -> dict:
    return _read(MODEL_DIR / "calibration_weather.json")


@functools.lru_cache(maxsize=1)
def calibration_weather_index() -> tuple[dict, dict]:
    cw = load_calibration_weather()
    return cw, {d: i for i, d in enumerate(cw["dates"])}


@functools.lru_cache(maxsize=1)
def load_forecast_history() -> dict:
    p = DATA_DIR / "forecast_history.json"
    if not p.is_file():
        return {"series": [], "method": None, "computed_at": None, "caveats": []}
    return _read(p)


# --------------------------------------------------------------------------- #
# "what was known on date D" -- publication-lagged climate indices
# --------------------------------------------------------------------------- #

def _month_end(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


@functools.lru_cache(maxsize=1)
def _oni_table() -> list[tuple[date, date, float]]:
    """[(last day of the season's final month, centre month 1st, value)] ascending."""
    from app.data.loader import load_enso
    rows = []
    for r in load_enso() or []:
        seas = r.get("oni_seasons") or {}
        y = int(r["year"])
        for i, name in enumerate(ONI_SEASONS):
            v = seas.get(name)
            if v is None:
                continue
            centre_m = i + 1
            last_m, last_y = centre_m + 1, y
            if last_m > 12:
                last_m, last_y = last_m - 12, y + 1
            rows.append((_month_end(last_y, last_m), date(y, centre_m, 1), float(v)))
    rows.sort()
    return rows


def oni_known_on(d: date | str, lag_days: int = ONI_PUBLICATION_LAG_DAYS):
    """Latest 3-month ONI a forecaster could have read on ``d``.

    Returns ``(value, season_label)`` or ``(None, None)``. The season label is
    e.g. ``"JAS 2026"``.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    tab = _oni_table()
    k = bisect.bisect_right(_oni_keys(), d - timedelta(days=lag_days)) - 1
    if k < 0:
        return None, None
    _, centre, v = tab[k]
    return v, f"{ONI_SEASONS[centre.month - 1]} {centre.year}"


@functools.lru_cache(maxsize=1)
def _oni_keys() -> list[date]:
    return [r[0] for r in _oni_table()]


def oni_for_season_centred(year: int, centre_month: int):
    for _, centre, v in _oni_table():
        if centre.year == year and centre.month == centre_month:
            return v
    return None


@functools.lru_cache(maxsize=1)
def _dmi_weekly() -> tuple[list[date], list[float]]:
    w = load_iod()["weekly"]
    return [date.fromisoformat(x) for x in w["dates"]], [float(v) for v in w["dmi"]]


def dmi_known_on(d: date | str, window_days: int = 91,
                 lag_days: int = DMI_PUBLICATION_LAG_DAYS):
    """Mean weekly DMI over the ~3 months ending at the latest week usable on ``d``.

    Returns ``(value, last_week_iso)`` or ``(None, None)``.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    dates, vals = _dmi_weekly()
    last = bisect.bisect_right(dates, d - timedelta(days=lag_days)) - 1
    if last < 0:
        return None, None
    first = bisect.bisect_left(dates, dates[last] - timedelta(days=window_days - 7))
    sel = vals[first:last + 1]
    return sum(sel) / len(sel), dates[last].isoformat()


__all__ = [
    "load_iod", "load_hazard_fit", "load_calibration_weather",
    "calibration_weather_index", "load_forecast_history", "oni_known_on",
    "dmi_known_on", "oni_for_season_centred", "ONI_SEASONS",
]
