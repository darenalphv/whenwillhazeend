"""MODEL-EARTH: boundary layer (H1-H4), LPM-3L, haze3d v2. MODEL_SPEC 9 rows 6-7."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from app.data.loader import load_earth_json, load_fire_clusters, load_receptors
from app.model import transport as T
from app.model.haze3d import haze_field

SG_KL = ("singapore", "sg-west", "sg-central", "my-kuala-lumpur", "my-shah-alam",
         "my-klang", "my-petaling-jaya")


# ---- H1-H4 -----------------------------------------------------------------

def test_palangkaraya_profile_mean_and_afternoon_ratio():
    vals = [T.profile_factor("palangkaraya", h) for h in range(24)]
    assert sum(vals) / 24 == pytest.approx(1.0, abs=1e-9)
    afternoon = sum(T.profile_factor("palangkaraya", h) for h in (13, 14, 15)) / 3
    assert afternoon == pytest.approx(2.46, abs=0.05)


def test_station_profiles_differ():
    # Kuching collapses further at night and rises higher in the afternoon
    assert T.profile_factor("kuching", 3) < T.profile_factor("singapore", 3)
    assert T.profile_factor("kuching", 14) > T.profile_factor("singapore", 14)


def test_floor_and_ceiling():
    assert T.diurnal_mixing_height(50.0, 3.0) == pytest.approx(100.0)
    assert T.diurnal_mixing_height(5000.0, 13.0) == pytest.approx(2500.0)


# ---- LPM mass balance --------------------------------------------------------

def test_lpm_conserves_mass():
    met = T.uniform_met(4.0, 120.0, 600.0, rain_mm_day=12.0,
                        start=datetime(2015, 9, 1, tzinfo=timezone.utc), hours=48)
    lpm = T.LPM(met, dt_s=1800, seed=3, t_start_h=24.0)
    for _ in range(48):
        lpm.release([113.9] * 10, [-2.2] * 10, [100.0] * 10, T.KIND_PEAT, 0)
        lpm.step()
    budget = lpm.burden() + lpm.removed_dry + lpm.removed_wet + lpm.exported
    assert budget == pytest.approx(lpm.released, rel=1e-9)
    assert lpm.removed_wet > 0 and lpm.removed_dry > 0


# ---- haze3d v2 ---------------------------------------------------------------

def _clusters(year):
    out = []
    for f in load_fire_clusters(year)["features"]:
        lon, lat = f["geometry"]["coordinates"][:2]
        p = f["properties"]
        out.append({"lat": lat, "lon": lon, "area_ha": p["area_ha"], "pool": p["pool"],
                    "date": p.get("date"), "duration_days": p.get("duration_days")})
    return out


@pytest.fixture(scope="module")
def v2_2015():
    return haze_field(_clusters(2015), start_date="2015-10-14", hours=12, step_hours=2,
                      grid_n=40)


def test_v2_response_shape(v2_2015):
    r = v2_2015
    assert r["version"] == 2
    n = r["grid"]["nx"] * r["grid"]["ny"]
    assert len(r["frames"]) == 6
    for f in r["frames"]:
        assert len(f["values"]) == n and len(f["values_aloft"]) == n
        assert f["aloft_top_m"] > f["aloft_base_m"] >= 0
    w = r["wind"]
    assert len(w["frames"]) == len(r["frames"])
    for wf, f in zip(w["frames"], r["frames"]):
        assert wf["hour"] == f["hour"]
        assert len(wf["u"]) == len(w["levels_m"]) * w["nx"] * w["ny"]
    assert r["conditions"]["wind_source"] == "observed"
    assert r["conditions"]["emissions_source"] == "observed"
    regions = {s["region"] for s in r["sources"]}
    assert {"kalimantan", "sumatra"} <= regions
    for s in r["sources"]:
        assert {"emission_g_s", "injection_m", "flaming_fraction", "region", "on"} <= s.keys()
        assert 0.0 <= s["flaming_fraction"] <= 1.0
        assert len(s["emission_g_s_frames"]) == len(r["frames"])
    ids = {x["id"] for x in r["receptor_series"]}
    assert set(SG_KL) <= ids
    assert r["caveats"] and r["validation"] is not None


def test_v2_caveat_does_not_call_2015_singapore_haze_predominantly_sumatran(v2_2015):
    text = " ".join(v2_2015["caveats"])
    assert "predominantly" not in text
    assert "Hansen" in text and "co-dominant" in text


def test_arrival_regression_uniform_190_deg():
    """Coordinator regression: with wind from 190 deg at 3 m/s for 24 h and only
    Kalimantan clusters, nothing reaches KL or Singapore, so no arrival is reported
    (v1 said Singapore +2 h and KL +10 h)."""
    r = haze_field(_clusters(2015), wind_speed_ms=3.0, wind_dir_deg=190.0, hours=24,
                   start_date="2015-10-14", release_from_start=True,
                   wind_mode="uniform", emissions_mode="sources", grid_n=48)
    by = {x["id"]: x for x in r["receptor_series"]}
    for rid in SG_KL:
        assert by[rid]["arrival_hours"] is None, rid
        assert by[rid]["downwind"] is False, rid
    assert by["palangkaraya"]["downwind"] is True


def test_default_request_is_fast():
    src = _clusters(2015)
    haze_field(src, start_date="2015-10-14", hours=6)          # warm caches
    t = time.perf_counter()
    haze_field(src, start_date="2015-10-14", hours=24)
    assert time.perf_counter() - t < 2.0          # ~0.6 s on the dev laptop


def test_receptors_include_selangor_and_nea_regions():
    ids = {r["id"] for r in load_receptors()}
    assert set(SG_KL) | {"sg-north", "sg-south", "sg-east"} <= ids


# ---- spec section 9 row 6 targets (offline season run) -------------------------

@pytest.fixture(scope="module")
def season_2015():
    v = (load_earth_json("lpm_validation.json") or {}).get("2015")
    if v is None:
        pytest.skip("offline LPM validation not built")
    return v


def test_target_sep_oct_2015_primary_tg(season_2015):
    assert 4.0 <= season_2015["target_sep_oct_primary_tg"]["modelled"] <= 11.0


def test_target_kuching_borneo_share(season_2015):
    assert season_2015["target_kuching_borneo_share"]["modelled"] > 0.70


def test_target_singapore_jul_oct_2015_within_factor_2(season_2015):
    sg = season_2015["target_singapore_jul_oct"]["modelled_primary"]
    assert 13.5 <= sg <= 60.0, sg


def test_lifetime_in_3_to_6_days(season_2015):
    assert 3.0 <= season_2015["lifetime_days"] <= 6.0


def test_srm_reproduces_its_own_season():
    """The SRM is emission-weighted per month, so applying 2015's matrix to 2015's
    emissions must give back the offline Jul-Oct mean at Singapore within 15%."""
    import numpy as np
    from datetime import date, timedelta
    from app.model import emissions as E
    info = T.srm_info()
    if info is None or 2015 not in info["years"]:
        pytest.skip("SRM not built")
    regions = E.region_table()
    days = [date(2015, 7, 1) + timedelta(days=k) for k in range(123)]
    Ed = np.zeros((len(days), len(info["tag_region"])))
    for k, d in enumerate(days):
        em = E.daily_emissions(d)
        Ed[k] += np.bincount(em["region"] * 2, weights=em["peat_kg"], minlength=Ed.shape[1])
        Ed[k] += np.bincount(em["region"] * 2 + 1, weights=em["nonpeat_kg"], minlength=Ed.shape[1])
    C = T.srm_apply(Ed, days[0], year=2015)
    i = info["receptor_ids"].index("singapore")
    v = (load_earth_json("lpm_validation.json") or {})["2015"]["singapore"]["jul_oct_mean_fire_pm25_primary"]
    assert C[:, i].mean() == pytest.approx(v, rel=0.15)
    assert len(regions) * 2 == Ed.shape[1]


def test_dispersion_snapshot_reports_no_false_arrivals():
    """Same regression for the 2D map's Gaussian snapshot (/api/dispersion)."""
    from app.model.dispersion import haze_dispersion
    src = [s for s in _clusters(2015) if s["date"] and s["date"] <= "2015-10-14"][:80]
    r = haze_dispersion(src, wind_speed_ms=3.0, wind_dir_deg=190.0)
    by = {x["name"]: x for x in r["receptors"]}
    for name in ("Kuala Lumpur", "Singapore", "Singapore West", "Shah Alam"):
        assert by[name]["arrival_hours"] is None, name


def test_haze3d_route_serialises_v2(client=None):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        resp = c.post("/api/simulate/haze3d",
                      json={"year": 2015, "start_date": "2015-09-25", "hours": 6,
                            "step_hours": 2, "grid_n": 32})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["version"] == 2 and body["wind"]["frames"]
    sg = next(r for r in body["receptor_series"] if r["id"] == "singapore")
    assert sg["mean_fire_pm25"] > 50, "25 Sep 2015 was Singapore's worst 24-h PSI day"
    assert sg["source_shares"].get("sumatra", 0) > 0.5
