"""Tests for the live "this week" endpoints (CONTRACT_V2 amendment M5).

No network: every upstream is a fake httpx transport that serves real responses
recorded on 2026-10-16 (``app/live/fixtures/``, provenance in ``SOURCES.json``).
The suite-wide ``no_network`` fixture stays in force; ``httpx.MockTransport`` is
not one of the transports it blocks.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import config
from app.live import bands, build, cache, cams, common, firms, nea, wind
from app.live.common import MYT
from app.live.regions import classify, region_ids
from app.main import app

FIX = Path(__file__).resolve().parents[1] / "app" / "live" / "fixtures"
NOW = datetime(2026, 10, 16, 13, 30, tzinfo=MYT)
FAKE_KEY = "test-key-not-real-0000"


class FakeUpstream:
    """Serves the recorded fixtures; ``down`` holds hosts that answer 503."""

    def __init__(self) -> None:
        self.down: set[str] = set()
        self.down_paths: set[str] = set()  # substrings of URL paths that answer 503
        self.requests: list[httpx.Request] = []
        self.bodies: list[dict] = []
        self.etag = '"fixture-etag-1"'

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.content else {}
        self.bodies.append(body)
        if host in self.down or any(sub in path for sub in self.down_paths):
            return httpx.Response(503, text="down")

        if host == "api-open.data.gov.sg":
            kind = path.rsplit("/", 1)[-1]
            day = request.url.params.get("date", "")
            f = FIX / f"nea_{kind}-{day[-2:]}.json"
            if not day.startswith("2026-10-") or not f.is_file():
                return httpx.Response(404, json={"code": 17, "errorMsg": "no data"})
            return httpx.Response(200, content=f.read_bytes(),
                                  headers={"content-type": "application/json"})

        if host in ("air-quality-api.open-meteo.com", "customer-air-quality-api.open-meteo.com"):
            if host.startswith("customer-") and not body.get("apikey"):
                return httpx.Response(401, json={"error": True, "reason": "API key required"})
            name = "cams_points.json" if "us_aqi" in body.get("hourly", "") else "cams_field_3x3.json"
            return httpx.Response(200, content=(FIX / name).read_bytes(),
                                  headers={"content-type": "application/json"})

        if host in ("api.open-meteo.com", "customer-api.open-meteo.com"):
            if host.startswith("customer-") and not body.get("apikey"):
                return httpx.Response(401, json={"error": True, "reason": "API key required"})
            return httpx.Response(200, content=(FIX / "wind_2x2.json").read_bytes(),
                                  headers={"content-type": "application/json"})

        if host == "firms.modaps.eosdis.nasa.gov":
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304)
            name = "firms_snpp_sample.csv" if "suomi-npp" in path else "firms_noaa20_sample.csv"
            return httpx.Response(200, content=(FIX / name).read_bytes(), headers={
                "content-type": "text/csv", "etag": self.etag,
                "last-modified": "Fri, 16 Oct 2026 04:50:39 GMT"})

        return httpx.Response(404)


class _Unlimited(nea._RateLimiter):
    @property
    def capacity(self) -> int:
        return 1000


@pytest.fixture
def upstream(monkeypatch, tmp_path) -> FakeUpstream:
    fake = FakeUpstream()

    def client_factory(timeout):
        return httpx.Client(transport=httpx.MockTransport(fake), timeout=timeout)

    for mod in (common, nea, firms):
        monkeypatch.setattr(mod, "http_client", client_factory, raising=False)
    for mod in (common, build, nea, cams):
        monkeypatch.setattr(mod, "now_myt", lambda: NOW, raising=False)
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    monkeypatch.delenv("DATA_GOV_SG_API_KEY", raising=False)
    # Most tests exercise CAMS through the developer opt-in ('free'); the 'off'
    # default and the 'key' mode have their own tests below.
    monkeypatch.setenv("WWHE_OPEN_METEO_ALLOW_FREE", "1")
    cache.set_disk_dir(tmp_path / "cache")
    for s in (nea.store, cams.points_store, cams.field_store, firms.store, wind.store):
        s.clear()
    monkeypatch.setattr(nea, "BACKGROUND_FILL", False)
    monkeypatch.setattr(nea, "limiter", _Unlimited())
    nea._recent_failures.clear()
    # A 3 x 3 field (1 deg) and 2 x 2 wind (2 deg) over 101-103 E, 1-3 N, matching the fixtures.
    monkeypatch.setitem(cams.FIELD_BOUNDS, "lon_min", 101.0)
    monkeypatch.setitem(cams.FIELD_BOUNDS, "lon_max", 103.0)
    monkeypatch.setitem(cams.FIELD_BOUNDS, "lat_min", 1.0)
    monkeypatch.setitem(cams.FIELD_BOUNDS, "lat_max", 3.0)
    yield fake
    for s in (nea.store, cams.points_store, cams.field_store, firms.store, wind.store):
        s.clear()
    cache.set_disk_dir(cache._default_dir())


@pytest.fixture
def api(upstream) -> TestClient:
    with TestClient(app) as c:
        yield c


def _walk(obj, path=""):
    if isinstance(obj, dict):
        yield path, obj
        for k, v in obj.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk(v, f"{path}[{i}]")


# --------------------------------------------------------------------------- #
# Bands
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("index", [bands.PSI, bands.API])
@pytest.mark.parametrize("value,expected", [
    (0, "Good"), (50, "Good"), (51, "Moderate"), (100, "Moderate"), (101, "Unhealthy"),
    (200, "Unhealthy"), (201, "Very Unhealthy"), (300, "Very Unhealthy"), (301, "Hazardous"),
    (500, "Hazardous"), (50.4, "Moderate"),
])
def test_psi_api_band_edges(index, value, expected):
    assert bands.band(index, value) == expected


@pytest.mark.parametrize("value,expected", [
    (50, "Good"), (51, "Moderate"), (100, "Moderate"), (101, "Unhealthy for Sensitive Groups"),
    (150, "Unhealthy for Sensitive Groups"), (151, "Unhealthy"), (200, "Unhealthy"),
    (201, "Very Unhealthy"), (300, "Very Unhealthy"), (301, "Hazardous"),
])
def test_us_aqi_band_edges(value, expected):
    assert bands.band(bands.US_AQI, value) == expected


def test_band_edge_cases():
    assert bands.band(bands.PSI, None) is None
    with pytest.raises(ValueError):
        bands.band("AQI", 10)
    # Same number, different scales, different bands: 150 is Unhealthy on PSI.
    assert bands.band(bands.PSI, 150) == "Unhealthy"
    assert bands.band(bands.US_AQI, 150) == "Unhealthy for Sensitive Groups"


# --------------------------------------------------------------------------- #
# /api/now/observed - labels, headline, failure isolation
# --------------------------------------------------------------------------- #

def test_observed_shape_and_labels(api):
    r = api.get("/api/now/observed")
    assert r.status_code == 200, r.text
    d = r.json()
    ids = [p["id"] for p in d["places"]]
    assert ids == ["sg-north", "sg-south", "sg-east", "sg-west", "sg-central",
                   "my-kuala-lumpur", "my-shah-alam", "my-klang", "my-petaling-jaya"]
    for p in d["places"]:
        assert {"id", "name", "area", "country", "lat", "lon", "official", "pm25", "modelled"} <= set(p)
        assert "Singapore" not in p["name"]  # short names; the area is separate
        m = p["modelled"]
        assert m["index"] == "US AQI (modelled, CAMS)" and m["measured"] is False
        assert m["grid_km"] == cams.GRID_KM and "CAMS" in m["source"]
        assert m["band"] == bands.band(bands.US_AQI, m["max"]["v"])
        if p["country"] == "SG":
            assert p["area"] == "Singapore"
            o = p["official"]
            assert o["index"] == "PSI" and o["averaging"] == "24h" and o["measured"] is True
            assert o["licence"] == "Singapore Open Data Licence v1.0"
            assert o["band"] == bands.band(bands.PSI, o["max"]["v"])
            assert p["pm25"]["averaging"] == "1h" and p["pm25"]["units"] == "µg/m³"
            assert "index" not in p["pm25"]  # a concentration is not an index
        else:
            assert p["official"] is None and p["pm25"] is None
            assert p["official_portal"]["url"] == "https://eqms.doe.gov.my/APIMS/main"
    assert {p["area"] for p in d["places"] if p["country"] == "MY"} == {"Kuala Lumpur", "Selangor"}


def test_no_cams_value_is_ever_labelled_api_or_psi(api):
    d = api.get("/api/now/observed").json()
    for path, node in _walk(d):
        if "portal" in path:
            continue  # a link to DOE's portal, not a reading
        idx = node.get("index")
        src = str(node.get("source", ""))
        if "CAMS" in src or "Open-Meteo" in src:
            assert idx in (None, bands.US_AQI), path
            assert node.get("measured") is not True, path
        if idx in ("PSI", "API"):
            assert "NEA" in src or "DOE" in src, path
    # No reading is labelled API at all: no Malaysian official feed is used.
    assert all(n.get("index") != "API" for path, n in _walk(d) if "portal" not in path)


def test_observed_real_week_values(api):
    """The recorded week: Central peaked at 24-h PSI 227 on 16 Oct; KL's model at 272."""
    d = api.get("/api/now/observed").json()
    by = {p["id"]: p for p in d["places"]}
    assert by["sg-central"]["official"]["max"] == {"t": "2026-10-16T09:00:00+08:00", "v": 227}
    assert by["sg-west"]["official"]["max"] == {"t": "2026-10-15T12:00:00+08:00", "v": 212}
    assert by["sg-west"]["pm25"]["max"] == {"t": "2026-10-14T15:00:00+08:00", "v": 288}
    assert by["my-kuala-lumpur"]["modelled"]["max"] == {"t": "2026-10-13T19:00:00+08:00", "v": 272}
    # KL and the three Selangor towns share one model value: said, not hidden.
    assert set(by["my-kuala-lumpur"]["modelled"]["same_model_values_as"]) == {
        "my-shah-alam", "my-klang", "my-petaling-jaya"}
    assert any("identical values for Kuala Lumpur" in c for c in d["caveats"])


