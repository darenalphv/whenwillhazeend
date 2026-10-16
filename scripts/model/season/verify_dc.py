"""Verify app.model.drought_code against the cffdrs reference data.

Downloads (no login) from the cffdrs R package repository, GPL-2:
  tests/testthat/data/DroughtCode.csv  -- 4,392 single-step cases
  tests/testthat/data/fwi_01.csv       -- a multi-day FWI system series (DC column)
and reports the maximum absolute error. Nothing is vendored into the repo.

Usage: .venv/bin/python scripts/model/season/verify_dc.py [--cache DIR]
Prints a JSON summary; calibrate.py copies it into hazard_fit.json.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from app.model.drought_code import dc_step  # noqa: E402

BASE = "https://raw.githubusercontent.com/cffdrs/cffdrs_r/main/"
FILES = {"single": "tests/testthat/data/DroughtCode.csv",
         "series": "tests/testthat/data/fwi_01.csv"}


def _get(path: str, cache: pathlib.Path | None) -> str:
    if cache:
        p = cache / pathlib.Path(path).name
        if p.exists():
            return p.read_text()
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        txt = r.read().decode()
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
        (cache / pathlib.Path(path).name).write_text(txt)
    return txt


def verify(cache=None) -> dict:
    rows = list(csv.DictReader(io.StringIO(_get(FILES["single"], cache))))
    worst, n_trop, worst_trop = 0.0, 0, 0.0
    for r in rows:
        got = dc_step(float(r["dc_yda"]), float(r["temp"]), float(r["prec"]),
                      float(r["lat"]), int(r["mon"]), r["lat.adjust"].upper() == "TRUE")
        err = abs(got - float(r["DroughtCode"]))
        worst = max(worst, err)
        if -20 < float(r["lat"]) <= 20 and r["lat.adjust"].upper() == "TRUE":
            n_trop += 1
            worst_trop = max(worst_trop, err)
    ser = list(csv.DictReader(io.StringIO(_get(FILES["series"], cache))))
    dc, worst_s = 15.0, 0.0
    for r in ser:
        dc = dc_step(dc, float(r["TEMP"]), float(r["PREC"]), float(r["LAT"]), int(r["MON"]))
        worst_s = max(worst_s, abs(dc - float(r["DC"])))
        dc = float(r["DC"])  # re-anchor so rounding in the file does not accumulate
    return {
        "reference": "cffdrs R package (github.com/cffdrs/cffdrs_r) test data",
        "single_step_cases": len(rows),
        "single_step_max_abs_error": round(worst, 4),
        "tropical_cases": n_trop,
        "tropical_max_abs_error": round(worst_trop, 4),
        "series_days": len(ser),
        "series_max_abs_error": round(worst_s, 4),
        "note": "reference values are printed to 1 decimal (single) / 4 significant "
                "figures (series), so errors up to ~0.05 / ~0.05 are rounding",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache")
    a = ap.parse_args()
    print(json.dumps(verify(pathlib.Path(a.cache) if a.cache else None), indent=2))
