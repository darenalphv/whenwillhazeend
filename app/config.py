"""Application configuration.

Everything here has a working default so the app runs with **no environment
variables, no API keys and no network access**. Environment overrides all use
the ``WWHE_`` prefix.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app import __version__

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    """Runtime settings. Frozen: read once at import, never mutated at runtime."""

    app_name: str = "whenwillhazeend"
    version: str = __version__

    base_dir: Path = BASE_DIR
    root_dir: Path = ROOT_DIR
    static_dir: Path = field(default_factory=lambda: BASE_DIR / "static")
    templates_dir: Path = field(default_factory=lambda: BASE_DIR / "templates")

    # The default region and reference point (Palangkaraya, Kalimantan Tengah —
    # the city that recorded PM2.5 of 3741 ug/m3 in October 2015).
    default_region: str = "kalimantan"
    reference_lat: float = -2.21
    reference_lon: float = 113.92

    # Monte-Carlo defaults. Hard ceilings live in app/api/schemas.py so that
    # out-of-range requests are rejected with 422 rather than silently clamped.
    default_season_sims: int = 5000
    default_exhaustion_sims: int = 2000
    # 200 years truncates below the modelled exhaustion date (~2264), which made the
    # API return p50=None / never_pct=100 and left the UI with nothing to show.
    # The p50 is stable at 2264 for horizons of 300 and 500, so 300 is enough.
    default_horizon_years: int = 300

    # Seeded (deterministic) simulations are memoised; unseeded ones never are.
    sim_cache_size: int = 64

    # Offline mode skips the live-weather probe on /api/health entirely.
    # The models themselves always fall back to bundled climatology regardless.
    offline: bool = field(default_factory=lambda: _env_bool("WWHE_OFFLINE", False))
    probe_live_weather: bool = field(
        default_factory=lambda: _env_bool("WWHE_PROBE_LIVE_WEATHER", True)
    )
    # How long a live-weather probe result is trusted before re-probing.
    live_probe_ttl_s: int = field(
        default_factory=lambda: _env_int("WWHE_LIVE_PROBE_TTL_S", 300)
    )


settings = Settings()


# --------------------------------------------------------------------------- #
# Open-Meteo licensing policy
#
# Open-Meteo's free API is licensed for non-commercial use only, and this site
# funnels to a company, so by default the live site makes NO Open-Meteo calls.
# Every model and feed falls back (bundled ERA5 weather + climatology; no CAMS
# readings) and says why. Read per call, so a key added in the host's
# environment takes effect on restart and tests can monkeypatch it.
# --------------------------------------------------------------------------- #

OPEN_METEO_OFF_REASON = (
    "Live Open-Meteo data is switched off: its free tier is licensed for "
    "non-commercial use only, and no commercial API key is configured."
)


def open_meteo_key() -> str | None:
    """The commercial key from the environment, or None. Never log it."""
    return os.environ.get("OPEN_METEO_API_KEY", "").strip() or None


def open_meteo_mode() -> str:
    """'key' (commercial endpoints), 'free' (explicit developer opt-in via
    WWHE_OPEN_METEO_ALLOW_FREE=1, never in production), or 'off' (default)."""
    if open_meteo_key():
        return "key"
    if _env_bool("WWHE_OPEN_METEO_ALLOW_FREE", False):
        return "free"
    return "off"
