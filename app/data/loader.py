"""Bundled-data loaders for whenwillhazeend.com.

Every function reads a JSON file that sits next to this module and returns a plain,
JSON-serialisable Python object. No network, no API keys, no pydantic. All loaders are
``functools.lru_cache``d, so callers get the same object back on every call --

    **treat the returned objects as read-only.** Mutating them mutates the cache.

Paths are resolved from ``pathlib.Path(__file__).parent``, never from the process working
directory, so the package works regardless of where uvicorn is started from.

Provenance for every figure in these files is in ``README.md`` (human-readable) and
``provenance.json`` (machine-readable, served to the browser's Methods section).
The short version: burned-area totals, ONI, climatology, emission factors and mortality
figures are published measurements; peat/forest pool areas and the Kalimantan share of
national burned area are *estimated*; the fire-cluster geometries are *synthesised*.
"""

from __future__ import annotations

import functools
import json
import pathlib
from typing import Any

DATA_DIR = pathlib.Path(__file__).parent
FIRES_DIR = DATA_DIR / "fires"

#: Years for which a ``fires/fires_{year}.json`` file is bundled.
FIRE_YEARS: tuple[int, ...] = tuple(
    sorted(int(p.stem.split("_")[1]) for p in FIRES_DIR.glob("fires_*.json"))
) if FIRES_DIR.is_dir() else ()


def _read(name: str) -> Any:
    path = DATA_DIR / name
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def load_seasons() -> list[dict]:
    """Historical Indonesian/Kalimantan fire seasons, ascending by year (1997-2025).

    Each row carries ``year``, ``enso_phase``, ``oni_peak`` (peak |ONI| over the
    Aug-Nov fire-season window), the rainfall-derived ``start_date``/``peak_date``/
    ``end_date``/``duration_days``, burned area for Indonesia and Kalimantan, a
    ``source`` string and an ``estimated`` flag.

    ``duration_days`` is ``0`` and the dates are ``None`` for 2010 and 2022 -- in those
    two La Nina years no drought window occurred at all. That is a real signal, not a
    gap, and it is the floor the season model should reproduce.
    """
    return _read("seasons.json")


@functools.lru_cache(maxsize=1)
def load_fuel_stock() -> dict:
    """Current burnable stock in Kalimantan, as five mutually exclusive pools.

    ``pools[].burn_susceptibility`` is a *model parameter*, normalised so drained peat
    equals 1.0. It is not a measurement and every pool is flagged ``estimated``.
    """
    return _read("fuel_stock.json")


@functools.lru_cache(maxsize=1)
def load_regions() -> dict:
    """GeoJSON ``FeatureCollection`` of the five Kalimantan provinces.

    Geometry is real (OpenStreetMap via geoBoundaries, ODbL 1.0) but heavily simplified
    for the wire -- under 190 coordinate pairs per province. Use ``properties.area_km2``
    for areas, never the geometry.
    """
    return _read("regions.json")


@functools.lru_cache(maxsize=32)
def load_fire_clusters(year: int) -> dict:
    """GeoJSON ``FeatureCollection`` of synthesised fire clusters for ``year``.

    The geometries are **modelled, not observed** (see ``synthesised`` and ``source`` on
    the collection). Only two things are tied to published statistics: the sum of
    ``area_ha`` over all features, which reconciles exactly to that year's
    ``burned_area_mha_kalimantan`` in :func:`load_seasons`, and the split between
    provinces.

    Raises:
        FileNotFoundError: if no file is bundled for that year. ``FIRE_YEARS`` lists
            the years that are available.
    """
    path = FIRES_DIR / f"fires_{int(year)}.json"
    if not path.is_file():
        raise FileNotFoundError(
            f"no fire clusters bundled for {year}; available years: "
            f"{FIRE_YEARS[0]}-{FIRE_YEARS[-1]}" if FIRE_YEARS
            else f"no fire clusters bundled for {year}"
        )
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


@functools.lru_cache(maxsize=1)
def load_receptors() -> list[dict]:
    """Downwind cities that receive Kalimantan/Sumatra haze.

    Twelve cities with ``lat``, ``lon``, ``country``, ``population`` and an
    ``on_kalimantan`` flag. No PM2.5 values -- concentrations are modelled by
    ``app.model.dispersion``.
    """
    return _read("receptors.json")


@functools.lru_cache(maxsize=1)
def load_climatology() -> dict:
    """Monthly rainfall and temperature normals (1991-2020) per Kalimantan province.

    ``provinces[<name>]`` gives twelve-element ``rain_mm``, ``tmax_c`` and ``tmin_c``
    arrays (January first) plus ``annual_rain_mm`` -- the annual total the KBDI model
    needs. Derived from ERA5 daily fields at each provincial capital.
    """
    return _read("climatology.json")


@functools.lru_cache(maxsize=1)
def load_enso() -> list[dict]:
    """Yearly ONI (Oceanic Nino Index), 1990-2026, ascending by year.

    ``oni_peak_aug_nov`` is the largest-magnitude value among the JAS/ASO/SON/OND
    seasons -- the fire-season window, not the calendar-year mean. ``phase`` is
    ``la_nina`` / ``neutral`` / ``el_nino`` classified off that peak at +/-0.5.
    The 2026 row is provisional: the Aug-Nov seasons are not yet published.
    """
    return _read("enso.json")


@functools.lru_cache(maxsize=1)
def load_provenance() -> dict:
    """Machine-readable provenance table (contract amendment A6).

    ``{"datasets": [{"id", "label", "source", "url", "coverage", "status", "notes"}]}``
    where ``status`` is one of ``measured``, ``estimated``, ``synthesised``, ``modelled``.
    This is what the site shows a reader who asks where a number came from, so it must
    stay accurate: nothing synthesised may be described as estimated, and nothing
    estimated may be described as measured.
    """
    return _read("provenance.json")


__all__ = [
    "DATA_DIR",
    "FIRES_DIR",
    "FIRE_YEARS",
    "load_seasons",
    "load_fuel_stock",
    "load_regions",
    "load_fire_clusters",
    "load_receptors",
    "load_climatology",
    "load_enso",
    "load_provenance",
]
