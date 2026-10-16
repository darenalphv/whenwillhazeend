"""Model wind at 10 m and 850 hPa via the Open-Meteo Forecast API (modelled).

Used only to animate smoke drift on the "This week" map, aligned to the CAMS
PM2.5 frames. It is numerical-weather-model output (Open-Meteo ``best_match``,
global NWP), and "past days" are archived forecasts, not observations.

Verified 2026-10-16: the forecast endpoint serves ``wind_speed_850hPa`` /
``wind_direction_850hPa`` together with ``past_days`` and
``temporal_resolution=hourly_3``. 850 hPa is roughly 1,500 m above sea level in
the tropics - an approximation, stated in the response.

Grid: every 2 deg over the CAMS field bbox (13 x 8 = 104 points, i.e. 104
Open-Meteo calls per refresh, cached 6 h). The same ``OPEN_METEO_API_KEY``
switch as :mod:`app.live.cams` moves this to ``customer-api.open-meteo.com``.
Open-Meteo returns speed and the direction the wind blows *from*; u/v are
derived here: u = -s sin(dir), v = -s cos(dir) (u east-positive, v north-positive).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import numpy as np

from app.live.cache import Result, Store, cached
from app.live.cams import FIELD_BOUNDS, PAST_DAYS
from app.live.common import MYT, iso

FREE_BASE = "https://api.open-meteo.com/v1/forecast"
CUSTOMER_BASE = "https://customer-api.open-meteo.com/v1/forecast"
SOURCE = "Open-Meteo Forecast API (best_match global weather models), archived forecasts for past days"
LICENCE = "CC BY 4.0"

GRID_DEG = 2.0
LEVELS_M = [10, 1500]
LEVEL_NOTE = ("Level 0 is 10 m above ground. Level 1 is the 850 hPa pressure level, roughly "
              "1,500 m above sea level in the tropics (approximate; it varies with weather). "
              "Both are weather-model output, not measurements.")
TTL_S = 6 * 3600
MAX_STALE_S = 24 * 3600
TIMEOUT_S = 20.0
VARS = ("wind_speed_10m", "wind_direction_10m", "wind_speed_850hPa", "wind_direction_850hPa")

store = Store("wind", codec="npz")


def grid_spec(deg: float = GRID_DEG) -> dict:
    b = FIELD_BOUNDS
    nx = int((b["lon_max"] - b["lon_min"]) // deg) + 1
    ny = int((b["lat_max"] - b["lat_min"]) // deg) + 1
    return {"lon_min": b["lon_min"], "lon_max": b["lon_min"] + (nx - 1) * deg,
            "lat_min": b["lat_min"], "lat_max": b["lat_min"] + (ny - 1) * deg,
            "nx": nx, "ny": ny, "dlon": deg, "dlat": deg}


def _points(spec: dict) -> list[tuple[float, float]]:
    return [(spec["lat_min"] + r * spec["dlat"], spec["lon_min"] + c * spec["dlon"])
            for r in range(spec["ny"]) for c in range(spec["nx"])]


def _post(params: dict) -> list[dict]:
    from app.live.common import http_client

    key = os.environ.get("OPEN_METEO_API_KEY", "").strip()
    data = {k: str(v) for k, v in params.items()}
    if key:
        data["apikey"] = key
    with http_client(TIMEOUT_S) as client:
        r = client.post(CUSTOMER_BASE if key else FREE_BASE, data=data)
        r.raise_for_status()
        payload = r.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError("Open-Meteo returned an error payload")
    return payload if isinstance(payload, list) else [payload]


def parse(payload: list[dict], npts: int) -> dict[str, np.ndarray]:
    if len(payload) != npts:
        raise ValueError("Open-Meteo returned a different number of locations")
    times = payload[0]["hourly"]["time"]
    nt = len(times)
    out = {k: np.full((nt, npts), np.nan, dtype=np.float32) for k in ("u10", "v10", "u850", "v850")}
    for j, loc in enumerate(payload):
        h = loc["hourly"]
        if h["time"] != times:
            raise ValueError("Open-Meteo returned misaligned time axes")
        for lvl, (s_key, d_key) in {"10": VARS[:2], "850": VARS[2:]}.items():
            s = np.array([np.nan if v is None else v for v in h[s_key]], dtype=np.float64)
            d = np.radians(np.array([np.nan if v is None else v for v in h[d_key]], dtype=np.float64))
            out[f"u{lvl}"][:, j] = -s * np.sin(d)
            out[f"v{lvl}"][:, j] = -s * np.cos(d)
    out["epoch"] = np.array([int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp())
                             for t in times], dtype=np.int64)
    return out


def get_wind() -> tuple[dict, Result]:
    spec = grid_spec()
    pts = _points(spec)

    def load(_prev):
        payload = _post({
            "latitude": ",".join(f"{p[0]:g}" for p in pts),
            "longitude": ",".join(f"{p[1]:g}" for p in pts),
            "hourly": ",".join(VARS),
            "wind_speed_unit": "ms",
            "temporal_resolution": "hourly_3",
            "past_days": PAST_DAYS,
            "forecast_days": 1,
            "timezone": "GMT",
        })
        return parse(payload, len(pts)), {"spec": spec}

    res = cached(store, f"grid:{GRID_DEG}", ttl=TTL_S, max_stale=MAX_STALE_S, loader=load,
                 lock_timeout=TIMEOUT_S + 5)
    return res.meta.get("spec", spec), res


def _r(a: np.ndarray) -> list:
    return [None if np.isnan(x) else round(float(x), 1) for x in a]


def frames_for(value: dict, times_epoch: list[int]) -> list[dict]:
    """Wind frames at exactly the given epoch seconds (the PM2.5 frame times).
    A time the wind model lacks is skipped rather than interpolated."""
    index = {int(t): i for i, t in enumerate(value["epoch"].tolist())}
    frames = []
    for t in times_epoch:
        i = index.get(int(t))
        if i is None:
            continue
        frames.append({
            "t": iso(datetime.fromtimestamp(int(t), MYT)),
            "u": _r(value["u10"][i]) + _r(value["u850"][i]),
            "v": _r(value["v10"][i]) + _r(value["v850"][i]),
        })
    return frames
