"""MODEL-EARTH: health (M1-M2). MODEL_SPEC 7.4 and 9 row 8."""

from __future__ import annotations

import pytest

from app.model import health as H


def test_published_2015_is_a_range_not_a_point():
    r = H.published_estimates(2015)
    assert r["low"] == 11880 and r["high"] == 100300
    assert r["central"] is None
    assert len(r["estimates"]) == 4
    assert "12,000" in r["headline"] and "100,000" in r["headline"]
    for e in r["estimates"]:
        assert e["source"] and e["metric"]


def test_2006_estimates():
    vals = sorted(e["value"] for e in H.published_estimates(2006)["estimates"])
    assert vals == [22100, 37600]


def test_gemm_shape():
    assert H.gemm_hr(0.0) == pytest.approx(1.0)
    assert H.gemm_hr(2.4) == pytest.approx(1.0)
    hrs = [H.gemm_hr(c) for c in (5, 10, 20, 40, 80)]
    assert hrs == sorted(hrs)
    assert 0.0 < H.gemm_attributable_fraction(12.5, 30.0) < 1.0
    assert H.gemm_attributable_fraction(12.5, 0.0) == pytest.approx(0.0)


def test_shortterm_matches_atkinson():
    # 1 death/day baseline, 10 ug/m3 for one day -> ~1.04% of a death
    assert H.shortterm_deaths(1.0, [10.0]) == pytest.approx(1 - 2.718281828 ** -0.0104, rel=1e-6)


def test_scenario_estimate_is_gated_and_says_why():
    out = H.scenario_estimate()
    assert out["enabled"] is False
    names = " ".join(m["input"] for m in out["missing_inputs"])
    assert "Baseline NCD+LRI mortality" in names
    assert H.SCENARIO_ENABLED is False


def test_scenario_estimate_runs_when_forced_with_inputs():
    out = H.scenario_estimate(c_fire_annual=[10.0, 2.0], population_25plus=[1e6, 2e6],
                              baseline_rate=0.006, daily_deaths=[50.0],
                              fire_pm25_daily=[[100.0] * 60], force=True)
    g = out["long_term_gemm"]
    assert g["with_china_low"] <= g["with_china_central"] <= g["with_china_high"]
    assert out["short_term_atkinson"]["central"] > 0
