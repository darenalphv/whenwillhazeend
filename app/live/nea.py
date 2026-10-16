"""Singapore NEA PSI and PM2.5 readings via data.gov.sg (official, measured).

Endpoints (data.gov.sg v2 real-time API, documented, no key required):

* ``/v2/real-time/api/psi?date=YYYY-MM-DD``  -> ``readings.psi_twenty_four_hourly``
* ``/v2/real-time/api/pm25?date=YYYY-MM-DD`` -> ``readings.pm25_one_hourly`` (µg/m³)

Each returns the hourly items for one date, keyed by region (north, south, east,
west, central), timestamps in SGT (``+08:00``, same offset as Kuala Lumpur).

Licence: Singapore Open Data Licence v1.0 - commercial use permitted, attribution
required, no implied endorsement (https://data.gov.sg/open-data-licence).

Rate limit (guide.data.gov.sg, "API rate limits"): **6 calls per 10 s without a
key**, 12 with a dev key, 30 with a prod key; excess returns HTTP 429. A week is
16 calls (8 dates x 2 endpoints), so this module

* caches each (endpoint, date) separately - finished days rarely change, so they
  are kept 12 h and only today's file is refetched every 30 min;
* paces calls with a sliding-window limiter (5 / 10 s, or 25 / 10 s when the
  optional ``DATA_GOV_SG_API_KEY`` is set - sent as the ``x-api-key`` header,
  never logged);
* inside a request, fetches only what the limiter allows *right now* (never
  sleeps, never waits on the other worker), within ``deadline_s``. The rest is
  reported as missing and filled by a background thread, so a cold start returns
  the newest days in ~1-2 s and the full week ~20 s later.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from typing import Any

from app.live.cache import Store, single_flight
from app.live.common import MYT, SourceUnavailable, describe_error, http_client, log, now_myt

BASE = "https://api-open.data.gov.sg/v2/real-time/api"
KINDS: dict[str, tuple[str, str]] = {
    "psi": ("psi", "psi_twenty_four_hourly"),
    "pm25": ("pm25", "pm25_one_hourly"),
}
REGIONS = ("north", "south", "east", "west", "central")

SOURCE = "National Environment Agency (NEA), Singapore, via data.gov.sg"
LICENCE = "Singapore Open Data Licence v1.0"
LICENCE_URL = "https://data.gov.sg/open-data-licence"
API_URL = {k: f"{BASE}/{v[0]}" for k, v in KINDS.items()}

TIMEOUT_S = 8.0
TTL_TODAY_S = 30 * 60
TTL_YESTERDAY_S = 2 * 3600
TTL_PAST_S = 12 * 3600
MAX_STALE_S = 36 * 3600
DEFAULT_DEADLINE_S = 9.0
BACKGROUND_FILL = True  # tests switch this off

store = Store("nea")


def _api_key() -> str | None:
    return os.environ.get("DATA_GOV_SG_API_KEY", "").strip() or None


class _RateLimiter:
    """Sliding-window limiter: at most ``capacity`` calls in any ``window`` s."""

    def __init__(self, window: float = 10.5) -> None:
        self.window = window
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return 25 if _api_key() else 5

    def acquire(self, deadline: float) -> bool:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._calls and now - self._calls[0] >= self.window:
                    self._calls.popleft()
                if len(self._calls) < self.capacity:
                    self._calls.append(now)
                    return True
                wait = self.window - (now - self._calls[0]) + 0.05
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(wait)

    def penalise(self) -> None:
        """After a 429, treat the window as full."""
        with self._lock:
            now = time.monotonic()
            self._calls.extend([now] * self.capacity)


limiter = _RateLimiter()


# --------------------------------------------------------------------------- #
# Parsing (pure; fixtures exercise this directly)
# --------------------------------------------------------------------------- #

def parse_day(payload: dict, kind: str) -> list[list[Any]]:
    """``[[iso_timestamp, {region: value}], ...]`` sorted oldest first."""
    if not isinstance(payload, dict) or payload.get("code") not in (0, None):
        raise ValueError(f"data.gov.sg error: {payload.get('errorMsg') if isinstance(payload, dict) else payload!r}")
    items = (payload.get("data") or {}).get("items")
    if not isinstance(items, list):
        raise ValueError("data.gov.sg payload has no data.items list")
    reading_key = KINDS[kind][1]
    rows: list[list[Any]] = []
    for item in items:
        ts = item.get("timestamp")
        readings = (item.get("readings") or {}).get(reading_key)
        if not ts or not isinstance(readings, dict):
            continue
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:  # never seen, but SGT is the documented zone
            dt = dt.replace(tzinfo=MYT)
        vals = {r: int(round(v)) for r, v in readings.items()
                if r in REGIONS and isinstance(v, (int, float))}
        if vals:
            rows.append([dt.astimezone(MYT).isoformat(), vals])
    rows.sort(key=lambda r: r[0])
    return rows


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #

def fetch_day(kind: str, day: date, timeout: float = TIMEOUT_S) -> list[list[Any]]:
    """One (endpoint, date) from data.gov.sg. Raises on any failure."""
    headers = {}
    key = _api_key()
    if key:
        headers["x-api-key"] = key
    with http_client(timeout) as client:
        r = client.get(API_URL[kind], params={"date": day.isoformat()}, headers=headers)
        if r.status_code == 429:
            limiter.penalise()
        r.raise_for_status()
        return parse_day(r.json(), kind)


def _ttl(day: date, today: date) -> float:
    if day >= today:
        return TTL_TODAY_S
    if day == today - timedelta(days=1):
        return TTL_YESTERDAY_S
    return TTL_PAST_S


def _key(kind: str, day: date) -> str:
    return f"{kind}:{day.isoformat()}"


# A failed (kind, day) is not retried *inside a request* for this long; the
# background filler keeps trying. Stops a slow or down NEA from adding its
# timeout to every page view.
INLINE_RETRY_AFTER_S = 120.0
_recent_failures: dict[tuple[str, date], float] = {}


def _fill(jobs: list[tuple[str, date]], today: date, deadline: float, *,
          inline: bool) -> dict[tuple[str, date], str]:
    """Fetch the jobs that are not fresh, in order, until ``deadline``.

    ``inline`` (inside a request): never sleep for the rate limiter, never wait
    for the other worker's lock, skip recently failed jobs. Background: pace
    politely and keep going until ``deadline``.

    Returns ``{(kind, day): reason}`` for jobs that failed or were not reached.
    """
    failures: dict[tuple[str, date], str] = {}
    lock_wait = 0.0 if inline else max(0.0, deadline - time.monotonic())
    with single_flight("nea:fill", timeout=lock_wait) as got:
        for kind, day in jobs:
            e = store.get(_key(kind, day))
            if e is not None and e.age() < _ttl(day, today):
                continue  # filled by the other worker while we waited
            if not got:
                failures[(kind, day)] = "being fetched by another worker"
                continue
            if inline and time.monotonic() - _recent_failures.get((kind, day), -1e9) < INLINE_RETRY_AFTER_S:
                failures[(kind, day)] = "recent fetch failed; retrying in the background"
                continue
            remaining = deadline - time.monotonic()
            if remaining < 1.0 or not limiter.acquire(time.monotonic() if inline else deadline):
                failures[(kind, day)] = "queued (data.gov.sg rate limit)"
                continue
            try:
                store.put(_key(kind, day), fetch_day(kind, day, timeout=min(TIMEOUT_S, remaining)))
                _recent_failures.pop((kind, day), None)
            except Exception as exc:
                _recent_failures[(kind, day)] = time.monotonic()
                failures[(kind, day)] = describe_error(exc)
                log.warning("NEA %s %s fetch failed: %s", kind, day, describe_error(exc))
    return failures


_bg_lock = threading.Lock()
_bg_running = False


def background_running() -> bool:
    with _bg_lock:
        return _bg_running


def _background(jobs: list[tuple[str, date]], today: date) -> None:
    global _bg_running
    with _bg_lock:
        if _bg_running:
            return
        _bg_running = True

    def run() -> None:
        global _bg_running
        try:
            deadline = time.monotonic() + 90.0
            pending = jobs
            while pending and time.monotonic() < deadline - 5:
                failed = _fill(pending, today, deadline, inline=False)
                pending = [j for j in failed
                           if (e := store.get(_key(*j))) is None or e.age() >= _ttl(j[1], today)]
                if pending:
                    time.sleep(5.0)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("NEA background fill failed: %s", describe_error(exc))
        finally:
            with _bg_lock:
                _bg_running = False

    threading.Thread(target=run, name="nea-fill", daemon=True).start()


def get_window(start: datetime, end: datetime, *,
               deadline_s: float = DEFAULT_DEADLINE_S) -> dict:
    """Hourly PSI (24-h) and PM2.5 (1-h) by region between ``start`` and ``end``.

    Never raises for a partial result; raises :class:`SourceUnavailable` only if
    no PSI reading at all is available for the window.
    """
    today = now_myt().date()
    first, last = start.astimezone(MYT).date(), end.astimezone(MYT).date()
    days = [last - timedelta(days=i) for i in range((last - first).days + 1)]  # newest first
    jobs = [(kind, d) for kind in ("psi", "pm25") for d in days]

    need = []
    for kind, d in jobs:
        e = store.get(_key(kind, d), fresh_for=_ttl(d, today))
        if e is None or e.age() >= _ttl(d, today):
            need.append((kind, d))

    failures: dict[tuple[str, date], str] = {}
    if need:
        if background_running():
            failures = {job: "being fetched in the background" for job in need}
        else:
            failures = _fill(need, today, time.monotonic() + deadline_s, inline=True)
        if failures and BACKGROUND_FILL:
            _background(list(failures), today)

    out: dict[str, dict[str, list[tuple[datetime, int]]]] = {k: {r: [] for r in REGIONS} for k in KINDS}
    missing: dict[str, list[str]] = {k: [] for k in KINDS}
    stale: dict[str, list[str]] = {k: [] for k in KINDS}
    fetched: list[float] = []
    lo, hi = start.astimezone(MYT), end.astimezone(MYT)
    for kind, d in jobs:
        e = store.get(_key(kind, d))
        if e is None or e.age() >= MAX_STALE_S:
            missing[kind].append(d.isoformat())
            continue
        if (kind, d) in failures or e.age() >= _ttl(d, today):
            stale[kind].append(d.isoformat())
        fetched.append(e.fetched_at)
        for ts, vals in e.value:
            dt = datetime.fromisoformat(ts)
            if lo <= dt <= hi:
                for region, v in vals.items():
                    if region in out[kind]:
                        out[kind][region].append((dt, v))
    for kind in out:
        for region in out[kind]:
            out[kind][region].sort(key=lambda p: p[0])
        missing[kind].sort()
        stale[kind].sort()

    if not any(out["psi"][r] for r in REGIONS):
        reasons = sorted(set(failures.values())) or ["no readings in window"]
        raise SourceUnavailable("; ".join(reasons))

    return {
        "series": out,
        "missing_dates": missing,
        "stale_dates": stale,
        "errors": sorted(set(failures.values())),
        "fetched_at": max(fetched) if fetched else None,
        "oldest_fetch": min(fetched) if fetched else None,
    }
