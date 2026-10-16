"""The places the "this week" panel pins, and what official data exists for each.

Singapore: NEA's five PSI reporting regions (label coordinates as NEA publishes
them in ``regionMetadata``).

Kuala Lumpur and Selangor: city-centre coordinates. Malaysia's official index is
the Air Pollutant Index (API; *Indeks Pencemaran Udara*, IPU) from the Department
of Environment (DOE). Finding as of 2026-10-16:

* The old public JSON endpoints under ``apims.doe.gov.my/data/public_v2/`` return
  404; the site now redirects to the MyEQMS portal, ``eqms.doe.gov.my``.
* MyEQMS / APIMS is a single-page web app. It publishes no API documentation and
  no terms permitting reuse of the readings.
* data.gov.my's catalogue has only "Monthly Air Pollution" (national monthly mean
  concentrations, DOE + DOSM, data as of 2024-12-31) - not hourly API readings.
  Its real-time developer APIs (developer.data.gov.my) cover GTFS transit and
  MET Malaysia weather only.

So there is no documented, reuse-licensed source for Malaysian API readings, and
the app does not scrape the portal's undocumented endpoints. Malaysian places get
``official: null``, the CAMS model clearly labelled as modelled, and a link to
the official portal so readers can check the real readings there.
"""

from __future__ import annotations

from dataclasses import dataclass

APIMS_PORTAL = {
    "name": "APIMS - Air Pollutant Index Management System (DOE Malaysia)",
    "url": "https://eqms.doe.gov.my/APIMS/main",
    "index": "API",
    "note": ("Official Malaysian API (IPU) readings are published here. No documented "
             "public data feed with reuse terms exists, so this site does not copy them."),
}

MALAYSIA_OFFICIAL_FINDING = (
    "Malaysia's official Air Pollutant Index (API / IPU) is not shown: DOE publishes it "
    "on the APIMS portal (eqms.doe.gov.my) but offers no documented public data feed or "
    "reuse licence for it, so Kuala Lumpur and Selangor show only the CAMS model, "
    "labelled as modelled. Check APIMS for the official readings."
)


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    area: str
    country: str
    lat: float
    lon: float
    nea_region: str | None = None  # NEA PSI region key, Singapore only


PLACES: tuple[Place, ...] = (
    Place("sg-north", "North", "Singapore", "SG", 1.41803, 103.82, "north"),
    Place("sg-south", "South", "Singapore", "SG", 1.29587, 103.82, "south"),
    Place("sg-east", "East", "Singapore", "SG", 1.35735, 103.94, "east"),
    Place("sg-west", "West", "Singapore", "SG", 1.35735, 103.70, "west"),
    Place("sg-central", "Central", "Singapore", "SG", 1.35735, 103.82, "central"),
    Place("my-kuala-lumpur", "Kuala Lumpur", "Kuala Lumpur", "MY", 3.1390, 101.6869),
    Place("my-shah-alam", "Shah Alam", "Selangor", "MY", 3.0733, 101.5185),
    Place("my-klang", "Klang", "Selangor", "MY", 3.0449, 101.4455),
    Place("my-petaling-jaya", "Petaling Jaya", "Selangor", "MY", 3.1073, 101.6067),
)


def display_name(place: Place) -> str:
    """A name for sentences: "Singapore's West region", "Kuala Lumpur", "Shah Alam"."""
    if place.country == "SG":
        return f"Singapore's {place.name} region"
    return place.name
