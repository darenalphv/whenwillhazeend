"""Fire emissions: per-hectare (E1), daily gridded (E2), burn-stage toggle (E3).

MODEL_SPEC section 1.4. Every number comes from ``app/data/earth/parameters.json``
(copied from research/PARAMETERS.json with its source and ``verified`` flag).

E1, per hectare burned::

    PM_kg(ha) = FC [t DM/ha] x EF [g/kg]          (t->kg and g->kg cancel)

    peat:  FC(n) = rho_b x d1 x r_n
           rho_b = 0.11 g/cm3 (0.093-0.127)     Kiely 2019 lit. mean; Warren 2012
           d1    = 0.26 m (0.17-0.37)           Huijnen 2016; Konecny 2016; Kiely 2019
           r_n   = 1, 0.56, 0.33, 0.11 for fire n = 1, 2, 3, >=4   Konecny 2016 [D]
           => FC(1) = 286 t/ha (187-470) [D]
           EF    = 22.3 g/kg (17.3-28.0)        Kiely 2019 mean of Jayarathne 2018
                                                (17.3), Stockwell 2016 (21.5),
                                                Wooster 2018 (28)
           => 6.38 t PM2.5 per first-fire hectare (3.2-13.2) [D]
    tropical forest: EF 8.3 +/- 3.3 g/kg (Andreae 2019); FC 55 / 75 t/ha UNVERIFIED
    scrub / grass:   EF 6.7 +/- 3.3 g/kg (Andreae 2019); FC 12 t/ha UNVERIFIED

The peat EF that the v1 data called "Stockwell et al. 2016, 17.3" is Jayarathne et
al. 2018 (ACP 18:2585); Stockwell 2016 gives a preliminary 21.5. Akagi 2011 has no
peat PM2.5 EF at all (9.1 is tropical forest).

E2, daily emissions for transport: GFED4.1s monthly dry matter x daily fraction at
0.25 deg, partitioned by fire type, with the E1 emission factors applied to the peat
and non-peat dry matter separately (built offline by
``scripts/model/earth/build_emissions.py``). The spec's primary path is CAMS GFAS x a
peat correction; GFAS needs an account, and GFED's own fire-type partition makes the
correction unnecessary (the peat EF is applied directly).
"""

from __future__ import annotations

import functools
import math
from datetime import date, timedelta

__all__ = [
    "REPEAT_FIRE_RATIOS", "peat_fuel_consumption_t_ha", "pm25_kg_per_ha",
    "pool_emission_factors", "burn_stage_ef", "e1_table",
    "daily_emissions", "emission_years", "region_table", "season_totals",
]


def _p(pid: str, default: float, field: str = "value") -> float:
    try:
        from app.data.loader import load_earth_parameters
        v = (load_earth_parameters().get("parameters", {}).get(pid) or {}).get(field)
    except Exception:
        v = None
    return float(v) if v is not None else float(default)


#: Konecny et al. 2016 Table 3 fuel ratios 206/115/69/23 t/ha [D].
REPEAT_FIRE_RATIOS = (1.0, 0.56, 0.33, 0.11)

#: Forest and scrub fuel consumption carried over from v1. They are not traced to a
#: table (MODEL_SPEC 1.4) and are UNVERIFIED; GFED5 per-class Equatorial Asia fuel
#: consumption is the preferred replacement once extracted.
NONPEAT_FUEL_T_HA = {"primary_forest": 55.0, "secondary_forest": 75.0, "scrub_grass": 12.0}


def _variant_values(variant: str):
    rho = _p("peat_bulk_density", 0.11, {"central": "value", "low": "value",
                                           "high": "high"}.get(variant, "value"))
    d1 = _p("peat_burn_depth_first_fire_central", 0.26,
            {"central": "value", "low": "low", "high": "high"}.get(variant, "value"))
    ef = _p("ef_pm25_peat_central", 22.3,
            {"central": "value", "low": "low", "high": "high"}.get(variant, "value"))
    return rho, d1, ef


