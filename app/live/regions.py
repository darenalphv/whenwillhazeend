"""Coarse region polygons for counting fire hotspots.

Hand-drawn simplifications (vertices every ~50-150 km, with a margin offshore),
good enough to count tens of thousands of VIIRS detections by island. The
Kalimantan / Sarawak-Sabah line follows the Indonesia-Malaysia land border only
approximately, so a detection within ~20 km of that border can land on the
wrong side. Anything inside the domain but outside every polygon counts as
"other" (Java, Bali, Sulawesi, small islands, southern Thailand).

Vertices are (lon, lat).
"""

from __future__ import annotations

import numpy as np

# The window the live map shows (a little wider than the CAMS field grid).
DOMAIN = {"lon_min": 94.0, "lon_max": 120.5, "lat_min": -9.0, "lat_max": 8.0}

REGION_LABELS = {
    "kalimantan": "Kalimantan (Indonesian Borneo)",
    "sumatra": "Sumatra (incl. Riau Islands, Bangka-Belitung)",
    "peninsular_malaysia": "Peninsular Malaysia",
    "sarawak_sabah_brunei": "Sarawak, Sabah and Brunei",
    "other": "Elsewhere in view (Java, Sulawesi, southern Thailand, small islands)",
}

# Indonesia-Malaysia land border on Borneo, west (Tanjung Datu) to east (Sebatik).
_BORNEO_BORDER = [
    (109.64, 2.08), (109.95, 1.65), (110.40, 1.05), (111.00, 0.95), (111.80, 1.00),
    (112.20, 1.45), (112.90, 1.55), (113.60, 1.25), (114.10, 1.45), (114.60, 1.45),
    (114.80, 2.05), (115.20, 2.50), (115.55, 3.05), (115.60, 3.45), (115.60, 4.00),
    (115.90, 4.35), (116.50, 4.38), (117.20, 4.35), (117.60, 4.20), (117.95, 4.17),
]

_POLYGONS: dict[str, list[list[tuple[float, float]]]] = {
    "kalimantan": [
        _BORNEO_BORDER + [
            (118.40, 4.10), (118.40, 2.00), (119.30, 1.10), (118.60, 0.10), (117.90, -0.70),
            (117.20, -1.40), (116.90, -2.20), (116.70, -3.20), (116.40, -4.40),
            (114.50, -4.10), (113.00, -3.70), (111.50, -3.50), (110.00, -3.30),
            (109.50, -2.00), (108.70, -0.50), (108.60, 1.00), (108.90, 1.90),
        ],
    ],
    "sarawak_sabah_brunei": [
        list(reversed(_BORNEO_BORDER)) + [
            (109.40, 2.30), (111.00, 3.00), (113.00, 3.70), (114.00, 4.90), (115.00, 5.40),
            (116.00, 6.40), (116.80, 7.40), (117.70, 7.30), (118.40, 6.20), (119.50, 5.40),
            (118.90, 4.40), (118.40, 4.10),
        ],
    ],
    "sumatra": [
        [   # the island, clockwise from Banda Aceh
            (95.00, 5.90), (96.00, 5.50), (97.60, 5.40), (98.80, 4.00), (100.20, 2.60),
            (101.20, 2.20), (102.10, 1.70), (102.90, 1.20), (103.60, 0.60), (104.20, -0.40),
            (104.70, -1.40), (105.30, -1.90), (106.10, -3.00), (106.10, -4.10), (106.00, -6.00),
            (104.50, -6.10), (103.20, -4.80), (102.00, -3.90), (100.80, -2.20), (100.10, -1.00),
            (99.20, 0.20), (98.40, 1.60), (97.20, 3.20), (96.00, 4.00), (95.00, 5.20),
        ],
        [(103.85, 0.70), (104.80, 0.70), (104.80, 1.18), (103.85, 1.18)],     # Batam, Bintan
        [(105.05, -1.45), (108.40, -1.45), (108.40, -3.40), (105.05, -3.40)],  # Bangka-Belitung
        [(97.00, 1.60), (98.20, 1.60), (98.20, -0.20), (97.00, -0.20)],        # Nias
    ],
    "peninsular_malaysia": [
        [
            (100.10, 6.75), (100.40, 6.55), (100.90, 6.30), (101.10, 5.80), (101.60, 5.85),
            (102.10, 6.30), (103.10, 5.60), (103.50, 4.30), (103.50, 3.30), (104.00, 2.50),
            (104.40, 1.40), (104.05, 1.48), (103.60, 1.48), (103.45, 1.22), (103.30, 1.50),
            (102.50, 1.95), (101.80, 2.50), (101.20, 2.95), (100.80, 3.90), (100.50, 4.40),
            (100.20, 5.30), (100.25, 6.00), (99.60, 6.10), (99.60, 6.55),
        ],
    ],
}

# Evaluation order matters only where polygons might touch (they should not).
REGION_ORDER = ("kalimantan", "sarawak_sabah_brunei", "sumatra", "peninsular_malaysia")


def _inside(lon: np.ndarray, lat: np.ndarray, poly: list[tuple[float, float]]) -> np.ndarray:
    """Even-odd ray casting, vectorised over points."""
    inside = np.zeros(lon.shape, dtype=bool)
    xs = np.array([p[0] for p in poly])
    ys = np.array([p[1] for p in poly])
    xj, yj = xs[-1], ys[-1]
    for xi, yi in zip(xs, ys):
        crosses = (yi > lat) != (yj > lat)
        with np.errstate(divide="ignore", invalid="ignore"):
            x_at = (xj - xi) * (lat - yi) / (yj - yi) + xi
        inside ^= crosses & (lon < x_at)
        xj, yj = xi, yi
    return inside


def classify(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Region index per point: position in ``REGION_ORDER``, or len(REGION_ORDER) = other."""
    out = np.full(lon.shape, len(REGION_ORDER), dtype=np.int8)
    for idx, rid in enumerate(REGION_ORDER):
        todo = out == len(REGION_ORDER)
        if not todo.any():
            break
        hit = np.zeros(lon.shape, dtype=bool)
        for poly in _POLYGONS[rid]:
            hit |= _inside(lon, lat, poly)
        out[todo & hit] = idx
    return out


def region_ids() -> tuple[str, ...]:
    return REGION_ORDER + ("other",)
