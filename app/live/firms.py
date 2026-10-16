"""NASA FIRMS VIIRS active-fire detections, last 7 days (satellite, near real time).

Source: the open FIRMS "active fire" text files - no key needed - for the
SouthEast_Asia region, VIIRS 375 m Collection 2 NRT:

* Suomi NPP: ``suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_SouthEast_Asia_7d.csv`` (primary)
* NOAA-20:   ``noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_7d.csv`` (fallback only)

The two satellites see the same fires ~50 minutes apart, so their detections are
never added together (that would double count); NOAA-20 is used only when the
Suomi NPP file cannot be fetched, and the response names the sensor used.

Terms: NASA Earth science data are open with no restrictions on use, commercial
included; NASA asks for acknowledgement, and the LANCE/FIRMS disclaimer applies
(provided "as is"; not for tactical decisions or local-scale conditions).

Memory: the file is ~6 MB / ~70k rows. It is streamed line by line and only the
nominal/high-confidence rows inside the map domain are kept, as five compact
numpy arrays (~30k rows -> under 1 MB). The raw text is never held whole. A
conditional GET (``If-None-Match``) skips the download when the file is
unchanged.
"""

from __future__ import annotations

import array
import csv
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone

import numpy as np

from app.live.cache import Result, Store, cached
from app.live.common import MYT, SourceUnavailable, describe_error, http_client, iso, log
from app.live.regions import DOMAIN, REGION_LABELS, classify, region_ids

BASE = "https://firms.modaps.eosdis.nasa.gov/data/active_fire"
SENSORS = {
    "suomi-npp": {"label": "Suomi NPP", "url": f"{BASE}/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_SouthEast_Asia_7d.csv"},
    "noaa-20": {"label": "NOAA-20", "url": f"{BASE}/noaa-20-viirs-c2/csv/J1_VIIRS_C2_SouthEast_Asia_7d.csv"},
}
ORDER = ("suomi-npp", "noaa-20")

SOURCE_TMPL = "NASA FIRMS, VIIRS 375 m active fire NRT (Collection 2), {sensor}"
ATTRIBUTION = (
    "We acknowledge the use of data from NASA's Fire Information for Resource "
    "Management System (FIRMS) (https://www.earthdata.nasa.gov/firms), part of "
    "NASA's Earth Science Data and Information System (ESDIS), with near-real-time "
    "data from LANCE. Provided \"as is\"; see the LANCE disclaimer: "
    "https://www.earthdata.nasa.gov/data/projects/lance/disclaimer"
)
DISCLAIMER_URL = "https://www.earthdata.nasa.gov/data/projects/lance/disclaimer"

TTL_S = 60 * 60
MAX_STALE_S = 24 * 3600
TIMEOUT_S = 30.0
BIN_DEG = 0.25
CONF_CODE = {"nominal": 1, "n": 1, "high": 2, "h": 2}

store = Store("firms", codec="npz")


# --------------------------------------------------------------------------- #
# Parsing (streamed)
# --------------------------------------------------------------------------- #

