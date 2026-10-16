"""Bundled datasets for whenwillhazeend.com.

Re-exports the loaders from :mod:`app.data.loader`. Everything here is offline: the app
works with no network and no API keys.

See ``README.md`` in this directory for the full provenance table, or call
:func:`load_provenance` for the machine-readable version.
"""

from app.data.loader import (
    DATA_DIR,
    FIRE_YEARS,
    FIRES_DIR,
    load_climatology,
    load_enso,
    load_fire_clusters,
    load_fuel_stock,
    load_provenance,
    load_receptors,
    load_regions,
    load_seasons,
)

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
