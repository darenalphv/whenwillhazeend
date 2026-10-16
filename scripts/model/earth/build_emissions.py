"""E2 + B1-B3: daily fire PM2.5 by 0.25-deg cell, and a satellite-consistent burned-area
series, from GFED4.1s (van der Werf et al. 2017, ESSD 9:697).

Inputs (open HTTP, no account): scripts/model/earth/_raw/gfed4/GFED4.1s_<year>[_beta].hdf5
from https://www.geo.vu.nl/~gwerf/GFED/GFED4/ ; province polygons from geoBoundaries
(IDN, MYS ADM1, CC BY 4.0 / ODbL) in _raw/geo/. LICENCE: the GFED download pages state
no licence (MODEL_SPEC 3.4 B2). Derived numbers are shipped with attribution and that
caveat recorded in provenance.

Why GFED4.1s and not CAMS GFAS (the spec's primary path): GFAS needs an Atmosphere
Data Store account. GFED4.1s gives monthly dry matter at 0.25 deg partitioned by fire
type (PEAT, DEFO, SAVA, AGRI, TEMF) with a daily fraction, which is enough to apply
the spec's emission factors directly to peat and non-peat dry matter. This replaces
the spec's F_peat scaling of GFAS (whose peat EF is the 9.1 g/kg forest stand-in):
here the peat EF itself is 22.3 g/kg (Kiely 2019), so no scaling factor is needed.
GFED4.1s 2017+ ("beta") is active-fire based and has no burned area.

Outputs:
  app/data/earth/emissions_gfed4s.npz  -- daily PM2.5 per active cell, Jun 20 - Dec 5
  app/data/earth/burned_area.json      -- B1-B3 series and per-region annual totals
"""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import date, timedelta

import h5py
import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
RAW = HERE / "_raw"
OUT = ROOT / "app" / "data" / "earth"
PARAMS = json.loads((OUT / "parameters.json").read_text())["parameters"]

LON = (95.0, 120.0)
LAT = (-8.0, 8.0)
YEARS = list(range(1997, 2024))
SEASON = (date(2000, 6, 20), date(2000, 12, 5))       # month/day only

EF_PEAT = PARAMS["ef_pm25_peat_central"]["value"]           # 22.3  Kiely 2019
EF_TROP = PARAMS["ef_pm25_tropical_forest"]["value"]        # 8.3   Andreae 2019
EF_SAVA = PARAMS["ef_pm25_savanna"]["value"]                # 6.7   Andreae 2019
# Agricultural-residue burning has no EF in the verified parameter set; the savanna
# value stands in for it [J]. GFED's AGRI share in Equatorial Asia is ~1% of carbon.
EF_AGRI = EF_SAVA

# Source regions. Province names are geoBoundaries shapeName values.
REGIONS = [
    # id,               label,                      island,               provinces
    ("kalbar",  "West Kalimantan",          "kalimantan", ["West Kalimantan"]),
    ("kalteng", "Central Kalimantan",       "kalimantan", ["Central Kalimantan"]),
    ("kalsel",  "South Kalimantan",         "kalimantan", ["South Kalimantan"]),
    ("kaltim",  "East & North Kalimantan",  "kalimantan", ["East Kalimantan", "North Kalimantan"]),
    ("riau",    "Riau",                     "sumatra",    ["Riau"]),
    ("jambi",   "Jambi",                    "sumatra",    ["Jambi"]),
    ("sumsel",  "South Sumatra",            "sumatra",    ["South Sumatra"]),
    ("babel",   "Bangka-Belitung",          "sumatra",    ["Bangka-Belitung Islands"]),
    ("lampung", "Lampung",                  "sumatra",    ["Lampung"]),
    ("sum_n",   "North & West Sumatra, Aceh, Bengkulu", "sumatra",
     ["North Sumatra", "West Sumatra", "Aceh", "Bengkulu"]),
    ("kepri",   "Riau Islands",             "sumatra",    ["Riau Islands"]),
    ("pen_my",  "Peninsular Malaysia",      "peninsular_malaysia",
     ["Selangor", "Johor", "Kuala Lumpur", "Malacca", "Negeri Sembilan", "Kelantan",
      "Putrajaya", "Terengganu", "Pahang", "Kedah", "Perlis", "Perak", "Penang"]),
    ("sarawak", "Sarawak",                  "malaysian_borneo", ["Sarawak"]),
    ("sabah",   "Sabah",                    "malaysian_borneo", ["Sabah", "Labuan"]),
    ("java",    "Java and other",           "other",
     ["Banten", "West Java", "Central Java", "East Java", "Jakarta Special Capital Region",
      "Special Region of Yogyakarta", "Bali"]),
]
OTHER = len(REGIONS)        # cells outside every polygon (sea, Sulawesi, ...)


