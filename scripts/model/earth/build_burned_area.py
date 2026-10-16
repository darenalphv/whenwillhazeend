"""B1-B3: the model-side satellite-consistent burned-area series for Kalimantan/Sumatra,
and the stock model's base rates and ENSO/IOD multipliers fitted to it.

Input:  scripts/model/earth/_raw/gfed_annual_by_region.json (from build_emissions.py)
Output: app/data/earth/burned_area.json

Method (MODEL_SPEC 3.4 B2, with the open-data substitution recorded):
* The spec wants GFED5.1 gridded burned area. Its gridded files are on an SFTP server
  this pipeline does not log in to, and GFED5's public summary tables carry emissions,
  not burned area. So the per-year, per-province SHAPE comes from GFED4.1s gridded burned
  area (MCD64A1 C5.1 + small fires; clipped to province polygons so the Kalimantan share
  is measured each year), and the LEVEL is moved onto GFED5 / MCD64A1 with the Equatorial
  Asia ratios of Chen et al. 2023 (ESSD 15:5227):
      GFED5 / GFED4s  = 3.95 / 2.05 = 1.93  -> central  [D]
      MCD64A1 / GFED4s = 1.43 / 2.05 = 0.70  -> low      [D]
      GFED5 / MCD64A1 = 2.6, applied to GFED4s  -> high  [D/J: an upper bound]
* GFED4.1s 2017+ is active-fire based and has no burned area. For those years burned
  area is estimated from GFED4.1s dry matter with a two-coefficient fit (peat and
  non-peat DM) over 2001-2016 [D], and flagged basis="dm_regression".
* Peat share (B3): burned area in 0.25-deg cells where peat is >= 50% of the cell's dry
  matter. That is a cell-level proxy, not a peat-map intersection [J]; it overstates peat
  area burned in mixed cells.
"""
from __future__ import annotations

import json
import pathlib

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SRC = HERE / "_raw" / "gfed_annual_by_region.json"
OUT = ROOT / "app" / "data" / "earth" / "burned_area.json"
P = json.loads((ROOT / "app" / "data" / "earth" / "parameters.json").read_text())["parameters"]

KAL = ["kalbar", "kalteng", "kalsel", "kaltim"]
SUM = ["riau", "jambi", "sumsel", "babel", "lampung", "sum_n", "kepri"]
R_CENTRAL = 3.95 / 2.05     # Chen 2023 GFED5 vs GFED4s, EQAS
R_LOW = 1.43 / 2.05         # Chen 2023 MCD64A1 C6 vs GFED4s, EQAS
R_HIGH = 3.71 / 1.43        # Chen 2023 GFED5 vs MCD64A1 (=2.6) applied to GFED4s


