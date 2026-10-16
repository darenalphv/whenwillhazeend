"""Every endpoint answers 200 with the keys CONTRACT.md promises."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import IN_SEASON_AS_OF, SEED

AQI_CATEGORIES = {
    "Good",
    "Moderate",
    "Unhealthy for Sensitive Groups",
    "Unhealthy",
    "Very Unhealthy",
    "Hazardous",
}


def test_index_serves_html(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200, (
        "GET / did not serve app/templates/index.html: " + resp.text[:400]
    )
    assert "text/html" in resp.headers["content-type"]


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {"status", "version", "live_weather_available"}
    assert body["status"] == "ok"
    # Network is severed by the autouse fixture, so this must be an honest False.
    assert body["live_weather_available"] is False


def test_history(client: TestClient) -> None:
    resp = client.get("/api/history")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {"seasons", "summary"}
    seasons = body["seasons"]
    assert isinstance(seasons, list) and seasons, "no seasons in the bundled dataset"
    assert isinstance(body["summary"], dict) and body["summary"]

    years = [s["year"] for s in seasons]
    assert years == sorted(years), "seasons must be ascending by year"

    required = {
        "year",
        "enso_phase",
        "start_date",
        "end_date",
        "duration_days",
        "burned_area_mha_kalimantan",
        "source",
        "estimated",
    }
    for season in seasons:
        missing = required - season.keys()
        assert not missing, f"season {season.get('year')} missing keys {missing}"

    # The contract names a minimum set of years; 2015 anchors the model.
    assert 2015 in years
    assert {1997, 2015, 2019}.issubset(set(years))


def test_history_year(client: TestClient, seasons: list[dict]) -> None:
    year = 2015 if any(s["year"] == 2015 for s in seasons) else seasons[-1]["year"]
    resp = client.get(f"/api/history/{year}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {"season", "clusters"}
    assert body["season"]["year"] == year
    clusters = body["clusters"]
    assert clusters["type"] == "FeatureCollection"
    assert isinstance(clusters["features"], list)
    for feature in clusters["features"][:5]:
        props = feature["properties"]
        assert props.keys() >= {"date", "area_ha", "pool"}
        assert feature["geometry"]["type"] in {"Point", "Polygon", "MultiPolygon"}


def test_history_year_unknown_is_404(client: TestClient) -> None:
    resp = client.get("/api/history/1981")
    assert resp.status_code == 404, resp.text
    assert "1981" in resp.json()["detail"]


@pytest.mark.parametrize("year", [1000, 3000, 1979, 2101])
def test_history_year_out_of_range_is_422(client: TestClient, year: int) -> None:
    assert client.get(f"/api/history/{year}").status_code == 422


def test_regions(client: TestClient) -> None:
    resp = client.get("/api/regions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    features = body["features"]
    assert len(features) == 5, "five Kalimantan provinces expected"
    for feature in features:
        props = feature["properties"]
        assert props.keys() >= {
            "name",
            "area_mha",
            "peat_mha",
            "capital",
            "capital_lat",
            "capital_lon",
        }


def test_fuel(client: TestClient) -> None:
    resp = client.get("/api/fuel")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {"as_of", "pools", "total_land_mha"}
    pools = body["pools"]
    assert isinstance(pools, list) and pools
    for pool in pools:
        assert pool.keys() >= {
            "id",
            "label",
            "area_mha",
            "fuel_load_t_ha",
            "pm25_ef_g_kg",
            "burn_susceptibility",
        }
        # burn_susceptibility is a fitted model parameter, never a measurement.
        assert pool.get("estimated") in (True, False)


def test_receptors(client: TestClient) -> None:
    resp = client.get("/api/receptors")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert isinstance(body, list) and body
    for receptor in body:
        assert receptor.keys() >= {"name", "lat", "lon"}


def test_meteo(client: TestClient) -> None:
    resp = client.get(
        "/api/meteo",
        params={"lat": -2.21, "lon": 113.92, "start": "2015-09-01", "end": "2015-09-10"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.keys() >= {"live", "lat", "lon", "dates", "tmax_c", "rain_mm", "source"}
    # Offline: must fall back, say so, and explain why. Never raise.
    assert body["live"] is False
    assert body["fallback_reason"], "a non-live response must explain the fallback"
    assert len(body["dates"]) == len(body["rain_mm"]) == 10


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 95, "lon": 113.9, "start": "2015-09-01", "end": "2015-09-10"},
        {"lat": -2.2, "lon": 500, "start": "2015-09-01", "end": "2015-09-10"},
        {"lat": -2.2, "lon": 113.9, "start": "not-a-date", "end": "2015-09-10"},
        {"lat": -2.2, "lon": 113.9, "start": "2015-09-10", "end": "2015-09-01"},
        {"lat": -2.2, "lon": 113.9, "start": "1850-01-01", "end": "1850-01-05"},
    ],
)
def test_meteo_rejects_bad_input(client: TestClient, params: dict) -> None:
    assert client.get("/api/meteo", params=params).status_code == 422


def test_dispersion(client: TestClient) -> None:
    payload = {
        "sources": [
            {"lat": -2.21, "lon": 113.92, "area_ha": 1240.0, "pool": "peat_drained"},
            {"lat": -1.80, "lon": 113.40, "area_ha": 640.0, "pool": "peat_intact"},
        ],
        "wind_speed_ms": 4.5,
        "wind_dir_deg": 135.0,
        "mixing_height_m": 800.0,
        "rain_mm": 0.0,
    }
    resp = client.post("/api/dispersion", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "receptors" in body or "plume" in body, body.keys()
    assert body["plume"]["type"] == "FeatureCollection"

    receptors = body["receptors"]
    assert isinstance(receptors, list) and receptors
    for receptor in receptors:
        assert receptor.keys() >= {
            "name",
            "lat",
            "lon",
            "pm25_ugm3",
            "aqi",
            "category",
            "arrival_hours",
            "distance_km",
        }
        assert receptor["category"] in AQI_CATEGORIES
        assert receptor["pm25_ugm3"] >= 0
        assert receptor["distance_km"] >= 0


def test_dispersion_rain_scavenges(client: TestClient) -> None:
    """Wet deposition must reduce concentrations, not raise them."""
    base = {
        "sources": [
            {"lat": -2.21, "lon": 113.92, "area_ha": 1240.0, "pool": "peat_drained"}
        ],
        "wind_speed_ms": 4.5,
        "wind_dir_deg": 135.0,
    }
    dry = client.post("/api/dispersion", json={**base, "rain_mm": 0.0}).json()
    wet = client.post("/api/dispersion", json={**base, "rain_mm": 60.0}).json()
    dry_total = sum(r["pm25_ugm3"] for r in dry["receptors"])
    wet_total = sum(r["pm25_ugm3"] for r in wet["receptors"])
    assert wet_total <= dry_total


@pytest.mark.parametrize(
    "payload",
    [
        {"sources": [], "wind_speed_ms": 4.0, "wind_dir_deg": 90.0},
        {
            "sources": [
                {"lat": -2.2, "lon": 113.9, "area_ha": 100.0, "pool": "peat_drained"}
            ],
            "wind_speed_ms": 4.0,
            "wind_dir_deg": 400.0,
        },
        {
            "sources": [
                {"lat": -2.2, "lon": 113.9, "area_ha": -5.0, "pool": "peat_drained"}
            ],
            "wind_speed_ms": 4.0,
            "wind_dir_deg": 90.0,
        },
        {
            "sources": [{"lat": -2.2, "lon": 113.9, "area_ha": 100.0}],
            "wind_speed_ms": 4.0,
            "wind_dir_deg": 90.0,
        },
        {
            "sources": [
                {"lat": -2.2, "lon": 113.9, "area_ha": 100.0, "pool": "peat_drained"}
            ],
            "wind_speed_ms": 4.0,
            "wind_dir_deg": 90.0,
            "mixing_height_m": 10.0,
        },
    ],
)
def test_dispersion_rejects_bad_input(client: TestClient, payload: dict) -> None:
    assert client.post("/api/dispersion", json=payload).status_code == 422


def test_openapi_lists_every_contract_route(client: TestClient) -> None:
    spec = client.get("/api/openapi.json").json()
    for path in (
        "/api/health",
        "/api/history",
        "/api/history/{year}",
        "/api/regions",
        "/api/fuel",
        "/api/receptors",
        "/api/simulate/season",
        "/api/simulate/exhaustion",
        "/api/dispersion",
        "/api/meteo",
    ):
        assert path in spec["paths"], f"{path} missing from the OpenAPI schema"
