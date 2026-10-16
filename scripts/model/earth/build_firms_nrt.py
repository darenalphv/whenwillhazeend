"""E2 fallback path (MODEL_SPEC 1.4): near-real-time daily emissions from NASA FIRMS FRP.

Input (open, no key): FIRMS 7-day VIIRS CSVs for South-East Asia (S-NPP, NOAA-20,
NOAA-21), https://firms.modaps.eosdis.nasa.gov/data/active_fire/... saved under
scripts/model/earth/_raw/firms/. NASA LANCE FIRMS NRT data; cite NASA FIRMS.

Method [J where marked]:
  per 0.25-deg cell, day, satellite and pass (day/night): sum of FRP (MW)
  daily mean FRP = mean over the satellite-passes of that day (zeros where a pass saw
      nothing in that cell) [J: two VIIRS passes per satellite a day, ~01:30 and
      13:30 local; no cloud or smoke gap-filling, which GFAS would do]
  FRE (MJ/day) = mean FRP x 86,400 s
  DM = FRE x beta, beta = 5.87 kg/MJ peat, 0.96 tropical forest (Kaiser et al. 2012
      Table 2; calibrated against MODIS FRP, applied to VIIRS here [J])
  peat fraction of a cell = GFED4.1s 1997-2023 peat share of that cell's emissions [J]
  PM2.5 = DM_peat x 22.3 + DM_other x 8.3 g/kg (Kiely 2019; Andreae 2019)

The 7-day file starts mid-episode. So that a model run has smoke already in the air,
the first FIRMS day is repeated for the 4 days before it [J], flagged in the output.

Output: app/data/earth/emissions_nrt.npz
"""
from __future__ import annotations

import csv
import json
import pathlib
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from build_emissions import LAT, LON, OTHER, REGIONS, _polys, _region_of  # noqa: E402

RAW = HERE / "_raw" / "firms"
OUT = ROOT / "app" / "data" / "earth"
P = json.loads((OUT / "parameters.json").read_text())["parameters"]
BETA_PEAT = P["frp_beta_peat"]["value"]
BETA_FOREST = P["frp_beta_tropical_forest"]["value"]
EF_PEAT = P["ef_pm25_peat_central"]["value"]
EF_TROP = P["ef_pm25_tropical_forest"]["value"]
FILES = ["SUOMI_VIIRS_C2_SouthEast_Asia_7d.csv", "J1_VIIRS_C2_SouthEast_Asia_7d.csv",
         "J2_VIIRS_C2_SouthEast_Asia_7d.csv"]
BACKFILL_DAYS = 4
D = 0.25


def main():
    gf = np.load(OUT / "emissions_gfed4s.npz")
    peat_map = {(round(float(a), 3), round(float(b), 3)): float(s)
                for a, b, s in zip(gf["lat"], gf["lon"], gf["peat_share"])}
    frp = defaultdict(float)                 # (cell, day) -> sum over passes of FRP
    passes = defaultdict(set)                # day -> {(sat, daynight)}
    days = set()
    for fn in FILES:
        p = RAW / fn
        if not p.exists():
            continue
        with p.open() as fh:
            for row in csv.DictReader(fh):
                la, lo = float(row["latitude"]), float(row["longitude"])
                if not (LAT[0] <= la <= LAT[1] and LON[0] <= lo <= LON[1]):
                    continue
                d = row["acq_date"]
                key = (row["satellite"], row["daynight"])
                passes[d].add(key)
                days.add(d)
                cy = np.floor(la / D) * D + D / 2
                cx = np.floor(lo / D) * D + D / 2
                frp[((round(cy, 3), round(cx, 3)), d)] += float(row["frp"] or 0.0)
    days = sorted(days)
    # drop a trailing partial day (fewer than 4 satellite-passes)
    days = [d for d in days if len(passes[d]) >= 4]
    cells = sorted({c for (c, d) in frp if d in days})
    polys = _polys()
    lat = np.array([c[0] for c in cells])
    lon = np.array([c[1] for c in cells])
    region = np.array([_region_of(float(b), float(a), polys) for a, b in cells], np.int8)
    peat = np.array([peat_map.get(c, 0.0) for c in cells])
    n_back = BACKFILL_DAYS
    d0 = date.fromisoformat(days[0]) - timedelta(days=n_back)
    all_days = [d0 + timedelta(days=k) for k in range(n_back + len(days))]
    pm_peat = np.zeros((len(cells), len(all_days)), np.float32)
    pm_non = np.zeros_like(pm_peat)
    ci = {c: i for i, c in enumerate(cells)}
    for (c, d), v in frp.items():
        if d not in days:
            continue
        k = n_back + days.index(d)
        mean_frp_mw = v / len(passes[d])
        fre_mj = mean_frp_mw * 86400.0
        i = ci[c]
        dm_p = fre_mj * peat[i] * BETA_PEAT              # kg DM
        dm_n = fre_mj * (1 - peat[i]) * BETA_FOREST
        pm_peat[i, k] = dm_p * EF_PEAT / 1000.0          # kg PM2.5
        pm_non[i, k] = dm_n * EF_TROP / 1000.0
    for k in range(n_back):
        pm_peat[:, k] = pm_peat[:, n_back]
        pm_non[:, k] = pm_non[:, n_back]
    np.savez_compressed(
        OUT / "emissions_nrt.npz", lat=lat.astype(np.float32), lon=lon.astype(np.float32),
        region=region, peat_share=peat.astype(np.float32),
        days=np.array([d.isoformat() for d in all_days]),
        backfilled_days=np.array([d.isoformat() for d in all_days[:n_back]]),
        pm_peat=pm_peat, pm_nonpeat=pm_non,
        region_ids=np.array([r[0] for r in REGIONS] + ["other"]),
        region_island=np.array([r[2] for r in REGIONS] + ["other"]),
        source=np.array("NASA FIRMS VIIRS NRT (S-NPP, NOAA-20, NOAA-21) 7-day FRP -> FRE x "
                        "Kaiser 2012 beta x Kiely 2019 / Andreae 2019 EFs; peat share from "
                        "GFED4.1s; first day repeated 4 days back [J]"))
    isl = np.array([r[2] for r in REGIONS] + ["other"])[region]
    for k, d in enumerate(all_days):
        tot = pm_peat[:, k] + pm_non[:, k]
        print(d, {n: round(float(tot[isl == n].sum()) / 1e6, 1) for n in ("kalimantan", "sumatra")},
              "kt PM2.5/day", "(backfilled)" if k < n_back else "")
    print("wrote", OUT / "emissions_nrt.npz")


if __name__ == "__main__":
    main()
