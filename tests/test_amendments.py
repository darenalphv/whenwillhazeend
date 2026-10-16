"""Contract amendments A1-A6 (2026-09-18).

The frontend consumes these fields directly, so they are asserted here even
though the API is only a pass-through for A1-A5. A pass-through that quietly
drops a key is indistinguishable from a model that never produced it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import IN_SEASON_AS_OF, SEED

PROVENANCE_STATUSES = {"measured", "estimated", "synthesised", "modelled"}


def _summary_block(body: dict) -> dict:
    """A4 names ``season_summary()["summary"]``; /api/history nests that under
    its own ``summary`` key. Accept either nesting rather than guessing."""
    summary = body["summary"]
    inner = summary.get("summary")
    return inner if isinstance(inner, dict) else summary


def test_a1_season_echoes_resolved_oni(client: TestClient) -> None:
    body = client.post(
        "/api/simulate/season",
        json={"as_of": IN_SEASON_AS_OF, "n_sims": 200, "seed": SEED},
    ).json()
    assert "oni" in body, "A1: top-level resolved ONI missing"
    assert isinstance(body["oni"], (int, float))

    # An explicitly supplied ONI must come back unchanged.
    echoed = client.post(
        "/api/simulate/season",
        json={"as_of": IN_SEASON_AS_OF, "n_sims": 200, "seed": SEED, "oni": 1.8},
    ).json()
    assert echoed["oni"] == pytest.approx(1.8)


def test_a2_drivers_have_stable_ids(client: TestClient) -> None:
    body = client.post(
        "/api/simulate/season",
        json={"as_of": IN_SEASON_AS_OF, "n_sims": 200, "seed": SEED},
    ).json()
    ids = [d.get("id") for d in body["drivers"]]
    assert all(isinstance(i, str) and i for i in ids), f"A2: driver ids missing: {ids}"
    assert all(i == i.lower() and " " not in i for i in ids), f"A2: not snake_case: {ids}"
    assert len(set(ids)) == len(ids), f"A2: duplicate driver ids: {ids}"


def test_a3_dispersion_carries_caveats_and_totals(client: TestClient) -> None:
    body = client.post(
        "/api/dispersion",
        json={
            "sources": [
                {"lat": -2.21, "lon": 113.92, "area_ha": 1240.0, "pool": "peat_drained"}
            ],
            "wind_speed_ms": 4.0,
            "wind_dir_deg": 135.0,
        },
    ).json()
    assert body.keys() >= {"receptors", "plume", "caveats", "total_pm25_kg", "live"}
    assert isinstance(body["caveats"], list) and body["caveats"], (
        "A3: an endpoint with no caveats drops out of the Methods section"
    )
    assert body["total_pm25_kg"] > 0
    assert isinstance(body["live"], bool)


def test_a4_history_summary_fields(client: TestClient) -> None:
    body = client.get("/api/history").json()
    summary = _summary_block(body)
    required = {
        "n_seasons",
        "first_year",
        "last_year",
        "mean_duration_days",
        "mean_duration_el_nino",
        "mean_duration_la_nina",
        "total_burned_mha_kalimantan",
        "caveats",
    }
    missing = required - summary.keys()
    assert not missing, f"A4: summary missing {missing}"
    assert summary["n_seasons"] == len(body["seasons"])
    assert summary["first_year"] <= summary["last_year"]
    assert isinstance(summary["caveats"], list) and summary["caveats"]
    # El Nino seasons burn longer than La Nina seasons; if this inverts, the
    # ENSO labelling is wrong somewhere upstream.
    assert summary["mean_duration_el_nino"] >= summary["mean_duration_la_nina"]


def test_a5_per_pool_labels_match_fuel_pools(client: TestClient) -> None:
    fuel = client.get("/api/fuel").json()
    labels = {p["id"]: p["label"] for p in fuel["pools"]}

    body = client.post(
        "/api/simulate/exhaustion",
        json={"n_sims": 100, "horizon_years": 100, "seed": SEED},
    ).json()
    for pool in body["per_pool"]:
        assert "label" in pool, f"A5: per_pool entry {pool.get('id')} has no label"
        assert pool["label"] == labels[pool["id"]], (
            f"A5: label for {pool['id']} does not match /api/fuel"
        )


def test_a6_provenance_endpoint(client: TestClient) -> None:
    resp = client.get("/api/provenance")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    datasets = body["datasets"]
    assert isinstance(datasets, list) and datasets

    seen = set()
    for dataset in datasets:
        assert dataset.keys() >= {
            "id",
            "label",
            "source",
            "url",
            "coverage",
            "status",
            "notes",
        }, dataset
        assert dataset["status"] in PROVENANCE_STATUSES, dataset["status"]
        assert dataset["id"] not in seen, f"duplicate provenance id {dataset['id']}"
        seen.add(dataset["id"])

    # The load-bearing honesty claim: the fire clusters must not be described
    # as measured data.
    clusters = next((d for d in datasets if d["id"] == "fire_clusters"), None)
    assert clusters is not None, "provenance must cover the fire-cluster geometries"
    assert clusters["status"] == "synthesised", (
        f"fire clusters are labelled '{clusters['status']}', not 'synthesised'"
    )


def test_provenance_covers_the_datasets_the_api_serves(client: TestClient) -> None:
    """Every dataset the API serves must be accounted for in the table.

    The data layer rows are per-*number* rather than per-file (e.g. Kalimantan
    burned area is split into measured years and apportioned years, which is a
    more honest cut than one row per JSON file), so coverage is checked by id
    prefix.
    """
    ids = {d["id"] for d in client.get("/api/provenance").json()["datasets"]}
    for prefix in ("seasons", "regions", "fuel", "enso", "climatology",
                   "receptors", "fire_clusters"):
        assert any(i == prefix or i.startswith(prefix + "_") for i in ids), (
            f"provenance table has no row covering '{prefix}'"
        )


def test_provenance_does_not_launder_estimates_as_measurements(
    client: TestClient,
) -> None:
    """The fitted model parameters must not be filed under 'measured'."""
    rows = {d["id"]: d for d in client.get("/api/provenance").json()["datasets"]}
    assert rows["fuel_susceptibility"]["status"] in {"modelled", "estimated"}
    assert rows["fire_clusters"]["status"] == "synthesised"
    for row in rows.values():
        if row["status"] != "measured":
            continue
        if row["coverage"] == "none":
            # A row documenting the deliberate *absence* of a figure (e.g.
            # hotspot counts, which are not comparable across satellites) has
            # nothing to cite, but it must still say why.
            assert row["notes"], f"{row['id']} has no data and no explanation"
            continue
        assert row["source"], f"{row['id']} claims 'measured' with no source"