def peat_fuel_consumption_t_ha(n_fire: int = 1, variant: str = "central") -> float:
    """t DM/ha consumed by the n-th fire on a peat hectare (spec E1).

    variant "low" = 0.17 m x 0.11 g/cm3 = 187; "high" = 0.37 m x 0.127 = 470.
    """
    rho, d1, _ = _variant_values(variant)
    n = max(1, int(n_fire))
    r = REPEAT_FIRE_RATIOS[min(n, 4) - 1]
    # g/cm3 -> t/m3 is x1; x 1e4 m2/ha
    return rho * d1 * r * 1.0e4


def pool_emission_factors(variant: str = "central") -> dict:
    """PM2.5 EF (g/kg DM) per v1 fuel pool."""
    key = {"central": "value", "low": "low", "high": "high"}.get(variant, "value")
    peat = _p("ef_pm25_peat_central", 22.3, key)
    forest = _p("ef_pm25_tropical_forest", 8.3, key)
    sav = _p("ef_pm25_savanna", 6.7, key)
    return {"peat_intact": peat, "peat_drained": peat, "primary_forest": forest,
            "secondary_forest": forest, "scrub_grass": sav}


def pm25_kg_per_ha(pool: str, n_fire: int = 1, variant: str = "central") -> float:
    """kg PM2.5 emitted per hectare burned, for a pool and fire number (spec E1)."""
    ef = pool_emission_factors(variant).get(pool)
    if ef is None:
        raise KeyError(f"unknown pool {pool!r}")
    if pool.startswith("peat"):
        fc = peat_fuel_consumption_t_ha(n_fire, variant)
    else:
        fc = NONPEAT_FUEL_T_HA[pool]
    return fc * ef        # t/ha * g/kg = kg/ha


def burn_stage_ef(t_days, enabled: bool = False, floor: float = 17.3):
    """E3: Roulston 2018 decaying peat EF, renormalised to the landscape mean.

    Off by default (a sensitivity toggle only): the decay was measured on dense
    Malaysian agricultural peat. When on, EF(t) = max(floor, 58 * 0.91^t), rescaled
    so its mean over the first 30 days equals 22.3 g/kg [J].
    """
    central = _p("ef_pm25_peat_central", 22.3)
    if not enabled:
        return central
    e0 = _p("roulston_ef_initial", 58.0)
    k = _p("roulston_ef_decay_per_day", 0.09)
    raw = [max(floor, e0 * (1 - k) ** d) for d in range(30)]
    scale = central / (sum(raw) / len(raw))
    return max(floor, e0 * (1 - k) ** float(t_days)) * scale


def e1_table() -> dict:
    """The E1 numbers with ranges, for /api/fuel-style display."""
    out = {}
    for n in (1, 2, 3, 4):
        out[f"peat_fire_{n}"] = {
            "fuel_t_ha": round(peat_fuel_consumption_t_ha(n), 1),
            "pm25_t_ha": round(pm25_kg_per_ha("peat_drained", n) / 1000.0, 3),
            "pm25_t_ha_low": round(pm25_kg_per_ha("peat_drained", n, "low") / 1000.0, 3),
            "pm25_t_ha_high": round(pm25_kg_per_ha("peat_drained", n, "high") / 1000.0, 3),
        }
    for pool in ("primary_forest", "secondary_forest", "scrub_grass"):
        out[pool] = {"fuel_t_ha": NONPEAT_FUEL_T_HA[pool],
                     "fuel_verified": False,
                     "pm25_t_ha": round(pm25_kg_per_ha(pool) / 1000.0, 3)}
    out["sources"] = {
        "ef_peat": "Kiely et al. 2019 (doi:10.5194/acp-19-11105-2019): 22.3 g/kg; low 17.3 Jayarathne et al. 2018; high 28 Wooster et al. 2018",
        "peat_depth": "Huijnen et al. 2016 (0.26 m); Konecny et al. 2016 (0.17 m, repeat-fire ratios); Kiely 2019 (0.37 m)",
        "bulk_density": "Kiely 2019 literature mean 0.11 g/cm3; Warren et al. 2012 0.127",
        "ef_forest_scrub": "Andreae 2019 Table 1 (8.3, 6.7 g/kg)",
        "nonpeat_fuel": "UNVERIFIED v1 values (not traced to a table)",
    }
    return out


