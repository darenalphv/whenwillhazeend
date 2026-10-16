"""Fetch ERA5 winds, boundary-layer height and precipitation for the LPM-3L domain.

Source: ERA5 (Hersbach et al. 2020, Copernicus C3S) as re-gridded by Google's
WeatherBench 2 to 1.5 deg, 6-hourly, 13 pressure levels (conservative regridding),
public bucket gs://weatherbench2 over HTTPS -- no account, no key.

Why this and not the CDS: the Copernicus CDS needs an account. Open-Meteo's ERA5
archive does not serve pressure levels (returns null for 850 hPa; checked). The
WeatherBench 2 copy is ERA5 itself, coarsened, so the fidelity lost is horizontal
(1.5 deg instead of 0.25 deg) and temporal (6-hourly instead of hourly), plus no
100 m wind (the 1000 hPa wind is used in its place).

If the owner later sets up ~/.cdsapirc, ``fetch_era5_cds.py`` (not written: needs an
account to test) would be the drop-in upgrade; the output format below is the
contract: one npz per year in _raw/met/.

Usage:  scripts/model/earth/_raw/tools_venv/bin/python scripts/model/earth/fetch_era5_wb2.py 2015 2019
Output: scripts/model/earth/_raw/met/era5wb2_<year>.npz   (float32, domain subset)
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request
from datetime import datetime, timedelta

import numcodecs
import numpy as np

BASE = ("https://storage.googleapis.com/weatherbench2/datasets/era5/"
        "1959-2023_01_10-6h-240x121_equiangular_with_poles_conservative.zarr")
RAW = pathlib.Path(__file__).parent / "_raw"
OUT = RAW / "met"
LON_RANGE = (91.5, 124.5)     # domain 95-120E padded by >= 1 cell
LAT_RANGE = (-12.0, 12.0)     # domain 8S-8N padded
LEVELS = (1000, 925, 850)
SEASON = ((6, 20), (12, 5))    # Jun 20 .. Dec 5 (spin-up and run-out around Jul-Nov)
BLOSC = numcodecs.Blosc()


def _get(path: str, tries: int = 4) -> bytes:
    for k in range(tries):
        try:
            with urllib.request.urlopen(f"{BASE}/{path}", timeout=120) as r:
                return r.read()
        except Exception as exc:  # network hiccup: back off and retry
            if k == tries - 1:
                raise
            print("  retry", path, exc, flush=True)
            time.sleep(3 * (k + 1))
    raise RuntimeError("unreachable")


def _meta():
    p = RAW / "arco" / "wb2_15.json"
    if p.exists():
        return json.loads(p.read_text())["metadata"]
    m = json.loads(_get(".zmetadata"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m))
    return m["metadata"]


def _array(meta, name, chunk_key):
    a = meta[f"{name}/.zarray"]
    raw = BLOSC.decode(_get(f"{name}/{chunk_key}"))
    return np.frombuffer(raw, dtype=a["dtype"]).reshape(a["chunks"])


def main(years):
    meta = _meta()
    lat = _array(meta, "latitude", "0")
    lon = _array(meta, "longitude", "0")
    lev = _array(meta, "level", "0")
    tattr = meta["time/.zattrs"]
    t0 = datetime.fromisoformat(tattr["units"].split("since ")[1].strip()[:19])
    tchunk = meta["u_component_of_wind/.zarray"]["chunks"][0]
    li = [int(np.where(lev == L)[0][0]) for L in LEVELS]
    xi = np.where((lon >= LON_RANGE[0]) & (lon <= LON_RANGE[1]))[0]
    yi = np.where((lat >= LAT_RANGE[0]) & (lat <= LAT_RANGE[1]))[0]
    OUT.mkdir(parents=True, exist_ok=True)
    for year in years:
        out = OUT / f"era5wb2_{year}.npz"
        if out.exists():
            print("have", out)
            continue
        a = datetime(year, *SEASON[0])
        b = datetime(year, *SEASON[1])
        i0 = int((a - t0).total_seconds() // 21600)
        i1 = int((b - t0).total_seconds() // 21600)
        c0, c1 = i0 // tchunk, i1 // tchunk
        fields = {k: [] for k in ("u10", "v10", "u1000", "v1000", "u925", "v925",
                                  "u850", "v850", "blh", "tp6")}
        times = []
        for c in range(c0, c1 + 1):
            t_start = time.time()
            u = _array(meta, "u_component_of_wind", f"{c}.0.0.0")
            v = _array(meta, "v_component_of_wind", f"{c}.0.0.0")
            u10 = _array(meta, "10m_u_component_of_wind", f"{c}.0.0")
            v10 = _array(meta, "10m_v_component_of_wind", f"{c}.0.0")
            blh = _array(meta, "boundary_layer_height", f"{c}.0.0")
            tp = _array(meta, "total_precipitation_6hr", f"{c}.0.0")
            sub = np.ix_(xi, yi)
            for k in range(tchunk):
                gi = c * tchunk + k
                if gi < i0 or gi > i1:
                    continue
                times.append((t0 + timedelta(hours=6 * gi)).isoformat())
                fields["u10"].append(u10[k][sub]); fields["v10"].append(v10[k][sub])
                fields["blh"].append(blh[k][sub]); fields["tp6"].append(tp[k][sub])
                for L, idx in zip(LEVELS, li):
                    fields[f"u{L}"].append(u[k, idx][sub])
                    fields[f"v{L}"].append(v[k, idx][sub])
            print(f"{year} chunk {c - c0 + 1}/{c1 - c0 + 1} {time.time() - t_start:.1f}s", flush=True)
        # arrays are (time, lon, lat) -> store (time, lat, lon), south->north, west->east
        arrs = {k: np.stack(vv).transpose(0, 2, 1).astype(np.float32) for k, vv in fields.items()}
        np.savez_compressed(out, lat=lat[yi].astype(np.float32), lon=lon[xi].astype(np.float32),
                            time=np.array(times), **arrs)
        print("wrote", out, {k: v.shape for k, v in arrs.items()})


if __name__ == "__main__":
    main([int(y) for y in sys.argv[1:]] or [2015])
