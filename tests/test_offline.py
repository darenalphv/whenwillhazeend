"""The app must work with no network and no API keys.

The suite is offline by default (see conftest.no_network). This module proves
the stronger claim from CONTRACT.md: the app *imports* and *serves* in a process
where outbound connections are impossible, exercised through the raw ASGI
interface so that not even the test client touches httpx.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import IN_SEASON_AS_OF, SEED

ROOT = Path(__file__).resolve().parents[1]

# Runs in a subprocess with sockets and httpx transports disabled before
# app.main is ever imported.
OFFLINE_SCRIPT = r'''
import json, socket, sys, asyncio

class Blocked(RuntimeError):
    pass

def _blocked(*a, **k):
    raise Blocked("network is disabled")

# Kill sockets outright, before anything imports.
socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked
socket.getaddrinfo = _blocked

import httpx
httpx.HTTPTransport.handle_request = _blocked
httpx.AsyncHTTPTransport.handle_async_request = _blocked

from app.main import app  # must not raise

async def call(method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "headers": [
            (b"host", b"testserver"), (b"content-type", b"application/json"),
            (b"content-length", str(len(raw)).encode()),
        ],
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
    }
    messages = [{"type": "http.request", "body": raw, "more_body": False}]
    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}
    out = {"status": None, "body": b""}
    async def send(message):
        if message["type"] == "http.response.start":
            out["status"] = message["status"]
        elif message["type"] == "http.response.body":
            out["body"] += message.get("body", b"")
    await app(scope, receive, send)
    return out

async def main():
    results = {}
    for method, path, body in [
        ("GET", "/api/health", None),
        ("GET", "/api/fuel", None),
        ("POST", "/api/simulate/season",
         {"as_of": "__AS_OF__", "n_sims": 200, "seed": __SEED__}),
        ("POST", "/api/simulate/exhaustion",
         {"n_sims": 100, "horizon_years": 100, "seed": __SEED__}),
    ]:
        out = await call(method, path, body)
        payload = json.loads(out["body"]) if out["body"] else None
        results[path] = {"status": out["status"],
                         "keys": sorted(payload.keys()) if isinstance(payload, dict) else None,
                         "detail": payload.get("detail") if isinstance(payload, dict) else None}
    print(json.dumps(results))

asyncio.run(main())
'''


def test_app_imports_and_serves_with_no_network() -> None:
    script = OFFLINE_SCRIPT.replace("__AS_OF__", IN_SEASON_AS_OF).replace(
        "__SEED__", str(SEED)
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert proc.returncode == 0, (
        "the app failed to import or serve without network:\n" + proc.stderr[-4000:]
    )
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    for path, result in results.items():
        assert result["status"] == 200, f"{path} -> {result['status']}: {result['detail']}"
    assert results["/api/health"]["keys"] == [
        "live_weather_available",
        "status",
        "version",
    ]


def test_every_endpoint_answers_while_offline(client: TestClient) -> None:
    """A sweep: nothing 5xx's, nothing hangs, when the wire is cut."""
    year = client.get("/api/history").json()["seasons"][-1]["year"]
    calls = [
        ("GET", "/", None, None),
        ("GET", "/api/health", None, None),
        ("GET", "/api/history", None, None),
        ("GET", f"/api/history/{year}", None, None),
        ("GET", "/api/regions", None, None),
        ("GET", "/api/fuel", None, None),
        ("GET", "/api/receptors", None, None),
        (
            "GET",
            "/api/meteo",
            {"lat": -2.21, "lon": 113.92, "start": "2026-09-01", "end": "2026-09-05"},
            None,
        ),
        (
            "POST",
            "/api/simulate/season",
            None,
            {"as_of": IN_SEASON_AS_OF, "n_sims": 200, "seed": SEED},
        ),
        (
            "POST",
            "/api/simulate/exhaustion",
            None,
            {"n_sims": 100, "horizon_years": 100, "seed": SEED},
        ),
        (
            "POST",
            "/api/dispersion",
            None,
            {
                "sources": [
                    {
                        "lat": -2.21,
                        "lon": 113.92,
                        "area_ha": 900.0,
                        "pool": "peat_drained",
                    }
                ],
                "wind_speed_ms": 3.0,
                "wind_dir_deg": 120.0,
            },
        ),
    ]
    for method, path, params, body in calls:
        resp = client.request(method, path, params=params, json=body)
        assert resp.status_code == 200, f"{method} {path} -> {resp.status_code}: {resp.text[:300]}"
