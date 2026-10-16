"""Season model v2 (MODEL_SPEC section 2, S1-S6) and the section 0.4 data fixes.

Spec section 9 rows 1 and 5:
  * psi_peak contains only 24-h PSI with a metric field; excess deaths is a range;
  * the Drought Code matches cffdrs on a reference series;
  * the 2019 forecast from 1 Aug 2019 is longer than the ENSO-only model's.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.model import drought_code as dcm

ROOT = pathlib.Path(__file__).resolve().parents[1]

# --------------------------------------------------------------------------- #
# Drought Code vs the cffdrs reference (github.com/cffdrs/cffdrs_r, GPL-2):
# an excerpt of tests/testthat/data/DroughtCode.csv and fwi_01.csv. The full
# 4,392-case check is scripts/model/season/verify_dc.py (max error 0.005).
# --------------------------------------------------------------------------- #

# dc_yda, temp, prec, lat, mon, lat_adjust, expected
CFFDRS_SINGLE = [
    (158.4, 42.9, 55.61, -17.1, 1, True, 53.79),     # tropical, heavy rain
    (377.1, 42.9, 55.61, -17.1, 1, True, 206.35),
    (595.8, 42.9, 55.61, -17.1, 1, True, 331.56),
    (814.5, 42.9, 55.61, -17.1, 1, True, 427.24),
    (69.3, 42.9, -10.0, -17.1, 1, True, 78.23),      # tropical, dry
    (288.0, 42.9, -10.0, -17.1, 1, True, 296.93),
    (315.9, -30.0, 55.61, -17.1, 1, True, 158.0),    # temperature floor
    (818.4, 18.6, 121.22, 55.8, 1, True, 195.47),    # northern table
    (684.6, -5.7, 55.61, 55.8, 7, True, 368.23),
]
# fwi_01.csv (lat 40N, April): TEMP, PREC, DC, starting from DC 15
CFFDRS_SERIES = [(17, 0, 19.01), (20, 2.4, 23.57), (8.5, 0, 26.05), (6.5, 0, 28.18),
                 (13, 0, 31.47), (6, 0.4, 33.5), (5.5, 0, 35.45), (8.5, 0, 37.93),
                 (9.5, 0, 40.6), (7, 9, 29.52), (6.5, 1, 31.65)]


@pytest.mark.parametrize("row", CFFDRS_SINGLE)
def test_drought_code_matches_cffdrs_single_step(row):
    dc0, t, p, lat, mon, adj, want = row
    assert dcm.dc_step(dc0, t, p, lat, mon, adj) == pytest.approx(want, abs=0.006)


def test_drought_code_matches_cffdrs_series():
    dc = 15.0
    for t, p, want in CFFDRS_SERIES:
        dc = dcm.dc_step(dc, t, p, 40.0, 4)
        assert dc == pytest.approx(want, abs=0.01)


def test_vectorised_dc_equals_scalar():
    import numpy as np
    dc = np.array([0.0, 100.0, 350.0, 700.0])
    t = np.array([33.0, 31.0, 34.0, 29.0])
    p = np.array([0.0, 2.8, 2.9, 80.0])
    vec = dcm.dc_step_vec(np, dc, t, p)
    for i in range(4):
        assert vec[i] == pytest.approx(dcm.dc_step(dc[i], t[i], p[i]), abs=1e-9)


def test_drought_readiness_is_bounded_and_anchored():
    assert dcm.drought_readiness(0) == 0.0
    assert dcm.drought_readiness(dcm.DC_WET_MAX) == 0.0
    assert dcm.drought_readiness(dcm.DC_SEVERE_HAZE) == pytest.approx(1.0)
    assert dcm.drought_readiness(900) == 1.0


# --------------------------------------------------------------------------- #
# seasons.json corrections (section 0.4)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def seasons_by_year():
    rows = json.loads((ROOT / "app/data/seasons.json").read_text())
    return {r["year"]: r for r in rows}


def test_psi_field_holds_only_24h_psi(seasons_by_year):
    for y, s in seasons_by_year.items():
        assert "psi_peak_singapore_metric" in s and "24-hour PSI" in s["psi_peak_singapore_metric"]
        assert s["psi_peak_singapore"] in (None, (s.get("singapore_peaks") or {}).get("psi_24h_max"))
    sp = seasons_by_year[2015]["singapore_peaks"]
    assert sp["psi_24h_max"] == 322
    assert sp["pm25_1h_max_ugm3"] == 471
    assert seasons_by_year[2015]["psi_peak_singapore"] == 322      # 471 is PM2.5, not a PSI
    assert seasons_by_year[2013]["psi_peak_singapore"] is None      # 401 is a 3-h PSI
    assert seasons_by_year[2013]["singapore_peaks"]["psi_3h_max"] == 401


def test_excess_deaths_is_a_range_with_sources(seasons_by_year):
    r = seasons_by_year[2015]["excess_deaths_range"]
    assert r["low"] == 11880 and r["high"] == 100300 and r["central"] is None
    vals = sorted(e["value"] for e in r["estimates"])
    assert vals == [11880, 44040, 75600, 100300]
    assert all(e.get("doi") for e in r["estimates"])
    assert "exposure-response" in r["note"]
    assert seasons_by_year[2015]["excess_deaths_est"] == 100300     # v1 scalar kept
    assert "Koplitz" in seasons_by_year[2015]["source"]
    assert {e["value"] for e in seasons_by_year[2006]["excess_deaths_range"]["estimates"]} == {37600, 22100}


def test_burned_area_ranges(seasons_by_year):
    r15 = seasons_by_year[2015]["burned_area_mha_indonesia_range"]
    assert (r15["low"], r15["high"]) == (2.61, 4.60)
    r19 = seasons_by_year[2019]["burned_area_mha_indonesia_range"]
    assert (r19["low"], r19["high"]) == (1.65, 3.8)
    r23 = seasons_by_year[2023]["burned_area_mha_indonesia_range"]
    assert (r23["low"], r23["high"]) == (1.11, 1.70)
    for r in (r15, r19, r23):
        assert r["low"] == min(e["value"] for e in r["estimates"])
        assert r["high"] == max(e["value"] for e in r["estimates"])


def test_enso_labels_use_the_burn_window(seasons_by_year):
    assert seasons_by_year[2019]["enso_phase"] == "neutral"
    assert seasons_by_year[2006]["enso_phase"] == "neutral"
    assert seasons_by_year[2015]["enso_phase"] == "el_nino"
    assert seasons_by_year[2019]["iod_phase"] == "positive"
    assert seasons_by_year[2019]["dmi_son"] == pytest.approx(1.63, abs=0.02)


def test_palangka_raya_attribution(seasons_by_year):
    s = seasons_by_year[2015]
    assert "Santoso" in s["source"] and "10.1080/10962247.2022.2077474" in s["source"]
    assert s["pm25_peak_metric"].startswith("24-hour")


def test_season_dates_are_fire_observed_from_2001(seasons_by_year):
    for y, s in seasons_by_year.items():
        assert s["target_source"] == ("rainfall_rule" if y <= 2000 else "firms_modis")
        assert "rainfall_rule_dates" in s
    # Fanin & van der Werf 2017: 2015 fires stopped in late October
    end15 = seasons_by_year[2015]["end_date"]
    assert "2015-10-15" <= end15 <= "2015-11-05"


# --------------------------------------------------------------------------- #
# the model
# --------------------------------------------------------------------------- #

def test_season_endpoint_v2_fields(client: TestClient):
    from tests.conftest import IN_SEASON_AS_OF, SEED
    body = client.post("/api/simulate/season",
                       json={"as_of": IN_SEASON_AS_OF, "n_sims": 500, "seed": SEED}).json()
    assert 0.0 <= body["drought_readiness"] <= 1.0
    ids = {d["id"] for d in body["drivers"]}
    assert {"dc", "iod", "rain_12wk", "oni", "monsoon"} <= ids
    assert "kbdi" not in ids
    for k in ("as_of", "region", "oni", "active", "days_remaining", "end_date",
              "daily_end_probability", "drivers", "confidence", "live_weather",
              "scenario", "caveats"):
        assert k in body


def test_indices_are_publication_lagged():
    from app.data.loader_season import oni_known_on, dmi_known_on
    # On 1 Aug 2019 the JAS ONI did not exist yet; MJJ was not yet published either.
    v, lbl = oni_known_on("2019-08-01")
    assert lbl == "AMJ 2019"
    v2, lbl2 = oni_known_on("2019-10-15")
    assert lbl2 == "JAS 2019" and v2 == pytest.approx(0.20, abs=0.02)
    _, wk = dmi_known_on("2019-08-01")
    assert wk < "2019-08-01"


def test_hazard_fit_records_honest_skill():
    fit = json.loads((ROOT / "app/model/hazard_fit.json").read_text())
    sk = fit["skill"]
    for k in ("rmse_days", "rmse_days_loo", "rmse_days_forecast", "anchor_rmse_days"):
        assert isinstance(sk[k], (int, float))
    assert sk["rmse_days"] <= sk["rmse_days_loo"] <= sk["rmse_days_forecast"] + 5
    assert sk["rmse_days_forecast"] < sk["naive_rmse_days_mean_predictor"]
    assert fit["target_is_rainfall_derived"] is False
    assert fit["v1_on_fire_labels"]["as_shipped"]["rmse_days_forecast"] > 0
    assert fit["drought_code_verification"]["single_step_max_abs_error"] < 0.01
    for c in ("c1", "c2", "c3", "c4", "c5", "c6", "c7"):
        assert fit["coefficients"][c] >= 0.0


def test_2019_forecast_from_1_aug_longer_than_enso_only():
    """MODEL_SPEC section 9 row 5, recomputed here rather than read back."""
    import numpy as np
    from app.model import burn
    from app.model import calibrate as cal
    fit = json.loads((ROOT / "app/model/hazard_fit.json").read_text())
    rec = cal.Record(json.loads(cal.WEATHER_PATH.read_text()))
    rec.set_seasonal(fit["seasonal"])
    i = rec.index["2019-08-01"]
    d = date(2019, 8, 1)
    meds = []
    for coeffs, gen, dmi, sd in (
            (fit["coefficients"], fit["rain_generator"], float(rec.dmi[i]), fit["frailty"]["sd"]),
            (fit["enso_only_ablation"]["coefficients"], fit["rain_generator_enso_only"], 0.0,
             fit["enso_only_ablation"]["frailty_sd"])):
        ed, _, _ = burn.run_hazard_simulation(
            d, d, cal.init_state(rec, i), coeffs, gen, float(rec.oni[i]), dmi,
            n_sims=5000, seed=2019, seasonal=fit["seasonal"], frailty_sd=sd)
        meds.append(float(np.median(ed)))
    assert meds[0] > meds[1], meds


def test_hindcast_cannot_see_weather_after_as_of(monkeypatch):
    """Rewrite every bundled weather day after as_of; a hindcast must not move."""
    from app.data import loader_season
    from app.model import burn
    cw, idx = loader_season.calibration_weather_index()
    as_of = "2019-09-01"
    res_a = burn.simulate_season_end(as_of, n_sims=400, seed=3, allow_forecast=False)
    poisoned = dict(cw)
    poisoned["rain_mm"] = [r if d <= as_of else 500.0 for d, r in zip(cw["dates"], cw["rain_mm"])]
    monkeypatch.setattr(loader_season, "calibration_weather_index", lambda: (poisoned, idx))
    burn._WX_CACHE.clear()
    res_b = burn.simulate_season_end(as_of, n_sims=400, seed=3, allow_forecast=False)
    assert res_a["days_remaining"] == res_b["days_remaining"]
    assert res_a["drought_code"] == res_b["drought_code"]
    # control: the same poison *before* as_of must change the answer, so the
    # patch demonstrably reaches the model
    early = dict(cw)
    early["rain_mm"] = [500.0 if "2019-08-01" <= d <= as_of else r
                        for d, r in zip(cw["dates"], cw["rain_mm"])]
    monkeypatch.setattr(loader_season, "calibration_weather_index", lambda: (early, idx))
    burn._WX_CACHE.clear()
    res_c = burn.simulate_season_end(as_of, n_sims=400, seed=3, allow_forecast=False)
    assert res_c["drought_code"] != res_a["drought_code"]
    burn._WX_CACHE.clear()


def test_season_simulation_is_fast():
    import time
    from app.model import burn
    burn.simulate_season_end("2026-09-18", n_sims=5000, seed=1)   # warm caches
    t = time.process_time()
    burn.simulate_season_end("2026-09-18", n_sims=5000, seed=2)
    assert time.process_time() - t < 1.0