def parse_lines(lines: Iterable[str]) -> dict[str, np.ndarray]:
    """Keep nominal/high detections inside DOMAIN. Accepts any iterable of lines."""
    reader = csv.reader(lines)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("empty FIRMS file") from None
    try:
        i_lat, i_lon = header.index("latitude"), header.index("longitude")
        i_date, i_time = header.index("acq_date"), header.index("acq_time")
        i_conf, i_frp = header.index("confidence"), header.index("frp")
    except ValueError as exc:
        raise ValueError(f"FIRMS header missing a column: {exc}") from None

    lat, lon, frp = array.array("f"), array.array("f"), array.array("f")
    t, conf = array.array("q"), array.array("b")
    d = DOMAIN
    epoch_cache: dict[str, int] = {}
    for row in reader:
        if len(row) <= max(i_lat, i_lon, i_date, i_time, i_conf, i_frp):
            continue
        c = CONF_CODE.get(row[i_conf].strip().lower())
        if c is None:  # low confidence, or unknown
            continue
        try:
            y, x = float(row[i_lat]), float(row[i_lon])
        except ValueError:
            continue
        if not (d["lon_min"] <= x <= d["lon_max"] and d["lat_min"] <= y <= d["lat_max"]):
            continue
        day = row[i_date]
        base = epoch_cache.get(day)
        if base is None:
            base = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            epoch_cache[day] = base
        hhmm = row[i_time].strip().zfill(4)
        try:
            secs = base + int(hhmm[:2]) * 3600 + int(hhmm[2:]) * 60
            f = float(row[i_frp]) if row[i_frp] else 0.0
        except ValueError:
            continue
        lat.append(y); lon.append(x); frp.append(f); t.append(secs); conf.append(c)
    return {
        "lat": np.frombuffer(lat, dtype=np.float32).copy(),
        "lon": np.frombuffer(lon, dtype=np.float32).copy(),
        "frp": np.frombuffer(frp, dtype=np.float32).copy(),
        "t": np.frombuffer(t, dtype=np.int64).copy(),
        "conf": np.frombuffer(conf, dtype=np.int8).copy(),
    }


def _download(sensor: str, prev) -> tuple[dict, dict]:
    url = SENSORS[sensor]["url"]
    headers = {}
    if prev is not None and prev.meta.get("etag"):
        headers["If-None-Match"] = prev.meta["etag"]
    with http_client(TIMEOUT_S) as client:
        with client.stream("GET", url, headers=headers) as r:
            if r.status_code == 304 and prev is not None:
                return prev.value, dict(prev.meta)
            r.raise_for_status()
            value = parse_lines(r.iter_lines())
            meta = {
                "sensor": sensor,
                "etag": r.headers.get("etag"),
                "last_modified": r.headers.get("last-modified"),
                "rows_kept": int(value["t"].size),
            }
    if value["t"].size == 0:
        raise ValueError("FIRMS file had no usable detections in the domain")
    return value, meta


def get_detections() -> tuple[Result, str, list[str]]:
    """(result, sensor, errors). Tries Suomi NPP, then NOAA-20."""
    errors = []
    for sensor in ORDER:
        try:
            res = cached(store, sensor, ttl=TTL_S, max_stale=MAX_STALE_S,
                         loader=lambda prev, s=sensor: _download(s, prev),
                         lock_timeout=TIMEOUT_S + 5)
            return res, sensor, errors
        except SourceUnavailable as exc:
            errors.append(f"{SENSORS[sensor]['label']}: {exc}")
            log.warning("FIRMS %s unavailable: %s", sensor, exc)
        except Exception as exc:  # pragma: no cover - defensive
            errors.append(f"{SENSORS[sensor]['label']}: {describe_error(exc)}")
    raise SourceUnavailable("; ".join(errors))


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #

