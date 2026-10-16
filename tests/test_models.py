"""The two headline models: season end, and fuel exhaustion.

These tests check *internal consistency* — ordering, conservation, and that a
knob does what its name says. They deliberately do not assert that any
particular number is correct; that is what calibration and the caveats are for.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import IN_SEASON_AS_OF, SEED

SCENARIOS = [
    "baseline",
    "no_enforcement",
    "strict_moratorium",
    "monsoon_early",
    "monsoon_late",
]


def season_payload(**overrides) -> dict:
    payload = {
        "as_of": IN_SEASON_AS_OF,
        "region": "kalimantan",
        "n_sims": 400,
        "seed": SEED,
        "scenario": "baseline",
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Season model
# --------------------------------------------------------------------------- #


def test_season_shape(client: TestClient) -> None:
    resp = client.post("/api/simulate/season", json=season_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {
        "as_of",
        "region",
        "active",
        "days_remaining",
        "end_date",
        "daily_end_probability",
        "drivers",
        "confidence",
        "live_weather",
        "scenario",
        "caveats",
    }
    assert body["as_of"] == IN_SEASON_AS_OF
    assert body["scenario"] == "baseline"
    # Network is severed in tests; the model must admit it used climatology.
    assert body["live_weather"] is False
    assert isinstance(body["caveats"], list) and body["caveats"], (
        "a modelled forecast must ship its caveats"
    )


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_season_percentiles_are_ordered(client: TestClient, scenario: str) -> None:
    """p10 <= p50 <= p90, in days and in dates, for every scenario."""
    resp = client.post(
        "/api/simulate/season", json=season_payload(scenario=scenario)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    days = body["days_remaining"]
    assert days.keys() >= {"p10", "p50", "p90", "mean"}
    if body["active"]:
        assert all(days[k] is not None for k in ("p10", "p50", "p90", "mean")), days
        assert days["p10"] <= days["p50"] <= days["p90"], days
        assert days["p10"] <= days["mean"] <= days["p90"], days
        assert days["p10"] >= 0

        ends = body["end_date"]
        assert ends.keys() >= {"p10", "p50", "p90"}
        assert ends["p10"] <= ends["p50"] <= ends["p90"], ends  # ISO sorts as dates


def test_season_daily_probability_is_a_survival_curve(client: TestClient) -> None:
    body = client.post("/api/simulate/season", json=season_payload()).json()
    curve = body["daily_end_probability"]
    assert isinstance(curve, list) and curve
    prev_survival = 1.0
    prev_date = ""
    for point in curve:
        assert point.keys() >= {"date", "p_end", "p_still_burning"}
        assert 0.0 <= point["p_end"] <= 1.0
        assert 0.0 <= point["p_still_burning"] <= 1.0
        assert point["date"] > prev_date, "dates must strictly increase"
        assert point["p_still_burning"] <= prev_survival + 1e-9, (
            "survival probability must be non-increasing"
        )
        prev_survival = point["p_still_burning"]
        prev_date = point["date"]


def test_season_driver_contributions_sum_to_one(client: TestClient) -> None:
    body = client.post("/api/simulate/season", json=season_payload()).json()
    drivers = body["drivers"]
    assert isinstance(drivers, list) and drivers
    for driver in drivers:
        assert driver.keys() >= {
            "name",
            "value",
            "unit",
            "contribution",
            "direction",
            "explain",
        }
        assert driver["direction"] in {"prolongs", "shortens", "neutral"}
        assert driver["explain"], "every driver needs a plain-language explanation"
    total = sum(d["contribution"] for d in drivers)
    assert total == pytest.approx(1.0, abs=0.05), (
        f"driver contributions sum to {total}, not ~1.0"
    )


def test_season_is_deterministic_under_a_seed(client: TestClient) -> None:
    first = client.post("/api/simulate/season", json=season_payload()).json()
    second = client.post("/api/simulate/season", json=season_payload()).json()
    assert first["days_remaining"] == second["days_remaining"]


def test_el_nino_prolongs_the_season(client: TestClient) -> None:
    """Sign check on the ONI coefficient: El Nino must not shorten the season."""
    la_nina = client.post(
        "/api/simulate/season", json=season_payload(oni=-1.5, n_sims=1500)
    ).json()
    el_nino = client.post(
        "/api/simulate/season", json=season_payload(oni=2.5, n_sims=1500)
    ).json()
    if la_nina["active"] and el_nino["active"]:
        assert el_nino["days_remaining"]["p50"] >= la_nina["days_remaining"]["p50"], (
            "a strong El Nino produced a shorter season than a La Nina"
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"n_sims": 20001},
        {"n_sims": 0},
        {"n_sims": -1},
        {"scenario": "wishful_thinking"},
        {"oni": 99.0},
        {"as_of": "not-a-date"},
        {"as_of": "2015-13-45"},
        {"region": ""},
        {"seed": -1},
        {"nonsense_field": 1},
    ],
)
def test_season_rejects_out_of_range_input(client: TestClient, payload: dict) -> None:
    """Out of range is 422 — never silently clamped to the nearest legal value."""
    resp = client.post("/api/simulate/season", json=season_payload(**payload))
    assert resp.status_code == 422, resp.text


def test_season_n_sims_at_the_ceiling_is_accepted(client: TestClient) -> None:
    """20000 is legal; 20001 is not. Check the boundary is inclusive."""
    resp = client.post("/api/simulate/season", json=season_payload(n_sims=20000))
    assert resp.status_code == 200, resp.text


# --------------------------------------------------------------------------- #
# Exhaustion model
# --------------------------------------------------------------------------- #


def exhaustion_payload(**overrides) -> dict:
    payload = {"n_sims": 200, "horizon_years": 150, "seed": SEED}
    payload.update(overrides)
    return payload


def test_exhaustion_shape(client: TestClient) -> None:
    resp = client.post("/api/simulate/exhaustion", json=exhaustion_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {
        "exhaustion_year",
        "trajectories",
        "per_pool",
        "cumulative_burned_mha_by_2050",
        "cumulative_pm25_kt",
        "assumptions",
        "caveats",
    }
    assert isinstance(body["caveats"], list) and body["caveats"]

    traj = body["trajectories"]
    assert traj.keys() >= {"years", "p10", "p50", "p90"}
    n = len(traj["years"])
    assert n > 1
    assert len(traj["p10"]) == len(traj["p50"]) == len(traj["p90"]) == n
    assert traj["years"] == sorted(traj["years"])
    for lo, mid, hi in zip(traj["p10"], traj["p50"], traj["p90"], strict=True):
        assert lo <= mid <= hi, (lo, mid, hi)

    for pool in body["per_pool"]:
        assert pool.keys() >= {"id", "exhaustion_year_p50", "remaining_mha_2050"}


def test_exhaustion_year_percentiles_are_ordered(client: TestClient) -> None:
    body = client.post(
        "/api/simulate/exhaustion", json=exhaustion_payload(horizon_years=300)
    ).json()
    year = body["exhaustion_year"]
    assert year.keys() >= {"p10", "p50", "p90", "never_pct"}
    assert 0.0 <= year["never_pct"] <= 100.0
    present = [year[k] for k in ("p10", "p50", "p90") if year[k] is not None]
    assert present == sorted(present), year


def test_no_fire_means_fire_never_exhausts_the_stock(client: TestClient) -> None:
    """burn_reduction_pct=100 with no conversion: the stock must survive.

    This is the headline policy counterfactual. If eliminating fire still burns
    the peat away, the burn-reduction knob is not wired to anything.
    """
    body = client.post(
        "/api/simulate/exhaustion",
        json=exhaustion_payload(
            burn_reduction_pct=100.0,
            deforestation_mha_per_yr=0.0,
            horizon_years=300,
        ),
    ).json()

    assert body["cumulative_burned_mha_by_2050"] == pytest.approx(0.0, abs=1e-6), (
        "100% burn reduction still burned area"
    )
    assert body["cumulative_pm25_kt"] == pytest.approx(0.0, abs=1e-6)
    assert body["exhaustion_year"]["never_pct"] == pytest.approx(100.0, abs=1e-6), (
        "with fire eliminated and no conversion, no trajectory may exhaust"
    )
    assert body["exhaustion_year"]["p50"] is None

    # With no fire and no conversion the forest must not fall. Peat still does:
    # drained peat oxidises and subsides whether or not it burns (Hooijer 2012;
    # Evans 2019), so the combined stock is no longer flat -- that is physics,
    # not fire. What must hold is that fire itself removes nothing.
    forest = body["trajectories"]["forest_p50"]
    assert forest[-1] >= forest[0] - 1e-6, "forest fell with fire and conversion off"
    carbon = body["headlines"]["peat"]["carbon_lost_mt_c"]
    assert carbon["fire_by_horizon"] == pytest.approx(0.0, abs=1e-6), (
        "fire removed peat with burn_reduction_pct=100"
    )
    assert carbon["drainage_by_horizon"] > 0, "drainage subsidence is not wired"


def test_burn_reduction_monotonically_delays_exhaustion(client: TestClient) -> None:
    def median_year(pct: float):
        body = client.post(
            "/api/simulate/exhaustion",
            json=exhaustion_payload(burn_reduction_pct=pct, horizon_years=300),
        ).json()
        return body["exhaustion_year"]["p50"]

    none_, half = median_year(0.0), median_year(50.0)
    if none_ is not None and half is not None:
        assert half >= none_, "halving burning brought exhaustion *forward*"


def test_exhaustion_echoes_its_assumptions(client: TestClient) -> None:
    body = client.post(
        "/api/simulate/exhaustion",
        json=exhaustion_payload(burn_reduction_pct=25.0, restoration_mha_per_yr=0.2),
    ).json()
    assumptions = body["assumptions"]
    assert assumptions["burn_reduction_pct"] == pytest.approx(25.0)
    assert assumptions["restoration_mha_per_yr"] == pytest.approx(0.2)


@pytest.mark.parametrize(
    "payload",
    [
        {"n_sims": 20001},
        {"n_sims": 0},
        {"horizon_years": 301},
        {"horizon_years": 0},
        {"burn_reduction_pct": 101.0},
        {"burn_reduction_pct": -1.0},
        {"restoration_mha_per_yr": -0.1},
        {"deforestation_mha_per_yr": -1.0},
        {"unexpected": True},
    ],
)
def test_exhaustion_rejects_out_of_range_input(
    client: TestClient, payload: dict
) -> None:
    resp = client.post("/api/simulate/exhaustion", json=exhaustion_payload(**payload))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize(
    "payload", [{"horizon_years": 300}, {"burn_reduction_pct": 100.0}, {"n_sims": 1}]
)
def test_exhaustion_boundaries_are_inclusive(client: TestClient, payload: dict) -> None:
    resp = client.post("/api/simulate/exhaustion", json=exhaustion_payload(**payload))
    assert resp.status_code == 200, resp.text


def test_never_pct_means_not_within_the_horizon(client: TestClient) -> None:
    """`never_pct` is horizon-relative, and the horizon is load-bearing.

    At the default 200-year horizon the baseline run reports never_pct=100 and
    a null median; extend to 300 years and it exhausts. That is not a
    contradiction — "never" means "not before the horizon" — but it means the
    hero number the frontend renders depends entirely on horizon_years, so the
    relationship is pinned here.
    """
    def run(horizon: int) -> dict:
        resp = client.post(
            "/api/simulate/exhaustion",
            json={"n_sims": 300, "horizon_years": horizon, "seed": SEED},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()["exhaustion_year"]

    short, long = run(120), run(300)
    assert long["never_pct"] <= short["never_pct"], (
        "a longer horizon reported *more* trajectories never exhausting"
    )
    if short["p50"] is None:
        assert short["never_pct"] == pytest.approx(100.0), (
            "a null median must be accompanied by never_pct=100"
        )