# --------------------------------------------------------------------------- #
# E2: daily gridded emissions
# --------------------------------------------------------------------------- #


class _YearArrays(dict):
    """Metadata of the GFED4.1s file; per-year arrays are decoded on first use.

    Decoding every year at once would hold ~110 MB per process; one year is ~4 MB
    and at most two are cached.
    """

    def __missing__(self, key):
        import re
        m = re.match(r"^(pm_peat|pm_nonpeat|ba_ha|diurnal)_(\d{4})$", key)
        if not m:
            raise KeyError(key)
        arrs = _year_arrays(int(m.group(2)))
        if key not in arrs:
            raise KeyError(key)
        return arrs[key]

    def __contains__(self, key):
        if dict.__contains__(self, key):
            return True
        import re
        m = re.match(r"^(pm_peat|pm_nonpeat|ba_ha|diurnal)_(\d{4})$", str(key))
        return bool(m) and key in _year_arrays(int(m.group(2)))


def _emis_path():
    from app.data.loader import earth_path
    return earth_path("emissions_gfed4s.npz")


@functools.lru_cache(maxsize=1)
def _npz():
    """GFED4.1s-derived emissions: metadata now, per-year arrays lazily (see _YearArrays)."""
    import numpy as np
    p = _emis_path()
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as z:
        meta = {k: z[k] for k in z.files if not k[-4:].isdigit()}
    return _YearArrays(meta)


@functools.lru_cache(maxsize=2)
def _year_arrays(year: int) -> dict:
    import numpy as np
    with np.load(_emis_path(), allow_pickle=False) as z:
        return {k: z[k] for k in z.files if k.endswith(f"_{year}")}


@functools.lru_cache(maxsize=1)
def _nrt():
    """Near-real-time FIRMS-FRP emissions (build_firms_nrt.py), or None."""
    import numpy as np
    from app.data.loader import earth_path
    p = earth_path("emissions_nrt.npz")
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def emission_years() -> tuple:
    """Years with bundled daily emissions (GFED4.1s, plus any FIRMS NRT year)."""
    z = _npz()
    ys = set(int(y) for y in z["years"]) if z is not None else set()
    n = _nrt()
    if n is not None:
        ys |= {int(str(d)[:4]) for d in n["days"]}
    return tuple(sorted(ys))


def _daily_nrt(d: date, ef_variant: str):
    import numpy as np
    n = _nrt()
    if n is None:
        return None
    days = [str(x) for x in n["days"]]
    if d.isoformat() not in days:
        return None
    k = days.index(d.isoformat())
    scale = 1.0
    if ef_variant != "central":
        scale = _p("ef_pm25_peat_central", 22.3,
                   {"low": "low", "high": "high"}.get(ef_variant, "value")) / 22.3
    nc = len(n["lat"])
    return {
        "lat": n["lat"].astype(np.float64), "lon": n["lon"].astype(np.float64),
        "region": n["region"].astype(np.int64),
        "area_m2": (27.8e3 ** 2) * np.cos(np.radians(n["lat"].astype(np.float64))),
        "peat_kg": n["pm_peat"][:, k].astype(np.float64) * scale,
        "nonpeat_kg": n["pm_nonpeat"][:, k].astype(np.float64),
        "ba_ha": None, "diurnal": np.full((8, nc), 1.0 / 8.0),
        "daily_resolved": True,
        "backfilled": d.isoformat() in [str(x) for x in n["backfilled_days"]],
        "source": str(n["source"]),
    }


def region_table() -> list[dict]:
    z = _npz()
    if z is None:
        return []
    return [{"index": i, "id": str(r), "island": str(isl)}
            for i, (r, isl) in enumerate(zip(z["region_ids"], z["region_island"]))]


