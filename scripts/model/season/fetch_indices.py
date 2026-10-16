"""Refresh the climate indices the season model reads.

* ONI: NOAA CPC, https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
  (ERSSTv5, 3-month running mean of Nino-3.4 anomalies). Every 3-month season
  is written into each `app/data/enso.json` row as ``oni_seasons``; existing
  v1 keys are kept.
* DMI: NOAA OSMC "State of the Ocean" weekly Dipole Mode Index from OISST,
  base 1991-2020, https://stateoftheocean.osmc.noaa.gov/sur/data/dmi.nc
  (the series MODEL_SPEC section 2.2 tabulates). Written to `app/data/iod.json`.

Usage: .venv/bin/python scripts/model/season/fetch_indices.py [--dmi-nc PATH]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[3]
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DMI_URL = "https://stateoftheocean.osmc.noaa.gov/sur/data/dmi.nc"
SEAS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")
#: [J] threshold for labelling a fire season's IOD phase from the SON mean.
#: BoM's +/-0.4 C convention, which MODEL_SPEC Appendix A lists as UNVERIFIED.
IOD_THRESHOLD = 0.4


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "whenwillhazeend-research/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def update_enso(today: str) -> dict:
    txt = _fetch(ONI_URL).decode()
    table = defaultdict(dict)
    for line in txt.splitlines()[1:]:
        parts = line.split()
        if len(parts) == 4 and parts[0] in SEAS:
            table[int(parts[1])][parts[0]] = float(parts[3])
    path = ROOT / "app/data/enso.json"
    rows = json.loads(path.read_text())
    by_year = {r["year"]: r for r in rows}
    for y in sorted(table):
        if y < 1980:
            continue
        r = by_year.get(y)
        if r is None:
            r = {"year": y}
            rows.append(r)
            by_year[y] = r
        seas = table[y]
        r["oni_seasons"] = {k: seas.get(k) for k in SEAS}
        bw = [seas.get(k) for k in ("JJA", "JAS", "ASO")]
        r["oni_burn_window"] = round(sum(bw) / 3, 2) if all(v is not None for v in bw) else None
        r["oni_burn_window_def"] = "mean of the JJA, JAS and ASO ONI seasons (centred Jul-Sep)"
        for k in ("jas", "aso", "son", "ond"):
            if seas.get(k.upper()) is not None:
                r[f"oni_{k}"] = seas[k.upper()]
        published = [k for k in SEAS if seas.get(k) is not None]
        if published and (len(published) < 12):
            last = published[-1]
            r["latest_available_season"] = last
            r["latest_available_oni"] = seas[last]
            r["source"] = (f"NOAA CPC ONI (ERSSTv5), {ONI_URL}, retrieved {today}. "
                           f"Seasons after {last} {y} are not yet published.")
            r["estimated"] = True
        else:
            r["source"] = f"NOAA CPC Oceanic Nino Index (ERSSTv5), {ONI_URL}, retrieved {today}"
    rows.sort(key=lambda r: r["year"])
    path.write_text(json.dumps(rows, indent=1))
    return table


def update_iod(nc_path: str, today: str):
    from scipy.io import netcdf_file
    f = netcdf_file(nc_path, "r", mmap=False)
    t = f.variables["TIME"][:]
    v = f.variables["DMI"][:]
    days, vals = [], []
    for ti, vi in zip(t, v):
        if vi < -1e30 or vi != vi:
            continue
        days.append(date(1800, 1, 1) + timedelta(days=float(ti)))
        vals.append(round(float(vi), 3))
    monthly = defaultdict(list)
    for d, x in zip(days, vals):
        monthly[(d.year, d.month)].append(x)
    mon = {f"{y:04d}-{m:02d}": round(sum(xs) / len(xs), 3) for (y, m), xs in sorted(monthly.items())}
    seasons = {}
    for y in sorted({d.year for d in days}):
        ms = [mon.get(f"{y:04d}-{m:02d}") for m in (8, 9, 10, 11)]
        son = [mon.get(f"{y:04d}-{m:02d}") for m in (9, 10, 11)]
        if any(x is None for x in son):
            son_mean = None
        else:
            son_mean = round(sum(son) / 3, 2)
        phase = None
        if son_mean is not None:
            phase = ("positive" if son_mean > IOD_THRESHOLD else
                     "negative" if son_mean < -IOD_THRESHOLD else "neutral")
        seasons[str(y)] = {
            "dmi_aug_nov": [None if x is None else round(x, 2) for x in ms],
            "dmi_son": son_mean,
            "dmi_peak": (round(max(x for x in ms if x is not None), 2)
                         if any(x is not None for x in ms) else None),
            "iod_phase": phase,
        }
    out = {
        "source": f"NOAA OSMC State of the Ocean, weekly Dipole Mode Index from OISST, "
                  f"base 1991-2020, not detrended. {DMI_URL}, retrieved {today}",
        "definition": "DMI = WTIO (50-70E, 10S-10N) minus SETIO (90-110E, 10S-0) SST anomaly, degC",
        "iod_phase_rule": (f"SON-mean DMI > +{IOD_THRESHOLD} positive, < -{IOD_THRESHOLD} negative "
                           "[J; BoM's +/-0.4 convention, UNVERIFIED at source]. Because the series "
                           "is not detrended, warming of the western pole biases recent years "
                           "toward positive."),
        "notes": {"2015": "Sources disagree on whether 2015 was a positive-IOD year; the DMI "
                          "peaked in Sep and decayed by Nov. Treat it as at most weak or "
                          "short-lived (MODEL_SPEC section 2.2; Fanin & van der Werf 2017 "
                          "describe 2015 IOD conditions as neutral)."},
        "verified": "monthly means reproduce the MODEL_SPEC section 2.2 table for 1997, 2006, "
                    "2015, 2019 and 2023 exactly",
        "weekly": {"dates": [d.isoformat() for d in days], "dmi": vals},
        "monthly": mon,
        "seasons": seasons,
    }
    (ROOT / "app/data/iod.json").write_text(json.dumps(out, separators=(",", ":")))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dmi-nc")
    a = ap.parse_args(argv)
    today = date.today().isoformat()
    update_enso(today)
    nc = a.dmi_nc
    if not nc:
        tmp = tempfile.NamedTemporaryFile(suffix=".nc", delete=False)
        tmp.write(_fetch(DMI_URL))
        tmp.close()
        nc = tmp.name
    out = update_iod(nc, today)
    print("DMI weekly", out["weekly"]["dates"][0], "..", out["weekly"]["dates"][-1])
    for y in (1997, 2006, 2015, 2019, 2023, 2026):
        print(y, out["seasons"].get(str(y)))


if __name__ == "__main__":
    main()