def test_headline_prefers_measured_over_modelled(api):
    h = api.get("/api/now/observed").json()["headline"]
    # The modelled KL value (272) is higher than any PSI, but the measured reading leads.
    assert h["measured"] is True and h["index"] == "PSI"
    assert h["place_id"] == "sg-central" and h["value"] == 227 and h["band"] == "Very Unhealthy"
    assert h["text"] == ("Singapore's Central region recorded a 24-hour PSI of 227 — Very "
                         "Unhealthy — at 9 am on 16 October. Official NEA reading.")
    assert "modelled" not in h["text"]


def test_headline_falls_back_to_modelled_when_nea_down(api, upstream):
    upstream.down.add("api-open.data.gov.sg")
    r = api.get("/api/now/observed")
    assert r.status_code == 200
    d = r.json()
    assert d["sources"]["nea"]["available"] is False
    assert all(p["official"] is None for p in d["places"])
    h = d["headline"]
    assert h["measured"] is False and h["index"] == bands.US_AQI
    assert h["place_id"] == "my-kuala-lumpur" and h["value"] == 272
    # "modelled" sits next to the number, not in a footnote.
    assert "modelled US AQI of 272" in h["text"]
    assert "not a measurement" in h["text"]


def test_modelled_headline_only_from_places_without_official():
    """A place with an official reading is never headlined by its modelled value."""
    sg = {"id": "sg-west", "official": {"index": "PSI", "averaging": "24h", "source": "NEA",
                                        "max": {"t": "2026-10-14T12:00:00+08:00", "v": 90}},
          "modelled": {"max": {"t": "2026-10-14T12:00:00+08:00", "v": 290}, "same_model_values_as": []}}
    assert build.compute_headline([sg]) is None
    kl = {"id": "my-kuala-lumpur", "official": None,
          "modelled": {"max": {"t": "2026-10-13T19:00:00+08:00", "v": 180}, "same_model_values_as": []}}
    h = build.compute_headline([sg, kl])
    assert h["place_id"] == "my-kuala-lumpur" and h["measured"] is False and h["band"] == "Unhealthy"


