"""Shared helpers: time zone, HTTP client, errors.

Times
-----
Everything this package returns is in **Asia/Kuala_Lumpur, UTC+08:00**.
Singapore Standard Time is the same offset (neither country observes daylight
saving), so NEA's ``+08:00`` timestamps pass through unchanged. A fixed offset is
used rather than ``zoneinfo`` so the slim container needs no tzdata package.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("whenwillhazeend.live")

MYT = timezone(timedelta(hours=8), "MYT")
TIMEZONE_LABEL = "Asia/Kuala_Lumpur (UTC+08:00)"
TIMEZONE_NOTE = (
    "All times are Asia/Kuala_Lumpur, UTC+08:00. Singapore (SGT) uses the same "
    "offset, so NEA's Singapore timestamps are shown unchanged."
)

USER_AGENT = "whenwillhazeend.com live-data (+https://whenwillhazeend.com)"


class SourceUnavailable(RuntimeError):
    """An upstream source could not supply data and nothing usable is cached."""


def now_myt() -> datetime:
    """Current time in UTC+08:00. Tests monkeypatch this."""
    return datetime.now(MYT)


def iso(dt: datetime) -> str:
    """ISO 8601 in UTC+08:00 to the second, e.g. ``2026-10-14T15:00:00+08:00``."""
    return dt.astimezone(MYT).replace(microsecond=0).isoformat()


def from_utc_naive(text: str) -> datetime:
    """Parse Open-Meteo's ``2026-10-14T06:00`` (requested with timezone=GMT)."""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).astimezone(MYT)


def human_time(dt: datetime) -> str:
    """``9 am on 16 October`` in UTC+08:00, for headline sentences."""
    dt = dt.astimezone(MYT)
    hour = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    minute = f":{dt.minute:02d}" if dt.minute else ""
    return f"{hour}{minute} {ampm} on {dt.day} {dt.strftime('%B')}"


def http_client(timeout: float):
    """A short-timeout httpx client. Imported lazily: no httpx cost at import."""
    import httpx

    return httpx.Client(
        timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def describe_error(exc: BaseException) -> str:
    """A short, key-free description of a failure for API responses.

    Never includes a URL: an Open-Meteo customer key or a data.gov.sg key must
    not leak through an error message. (Both keys are sent in a POST body or a
    header, never a URL, but this is the second line of defence.)
    """
    name = type(exc).__name__
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is not None:
        return f"{name} (HTTP {status})"
    return name
