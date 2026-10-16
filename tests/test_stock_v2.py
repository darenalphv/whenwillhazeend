"""MODEL-EARTH: peat column, subsidence and split headlines (F1-F4). MODEL_SPEC 9 row 4."""

from __future__ import annotations

import pytest

from app.model.stock import peat_years_to_zero, simulate_exhaustion

SEED = 20260918


def test_shallow_drained_peat_with_no_fire_reaches_zero_in_about_17_years():
    """Spec: 0.75 m / 0.043 m/yr ~= 17 yr."""
    assert peat_years_to_zero(0.75, 0.043) == pytest.approx(17.4, abs=0.1)
    assert peat_years_to_zero(0.75) == pytest.approx(17.4, abs=0.1)


@pytest.fixture(scope="module")
def baseline():
    return simulate_exhaustion(n_sims=300, horizon_years=200, seed=SEED)


def test_headlines_split_peat_and_forest(baseline):
    h = baseline["headlines"]
    for key in ("peat", "forest"):
        assert {"label", "exhaustion_year", "remaining_mha_2050"} <= h[key].keys()
        assert {"p10", "p50", "p90", "never_pct"} <= h[key]["exhaustion_year"].keys()
    # v1 aggregate keys kept
    assert {"p10", "p50", "p90", "never_pct"} <= baseline["exhaustion_year"].keys()


def test_drained_peat_runs_out_this_century_or_next(baseline):
    """The point of F1: with subsidence, 'never within 200 years' no longer holds."""
    peat = baseline["headlines"]["peat"]
    assert peat["exhaustion_year"]["never_pct"] < 50.0
    assert peat["half_gone_year"]["p50"] is not None
    assert peat["half_gone_year"]["p50"] < 2100


def test_no_fire_still_loses_drained_peat_to_subsidence():
    body = simulate_exhaustion(n_sims=100, horizon_years=60, seed=SEED,
                               burn_reduction_pct=100.0, deforestation_mha_per_yr=0.0,
                               restoration_mha_per_yr=0.0)
    assert body["cumulative_pm25_kt"] == pytest.approx(0.0, abs=1e-6)
    peat = body["headlines"]["peat"]
    frac_2050 = peat["remaining_mha_2050"] / peat["baseline_mha"]
    # the 0.5-1 m class (22%) is mostly gone after 24 years at 3.4-5 cm/yr
    assert 0.70 < frac_2050 < 0.85, frac_2050
    assert peat["carbon_lost_mt_c"]["fire_by_2050"] == pytest.approx(0.0, abs=1e-6)
    assert peat["carbon_lost_mt_c"]["drainage_by_2050"] > 0


def test_burn_reduction_never_brings_peat_exhaustion_forward():
    a = simulate_exhaustion(n_sims=200, horizon_years=250, seed=SEED)
    b = simulate_exhaustion(n_sims=200, horizon_years=250, seed=SEED, burn_reduction_pct=50.0)
    pa = a["headlines"]["peat"]["exhaustion_year"]["p50"]
    pb = b["headlines"]["peat"]["exhaustion_year"]["p50"]
    if pa is not None and pb is not None:
        assert pb >= pa


def test_base_rate_is_satellite_consistent(baseline):
    a = baseline["assumptions"]
    assert a["A_base_mha_per_yr"] > a["v1_A_base_mha_per_yr"]
    assert a["peat_burned_fraction_per_yr"]["low"] == pytest.approx(0.036)


def test_per_pool_ids_match_fuel_pools(baseline):
    from app.data.loader import load_fuel_stock
    ids = {p["id"] for p in load_fuel_stock()["pools"]}
    assert {p["id"] for p in baseline["per_pool"]} == ids