def test_headline_null_when_nothing_worth_headlining():
    calm = [
        {"id": "sg-west", "official": {"index": "PSI", "averaging": "24h", "source": "NEA",
                                       "max": {"t": "2026-10-14T12:00:00+08:00", "v": 100}},
         "modelled": {"max": {"t": "2026-10-14T12:00:00+08:00", "v": 140}, "same_model_values_as": []}},
        {"id": "my-klang", "official": None,
         "modelled": {"max": {"t": "2026-10-14T12:00:00+08:00", "v": 150}, "same_model_values_as": []}},
    ]
    assert build.compute_headline(calm) is None
    assert build.compute_headline([]) is None


def test_measured_wins_even_when_modelled_is_higher():
    places = [
        {"id": "sg-north", "official": {"index": "PSI", "averaging": "24h", "source": "NEA",
                                        "max": {"t": "2026-10-14T12:00:00+08:00", "v": 120}},
         "modelled": None},
        {"id": "my-kuala-lumpur", "official": None,
         "modelled": {"max": {"t": "2026-10-13T19:00:00+08:00", "v": 290}, "same_model_values_as": []}},
    ]
    h = build.compute_headline(places)
    assert h["measured"] is True and h["value"] == 120 and h["band"] == "Unhealthy"


def test_cams_down_keeps_official_readings(api, upstream):
    upstream.down.add("air-quality-api.open-meteo.com")
    d = api.get("/api/now/observed").json()
    assert d["sources"]["cams"]["available"] is False
    assert all(p["modelled"] is None for p in d["places"])
    assert d["places"][4]["official"]["max"]["v"] == 227
    assert d["headline"]["measured"] is True


