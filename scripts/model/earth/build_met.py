"""Package the raw ERA5 subsets into the compact met files the app ships.

Input:  scripts/model/earth/_raw/met/era5wb2_<year>.npz   (fetch_era5_wb2.py)
Output: app/data/earth/met_<year>.npz   one fire season, 6-hourly, 1.5 deg, int16
        app/data/earth/met_clim.npz     monthly composite (4 synoptic times) of all years

Daily-mean boundary-layer height (spec H1): ERA5 BLH is only available here at the four
synoptic times (07, 13, 19, 01 WIB), which sample the diurnal cycle unevenly. The daily
mean at each cell is therefore estimated as mean(BLH samples) / mean(P(sample hours)),
where P is the station profile assigned to the cell (Palangka Raya, Pontianak, Kuching,
Singapore, Pekanbaru; flat over sea). The model then rebuilds H(t) = H_day x P(hour),
so the diurnal SHAPE comes from the hourly ERA5 station composites and the day-to-day
LEVEL from ERA5 at the cell.
"""
from __future__ import annotations

import pathlib
import sys
import urllib.request

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from app.model.transport import (BLH_FLOOR_M, PROFILE_ORDER, _profile_table,  # noqa: E402
                                 _ptype_grid)

RAW = HERE / "_raw" / "met"
OUT = ROOT / "app" / "data" / "earth"
WB2 = ("https://storage.googleapis.com/weatherbench2/datasets/era5/"
       "1959-2023_01_10-6h-240x121_equiangular_with_poles_conservative.zarr")
SCALE = 0.01          # m/s per int16 unit
RAIN_SCALE = 0.01     # mm/h per int16 unit


def land_mask(lat, lon):
    cache = HERE / "_raw" / "arco" / "lsm.npy"
    if cache.exists():
        full = np.load(cache)
    else:
        import numcodecs
        raw = urllib.request.urlopen(f"{WB2}/land_sea_mask/0.0", timeout=120).read()
        full = np.frombuffer(numcodecs.Blosc().decode(raw), "<f4").reshape(240, 121)
        np.save(cache, full)
    glon = np.arange(240) * 1.5
    glat = np.linspace(-90, 90, 121)
    xi = [int(np.argmin(np.abs(glon - x))) for x in lon]
    yi = [int(np.argmin(np.abs(glat - y))) for y in lat]
    return full[np.ix_(xi, yi)].T > 0.3          # (ny, nx)


def build_year(y):
    z = np.load(RAW / f"era5wb2_{y}.npz")
    lat, lon = z["lat"].astype(float), z["lon"].astype(float)
    order = np.argsort(lat)
    lat = lat[order]
    times = [str(t) for t in z["time"]]
    land = land_mask(lat, lon)
    ptype = _ptype_grid(np, lat, lon, land)
    prof, off = _profile_table(np)

    def f(name):
        return z[name][:, order, :].astype(np.float64)

    blh = f("blh")
    n_t = blh.shape[0]
    hours_utc = np.array([int(t[11:13]) for t in times])
    # daily mean BLH per UTC day, corrected for the uneven sampling of the profile
    hday = np.zeros_like(blh)
    days = [t[:10] for t in times]
    uniq = sorted(set(days))
    for d in uniq:
        idx = [i for i, dd in enumerate(days) if dd == d]
        samp = blh[idx].mean(axis=0)
        pf = np.zeros_like(samp)
        for i in idx:
            solar = (hours_utc[i] + lon[None, :] / 15.0) % 24.0
            clock = (solar - off[ptype]) % 24.0
            i0 = np.floor(clock).astype(int) % 24
            fr = clock - np.floor(clock)
            pt = ptype.astype(int)
            pf += prof[pt, i0] * (1 - fr) + prof[pt, (i0 + 1) % 24] * fr
        pf /= len(idx)
        hday[idx] = np.maximum(samp / np.maximum(pf, 0.05), BLH_FLOOR_M)[None]
    rain = np.maximum(f("tp6"), 0.0) * 1000.0 / 6.0        # m per 6 h -> mm/h

    def q(a, s):
        return np.clip(np.rint(a / s), -32767, 32767).astype(np.int16)

    out = dict(lat=lat.astype(np.float32), lon=lon.astype(np.float32), t0=np.array(times[0]),
               dt_h=np.array(6.0), scale=np.array(SCALE), rain_scale=np.array(RAIN_SCALE),
               ptype=ptype.astype(np.int8), land=land,
               profile_order=np.array(PROFILE_ORDER),
               u_sfc=q(f("u1000"), SCALE), v_sfc=q(f("v1000"), SCALE),
               u_925=q(f("u925"), SCALE), v_925=q(f("v925"), SCALE),
               u_850=q(f("u850"), SCALE), v_850=q(f("v850"), SCALE),
               u10=q(f("u10"), SCALE), v10=q(f("v10"), SCALE),
               hday=np.clip(np.rint(hday), 0, 32767).astype(np.int16),
               rain=q(rain, RAIN_SCALE),
               source=np.array("ERA5 (Hersbach et al. 2020; Copernicus C3S) via WeatherBench 2 "
                               "(gs://weatherbench2), 1.5 deg conservative regrid, 6-hourly"))
    p = OUT / f"met_{y}.npz"
    np.savez_compressed(p, **out)
    print("wrote", p, round(p.stat().st_size / 1e6, 2), "MB", "n_t", n_t)
    return out, times


def build_clim(per_year):
    months = list(range(6, 13))
    keys = ("u_sfc", "v_sfc", "u_925", "v_925", "u_850", "v_850", "u10", "v10", "hday", "rain")
    acc = {k: np.zeros((len(months), 4) + per_year[0][0]["u_sfc"].shape[1:]) for k in keys}
    cnt = np.zeros((len(months), 4))
    for out, times in per_year:
        for i, t in enumerate(times):
            m = int(t[5:7])
            if m not in months:
                continue
            mi, si = months.index(m), int(t[11:13]) // 6
            for k in keys:
                acc[k][mi, si] += out[k][i].astype(np.float64)
            cnt[mi, si] += 1
    o0 = per_year[0][0]
    clim = {k: np.rint(acc[k] / np.maximum(cnt, 1)[:, :, None, None]).astype(np.int16) for k in keys}
    p = OUT / "met_clim.npz"
    np.savez_compressed(p, lat=o0["lat"], lon=o0["lon"], ptype=o0["ptype"], land=o0["land"],
                        months=np.array(months), scale=np.array(SCALE),
                        rain_scale=np.array(RAIN_SCALE),
                        years=np.array([int(t[0][:4]) for _, t in per_year]), **clim)
    print("wrote", p, round(p.stat().st_size / 1e6, 2), "MB")


if __name__ == "__main__":
    yrs = [int(a) for a in sys.argv[1:]] or sorted(
        int(p.stem.split("_")[1]) for p in RAW.glob("era5wb2_*.npz"))
    built = [build_year(y) for y in yrs]
    if built:
        build_clim(built)
