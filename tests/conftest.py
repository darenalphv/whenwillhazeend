"""Shared fixtures.

The important one is :func:`no_network`. The contract requires the app to work
with no network and no API keys, so the default posture for the whole suite is
*offline*: outbound HTTP is severed at the httpx transport layer and at the
socket layer. The in-process ASGI transport that TestClient uses is untouched,
so requests to our own app still work.
"""

from __future__ import annotations

import os

# Tests must never touch the network at app startup.
os.environ.setdefault("WWHE_LIVE_WARM", "0")


import socket
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import deps
from app.main import app

# Any as_of inside the south-east Asian burning season (roughly June-November).
IN_SEASON_AS_OF = "2026-09-18"
SEED = 20260918


class BlockedNetwork(RuntimeError):
    """Raised instead of making a real outbound connection."""


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Sever every real outbound connection for the duration of a test."""

    def _blocked(*args, **kwargs):
        raise BlockedNetwork(
            "outbound network access is blocked in tests; the app must work offline"
        )

    # httpx: the transports that actually talk to the wire. TestClient uses
    # ASGITransport, which is deliberately left alone.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked, raising=False)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", _blocked, raising=False
    )
    # Belt and braces: anything reaching for a raw socket fails too.
    monkeypatch.setattr(socket.socket, "connect", _blocked, raising=False)
    monkeypatch.setattr(socket, "create_connection", _blocked, raising=False)
    yield


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    """Memoised simulations and the live-weather probe must not leak between tests."""
    deps.clear_sim_caches()
    deps.reset_live_probe()
    yield
    deps.clear_sim_caches()
    deps.reset_live_probe()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


@pytest.fixture
def seasons(client: TestClient) -> list[dict]:
    resp = client.get("/api/history")
    assert resp.status_code == 200, resp.text
    return resp.json()["seasons"]