def test_both_sources_down_still_answers(api, upstream):
    upstream.down.update({"api-open.data.gov.sg", "air-quality-api.open-meteo.com"})
    r = api.get("/api/now/observed")
    assert r.status_code == 200
    d = r.json()
    assert d["headline"] is None
    assert all(p["official"] is None and p["modelled"] is None for p in d["places"])


def test_nea_rate_limit_gives_partial_week_not_an_error(api, upstream, monkeypatch):
    monkeypatch.setattr(nea, "limiter", nea._RateLimiter())  # the real 5-per-10-s pacing
    d = api.get("/api/now/observed").json()
    missing = d["sources"]["nea"]["missing_dates"]
    # newest PSI days first: today and the four before it are in, the rest wait
    assert "2026-10-16" not in missing["psi"] and missing["psi"] == ["2026-10-09", "2026-10-10", "2026-10-11"]
    assert any("not loaded yet" in c for c in d["caveats"])
    assert d["places"][4]["official"] is not None


def test_stale_value_served_when_refresh_fails(upstream, monkeypatch):
    cache.set_disk_dir(None)  # memory only, so expiring the entry below is enough
    first = cams.get_points()
    assert first.stale is False
    entry = cams.points_store.get(next(iter(cams.points_store._mem)))
    entry.fetched_at -= cams.POINTS_TTL_S + 60  # expire it
    upstream.down.add("air-quality-api.open-meteo.com")
    again = cams.get_points()
    assert again.stale is True and again.error and again.value == first.value


# --------------------------------------------------------------------------- #
# Time zones
# --------------------------------------------------------------------------- #

def test_timezone_handling(api):
    d = api.get("/api/now/observed").json()
    assert d["timezone"] == "Asia/Kuala_Lumpur (UTC+08:00)"
    assert any("Singapore (SGT) uses the same offset" in c for c in d["caveats"])
    for _, node in _walk(d):
        if "t" in node and isinstance(node["t"], str):
            assert node["t"].endswith("+08:00"), node
    # NEA's SGT stamp passes through as the same instant.
    raw = json.loads((FIX / "nea_psi-14.json").read_text())["data"]["items"][0]["timestamp"]
    rows = nea.parse_day(json.loads((FIX / "nea_psi-14.json").read_text()), "psi")
    assert datetime.fromisoformat(rows[-1][0]) == datetime.fromisoformat(raw)
    # Open-Meteo is requested in GMT; 11:00 UTC is 19:00 in Kuala Lumpur.
    assert common.iso(common.from_utc_naive("2026-10-13T11:00")) == "2026-10-13T19:00:00+08:00"
    assert common.human_time(datetime(2026, 10, 16, 9, tzinfo=MYT)) == "9 am on 16 October"
    assert common.human_time(datetime(2026, 10, 13, 19, tzinfo=MYT)) == "7 pm on 13 October"
    assert common.human_time(datetime(2026, 10, 13, 0, tzinfo=MYT)) == "12 am on 13 October"


