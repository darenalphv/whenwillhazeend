"""Build-time, leak-free odds history for the current season.

Writes app/data/forecast_history.json: one point per day from 1 Aug of the
season year to yesterday (UTC), each computed by
`app.model.forecast.compute_history_point` -- weather observed up to that day
only, ONI/DMI as published by that day, no weather forecast, fixed seed.
The /api/forecast/history route serves this file and computes later days on
demand (capped per request), so a stale build only costs a few requests.

Usage: .venv/bin/python scripts/model/season/build_forecast_history.py [--through YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone, date

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.model import forecast  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--through")
    a = ap.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    through = date.fromisoformat(a.through) if a.through else today - timedelta(days=1)
    year = forecast.season_year(through)
    t0 = time.process_time()
    out = forecast.build_history(year, through)
    cpu = time.process_time() - t0
    out["build_cpu_seconds"] = round(cpu, 2)
    (ROOT / "app/data/forecast_history.json").write_text(json.dumps(out, indent=1))
    n = len(out["series"])
    print(f"{n} points {out['series'][0]['as_of']}..{out['series'][-1]['as_of']}, "
          f"{cpu:.1f} s CPU ({cpu / max(n, 1) * 1000:.0f} ms/point)")


if __name__ == "__main__":
    main()