def _polys():
    out = {}
    for c in ("IDN", "MYS"):
        gj = json.loads((RAW / "geo" / f"{c}_ADM1.geojson").read_text())
        for f in gj["features"]:
            g = f["geometry"]
            rings = ([p[0] for p in g["coordinates"]] if g["type"] == "MultiPolygon"
                     else [g["coordinates"][0]])
            out[f["properties"]["shapeName"]] = [np.asarray(r, float) for r in rings]
    return out


def _inside(px, py, ring):
    x, y = ring[:, 0], ring[:, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    c = ((y > py) != (y2 > py)) & (px < (x2 - x) * (py - y) / np.where(y2 != y, y2 - y, 1e-12) + x)
    return bool(c.sum() % 2)


def _region_of(lon, lat, polys):
    pts = [(lon, lat), (lon - .08, lat - .08), (lon + .08, lat - .08),
           (lon - .08, lat + .08), (lon + .08, lat + .08)]
    for k, (_, _, _, provs) in enumerate(REGIONS):
        for pname in provs:
            for ring in polys.get(pname, []):
                if (ring[:, 0].min() - .2 <= lon <= ring[:, 0].max() + .2
                        and ring[:, 1].min() - .2 <= lat <= ring[:, 1].max() + .2):
                    if any(_inside(x, y, ring) for x, y in pts):
                        return k
    return OTHER


def _file(y):
    p = RAW / "gfed4" / f"GFED4.1s_{y}.hdf5"
    return p if p.exists() else RAW / "gfed4" / f"GFED4.1s_{y}_beta.hdf5"


def main(years=YEARS):
    f0 = h5py.File(_file(2015))
    lat = f0["lat"][:, 0]
    lon = f0["lon"][0]
    rows = np.where((lat >= LAT[0]) & (lat <= LAT[1]))[0]
    cols = np.where((lon >= LON[0]) & (lon <= LON[1]))[0]
    sub = np.ix_(rows, cols)
    area = f0["ancill/grid_cell_area"][:][sub].astype(np.float64)          # m2
    glat = lat[rows]
    glon = lon[cols]
    f0.close()
    polys = _polys()
    reg = np.full((len(rows), len(cols)), OTHER, np.int16)
    for j, la in enumerate(glat):
        for i, lo in enumerate(glon):
            reg[j, i] = _region_of(float(lo), float(la), polys)
    print("regions assigned", {REGIONS[k][0] if k < OTHER else "other": int((reg == k).sum())
                               for k in range(OTHER + 1)})

    # season days (same calendar positions every year; Feb 29 irrelevant)
    n_days = (SEASON[1] - SEASON[0]).days + 1
    per_year = {}
    annual = {}
    for y in years:
        f = h5py.File(_file(y))
        d0 = date(y, SEASON[0].month, SEASON[0].day)
        pm_peat = np.zeros((n_days, len(rows), len(cols)), np.float32)
        pm_np = np.zeros_like(pm_peat)
        ba_d = np.zeros_like(pm_peat)
        diurnal = np.zeros((7, 8, len(rows), len(cols)), np.float32)   # month 6..12
        ann = {"dm_peat": np.zeros(OTHER + 1), "dm_total": np.zeros(OTHER + 1),
               "pm": np.zeros(OTHER + 1), "ba_ha": np.zeros(OTHER + 1),
               "ba_peatcell_ha": np.zeros(OTHER + 1)}
        has_ba = "burned_area" in f
        daily_ok = True
        for m in range(1, 13):
            g = f[f"emissions/{m:02d}"]
            dm = g["DM"][:][sub].astype(np.float64) * area                       # kg DM
            part = {k: g[f"partitioning/DM_{k}"][:][sub].astype(np.float64)
                    for k in ("PEAT", "DEFO", "SAVA", "AGRI", "TEMF", "BORF")}
            dmp = dm * part["PEAT"]
            pmn = dm * ((part["DEFO"] + part["TEMF"] + part["BORF"]) * EF_TROP
                        + part["SAVA"] * EF_SAVA + part["AGRI"] * EF_AGRI) / 1000.0
            pmp = dmp * EF_PEAT / 1000.0
            ba = (f[f"burned_area/{m:02d}/burned_fraction"][:][sub].astype(np.float64)
                  * area / 1e4) if has_ba else None
            for k in range(OTHER + 1):
                sel = reg == k
                ann["dm_peat"][k] += dmp[sel].sum()
                ann["dm_total"][k] += dm[sel].sum()
                ann["pm"][k] += (pmp[sel] + pmn[sel]).sum()
                if ba is not None:
                    ann["ba_ha"][k] += ba[sel].sum()
                    ann["ba_peatcell_ha"][k] += ba[sel & (part["PEAT"] >= 0.5)].sum()
            if 6 <= m <= 12:
                dc = g["diurnal_cycle"] if "diurnal_cycle" in g else None
                keys = ["UTC_0-3h", "UTC_3-6h", "UTC_6-9h", "UTC_9-12h", "UTC_12-15h",
                        "UTC_15-18h", "UTC_18-21h", "UTC_21-24h"]
                for q, kk in enumerate(keys):
                    diurnal[m - 6, q] = dc[kk][:][sub] if dc is not None else 0.125
                ndm = (date(y + (m == 12), m % 12 + 1, 1) - date(y, m, 1)).days
                for dd in range(1, ndm + 1):
                    day = date(y, m, dd)
                    idx = (day - d0).days
                    if idx < 0 or idx >= n_days:
                        continue
                    if "daily_fraction" in g:
                        fr = g[f"daily_fraction/day_{dd}"][:][sub].astype(np.float64)
                    else:   # pre-2003: no daily information; spread evenly [J]
                        fr = 1.0 / ndm
                        daily_ok = False
                    pm_peat[idx] = pmp * fr
                    pm_np[idx] = pmn * fr
                    if ba is not None:
                        ba_d[idx] = ba * fr
        f.close()
        per_year[y] = (pm_peat, pm_np, ba_d, diurnal, has_ba)
        annual[y] = {k: v.tolist() for k, v in ann.items()}
        annual[y]["has_ba"] = has_ba
        annual[y]["daily_resolved"] = daily_ok
        print(y, "season PM Tg", round(float(pm_peat.sum() + pm_np.sum()) / 1e9, 2),
              "Sep-Oct", round(float((pm_peat[72:133].sum() + pm_np[72:133].sum())) / 1e9, 2),
              flush=True)

    # active cells: any emission in any season
    tot = sum(v[0].sum(axis=0) + v[1].sum(axis=0) for v in per_year.values())
    act = np.argwhere(tot > 0)
    print("active cells", len(act), "of", tot.size)
    aj, ai = act[:, 0], act[:, 1]
    peat_share = np.zeros(len(act))
    sp = sum(v[0].sum(axis=0) for v in per_year.values())[aj, ai]
    peat_share = sp / np.maximum(tot[aj, ai], 1e-9)
    arrays = {
        "lat": glat[aj].astype(np.float32), "lon": glon[ai].astype(np.float32),
        "region": reg[aj, ai].astype(np.int8),
        "area_m2": area[aj, ai].astype(np.float32),
        "peat_share": peat_share.astype(np.float32),
        "years": np.array(years, np.int16),
        "season_start_mmdd": np.array([SEASON[0].month, SEASON[0].day], np.int8),
        "n_days": np.array(n_days, np.int16),
        "region_ids": np.array([r[0] for r in REGIONS] + ["other"]),
        "region_island": np.array([r[2] for r in REGIONS] + ["other"]),
        "ef_peat": np.array(EF_PEAT), "ef_trop": np.array(EF_TROP), "ef_sava": np.array(EF_SAVA),
    }
    arrays["daily_resolved"] = np.array([bool(annual[y]["daily_resolved"]) for y in years])
    for y, (pp, pn, bd, dc, has_ba) in per_year.items():
        # kg/day, float32; most entries are zero so compression is effective
        arrays[f"pm_peat_{y}"] = pp[:, aj, ai].T.astype(np.float32)
        arrays[f"pm_nonpeat_{y}"] = pn[:, aj, ai].T.astype(np.float32)
        if has_ba:
            arrays[f"ba_ha_{y}"] = bd[:, aj, ai].T.astype(np.float16)
        arrays[f"diurnal_{y}"] = np.clip(dc[:, :, aj, ai] * 255.0 / 1.0, 0, 255).astype(np.uint8)
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / "emissions_gfed4s.npz", **arrays)
    print("wrote", OUT / "emissions_gfed4s.npz",
          round((OUT / "emissions_gfed4s.npz").stat().st_size / 1e6, 2), "MB")
    (RAW / "gfed_annual_by_region.json").write_text(json.dumps(
        {"regions": [r[0] for r in REGIONS] + ["other"], "annual": annual}))


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]] or YEARS)