def main():
    d = json.loads(SRC.read_text())
    reg = d["regions"]
    ann = {int(y): v for y, v in d["annual"].items()}

    def tot(y, key, names):
        return sum(ann[y][key][reg.index(n)] for n in names)

    # DM -> BA regression over 2001-2016 (MODIS era), per island
    fits = {}
    for isl, names in (("kalimantan", KAL), ("sumatra", SUM)):
        X, Yv = [], []
        for y in range(2001, 2017):
            dmp = tot(y, "dm_peat", names) / 1e9          # Tg
            dmn = (tot(y, "dm_total", names) - tot(y, "dm_peat", names)) / 1e9
            X.append([dmp, dmn])
            Yv.append(tot(y, "ba_ha", names) / 1e6)       # Mha
        X, Yv = np.array(X), np.array(Yv)
        coef, *_ = np.linalg.lstsq(X, Yv, rcond=None)
        coef = np.maximum(coef, 0.0)
        pred = X @ coef
        r2 = 1 - ((Yv - pred) ** 2).sum() / ((Yv - Yv.mean()) ** 2).sum()
        fits[isl] = {"mha_per_tg_peat_dm": round(float(coef[0]), 5),
                     "mha_per_tg_nonpeat_dm": round(float(coef[1]), 5),
                     "r2_2001_2016": round(float(r2), 3)}

    series = {"kalimantan": {}, "sumatra": {}}
    for y in sorted(ann):
        for isl, names in (("kalimantan", KAL), ("sumatra", SUM)):
            if ann[y]["has_ba"]:
                g4 = tot(y, "ba_ha", names) / 1e6
                gp = tot(y, "ba_peatcell_ha", names) / 1e6
                basis = "gfed4s_burned_area"
            else:
                dmp = tot(y, "dm_peat", names) / 1e9
                dmn = (tot(y, "dm_total", names) - tot(y, "dm_peat", names)) / 1e9
                f = fits[isl]
                g4 = f["mha_per_tg_peat_dm"] * dmp + f["mha_per_tg_nonpeat_dm"] * dmn
                gp = f["mha_per_tg_peat_dm"] * dmp
                basis = "dm_regression"
            series[isl][str(y)] = {
                "gfed4s_mha": round(g4, 4),
                "low": round(g4 * R_LOW, 4),
                "central": round(g4 * R_CENTRAL, 4),
                "high": round(g4 * R_HIGH, 4),
                "peat_cell_mha_gfed4s": round(gp, 4),
                "peat_share": round(gp / g4, 3) if g4 > 0 else None,
                "dm_tg": round(tot(y, "dm_total", names) / 1e9, 2),
                "pm25_tg": round(tot(y, "pm", names) / 1e9, 3),
                "basis": basis,
            }
    share = {}
    for y in sorted(ann):
        k = series["kalimantan"][str(y)]["gfed4s_mha"]
        s = series["sumatra"][str(y)]["gfed4s_mha"]
        share[str(y)] = round(k / (k + s), 3) if k + s > 0 else None

    # base rates for the stock model (2001-2016 means, MODIS-era burned area)
    yrs = [str(y) for y in range(2001, 2017)]
    kal = series["kalimantan"]
    peat_cell = float(np.mean([kal[y]["peat_cell_mha_gfed4s"] for y in yrs]))
    nonpeat = float(np.mean([kal[y]["gfed4s_mha"] - kal[y]["peat_cell_mha_gfed4s"] for y in yrs]))
    # Kalimantan share of Equatorial-Asia-domain peat dry matter, 2001-2016
    kp = sum(tot(int(y), "dm_peat", KAL) for y in yrs)
    allp = sum(sum(ann[int(y)]["dm_peat"]) for y in yrs)
    kal_peat_share = kp / allp
    peat_area = P["kalimantan_peat_area_moa2011"]["value"]
    gfed5_peat_ba = 0.56   # Chen et al. 2023: Equatorial Asia peat burned area, Mha/yr [V]
    base = {
        "nonpeat_mha_per_yr": {
            "low": round(nonpeat * R_LOW, 4), "central": round(nonpeat * R_CENTRAL, 4),
            "high": round(nonpeat * R_HIGH, 4), "gfed4s": round(nonpeat, 4),
            "period": "2001-2016",
            "definition": "Kalimantan burned area outside peat-dominated cells",
            "status": "derived",
        },
        "peat_burned_fraction_per_yr": {
            "low": P["kalimantan_peat_burned_fraction_per_yr"]["value"],
            "high": round(gfed5_peat_ba * kal_peat_share / peat_area, 4),
            "gfed4s_peat_cells": round(peat_cell / peat_area, 4),
            "low_source": "Vetrita & Cochrane 2020, MODIS C6 2001-2018 (a lower bound: MODIS misses small and smouldering fires)",
            "high_source": (f"GFED5 Equatorial Asia peat burned area 0.56 Mha/yr (Chen et al. 2023) "
                            f"x Kalimantan share of GFED4.1s peat dry matter 2001-2016 "
                            f"({kal_peat_share:.2f}) / MoA 2011 peat area {peat_area} Mha [D]"),
            "status": "derived",
        },
        "official_floor_klhk_mha_per_yr": {
            "value": 0.198,
            "note": "The v1 A_base: KLHK national x 0.29 in ENSO-neutral years. Kept only as the official floor for comparison.",
        },
    }
    out = {
        "generated_by": "scripts/model/earth/build_burned_area.py",
        "source": ("GFED4.1s gridded burned area and dry matter (van der Werf et al. 2017, "
                   "ESSD 9:697), clipped to geoBoundaries province polygons; level moved to "
                   "MCD64A1 / GFED5 with Chen et al. 2023 Equatorial-Asia ratios."),
        "licence": "GFED: no licence stated on the download pages (recorded per MODEL_SPEC B2). geoBoundaries: CC BY 4.0.",
        "ratios": {"central_gfed5_over_gfed4s": round(R_CENTRAL, 3),
                   "low_mcd64_over_gfed4s": round(R_LOW, 3),
                   "high_gfed5_over_mcd64": round(R_HIGH, 3),
                   "source": "Chen et al. 2023, ESSD 15:5227 [V]; ratios [D]"},
        "dm_regression_2017_2023": fits,
        "units": "Mha",
        "series": series,
        "kalimantan_share_of_kal_plus_sumatra": share,
        "base_rates": base,
        "indonesia_validation": {
            "2015": {"official": 2.61, "satellite": [4.60]},
            "2019": {"official": 1.65, "satellite": [2.03, 2.04, 2.09, 2.2, 2.64, 3.11, 3.8]},
            "2023": {"official": 1.16, "satellite": [1.11, 1.70]},
            "source": "MODEL_SPEC 3.4 B4 (KLHK SiPongi; Lohberger 2018; Gaveau 2021, 2026; Chen 2023)",
        },
        "caveats": [
            "GFED4.1s is built on MODIS burned area, which misses much of the small and smouldering burning on Indonesian peat; the GFED5 rescaling corrects the level for the region as a whole, not year by year.",
            "Peat share is a cell-level proxy (0.25 deg cells where peat is at least half of the dry matter burned), not an intersection with a peat map; published peat shares for 2019 run from 16% to 40% depending on the map and product.",
            "2017-2023 burned area is estimated from GFED4.1s active-fire-based dry matter, not mapped.",
        ],
    }
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print("wrote", OUT)
    print(json.dumps(base, indent=1))
    print("fits", fits)
    for y in ("1997", "2006", "2015", "2019", "2023"):
        print(y, series["kalimantan"][y], "share", share[y])


if __name__ == "__main__":
    main()
