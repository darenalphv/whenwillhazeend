"""Download NASA FIRMS yearly country archives and reduce them to daily counts.

Spec: research/MODEL_SPEC.md section 2.4, item S1.

Source (open, no login, no MAP_KEY):
    https://firms.modaps.eosdis.nasa.gov/data/country/<sensor>/<year>/<sensor>_<year>_Indonesia.csv
These are the FIRMS "yearly country summaries" -- the standard-processing
(not near-real-time) MODIS Collection 6.1 and VIIRS 375 m archives, clipped to
each country. Available 2000..(last complete year). The current year is not
published until it ends; see `app/model/fire_labels.py` (MAP_KEY path) for that.

Output: scripts/model/season/data/firms_daily_<sensor>.json
    {"source", "region": {"name","lat":[s,n],"lon":[w,e]}, "filters",
     "years": {"2015": {"dates": [...], "count": [...], "count_terra": [...]}}}
Raw CSVs are cached in --cache (default ~/.cache/whenwillhazeend/firms); they
are large (up to ~100 MB/yr for VIIRS) and are never committed.

Filters applied (all [J], documented in the output):
  * inside the box (Field et al. 2016 peat-belt boxes);
  * `type == 0` ("presumed vegetation fire") where the column exists --
    drops static industrial sources, volcanoes and offshore detections;
  * no confidence filter (MODIS low-confidence detections are kept; a relative
    10%-of-season-max threshold is insensitive to a constant fraction).
Usage:
    .venv/bin/python scripts/model/season/fetch_firms.py --sensor modis --years 2001-2025
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import pathlib
import sys
import urllib.request
from collections import defaultdict
from datetime import date, timedelta

BASE = "https://firms.modaps.eosdis.nasa.gov/data/country/{s}/{y}/{s}_{y}_Indonesia.csv"
HERE = pathlib.Path(__file__).resolve().parent
OUT_DIR = HERE / "data"

#: Field et al. 2016 (PNAS 113:9204) analysis boxes, quoted in MODEL_SPEC 2.2.
BOXES = {
    "kalimantan": {"name": "South-central Kalimantan peat belt (Field et al. 2016)",
                   "lat": [-4.0, 0.0], "lon": [110.0, 117.0]},
    "sumatra": {"name": "Southern Sumatra (Field et al. 2016)",
                "lat": [-6.0, 0.0], "lon": [99.0, 106.0]},
}
SENSOR_PATH = {"modis": "modis", "viirs": "viirs-snpp"}


def _download(url: str, dest: pathlib.Path) -> pathlib.Path:
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "whenwillhazeend-research/1.0"})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as fh:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            fh.write(chunk)
    tmp.rename(dest)
    return dest


def daily_counts(path: pathlib.Path, year: int, boxes=BOXES) -> dict:
    """Per-box daily detection counts for one yearly CSV."""
    counts = {b: defaultdict(int) for b in boxes}
    terra = {b: defaultdict(int) for b in boxes}
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        rd = csv.DictReader(fh)
        has_type = "type" in (rd.fieldnames or [])
        for row in rd:
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (KeyError, ValueError):
                continue
            if has_type and row.get("type", "0").strip() not in ("0", ""):
                continue
            d = row.get("acq_date", "")[:10]
            for b, spec in boxes.items():
                if spec["lat"][0] <= lat <= spec["lat"][1] and spec["lon"][0] <= lon <= spec["lon"][1]:
                    counts[b][d] += 1
                    if row.get("satellite", "").strip() in ("Terra", "T"):
                        terra[b][d] += 1
    out = {}
    d0, d1 = date(year, 1, 1), date(year, 12, 31)
    days = [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    for b in boxes:
        out[b] = {"dates": days,
                  "count": [counts[b].get(d, 0) for d in days],
                  "count_terra": [terra[b].get(d, 0) for d in days]}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sensor", choices=sorted(SENSOR_PATH), default="modis")
    ap.add_argument("--years", default="2001-2025")
    ap.add_argument("--cache", default=os.path.expanduser("~/.cache/whenwillhazeend/firms"))
    a = ap.parse_args(argv)
    y0, y1 = (int(x) for x in a.years.split("-"))
    s = SENSOR_PATH[a.sensor]
    out_path = OUT_DIR / f"firms_daily_{a.sensor}.json"
    result = {"years": {}}
    if out_path.exists():
        result = json.loads(out_path.read_text())
    for y in range(y0, y1 + 1):
        url = BASE.format(s=s, y=y)
        try:
            p = _download(url, pathlib.Path(a.cache) / s / f"{s}_{y}_Indonesia.csv")
        except Exception as exc:  # noqa: BLE001
            print(f"{y}: download failed ({exc})", file=sys.stderr)
            continue
        result["years"][str(y)] = daily_counts(p, y)
        k = result["years"][str(y)]["kalimantan"]["count"]
        print(f"{y}: kalimantan box total {sum(k)}, max day {max(k)}", flush=True)
    result.update({
        "source": f"NASA FIRMS yearly country archive ({s}, Indonesia), {BASE.format(s=s, y='<year>')}",
        "sensor": a.sensor,
        "boxes": BOXES,
        "filters": "inside box; type==0 (presumed vegetation fire) where present; all confidences",
        "retrieved": date.today().isoformat(),
    })
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, separators=(",", ":")))
    print("wrote", out_path)


if __name__ == "__main__":
    main()
