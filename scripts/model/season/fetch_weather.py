"""Pull the observed daily weather the season model is calibrated on.

Open-Meteo Historical Weather API (ERA5 reanalysis, no key, CC-BY 4.0), one
multi-location request for the peat-belt reference points in
`app.model.meteo.PEAT_BELT_POINTS`, 1981-01-01 to today-6 d, then reduced to
the areal mean that the live model also uses (`meteo.fetch_areal_weather`).

Writes:
  app/model/calibration_weather.json   areal-mean daily rain_mm / tmax_c (shipped)
  scripts/model/season/data/weather_points.json   per-point series (offline only)

Usage: .venv/bin/python scripts/model/season/fetch_weather.py [--start 1981-01-01]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from datetime import date, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.model import meteo  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1981-01-01")
    ap.add_argument("--end", default=(date.today() - timedelta(days=meteo.ARCHIVE_LAG_DAYS)).isoformat())
    a = ap.parse_args(argv)
    pts = meteo.PEAT_BELT_POINTS
    per_point = meteo.pull_multi(meteo.ARCHIVE_URL, pts, meteo._d(a.start), meteo._d(a.end),
                                 timeout=600.0)
    dates = sorted(set.intersection(*(set(p) for p in per_point)))
    rain = [round(sum(p[d]["rain_mm"] for p in per_point) / len(pts), 2) for d in dates]
    tmax = [round(sum(p[d]["tmax_c"] for p in per_point) / len(pts), 2) for d in dates]
    out = {
        "source": "open-meteo archive (ERA5), https://archive-api.open-meteo.com/v1/archive",
        "weather_source": "observed",
        "aggregation": "arithmetic mean of the daily values at the points below",
        "points": [{"name": n, "lat": la, "lon": lo} for n, la, lo in pts],
        "lat": round(sum(p[1] for p in pts) / len(pts), 3),
        "lon": round(sum(p[2] for p in pts) / len(pts), 3),
        "retrieved": date.today().isoformat(),
        "dates": dates, "tmax_c": tmax, "rain_mm": rain,
    }
    (ROOT / "app/model/calibration_weather.json").write_text(json.dumps(out, separators=(",", ":")))
    pp = {n: {"lat": la, "lon": lo,
              "rain_mm": [round(per_point[i][d]["rain_mm"], 2) for d in dates],
              "tmax_c": [round(per_point[i][d]["tmax_c"], 2) for d in dates]}
          for i, (n, la, lo) in enumerate(pts)}
    (ROOT / "scripts/model/season/data/weather_points.json").write_text(
        json.dumps({"dates": dates, "points": pp}, separators=(",", ":")))
    print(f"{len(dates)} days {dates[0]}..{dates[-1]}")


if __name__ == "__main__":
    main()