def test_window_bounds_series(api):
    d = api.get("/api/now/observed", params={"days": 2}).json()
    lo = datetime.fromisoformat(d["window"]["start"])
    hi = datetime.fromisoformat(d["window"]["end"])
    assert hi - lo == timedelta(days=2)
    for p in d["places"]:
        for s in (p["official"] or {}).get("series", []) + p["modelled"]["series"]:
            assert lo <= datetime.fromisoformat(s["t"]) <= hi
    assert api.get("/api/now/observed", params={"days": 8}).status_code == 422


# --------------------------------------------------------------------------- #
# /api/now/hotspots
# --------------------------------------------------------------------------- #

def test_hotspots_from_recorded_firms(api):
    r = api.get("/api/now/hotspots")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sensor"] == "suomi-npp" and "FIRMS" in d["attribution"]
    c = d["count_by_region"]
    assert set(c) == set(region_ids())
    assert c["kalimantan"] > c["sumatra"] > c["peninsular_malaysia"]
    # the sample holds 793 nominal/high rows; low-confidence rows are dropped
    assert d["total"] == sum(c.values()) <= 793
    assert sum(sum(v for k, v in day.items() if k != "date") for day in d["by_day"]) == d["total"]
    feats = d["clusters"]["features"]
    assert d["clusters"]["type"] == "FeatureCollection" and 0 < len(feats) < d["total"]
    assert sum(f["properties"]["count"] for f in feats) == d["total"]
    for f in feats[:20]:
        assert set(f["properties"]) == {"count", "frp_mw", "first_date", "last_date", "region", "daily"}
        assert len(f["properties"]["daily"]) == len(d["by_day"])
    assert d["data_updated"] == "2026-10-16T12:50:39+08:00"


def test_hotspot_low_confidence_is_dropped():
    with open(FIX / "firms_snpp_sample.csv") as fh:
        det = firms.parse_lines(fh)
    assert det["t"].size == 793 and set(np.unique(det["conf"]).tolist()) <= {1, 2}


def test_hotspots_fall_back_to_noaa20(api, upstream):
    upstream.down_paths.add("suomi-npp")
    d = api.get("/api/now/hotspots").json()
    assert d["sensor"] == "noaa-20" and "NOAA-20" in d["source"]
    assert any("using NOAA-20" in c for c in d["caveats"])
    assert d["total"] <= 97  # NOAA-20 alone; never added to Suomi NPP


def test_hotspots_503_when_firms_down(api, upstream):
    upstream.down.add("firms.modaps.eosdis.nasa.gov")
    r = api.get("/api/now/hotspots")
    assert r.status_code == 503 and "FIRMS" in r.json()["detail"]


def test_firms_conditional_get_reuses_unchanged_file(upstream):
    res, sensor, _ = firms.get_detections()
    n = res.value["t"].size
    firms.store._mem[sensor].fetched_at -= firms.TTL_S + 1
    for p in list((cache.disk_dir()).glob("firms__*")):
        p.unlink()
    res2, _, _ = firms.get_detections()
    assert upstream.requests[-1].headers.get("if-none-match") == upstream.etag
    assert res2.value["t"].size == n and res2.stale is False


def test_regions_known_places():
    pts = {"Palangka Raya": (113.92, -2.21, "kalimantan"), "Kuching": (110.35, 1.55, "sarawak_sabah_brunei"),
           "Pekanbaru": (101.45, 0.51, "sumatra"), "Kuala Lumpur": (101.69, 3.14, "peninsular_malaysia"),
           "Singapore": (103.82, 1.35, "other"), "Jakarta": (106.8, -6.2, "other"),
           "Batam": (104.03, 1.08, "sumatra"), "Tarakan": (117.58, 3.30, "kalimantan")}
    lon = np.array([v[0] for v in pts.values()])
    lat = np.array([v[1] for v in pts.values()])
    got = [region_ids()[i] for i in classify(lon, lat)]
    assert got == [v[2] for v in pts.values()]


# --------------------------------------------------------------------------- #
# /api/now/field
# --------------------------------------------------------------------------- #

