"""Season-to-date FIRMS fire counts for the current year (needs a MAP_KEY).

The open FIRMS yearly country archive (fetch_firms.py) is published only after
a year ends. Season-to-date counts -- needed to *resolve* the odds markets --
come from the FIRMS area API, which requires a free MAP_KEY. This script never
creates or requests a key: it runs only if the owner has set one in the
standard place, the ``FIRMS_MAP_KEY`` environment variable.

Writes app/data/fire_counts_current.json, read by
`app.model.forecast.resolution_status`:
    {"year", "dates": [...], "count": [...], "source", "retrieved", "caveat"}

Fidelity vs the archive used for the labels: MODIS standard-processing (SP)
data are used where available and near-real-time (NRT) data after that. NRT
files carry no `type` column, so static/industrial sources cannot be filtered
out of the most recent ~2-3 months; inside this box that is a small, roughly
constant count that a 10%-of-maximum threshold mostly absorbs.

Usage: FIRMS_MAP_KEY=... .venv/bin/python scripts/model/season/fetch_firms_nrt.py [--year 2026]
UNTESTED against the live API (no key was available when it was written).
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

ROOT = pathlib.Path(__file__).resolve().parents[3]
API = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{key}/{src}/110,-4,117,0/{n}/{day}"


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=date.today().year)
    a = ap.parse_args(argv)
    key = os.environ.get("FIRMS_MAP_KEY")
    if not key:
        print("FIRMS_MAP_KEY is not set; nothing to do (see module docstring).", file=sys.stderr)
        return 2
    start, end = date(a.year, 5, 1), min(date.today(), date(a.year + 1, 3, 31))
    seen, counts = set(), defaultdict(int)
    d = start
    while d <= end:
        n = min(10, (end - d).days + 1)
        for src in ("MODIS_SP", "MODIS_NRT"):
            try:
                txt = _get(API.format(key=key, src=src, n=n, day=d.isoformat()))
            except Exception as exc:  # noqa: BLE001
                print(f"{src} {d}: {exc}", file=sys.stderr)
                continue
            for row in csv.DictReader(io.StringIO(txt)):
                if row.get("type", "0").strip() not in ("0", ""):
                    continue
                k = (row.get("latitude"), row.get("longitude"), row.get("acq_date"),
                     row.get("acq_time"), row.get("satellite"))
                if k in seen:
                    continue
                seen.add(k)
                counts[row.get("acq_date", "")[:10]] += 1
        d += timedelta(days=n)
    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    out = {"year": a.year, "dates": days, "count": [counts.get(x, 0) for x in days],
           "source": "NASA FIRMS area API, MODIS_SP then MODIS_NRT, box 4S-0, 110-117E",
           "retrieved": date.today().isoformat(),
           "caveat": "NRT rows have no type column; static sources are not filtered for them."}
    (ROOT / "app/data/fire_counts_current.json").write_text(json.dumps(out))
    print(f"wrote {len(days)} days, total {sum(out['count'])} detections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
