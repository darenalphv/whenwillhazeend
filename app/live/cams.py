"""CAMS global air-quality model via the Open-Meteo Air Quality API (modelled).

What it is: the Copernicus Atmosphere Monitoring Service (CAMS) global
composition forecast, 0.4 deg (~45 km), 3-hourly, updated every 12 h (Open-Meteo
air-quality docs). "Past days" are archived forecasts, not measurements. Open-Meteo
interpolates to hourly and derives a US AQI using, for PM, a rolling mean over the
preceding 24 h. Nothing here is a measurement and every value this module hands
out is labelled modelled; it is never called API or PSI.

Licensing - read this:
* Data: CAMS / Copernicus via Open-Meteo, **CC BY 4.0**; attribution required.
* Service: Open-Meteo's free endpoint is for **non-commercial use only**. A
  subscription (API Standard, EUR 29/month as listed on 2026-10-16, 1M calls/month,
  includes the Air Quality API) grants a commercial licence and a key for the
  ``customer-`` endpoints. Set ``OPEN_METEO_API_KEY`` and every call in this module
  switches to ``customer-air-quality-api.open-meteo.com`` with ``apikey=``.
  The key goes in a POST form body - never in a URL, never in a log line.

Call budget: Open-Meteo counts **each coordinate** as one call (verified: one
1,000-point request exhausted the free 600/minute allowance). So the field grid
is 1.0 deg (390 points, one request) on the free tier and the native 0.4 deg
(2,268 points, three requests) with a key, and it is cached 6 h (CAMS itself
updates every 12 h). The nine pinned places are one 9-call request per 30 min.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from app.live.cache import Result, Store, cached
from app.live.common import MYT, from_utc_naive, iso, now_myt
from app.live.places import PLACES, Place

FREE_BASE = "https://air-quality-api.open-meteo.com/v1/air-quality"
CUSTOMER_BASE = "https://customer-air-quality-api.open-meteo.com/v1/air-quality"

SOURCE = ("CAMS global atmospheric composition forecast (Copernicus Atmosphere "
          "Monitoring Service), via Open-Meteo")
LICENCE = "CC BY 4.0"
GRID_KM = 45  # CAMS global 0.4 deg, "45 kilometre" per Open-Meteo's air-quality docs
NATIVE_DEG = 0.4

POINTS_TTL_S = 30 * 60
POINTS_MAX_STALE_S = 6 * 3600
FIELD_TTL_S = 6 * 3600
FIELD_MAX_STALE_S = 24 * 3600
POINTS_TIMEOUT_S = 10.0
FIELD_TIMEOUT_S = 25.0
PAST_DAYS = 8  # one more than the longest window, so 7 x 24 h is always covered
MAX_POINTS_PER_REQUEST = 800

# Field domain: Sumatra, the Peninsula, Singapore, Kalimantan (CONTRACT_V2 M5).
FIELD_BOUNDS = {"lon_min": 95.0, "lon_max": 120.0, "lat_min": -6.0, "lat_max": 8.0}
FIELD_DEG_FREE = 1.0
FIELD_DEG_KEYED = NATIVE_DEG

points_store = Store("cams-points")
field_store = Store("cams-field", codec="npz")


def attribution(year: int | None = None) -> str:
    year = year or now_myt().year
    return (f"Contains modified Copernicus Atmosphere Monitoring Service information {year}; "
            "neither the European Commission nor ECMWF is responsible for any use of it. "
            "Air-quality data by Open-Meteo.com, CC BY 4.0.")


def using_key() -> bool:
    return bool(os.environ.get("OPEN_METEO_API_KEY", "").strip())


def _post(params: dict, timeout: float) -> list[dict]:
    """POST to the free or customer endpoint. The key, if any, rides in the body."""
    from app.live.common import http_client

    key = os.environ.get("OPEN_METEO_API_KEY", "").strip()
    url = CUSTOMER_BASE if key else FREE_BASE
    data = {k: str(v) for k, v in params.items()}
    if key:
        data["apikey"] = key
    with http_client(timeout) as client:
        r = client.post(url, data=data)
        r.raise_for_status()
        payload = r.json()
    if isinstance(payload, dict) and payload.get("error"):
        raise ValueError("Open-Meteo returned an error payload")
    return payload if isinstance(payload, list) else [payload]


# --------------------------------------------------------------------------- #
# Pinned places
# --------------------------------------------------------------------------- #

def _ints(values: list) -> list[int | None]:
    return [None if v is None else int(round(v)) for v in values]


def parse_points(payload: list[dict]) -> dict:
    """Compact form: shared hourly times (UTC+8 ISO) and per-point series."""
    if not payload:
        raise ValueError("empty Open-Meteo response")
    times_raw = payload[0]["hourly"]["time"]
    out = {"times": [iso(from_utc_naive(t)) for t in times_raw], "points": []}
    for loc in payload:
        h = loc["hourly"]
        if h["time"] != times_raw:
            raise ValueError("Open-Meteo returned misaligned time axes")
        out["points"].append({
            "cell_lat": round(float(loc["latitude"]), 3),
            "cell_lon": round(float(loc["longitude"]), 3),
            "pm25": _ints(h["pm2_5"]),
            "us_aqi": _ints(h["us_aqi"]),
        })
    return out


def _points_params(places: tuple[Place, ...]) -> dict:
    return {
        "latitude": ",".join(f"{p.lat:.4f}" for p in places),
        "longitude": ",".join(f"{p.lon:.4f}" for p in places),
        "hourly": "pm2_5,us_aqi",
        "past_days": PAST_DAYS,
        "forecast_days": 1,
        "timezone": "GMT",
        "domains": "cams_global",
    }


def get_points(places: tuple[Place, ...] = PLACES) -> Result:
    """Hourly modelled PM2.5 and US AQI for ``places``. Raises SourceUnavailable."""
    key = "points:" + "|".join(p.id for p in places)

    def load(_prev):
        data = parse_points(_post(_points_params(places), POINTS_TIMEOUT_S))
        data["place_ids"] = [p.id for p in places]
        return data, {"keyed": using_key()}

    return cached(points_store, key, ttl=POINTS_TTL_S, max_stale=POINTS_MAX_STALE_S,
                  loader=load, lock_timeout=POINTS_TIMEOUT_S + 2)


# --------------------------------------------------------------------------- #
# Field
# --------------------------------------------------------------------------- #

def grid_spec(deg: float | None = None) -> dict:
    deg = deg if deg is not None else (FIELD_DEG_KEYED if using_key() else FIELD_DEG_FREE)
    b = FIELD_BOUNDS
    nx = int(round((b["lon_max"] - b["lon_min"]) / deg)) + 1
    ny = int(round((b["lat_max"] - b["lat_min"]) / deg)) + 1
    return {
        "lon_min": b["lon_min"], "lon_max": round(b["lon_min"] + (nx - 1) * deg, 4),
        "lat_min": b["lat_min"], "lat_max": round(b["lat_min"] + (ny - 1) * deg, 4),
        "nx": nx, "ny": ny, "dlon": deg, "dlat": deg,
        "order": "row-major, rows south to north, columns west to east; index = row * nx + col",
    }


def grid_points(spec: dict) -> list[tuple[float, float]]:
    return [(round(spec["lat_min"] + r * spec["dlat"], 4), round(spec["lon_min"] + c * spec["dlon"], 4))
            for r in range(spec["ny"]) for c in range(spec["nx"])]


def _field_chunk(pts: list[tuple[float, float]]) -> tuple[list[str], list[list[int]]]:
    payload = _post({
        "latitude": ",".join(str(p[0]) for p in pts),
        "longitude": ",".join(str(p[1]) for p in pts),
        "hourly": "pm2_5",
        "temporal_resolution": "hourly_3",
        "past_days": PAST_DAYS,
        "forecast_days": 1,
        "timezone": "GMT",
        "domains": "cams_global",
    }, FIELD_TIMEOUT_S)
    if len(payload) != len(pts):
        raise ValueError("Open-Meteo returned a different number of locations")
    times = payload[0]["hourly"]["time"]
    cols = []
    for loc in payload:
        if loc["hourly"]["time"] != times:
            raise ValueError("Open-Meteo returned misaligned time axes")
        cols.append([-1 if v is None else int(round(v)) for v in loc["hourly"]["pm2_5"]])
    return times, cols


def fetch_field(spec: dict) -> dict:
    """Fetch the whole grid. Returns numpy arrays: epoch seconds and (T, ny*nx) int16."""
    import numpy as np

    pts = grid_points(spec)
    chunks = [pts[i:i + MAX_POINTS_PER_REQUEST] for i in range(0, len(pts), MAX_POINTS_PER_REQUEST)]
    if len(chunks) == 1:
        results = [_field_chunk(chunks[0])]
    else:
        with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as ex:
            results = list(ex.map(_field_chunk, chunks))
    times = results[0][0]
    if any(r[0] != times for r in results):
        raise ValueError("chunks returned different time axes")
    values = np.full((len(times), len(pts)), -1, dtype=np.int16)
    j = 0
    for _, cols in results:
        for col in cols:
            values[:, j] = np.clip(np.asarray(col, dtype=np.int32), -1, 32000)
            j += 1
    epoch = np.array([int(datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp())
                      for t in times], dtype=np.int64)
    return {"epoch": epoch, "values": values}


def get_field() -> tuple[dict, Result]:
    spec = grid_spec()
    key = f"field:{spec['dlon']}"

    def load(_prev):
        return fetch_field(spec), {"spec": spec, "keyed": using_key()}

    res = cached(field_store, key, ttl=FIELD_TTL_S, max_stale=FIELD_MAX_STALE_S,
                 loader=load, lock_timeout=FIELD_TIMEOUT_S * 2)
    return res.meta.get("spec", spec), res


def field_frames(value: dict, start: datetime, end: datetime, step_hours: int) -> list[dict]:
    """Frames inside [start, end], every ``step_hours`` (aligned to 00 UTC)."""
    epoch = value["epoch"]
    values = value["values"]
    lo, hi = start.timestamp(), end.timestamp()
    frames = []
    for i, t in enumerate(epoch.tolist()):
        if t < lo or t > hi or (t // 3600) % step_hours:
            continue
        row = values[i].tolist()
        frames.append({
            "t": iso(datetime.fromtimestamp(t, MYT)),
            "values": [None if v < 0 else v for v in row],
        })
    return frames


def series_max(series: list[dict]) -> dict | None:
    best = None
    for p in series:
        if p["v"] is not None and (best is None or p["v"] > best["v"]):
            best = p
    return dict(best) if best else None