def test_field_shape_and_wind(api):
    r = api.get("/api/now/field")
    assert r.status_code == 200, r.text
    d = r.json()
    g = d["grid"]
    assert (g["nx"], g["ny"], g["dlon"]) == (3, 3, 1.0)
    assert d["measured"] is False and d["units"] == "µg/m³" and "CAMS" in d["source"]
    times = [datetime.fromisoformat(f["t"]) for f in d["frames"]]
    assert len(times) == 56 and all(b - a == timedelta(hours=3) for a, b in zip(times, times[1:]))
    for f in d["frames"]:
        assert len(f["values"]) == g["nx"] * g["ny"]
        assert all(v is None or isinstance(v, int) for v in f["values"])
    w = d["wind"]
    assert (w["nx"], w["ny"], w["levels_m"]) == (2, 2, [10, 1500])
    assert "850 hPa" in w["level_note"] and w["measured"] is False
    assert [f["t"] for f in w["frames"]] == [f["t"] for f in d["frames"]]
    assert all(len(f["u"]) == len(f["v"]) == 2 * w["nx"] * w["ny"] for f in w["frames"])


def test_wind_components_follow_meteorological_convention():
    payload = json.loads((FIX / "wind_2x2.json").read_text())
    h = payload[0]["hourly"]
    out = wind.parse(payload, 4)
    s, deg = h["wind_speed_10m"][0], h["wind_direction_10m"][0]
    # wind FROM the north-east blows towards the south-west: u < 0, v < 0
    assert out["u10"][0, 0] == pytest.approx(-s * np.sin(np.radians(deg)), abs=1e-4)
    assert out["v10"][0, 0] == pytest.approx(-s * np.cos(np.radians(deg)), abs=1e-4)
    assert np.hypot(out["u850"][0, 0], out["v850"][0, 0]) == pytest.approx(h["wind_speed_850hPa"][0], abs=1e-3)


def test_field_step_hours(api):
    d6 = api.get("/api/now/field", params={"step_hours": 6}).json()
    assert len(d6["frames"]) == 28
    assert api.get("/api/now/field", params={"step_hours": 5}).status_code == 422


def test_field_wind_down_keeps_pm25(api, upstream):
    upstream.down.add("api.open-meteo.com")
    d = api.get("/api/now/field").json()
    assert d["wind"] is None and len(d["frames"]) == 56
    assert any("wind is unavailable" in c for c in d["caveats"])


def test_field_503_when_cams_down(api, upstream):
    upstream.down.add("air-quality-api.open-meteo.com")
    r = api.get("/api/now/field")
    assert r.status_code == 503 and "CAMS" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# Open-Meteo commercial key switch
# --------------------------------------------------------------------------- #

def test_open_meteo_key_switches_to_customer_endpoints(api, upstream, monkeypatch, caplog):
    assert cams.FIELD_DEG_KEYED == cams.NATIVE_DEG  # keyed tier samples CAMS natively
    monkeypatch.setattr(cams, "FIELD_DEG_KEYED", 1.0)  # ...but the fixture is a 1-degree 3 x 3
    monkeypatch.setenv("OPEN_METEO_API_KEY", FAKE_KEY)
    monkeypatch.delenv("WWHE_OPEN_METEO_ALLOW_FREE", raising=False)
    assert config.open_meteo_mode() == "key"
    caplog.set_level(logging.DEBUG)
    obs = api.get("/api/now/observed")
    fld = api.get("/api/now/field")
    assert obs.status_code == fld.status_code == 200
    om = [(req, body) for req, body in zip(upstream.requests, upstream.bodies)
          if "open-meteo" in req.url.host]
    assert om and all(req.url.host.startswith("customer-") for req, _ in om)
    assert all(body.get("apikey") == FAKE_KEY for _, body in om)
    assert all(FAKE_KEY not in str(req.url) for req, _ in om)  # never in a URL
    assert FAKE_KEY not in obs.text and FAKE_KEY not in fld.text
    assert FAKE_KEY not in caplog.text
    assert obs.json()["sources"]["cams"]["keyed"] is True