def _local_day_index(t: np.ndarray, first_day) -> np.ndarray:
    local = t + 8 * 3600  # UTC+08:00, no DST
    first = int(datetime(first_day.year, first_day.month, first_day.day, tzinfo=timezone.utc).timestamp())
    return ((local - first) // 86400).astype(np.int64)


def aggregate(det: dict[str, np.ndarray], start: datetime, end: datetime,
              bin_deg: float = BIN_DEG) -> dict:
    """Counts, FRP, per-day series and binned clusters for detections in [start, end]."""
    t = det["t"]
    m = (t >= int(start.timestamp())) & (t <= int(end.timestamp()))
    lat, lon, frp, t = det["lat"][m], det["lon"][m], det["frp"][m], t[m]
    ids = region_ids()
    reg = classify(lon.astype(np.float64), lat.astype(np.float64)) if t.size else np.zeros(0, np.int8)

    counts = np.bincount(reg, minlength=len(ids)) if t.size else np.zeros(len(ids), int)
    frp_sum = np.bincount(reg, weights=frp, minlength=len(ids)) if t.size else np.zeros(len(ids))

    first_day = start.astimezone(MYT).date()
    last_day = end.astimezone(MYT).date()
    ndays = (last_day - first_day).days + 1
    days = [first_day + timedelta(days=i) for i in range(ndays)]
    dayi = np.clip(_local_day_index(t, first_day), 0, ndays - 1) if t.size else np.zeros(0, np.int64)
    per_day = np.zeros((ndays, len(ids)), dtype=np.int64)
    if t.size:
        np.add.at(per_day, (dayi, reg), 1)
    by_day = [{"date": d.isoformat(), **{rid: int(per_day[i, j]) for j, rid in enumerate(ids)}}
              for i, d in enumerate(days)]

    # Which of the satellite's two daily passes produced detections each local day.
    # In a burning season a pass with zero detections in this domain means the pass
    # is missing from the file (a data gap), not that no fire burned.
    local_hour = ((t + 8 * 3600) % 86400) // 3600 if t.size else np.zeros(0, np.int64)
    night = np.zeros(ndays, bool)
    daytime = np.zeros(ndays, bool)
    if t.size:
        night[np.unique(dayi[local_hour < 6])] = True
        daytime[np.unique(dayi[(local_hour >= 10) & (local_hour < 17)])] = True
    lo_local = start.astimezone(MYT)
    hi_local = end.astimezone(MYT)
    coverage = []
    for i, d in enumerate(days):
        # A pass "could have happened" only if its local time slot is inside the window.
        night_due = (i > 0 or lo_local.hour < 1) and (i < ndays - 1 or hi_local.hour >= 4)
        day_due = (i > 0 or lo_local.hour < 13) and (i < ndays - 1 or hi_local.hour >= 16)
        coverage.append({
            "date": d.isoformat(),
            "night_pass": bool(night[i]) if night_due else None,
            "day_pass": bool(daytime[i]) if day_due else None,
        })

    features = []
    if t.size:
        d = DOMAIN
        nxb = int(np.ceil((d["lon_max"] - d["lon_min"]) / bin_deg))
        ix = np.floor((lon - d["lon_min"]) / bin_deg).astype(np.int64)
        iy = np.floor((lat - d["lat_min"]) / bin_deg).astype(np.int64)
        key = iy * nxb + ix
        uniq, inv = np.unique(key, return_inverse=True)
        nb = uniq.size
        n = np.bincount(inv, minlength=nb)
        f_sum = np.bincount(inv, weights=frp, minlength=nb)
        lat_m = np.bincount(inv, weights=lat, minlength=nb) / n
        lon_m = np.bincount(inv, weights=lon, minlength=nb) / n
        t_min = np.full(nb, np.iinfo(np.int64).max); np.minimum.at(t_min, inv, t)
        t_max = np.full(nb, np.iinfo(np.int64).min); np.maximum.at(t_max, inv, t)
        reg_counts = np.zeros((nb, len(ids)), dtype=np.int64); np.add.at(reg_counts, (inv, reg), 1)
        bin_reg = reg_counts.argmax(axis=1)
        daily = np.zeros((nb, ndays), dtype=np.int64); np.add.at(daily, (inv, dayi), 1)
        for b in range(nb):
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(float(lon_m[b]), 3), round(float(lat_m[b]), 3)]},
                "properties": {
                    "count": int(n[b]),
                    "frp_mw": round(float(f_sum[b]), 1),
                    "first_date": datetime.fromtimestamp(int(t_min[b]), MYT).date().isoformat(),
                    "last_date": datetime.fromtimestamp(int(t_max[b]), MYT).date().isoformat(),
                    "region": ids[int(bin_reg[b])],
                    "daily": [int(x) for x in daily[b]],
                },
            })

    return {
        "count_by_region": {rid: int(counts[j]) for j, rid in enumerate(ids)},
        "frp_gw_by_region": {rid: round(float(frp_sum[j]) / 1000.0, 1) for j, rid in enumerate(ids)},
        "by_day": by_day,
        "coverage": coverage,
        "clusters": {"type": "FeatureCollection", "features": features},
        "total": int(t.size),
        "first_detection": iso(datetime.fromtimestamp(int(t.min()), MYT)) if t.size else None,
        "last_detection": iso(datetime.fromtimestamp(int(t.max()), MYT)) if t.size else None,
    }
