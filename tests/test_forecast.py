"""Forecast odds endpoints (CONTRACT_V2 amendment M2)."""

from __future__ import annotations

import json
import pathlib
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
BANNED = re.compile(r"\b(buy|bet|bets|wager|trade|trading|position|shares|polymarket)\b", re.I)


@pytest.fixture(scope="module")
def odds_2019(request):
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/api/forecast/odds", params={"as_of": "2019-09-01"})
    assert r.status_code == 200, r.text
    return r.json()


def test_odds_shape(odds_2019):
    o = odds_2019
    for k in ("as_of", "season_active", "n_sims", "markets", "distribution", "resolution",
              "model_error_days", "caveats", "p50_end_date"):
        assert k in o
    assert o["as_of"] == "2019-09-01" and o["mode"] == "hindcast"
    assert o["resolution"]["definition"] and o["resolution"]["source"]
    assert o["distribution"]["bucket"] == "week"
    assert sum(x["p"] for x in o["distribution"]["outcomes"]) == pytest.approx(1.0, abs=0.01)
    prev = -1.0
    for m in o["markets"]:
        for k in ("id", "question", "threshold_date", "resolves_by", "yes", "no"):
            assert k in m
        assert m["yes"] + m["no"] == pytest.approx(1.0, abs=1e-6)
        assert m["yes"] >= prev, "odds of ending by a later date cannot be lower"
        prev = m["yes"]
        assert m["question"].endswith("?") and "2019" in m["question"]
        assert m["resolves_by"] == m["threshold_date"] >= o["as_of"]


def test_model_error_is_the_forecast_mode_rmse(odds_2019):
    fit = json.loads((ROOT / "app/model/hazard_fit.json").read_text())
    assert odds_2019["model_error_days"] == fit["skill"]["rmse_days_forecast"]


def test_no_gambling_language(odds_2019):
    text = json.dumps(odds_2019)
    assert "Not a market" in text
    hits = BANNED.findall(text)
    assert not hits, hits


def test_odds_as_of_validation(client: TestClient):
    future = (date.today() + timedelta(days=5)).isoformat()
    assert client.get("/api/forecast/odds", params={"as_of": future}).status_code == 422
    assert client.get("/api/forecast/odds", params={"as_of": "nope"}).status_code == 422
    assert client.get("/api/forecast/odds", params={"as_of": "1975-01-01"}).status_code == 422


def test_odds_default_today_works_offline(client: TestClient):
    r = client.get("/api/forecast/odds")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["live_weather"] is False      # network is severed in tests
    assert all(m["status"] == "open" for m in o["markets"])


def test_history_shape_and_order(client: TestClient):
    r = client.get("/api/forecast/history")
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["method"] and "observed up to" in h["method"]
    dates = [p["as_of"] for p in h["series"]]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
    for p in h["series"]:
        assert set(p) >= {"as_of", "p50_end_date", "markets"}
        assert all(0.0 <= v <= 1.0 for v in p["markets"].values())


def test_history_backfill_is_capped():
    from app.model import forecast
    built = json.loads((ROOT / "app/data/forecast_history.json").read_text())
    last = date.fromisoformat(built["series"][-1]["as_of"])
    h = forecast.forecast_history(today=last + timedelta(days=8), cap=2)
    assert h["computed_on_demand"] <= 2
    assert h["pending_points"] >= 5


def test_history_point_is_deterministic():
    from app.model import forecast
    a = forecast.compute_history_point("2026-09-01", n_sims=500)
    b = forecast.compute_history_point("2026-09-01", n_sims=500)
    assert a == b


def test_resolution_rule_on_a_synthetic_season():
    from app.model.forecast import season_labels
    d0 = date(2030, 1, 1)
    dates = [(d0 + timedelta(days=i)).isoformat() for i in range(400)]
    counts = [5] * 400
    for i in range(210, 300):            # ~29 Jul .. 27 Oct
        counts[i] = 400
    lab = season_labels(dates, counts, 2030)
    assert lab["status"] == "season"
    assert lab["start"] <= "2030-07-29" and "2030-10-25" <= lab["end"] <= "2030-10-31"
