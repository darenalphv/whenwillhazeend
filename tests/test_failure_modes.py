"""When a layer breaks, the user is told — not handed an invented number.

Every failure below must surface as HTTP 503 with a `detail` that names the
component. A bare 500 is a bug; a plausible-looking fallback forecast would be
worse than a bug.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from tests.conftest import IN_SEASON_AS_OF, SEED


def _explode(*args, **kwargs):
    raise RuntimeError("drought index feed went dark")


def test_season_model_failure_is_503_not_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    burn = importlib.import_module("app.model.burn")
    monkeypatch.setattr(burn, "simulate_season_end", _explode)

    resp = client.post(
        "/api/simulate/season",
        json={"as_of": IN_SEASON_AS_OF, "n_sims": 100, "seed": SEED},
    )
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "season-end model" in detail
    assert "drought index feed went dark" in detail
    # Crucially: no forecast is smuggled into the error body.
    assert "days_remaining" not in resp.text


def test_exhaustion_model_failure_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stock = importlib.import_module("app.model.stock")
    monkeypatch.setattr(stock, "simulate_exhaustion", _explode)
    resp = client.post("/api/simulate/exhaustion", json={"n_sims": 50})
    assert resp.status_code == 503, resp.text
    assert "fuel-exhaustion model" in resp.json()["detail"]


def test_missing_data_file_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    loader = importlib.import_module("app.data.loader")

    def _missing(*args, **kwargs):
        raise FileNotFoundError("app/data/fuel_stock.json")

    monkeypatch.setattr(loader, "load_fuel_stock", _missing)
    resp = client.get("/api/fuel")
    assert resp.status_code == 503, resp.text
    detail = resp.json()["detail"]
    assert "dataset" in detail and "missing" in detail


def test_dispersion_failure_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispersion = importlib.import_module("app.model.dispersion")
    monkeypatch.setattr(dispersion, "haze_dispersion", _explode)
    resp = client.post(
        "/api/dispersion",
        json={
            "sources": [
                {"lat": -2.2, "lon": 113.9, "area_ha": 100.0, "pool": "peat_drained"}
            ],
            "wind_speed_ms": 3.0,
            "wind_dir_deg": 90.0,
        },
    )
    assert resp.status_code == 503, resp.text
    assert "dispersion model" in resp.json()["detail"]


def test_meteo_failure_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch_weather is contractually forbidden from raising; if it does anyway,
    we surface an outage rather than synthesising weather at the API layer."""
    meteo = importlib.import_module("app.model.meteo")
    monkeypatch.setattr(meteo, "fetch_weather", _explode)
    resp = client.get(
        "/api/meteo",
        params={"lat": -2.2, "lon": 113.9, "start": "2026-09-01", "end": "2026-09-02"},
    )
    assert resp.status_code == 503, resp.text
    assert "weather service" in resp.json()["detail"]


def test_health_survives_a_broken_meteo_layer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Health is a liveness probe: it reports, it does not fail."""
    meteo = importlib.import_module("app.model.meteo")
    monkeypatch.setattr(meteo, "fetch_weather", _explode)
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.text
    assert resp.json()["live_weather_available"] is False


def test_history_summary_failure_is_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = importlib.import_module("app.model.history")
    monkeypatch.setattr(history, "season_summary", _explode)
    resp = client.get("/api/history")
    assert resp.status_code == 503, resp.text
    assert "historical-season summariser" in resp.json()["detail"]
