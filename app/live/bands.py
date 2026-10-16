"""Band names per index, as each index's own authority names them (CONTRACT_V2 M5).

* **PSI** (Singapore, NEA) and **API / IPU** (Malaysia, DOE):
  0-50 Good, 51-100 Moderate, 101-200 Unhealthy, 201-300 Very Unhealthy,
  >300 Hazardous.
* **US AQI** (US EPA scale; here only ever a *modelled* CAMS equivalent):
  0-50 Good, 51-100 Moderate, 101-150 Unhealthy for Sensitive Groups,
  151-200 Unhealthy, 201-300 Very Unhealthy, >300 Hazardous.

The scales share some names but not breakpoints, so a band is always looked up
with the index it belongs to. Fractional values (CAMS can return them) take the
band of the integer they round up to, so 50.4 is Moderate, matching how both
agencies publish whole numbers.
"""

from __future__ import annotations

import math

PSI = "PSI"
API = "API"
US_AQI = "US AQI (modelled, CAMS)"

_PSI_API = ((50, "Good"), (100, "Moderate"), (200, "Unhealthy"),
            (300, "Very Unhealthy"), (math.inf, "Hazardous"))
_US_AQI = ((50, "Good"), (100, "Moderate"), (150, "Unhealthy for Sensitive Groups"),
           (200, "Unhealthy"), (300, "Very Unhealthy"), (math.inf, "Hazardous"))

_TABLES = {PSI: _PSI_API, API: _PSI_API, US_AQI: _US_AQI}

# The lowest band that a headline is written about. Below it, there is no claim.
HEADLINE_FLOOR = {PSI: 101, API: 101, US_AQI: 151}


def band(index: str, value: float | None) -> str | None:
    """The band name for ``value`` on ``index``'s own scale."""
    if value is None:
        return None
    if index not in _TABLES:
        raise ValueError(f"unknown index {index!r}")
    v = math.ceil(value) if value > 0 else 0
    for upper, name in _TABLES[index]:
        if v <= upper:
            return name
    return None  # pragma: no cover - math.inf catches everything


def headline_worthy(index: str, value: float | None) -> bool:
    return value is not None and math.ceil(value) >= HEADLINE_FLOOR[index]
