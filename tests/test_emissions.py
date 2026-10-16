"""MODEL-EARTH: emissions (E1-E3) and the burned-area series (B1-B4). MODEL_SPEC 9 rows 2-3, 6."""

from __future__ import annotations

from datetime import date

import pytest

from app.data.loader import load_burned_area, load_fuel_stock
from app.model import emissions as E


def test_first_fire_peat_hectare_is_6_38_t():
    # spec E1: 286 t/ha x 22.3 g/kg = 6.38 t PM2.5 per first-fire hectare
    assert E.peat_fuel_consumption_t_ha(1) == pytest.approx(286.0, abs=0.5)
    assert E.pm25_kg_per_ha("peat_drained", 1) / 1000.0 == pytest.approx(6.38, abs=0.02)


def test_first_fire_range_matches_spec():
    assert E.pm25_kg_per_ha("peat_drained", 1, "low") / 1000.0 == pytest.approx(3.2, abs=0.1)
    assert E.pm25_kg_per_ha("peat_drained", 1, "high") / 1000.0 == pytest.approx(13.2, abs=0.1)


def test_repeat_fire_ratios():
    first = E.pm25_kg_per_ha("peat_drained", 1)
    ratios = [E.pm25_kg_per_ha("peat_drained", n) / first for n in (2, 3, 4, 7)]
    assert ratios == pytest.approx([0.56, 0.33, 0.11, 0.11], abs=1e-9)


def test_nonpeat_efs_are_andreae_2019():
    efs = E.pool_emission_factors()
    assert efs["primary_forest"] == pytest.approx(8.3)
    assert efs["scrub_grass"] == pytest.approx(6.7)


def test_peat_ef_attribution_is_corrected():
    peat = [p for p in load_fuel_stock()["pools"] if p["id"].startswith("peat")]
    for p in peat:
        assert p["pm25_ef_g_kg"] == pytest.approx(22.3)
        assert "Jayarathne" in p["ef_source"]
        assert "Kiely" in p["ef_source"]


def test_burn_stage_ef_is_off_by_default():
    assert E.burn_stage_ef(0) == pytest.approx(22.3)
    assert E.burn_stage_ef(0, enabled=True) > E.burn_stage_ef(20, enabled=True)


def test_2015_sep_oct_primary_pm25_in_4_to_11_tg():
    """MODEL_SPEC 9 row 6: 2015 Sep-Oct total primary PM2.5 in 4-11 Tg."""
    tot = E.season_totals(2015, date(2015, 9, 1), date(2015, 10, 31))
    assert 4.0 <= tot["total_tg"] <= 11.0, tot
    assert tot["sumatra_tg"] > 0.5 and tot["kalimantan_tg"] > 0.5


def test_daily_emissions_cover_kalimantan_and_sumatra():
    em = E.daily_emissions(date(2015, 9, 20))
    assert em is not None
    regions = E.region_table()
    islands = {regions[i]["island"] for i in set(em["region"][(em["peat_kg"] + em["nonpeat_kg"]) > 0].tolist())}
    assert {"kalimantan", "sumatra"} <= islands
    assert E.daily_emissions(date(2015, 1, 15)) is None


def test_burned_area_series_is_a_range():
    ba = load_burned_area()
    k = ba["series"]["kalimantan"]["2015"]
    assert k["low"] < k["central"] < k["high"]
    assert ba["base_rates"]["nonpeat_mha_per_yr"]["central"] > 0.198, (
        "B2: the satellite-consistent base rate must exceed the v1 KLHK x 0.29 value")


def test_b4_validation_numbers():
    v = load_burned_area()["indonesia_validation"]
    assert v["2015"]["official"] == pytest.approx(2.61)
    assert max(v["2015"]["satellite"]) == pytest.approx(4.60)
    assert v["2019"]["official"] == pytest.approx(1.65)
    assert max(v["2019"]["satellite"]) == pytest.approx(3.8)
