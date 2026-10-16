"""Access to the data and model layers, with honest failure modes.

Two rules drive this module:

1. **Import late.** The data and model layers are imported inside the request,
   not at module import time, so a broken or half-written model file cannot stop
   the whole app (and therefore the caveats page, and therefore the honest
   explanation of why there is no number) from starting.

2. **Never invent a number.** If a layer raises, the request fails with HTTP 503
   and a `detail` that names the component that failed. There is no fallback
   forecast, no last-known-good value and no zero-filled placeholder. A user who
   sees "the drought-index service is unavailable" has learned something true; a
   user who sees an invented forecast has been lied to.
"""

from __future__ import annotations

import importlib
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from fastapi import HTTPException, status

from app.config import settings

log = logging.getLogger("whenwillhazeend.api")

#: Human-readable names for the modules we depend on, used in 503 messages.
COMPONENT_LABELS: dict[str, str] = {
    "app.data.loader": "the bundled fire/fuel dataset",
    "app.model.burn": "the season-end model",
    "app.model.stock": "the fuel-exhaustion model",
    "app.model.dispersion": "the haze dispersion model",
    "app.model.meteo": "the weather service",
    "app.model.history": "the historical-season summariser",
    "app.model.kbdi": "the drought-index model",
}


def _label(module: str) -> str:
    return COMPONENT_LABELS.get(module, module)


def _unavailable(module: str, exc: BaseException) -> HTTPException:
    """Build the 503 we return when a layer cannot answer."""
    if isinstance(exc, ModuleNotFoundError):
        reason = f"module not installed ({exc})"
    elif isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        reason = f"a required data file is missing ({exc})"
    else:
        reason = f"{type(exc).__name__}: {exc}"
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            f"{_label(module)} is unavailable — {reason}. "
            "No estimate is returned rather than a fabricated one."
        ),
    )


@contextmanager
def guard(module: str):
    """Turn any failure inside the block into a 503 naming ``module``.

    ``HTTPException`` passes through untouched so that deliberate 404s and
    422s raised inside the block keep their status.
    """
    try:
        yield
    except HTTPException:
        raise
    except BaseException as exc:  # noqa: BLE001 - deliberately broad
        log.exception("%s failed", module)
        raise _unavailable(module, exc) from exc


def _import(module: str):
    try:
        return importlib.import_module(module)
    except BaseException as exc:  # noqa: BLE001
        log.exception("could not import %s", module)
        raise _unavailable(module, exc) from exc


def call(module: str, func: str, /, *args: Any, **kwargs: Any) -> Any:
    """Import ``module``, call ``func`` on it, and translate failures to 503."""
    mod = _import(module)
    try:
        fn: Callable[..., Any] = getattr(mod, func)
    except AttributeError as exc:
        raise _unavailable(module, exc) from exc
    with guard(module):
        return fn(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Deterministic-simulation caches
#
# Only *seeded* runs are memoised: an unseeded run is asking for a fresh draw,
# and handing back a cached one would silently turn Monte-Carlo noise into a
# constant. Keys are plain tuples so lru_cache can hash them.
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=settings.sim_cache_size)
def _cached_season(key: tuple) -> dict:
    as_of, region, oni, n_sims, seed, scenario = key
    return call(
        "app.model.burn",
        "simulate_season_end",
        as_of=as_of,
        region=region,
        oni=oni,
        n_sims=n_sims,
        seed=seed,
        scenario=scenario,
    )


@lru_cache(maxsize=settings.sim_cache_size)
def _cached_exhaustion(key: tuple) -> dict:
    (
        n_sims,
        horizon_years,
        restoration,
        burn_reduction_pct,
        deforestation,
        seed,
    ) = key
    return call(
        "app.model.stock",
        "simulate_exhaustion",
        n_sims=n_sims,
        horizon_years=horizon_years,
        restoration_mha_per_yr=restoration,
        burn_reduction_pct=burn_reduction_pct,
        deforestation_mha_per_yr=deforestation,
        seed=seed,
    )


def simulate_season(*, as_of, region, oni, n_sims, seed, scenario) -> dict:
    key = (as_of, region, oni, n_sims, seed, scenario)
    if seed is None:
        return _cached_season.__wrapped__(key)
    return _cached_season(key)


def simulate_exhaustion(
    *,
    n_sims,
    horizon_years,
    restoration_mha_per_yr,
    burn_reduction_pct,
    deforestation_mha_per_yr,
    seed,
) -> dict:
    key = (
        n_sims,
        horizon_years,
        restoration_mha_per_yr,
        burn_reduction_pct,
        deforestation_mha_per_yr,
        seed,
    )
    if seed is None:
        return _cached_exhaustion.__wrapped__(key)
    return _cached_exhaustion(key)


def clear_sim_caches() -> None:
    """Drop memoised simulations (used by tests)."""
    _cached_season.cache_clear()
    _cached_exhaustion.cache_clear()


# --------------------------------------------------------------------------- #
# Live-weather probe for /api/health
# --------------------------------------------------------------------------- #

_probe_state: dict[str, Any] = {"at": 0.0, "live": False}


def live_weather_available(*, force: bool = False) -> bool:
    """Best-effort answer to 'can we reach Open-Meteo right now?'.

    Asks the meteo layer for a one-day window and reads its ``live`` flag. The
    meteo layer is contractually forbidden from raising and falls back to
    bundled climatology, so this returns ``False`` offline rather than failing.
    Result is cached for ``settings.live_probe_ttl_s`` seconds so that polling
    /api/health does not hammer the upstream API.
    """
    if settings.offline or not settings.probe_live_weather:
        return False

    now = time.monotonic()
    if not force and (now - _probe_state["at"]) < settings.live_probe_ttl_s:
        return bool(_probe_state["live"])

    live = False
    try:
        from datetime import date

        meteo = importlib.import_module("app.model.meteo")
        today = date.today().isoformat()
        result = meteo.fetch_weather(
            settings.reference_lat, settings.reference_lon, today, today
        )
        live = bool(isinstance(result, dict) and result.get("live"))
    except BaseException:  # noqa: BLE001 - health must never 503 on the probe
        log.warning("live-weather probe failed; reporting unavailable", exc_info=True)
        live = False

    _probe_state["at"] = now
    _probe_state["live"] = live
    return live


def reset_live_probe() -> None:
    """Forget the cached probe result (used by tests)."""
    _probe_state["at"] = 0.0
    _probe_state["live"] = False