def _day_index(z, d: date):
    mm, dd = (int(x) for x in z["season_start_mmdd"])
    k = (d - date(d.year, mm, dd)).days
    return k if 0 <= k < int(z["n_days"]) else None


def daily_emissions(d: date, ef_variant: str = "central"):
    """Per-cell PM2.5 (kg/day) on date ``d``, or None if the date is not bundled.

    Returns a dict of numpy arrays over the active 0.25-deg cells: lat, lon, region
    (index into region_table()), peat_kg, nonpeat_kg, ba_ha (or None), plus the
    3-hourly diurnal cycle of non-peat emissions (8 x n, sums to ~1) and metadata.
    """
    import numpy as np
    z = _npz()
    if z is None or d.year not in emission_years():
        return None
    if d.year not in [int(y) for y in z["years"]]:
        return _daily_nrt(d, ef_variant)
    k = _day_index(z, d)
    if k is None:
        return None
    y = d.year
    scale = 1.0
    if ef_variant != "central":
        scale = _p("ef_pm25_peat_central", 22.3,
                   {"low": "low", "high": "high"}.get(ef_variant, "value")) / float(z["ef_peat"])
    peat = z[f"pm_peat_{y}"][:, k].astype(np.float64) * scale
    nonpeat = z[f"pm_nonpeat_{y}"][:, k].astype(np.float64)
    ba = z[f"ba_ha_{y}"][:, k].astype(np.float64) if f"ba_ha_{y}" in z else None
    month_i = min(max(d.month - 6, 0), 6)
    di = z[f"diurnal_{y}"][month_i].astype(np.float64) / 255.0        # (8, n)
    s = di.sum(axis=0)
    di = np.where(s > 0, di / np.where(s > 0, s, 1.0), 1.0 / 8.0)
    yi = list(int(v) for v in z["years"]).index(y)
    return {
        "lat": z["lat"].astype(np.float64), "lon": z["lon"].astype(np.float64),
        "region": z["region"].astype(np.int64), "area_m2": z["area_m2"].astype(np.float64),
        "peat_kg": peat, "nonpeat_kg": nonpeat, "ba_ha": ba, "diurnal": di,
        "daily_resolved": bool(z["daily_resolved"][yi]),
        "source": ("GFED4.1s (van der Werf et al. 2017) dry matter x daily fraction, "
                   "0.25 deg; PM2.5 with Kiely 2019 peat EF and Andreae 2019 non-peat EFs"
                   + ("" if y < 2017 else "; 2017+ is GFED4.1s beta (active-fire based)")),
    }


def season_totals(year: int, start: date | None = None, end: date | None = None,
                  ef_variant: str = "central") -> dict:
    """Total primary PM2.5 (Tg) for a window, by island. Spec test: 2015 Sep-Oct 4-11 Tg."""
    import numpy as np
    z = _npz()
    if z is None or year not in [int(y) for y in z["years"]]:
        return {}
    mm, dd = (int(x) for x in z["season_start_mmdd"])
    d0 = date(year, mm, dd)
    a = 0 if start is None else max(0, (start - d0).days)
    b = int(z["n_days"]) if end is None else min(int(z["n_days"]), (end - d0).days + 1)
    scale = 1.0
    if ef_variant != "central":
        scale = _p("ef_pm25_peat_central", 22.3,
                   {"low": "low", "high": "high"}.get(ef_variant, "value")) / float(z["ef_peat"])
    peat = z[f"pm_peat_{year}"][:, a:b].sum(axis=1).astype(np.float64) * scale
    non = z[f"pm_nonpeat_{year}"][:, a:b].sum(axis=1).astype(np.float64)
    isl = z["region_island"][z["region"].astype(int)]
    out = {"total_tg": float((peat + non).sum() / 1e9),
           "peat_tg": float(peat.sum() / 1e9)}
    for name in ("kalimantan", "sumatra", "peninsular_malaysia", "malaysian_borneo", "other"):
        sel = isl == name
        out[f"{name}_tg"] = float((peat[sel] + non[sel]).sum() / 1e9)
    return out