def test_without_key_uses_free_endpoints(api, upstream):
    assert config.open_meteo_mode() == "free"
    api.get("/api/now/observed")
    om = [r for r in upstream.requests if "open-meteo" in r.url.host]
    assert om and all(not r.url.host.startswith("customer-") for r in om)
    assert all("apikey" not in b for r, b in zip(upstream.requests, upstream.bodies)
               if "open-meteo" in r.url.host)


# --------------------------------------------------------------------------- #
# Open-Meteo licensing policy 'off' (the production default)
# --------------------------------------------------------------------------- #

def _open_meteo_requests(upstream) -> list[httpx.Request]:
    return [r for r in upstream.requests if "open-meteo" in r.url.host]


@pytest.fixture
def off(monkeypatch, upstream):
    monkeypatch.delenv("WWHE_OPEN_METEO_ALLOW_FREE", raising=False)
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    assert config.open_meteo_mode() == "off"
    return upstream


def test_off_observed_keeps_nea_and_makes_no_open_meteo_call(api, off):
    r = api.get("/api/now/observed")
    assert r.status_code == 200
    d = r.json()
    assert _open_meteo_requests(off) == []
    cams_src = d["sources"]["cams"]
    assert cams_src["available"] is False and cams_src["mode"] == "off"
    assert cams_src["reason"] == config.OPEN_METEO_OFF_REASON
    assert d["sources"]["nea"]["available"] is True
    for p in d["places"]:
        assert p["modelled"] is None
        if p["country"] == "MY":
            assert p["official"] is None
            assert p["official_portal"]["url"] == "https://eqms.doe.gov.my/APIMS/main"
        else:
            assert p["official"]["index"] == "PSI"
    h = d["headline"]
    assert h["measured"] is True and h["place_id"] == "sg-central" and h["value"] == 227
    msg = [c for c in d["caveats"] if c.startswith("No modelled or official reading for Kuala Lumpur")]
    assert len(msg) == 1 and "https://eqms.doe.gov.my/APIMS/main" in msg[0]
    assert "non-commercial" in msg[0]
    assert not any("show only the CAMS model" in c for c in d["caveats"])


def test_off_field_is_503_with_licensing_reason_and_stays_in_openapi(api, off):
    r = api.get("/api/now/field")
    assert r.status_code == 503
    assert config.OPEN_METEO_OFF_REASON in r.json()["detail"]
    assert "/api/now/field" in api.get("/api/openapi.json").json()["paths"]
    assert _open_meteo_requests(off) == []


def test_off_wind_and_points_refuse_without_network(off):
    for fn in (cams.get_points, cams.get_field, wind.get_wind):
        with pytest.raises(cams.OpenMeteoOff):
            fn()
    assert _open_meteo_requests(off) == []


def test_off_does_not_serve_values_cached_under_another_mode(api, upstream, monkeypatch):
    assert api.get("/api/now/observed").json()["places"][5]["modelled"] is not None  # 'free'
    n = len(_open_meteo_requests(upstream))
    monkeypatch.delenv("WWHE_OPEN_METEO_ALLOW_FREE")
    d = api.get("/api/now/observed").json()
    assert all(p["modelled"] is None for p in d["places"])
    assert api.get("/api/now/field").status_code == 503
    assert len(_open_meteo_requests(upstream)) == n


def test_off_hotspots_unaffected(api, off):
    assert api.get("/api/now/hotspots").status_code == 200
    assert _open_meteo_requests(off) == []


def test_off_warm_up_thread_makes_no_open_meteo_call(off):
    import threading

    import app.live as live

    live.warm_in_background()
    for t in threading.enumerate():
        if t.name == "live-warm":
            t.join(timeout=30)
    assert _open_meteo_requests(off) == []
    assert any("firms" in r.url.host for r in off.requests)  # it did warm the rest


# --------------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------------- #

def test_routes_are_in_openapi(api):
    paths = api.get("/api/openapi.json").json()["paths"]
    for p in ("/api/now/observed", "/api/now/hotspots", "/api/now/field"):
        assert p in paths


def test_disk_cache_shared_between_processes(upstream):
    """A second process (simulated by clearing memory) reads the first one's fetch."""
    cams.get_points()
    n = len(upstream.requests)
    cams.points_store.clear()
    cams.get_points()
    assert len(upstream.requests) == n
