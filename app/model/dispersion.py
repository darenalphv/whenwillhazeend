"""Gaussian-plume transport of peat smoke to the downwind receptor cities.

Equations
---------
**Emission.**  For each burning cluster, following the contract and the
standard fuel-based accounting,

    PM2.5_kg = area_ha * fuel_load_t_ha * EF_g_kg

(the unit conversions ``t -> kg`` and ``g -> kg`` cancel exactly).  Pool fuel
loads and emission factors come from ``load_fuel_stock()`` (MODEL_SPEC E1): peat
286 t DM/ha for a first fire x 22.3 g/kg PM2.5 (Kiely et al. 2019, the mean of the
three Kalimantan field values 17.3 Jayarathne et al. 2018, 21.5 Stockwell et al. 2016
and 28 Wooster et al. 2018), tropical forest 8.3 and scrub 6.7 g/kg (Andreae 2019).  The mass is released at a constant rate over the cluster's smouldering
lifetime -- the ``duration_days`` carried on a fire-cluster feature, or
``DEFAULT_EPISODE_DAYS = 14`` when it is not given -- so the source strength is

    Q = PM2.5_kg * 1000 / (duration_days * 86400)   [g/s]

Fourteen days is the right order for a peat cluster: flaming vegetation is gone
in hours but the smouldering front consumes the 0.33 m peat layer over weeks
(Page et al. 2002; Turetsky et al. 2015). Using 24 h instead would overstate
concentrations by an order of magnitude.

**Transport.**  Steady-state Gaussian plume from a ground-level source with
total ground reflection (Pasquill 1961; Gifford 1961):

    C(x, y, 0) = Q / (pi * u * sigma_y * sigma_z) * exp(-y^2 / (2 sigma_y^2))

where x is downwind distance, y crosswind distance and u the transport wind.
Once the plume fills the boundary layer (sigma_z > 1.6 * H_mix) it is capped to
the uniformly-mixed form

    C = Q / (sqrt(2 pi) * u * sigma_y * H_mix) * exp(-y^2 / (2 sigma_y^2))

which is what actually governs concentrations hundreds of kilometres downwind.

**Dispersion coefficients.**  Briggs (1973) open-country interpolation of the
Pasquill-Gifford curves, with x in metres:

    class D:  sigma_y = 0.08 x (1 + 0.0001 x)^-1/2
              sigma_z = 0.06 x (1 + 0.0015 x)^-1/2
    class E:  sigma_y = 0.06 x (1 + 0.0001 x)^-1/2
              sigma_z = 0.03 x (1 + 0.0003 x)^-1

Those curves were fitted to travel distances of 100 m to 10 km and badly
under-predict lateral spread at regional range, where wind-direction meander
and mesoscale eddies dominate.  Beyond three hours of travel a Fickian
mesoscale term is therefore added in quadrature (Gifford 1982; equivalent to
CALPUFF's long-range puff growth):

    sigma_y_total^2 = sigma_y_Briggs^2 + 2 K_h max(0, t - 3 h),  K_h = 5e4 m2/s

which gives a plume roughly 100-200 km wide after 500-900 km of transport --
the width regional haze episodes actually have.

Class D (neutral) is used when the transport wind is at least 2.5 m/s, class E
(slightly stable) below that, which is the usual light-wind night-time regime
in which haze episodes get worst.  A burning cluster is not a point, so each
source is given a virtual-source initial spread
``sigma_y0 = sqrt(area_m2/pi) / 2.15`` and ``sigma_z0 = 20 m``, combined in
quadrature.

**Dry removal.**  Once the plume fills the boundary layer, dry deposition acts
as a first-order loss with rate ``v_d / H_mix``, ``v_d = 0.2 cm/s`` for
accumulation-mode PM2.5 over tropical vegetation (Seinfeld & Pandis 2016):

    depletion_dry = exp(-(v_d / H_mix) * x / u)

**Wet removal.**  Below-cloud scavenging of accumulation-mode aerosol as a
first-order loss along the trajectory,

    Lambda    = a * R^b,  a = 1.0e-4, b = 0.7,  R = 5 mm/h while raining
    f_rain    = min(1, rain_mm / (5 mm/h * 24 h))     [fraction of time raining]
    depletion = exp(-Lambda * f_rain * x / u)

The ``f_rain`` factor matters: a 40 mm day is about eight hours of rain, not
twenty-four, so the plume spends only part of its travel time being washed out.

(Sportisse 2007; Seinfeld & Pandis 2016, ch. 20).  This is why a wet day
clears the air over Singapore within hours while the fires themselves burn on.

**AQI.**  US EPA PM2.5 breakpoints in the pre-2024 form, because the contour
bands the map draws (35 / 55 / 150 / 250 ug/m3) are exactly those breakpoints.
The 2024 revision lowered the Good/Moderate boundary from 12.0 to 9.0 ug/m3;
this module keeps the older table for internal consistency and says so.

Sources
-------
* Pasquill, F. (1961). The estimation of the dispersion of windborne material.
  *Meteorological Magazine* 90, 33-49.
* Gifford, F.A. (1961). Use of routine meteorological observations for
  estimating atmospheric dispersion. *Nuclear Safety* 2, 47-51.
* Briggs, G.A. (1973). *Diffusion estimation for small emissions.* ATDL
  Contribution File No. 79, NOAA.
* Gifford, F.A. (1982). Horizontal diffusion in the atmosphere: a
  Lagrangian-dynamical theory. *Atmospheric Environment* 16(3), 505-512 --
  the mesoscale long-range growth term.
* Kiely, L. et al. (2019). *ACP* 19, 11105-11121 - peat PM2.5 EF 22.3 g/kg.
* Jayarathne, T. et al. (2018). *ACP* 18, 2585-2600 - peat PM2.5 EF 17.3 g/kg.
* Andreae, M.O. (2019). *ACP* 19, 8523-8546 - forest and savanna EFs.
* Seinfeld, J.H. & Pandis, S.N. (2016). *Atmospheric Chemistry and Physics*,
  3rd ed., ch. 18 (dispersion) and ch. 20 (wet deposition).
* Koplitz, S.N. et al. (2016). Public health impacts of the severe haze in
  Equatorial Asia in September-October 2015. *Environ. Res. Lett.* 11, 094023.
* US EPA (2018). Technical Assistance Document for the Reporting of Daily Air
  Quality - the Air Quality Index.
"""

from __future__ import annotations

import math

__all__ = ["haze_dispersion", "pm25_to_aqi", "aqi_category", "CAVEATS",
           "ARRIVAL_THRESHOLD_UGM3",
           "clean_sources", "localise_sources",
           "CONTOUR_BANDS", "BACKGROUND_PM25_UGM3", "DEFAULT_EPISODE_DAYS"]

DEFAULT_EPISODE_DAYS = 14.0      # smouldering lifetime of a peat cluster
RAIN_INTENSITY_MM_H = 5.0        # rate while it is actually raining
BACKGROUND_PM25_UGM3 = 12.0      # regional non-fire background (estimated)
CONTOUR_BANDS = (35.0, 55.0, 150.0, 250.0)
MIN_WIND_MS = 0.6                # plume model breaks down in true calm
#: Fire increment above background that counts as smoke having reached a city [J].
ARRIVAL_THRESHOLD_UGM3 = 5.0
MAX_RANGE_KM = 900.0
CROSSWIND_PAD_KM = 250.0
MAX_DOMAIN_EXPANSIONS = 3
SCAVENGING_A = 1.0e-4
SCAVENGING_B = 0.7
#: Mesoscale horizontal eddy diffusivity for travel beyond T_MESO (Gifford 1982;
#: the same order CALPUFF uses for long-range puff growth).
K_H_MESO_M2_S = 5.0e4
T_MESO_S = 10800.0               # 3 h: Briggs curves alone hold before this
#: Dry deposition velocity for accumulation-mode PM2.5 over tropical vegetation
#: (Seinfeld & Pandis 2016, ch. 19: 0.1-0.5 cm/s).
DRY_DEPOSITION_VELOCITY_MS = 0.002
GRID_NX, GRID_NY = 220, 200

#: Pre-2024 US EPA PM2.5 AQI breakpoints: (C_lo, C_hi, I_lo, I_hi, category)
AQI_TABLE = [
    (0.0, 12.0, 0, 50, "Good"),
    (12.1, 35.4, 51, 100, "Moderate"),
    (35.5, 55.4, 101, 150, "Unhealthy for Sensitive Groups"),
    (55.5, 150.4, 151, 200, "Unhealthy"),
    (150.5, 250.4, 201, 300, "Very Unhealthy"),
    (250.5, 500.4, 301, 500, "Hazardous"),
]


def pm25_to_aqi(pm25: float) -> int:
    c = max(0.0, float(pm25))
    for lo, hi, ilo, ihi, _ in AQI_TABLE:
        if c <= hi:
            return int(round(ilo + (ihi - ilo) * (c - lo) / (hi - lo)))
    # beyond the table: linear extrapolation of the Hazardous slope, capped
    return int(min(999, round(301 + (c - 250.5) * (500 - 301) / (500.4 - 250.5))))


def aqi_category(pm25: float) -> str:
    c = max(0.0, float(pm25))
    for lo, hi, _, _, cat in AQI_TABLE:
        if c <= hi:
            return cat
    return "Hazardous"


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

_KM_PER_DEG_LAT = 110.574


def _km_per_deg_lon(lat: float) -> float:
    return 111.320 * math.cos(math.radians(lat))


def _to_local(lat, lon, lat0, lon0):
    return ((lon - lon0) * _km_per_deg_lon(lat0), (lat - lat0) * _KM_PER_DEG_LAT)


def _to_lonlat(xk, yk, lat0, lon0):
    return (lon0 + xk / _km_per_deg_lon(lat0), lat0 + yk / _KM_PER_DEG_LAT)


def _sigmas(np, x_m, stability: str, u: float | None = None):
    """Briggs (1973) open-country sigmas, plus a mesoscale term at long range."""
    x = np.maximum(x_m, 1.0)
    if stability == "E":
        sy = 0.06 * x / np.sqrt(1.0 + 0.0001 * x)
        sz = 0.03 * x / (1.0 + 0.0003 * x)
    else:
        sy = 0.08 * x / np.sqrt(1.0 + 0.0001 * x)
        sz = 0.06 * x / np.sqrt(1.0 + 0.0015 * x)
    if u:
        t = x / u
        sy = np.sqrt(sy ** 2 + 2.0 * K_H_MESO_M2_S * np.maximum(0.0, t - T_MESO_S))
    return sy, sz


def _pool_lookup():
    try:
        from app.data.loader import load_fuel_stock
        fs = load_fuel_stock() or {}
    except Exception:
        fs = {}
    out = {}
    for p in fs.get("pools", []):
        out[str(p.get("id"))] = (float(p.get("fuel_load_t_ha") or 0.0),
                                 float(p.get("pm25_ef_g_kg") or 0.0))
    return out


def _concentration(np, X, Y, srcs, u, dir_to_rad, mixing_height_m, stability,
                   lam):
    """Sum the plume contributions of all sources at local points X, Y (km)."""
    ex, ny_ = math.sin(dir_to_rad), math.cos(dir_to_rad)   # downwind unit vector
    cx, cy = math.cos(dir_to_rad), -math.sin(dir_to_rad)   # crosswind unit vector
    total = np.zeros(np.shape(X), dtype=float)
    for s in srcs:
        dx, dy = X - s["xk"], Y - s["yk"]
        xd = (dx * ex + dy * ny_) * 1000.0      # downwind, metres
        yd = (dx * cx + dy * cy) * 1000.0       # crosswind, metres
        # A receptor sitting inside the burning cluster is not "upwind": clamp
        # it to the cluster radius rather than dropping its contribution.
        r0 = s["radius_m"]
        inside = (np.abs(xd) < r0) & (np.abs(yd) < r0)
        xd = np.where(inside, r0, xd)
        valid = xd > 1.0
        xs = np.where(valid, xd, 1.0)
        sy, sz = _sigmas(np, xs, stability, u)
        sy = np.sqrt(sy ** 2 + s["sy0"] ** 2)
        sz = np.sqrt(sz ** 2 + 400.0)           # sigma_z0 = 20 m
        gauss = s["q"] / (math.pi * u * sy * sz)
        mixed = s["q"] / (math.sqrt(2.0 * math.pi) * u * sy * mixing_height_m)
        core = np.where(sz > 1.6 * mixing_height_m, mixed, np.minimum(gauss, mixed))
        cross = np.exp(-np.clip(yd ** 2 / (2.0 * sy ** 2), 0.0, 700.0))
        travel_s = xs / u
        wet = np.exp(-lam * travel_s)
        dry = np.exp(-(DRY_DEPOSITION_VELOCITY_MS / mixing_height_m) * travel_s)
        total += np.where(valid, core * cross * wet * dry, 0.0)
    return total * 1e6      # g/m3 -> ug/m3


# ---------------------------------------------------------------------------
# marching squares
# ---------------------------------------------------------------------------

_CASES = {
    1: [("L", "B")], 2: [("B", "R")], 3: [("L", "R")], 4: [("T", "R")],
    5: [("L", "T"), ("B", "R")], 6: [("T", "B")], 7: [("L", "T")],
    8: [("L", "T")], 9: [("T", "B")], 10: [("L", "B"), ("T", "R")],
    11: [("T", "R")], 12: [("L", "R")], 13: [("B", "R")], 14: [("L", "B")],
}


def _edge_point(edge, j, i, xs, ys, tl, tr, br, bl, level):
    def lerp(a, b, pa, pb):
        d = (b - a)
        t = 0.5 if abs(d) < 1e-12 else (level - a) / d
        t = min(max(t, 0.0), 1.0)
        return (pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1]))
    P_tl, P_tr = (xs[j], ys[i]), (xs[j + 1], ys[i])
    P_bl, P_br = (xs[j], ys[i + 1]), (xs[j + 1], ys[i + 1])
    if edge == "T":
        return lerp(tl, tr, P_tl, P_tr)
    if edge == "B":
        return lerp(bl, br, P_bl, P_br)
    if edge == "L":
        return lerp(tl, bl, P_tl, P_bl)
    return lerp(tr, br, P_tr, P_br)


def _contour_rings(np, xs, ys, Z, level, close_tol=None):
    """Marching-squares iso-contour of Z at `level`; returns closed rings."""
    above = Z >= level
    a_tl = above[:-1, :-1]
    a_tr = above[:-1, 1:]
    a_br = above[1:, 1:]
    a_bl = above[1:, :-1]
    code = (a_tl.astype(np.uint8) * 8 + a_tr.astype(np.uint8) * 4
            + a_br.astype(np.uint8) * 2 + a_bl.astype(np.uint8))
    idx = np.argwhere((code != 0) & (code != 15))
    if len(idx) == 0:
        return []
    if close_tol is None:
        close_tol = 0.25 * min(abs(xs[1] - xs[0]), abs(ys[1] - ys[0]))

    segs = []
    for i, j in idx:
        tl, tr = float(Z[i, j]), float(Z[i, j + 1])
        br, bl = float(Z[i + 1, j + 1]), float(Z[i + 1, j])
        for e0, e1 in _CASES[int(code[i, j])]:
            p0 = _edge_point(e0, j, i, xs, ys, tl, tr, br, bl, level)
            p1 = _edge_point(e1, j, i, xs, ys, tl, tr, br, bl, level)
            segs.append((p0, p1))

    def key(p):
        return (round(p[0] / close_tol), round(p[1] / close_tol))

    adj: dict = {}
    for a, b in segs:
        adj.setdefault(key(a), []).append((a, b))
        adj.setdefault(key(b), []).append((b, a))

    used = set()
    rings = []
    for si, (a0, b0) in enumerate(segs):
        if si in used:
            continue
        chain = [a0, b0]
        used.add(si)
        # walk forward
        for _ in range(len(segs) + 2):
            cand = adj.get(key(chain[-1]), [])
            nxt = None
            for (pa, pb) in cand:
                gi = _seg_index(segs, pa, pb, key)
                if gi is not None and gi not in used:
                    nxt = (gi, pb)
                    break
            if nxt is None:
                break
            used.add(nxt[0])
            chain.append(nxt[1])
            if key(chain[-1]) == key(chain[0]):
                break
        if len(chain) >= 4:
            if chain[-1] != chain[0]:
                chain.append(chain[0])
            rings.append(chain)
    return rings


_SEG_INDEX_CACHE: dict = {}


def _seg_index(segs, pa, pb, key):
    cid = id(segs)
    table = _SEG_INDEX_CACHE.get(cid)
    if table is None or table[0] is not segs:
        m = {}
        for i, (a, b) in enumerate(segs):
            m.setdefault((key(a), key(b)), []).append(i)
            m.setdefault((key(b), key(a)), []).append(i)
        table = (segs, m)
        _SEG_INDEX_CACHE.clear()
        _SEG_INDEX_CACHE[cid] = table
    lst = table[1].get((key(pa), key(pb)), [])
    return lst[0] if lst else None


def _ring_area(ring):
    s = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def clean_sources(sources, pool_lut=None):
    """Validate raw source dicts and attach their PM2.5 mass.

    Shared by the snapshot model (`haze_dispersion`) and the time-stepped 3D
    field (`app.model.haze3d`) so the two can never disagree about what a
    source emits. Silently drops sources with no area, an unknown pool, or a
    pool carrying no fuel load / emission factor -- an unknown pool is a data
    problem, and inventing a fuel load for it would fabricate emissions.
    """
    if pool_lut is None:
        pool_lut = _pool_lookup()
    clean = []
    for s in sources or []:
        try:
            lat, lon = float(s["lat"]), float(s["lon"])
            area_ha = float(s.get("area_ha") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if area_ha <= 0:
            continue
        pool = str(s.get("pool") or "peat_drained")
        fl, ef = pool_lut.get(pool, (0.0, 0.0))
        if fl <= 0 or ef <= 0:
            continue
        try:
            dur = float(s.get("duration_days") or DEFAULT_EPISODE_DAYS)
        except (TypeError, ValueError):
            dur = DEFAULT_EPISODE_DAYS
        dur = min(max(dur, 0.25), 180.0)
        clean.append({"lat": lat, "lon": lon, "area_ha": area_ha, "pool": pool,
                      "episode_days": dur, "pm25_kg": area_ha * fl * ef})
    return clean


def localise_sources(clean):
    """Project sources to a local km frame and derive emission rate and spread.

    Mutates each source in place, adding xk/yk (km from the centroid), q (g/s),
    radius_m and sy0. Returns (lat0, lon0, total_pm25_kg).
    """
    lat0 = sum(s["lat"] for s in clean) / len(clean)
    lon0 = sum(s["lon"] for s in clean) / len(clean)
    total_kg = 0.0
    for s in clean:
        s["xk"], s["yk"] = _to_local(s["lat"], s["lon"], lat0, lon0)
        s["q"] = s["pm25_kg"] * 1000.0 / (s["episode_days"] * 86400.0)   # g/s
        radius_m = math.sqrt(s["area_ha"] * 1e4 / math.pi)
        s["sy0"] = max(radius_m / 2.15, 30.0)
        s["radius_m"] = radius_m
        total_kg += s["pm25_kg"]
    return lat0, lon0, total_kg


def haze_dispersion(sources: list[dict], wind_speed_ms: float, wind_dir_deg: float,
                    mixing_height_m: float = 800.0, rain_mm: float = 0.0) -> dict:
    """Modelled PM2.5 at the downwind receptor cities, plus plume contours.

    ``sources`` = ``[{"lat":..,"lon":..,"area_ha":..,"pool":"peat_drained"}, ...]``.
    ``wind_dir_deg`` is the meteorological direction the wind blows *from*.
    See the module docstring for every equation and its citation.
    """
    import numpy as np

    try:
        from app.data.loader import load_receptors
        receptors = load_receptors() or []
    except Exception:
        receptors = []

    pool_lut = _pool_lookup()
    u = float(max(MIN_WIND_MS, float(wind_speed_ms or 0.0)))
    mixing_height_m = float(max(100.0, min(float(mixing_height_m or 800.0), 4000.0)))
    rain_mm = float(max(0.0, rain_mm or 0.0))
    stability = "E" if u < 2.5 else "D"
    # intermittency: a 40 mm day is ~8 h of rain at 5 mm/h, not 24 h of drizzle
    f_rain = min(1.0, rain_mm / (RAIN_INTENSITY_MM_H * 24.0)) if rain_mm > 0 else 0.0
    lam = (SCAVENGING_A * (RAIN_INTENSITY_MM_H ** SCAVENGING_B) * f_rain
           if f_rain > 0 else 0.0)

    clean = clean_sources(sources, pool_lut)

    dir_to = (float(wind_dir_deg or 0.0) + 180.0) % 360.0
    dir_to_rad = math.radians(dir_to)

    if not clean:
        return _empty_result(receptors, u, wind_dir_deg, mixing_height_m,
                             rain_mm, stability)

    lat0, lon0, total_kg = localise_sources(clean)

    # --- receptors -------------------------------------------------------
    out_rec = []
    for r in receptors:
        try:
            rlat, rlon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        xk, yk = _to_local(rlat, rlon, lat0, lon0)
        c = float(_concentration(np, np.array([xk]), np.array([yk]), clean, u,
                                 dir_to_rad, mixing_height_m, stability, lam)[0])
        pm = c + BACKGROUND_PM25_UGM3
        dist_km = math.hypot(xk, yk)
        # Arrival only where the plume actually is: a downwind projection alone
        # reported smoke "arriving" at cities far off the plume axis. Require the
        # modelled fire increment to reach ARRIVAL_THRESHOLD_UGM3, then time it by
        # the nearest source that is upwind of the receptor.
        arrival = None
        if c >= ARRIVAL_THRESHOLD_UGM3:
            exs = [math.sin(dir_to_rad) * (xk - s_["xk"]) + math.cos(dir_to_rad) * (yk - s_["yk"])
                   for s_ in clean]
            pos = [e for e in exs if e > 0]
            if pos:
                arrival = min(pos) * 1000.0 / u / 3600.0
        out_rec.append({
            "name": r.get("name") or r.get("city") or "receptor",
            "lat": rlat, "lon": rlon,
            "pm25_ugm3": round(pm, 1),
            "aqi": pm25_to_aqi(pm),
            "category": aqi_category(pm),
            "arrival_hours": (round(arrival, 1) if arrival is not None else None),
            "distance_km": round(dist_km, 1),
        })
    out_rec.sort(key=lambda r: -r["pm25_ugm3"])

    # --- contour grid ----------------------------------------------------
    # Domain: the source bbox, padded crosswind and extended downwind. It is
    # grown until the lowest contour band closes inside it, so that marching
    # squares never has to close a ring across the grid edge.
    ex, ny_ = math.sin(dir_to_rad), math.cos(dir_to_rad)
    sxk = [s["xk"] for s in clean]
    syk = [s["yk"] for s in clean]
    pad, rng_km = CROSSWIND_PAD_KM, MAX_RANGE_KM
    for _attempt in range(MAX_DOMAIN_EXPANSIONS + 1):
        x_lo = min(sxk) - pad + min(0.0, ex * rng_km)
        x_hi = max(sxk) + pad + max(0.0, ex * rng_km)
        y_lo = min(syk) - pad + min(0.0, ny_ * rng_km)
        y_hi = max(syk) + pad + max(0.0, ny_ * rng_km)
        xs = np.linspace(x_lo, x_hi, GRID_NX)
        ys = np.linspace(y_hi, y_lo, GRID_NY)   # descending so row 0 is north
        X, Y = np.meshgrid(xs, ys)
        Z = _concentration(np, X, Y, clean, u, dir_to_rad, mixing_height_m,
                           stability, lam) + BACKGROUND_PM25_UGM3
        edge = max(float(Z[0].max()), float(Z[-1].max()),
                   float(Z[:, 0].max()), float(Z[:, -1].max()))
        if edge < CONTOUR_BANDS[0]:
            break
        pad *= 1.7
        rng_km *= 1.7

    features = []
    cell_km2 = abs(xs[1] - xs[0]) * abs(ys[1] - ys[0])
    for level in CONTOUR_BANDS:
        rings = _contour_rings(np, xs, ys, Z, level)
        polys = []
        for ring in rings:
            if _ring_area(ring) < 1.5 * cell_km2:
                continue
            polys.append([list(_to_lonlat(px, py, lat0, lon0)) for px, py in ring])
        if not polys:
            continue
        kept_area = sum(_ring_area(r) for r in rings
                        if _ring_area(r) >= 1.5 * cell_km2)
        # The contour bands are the EPA breakpoint floors, so the category a
        # band represents is the one just above the boundary value.
        features.append({
            "type": "Feature",
            "geometry": {"type": "MultiPolygon",
                         "coordinates": [[p] for p in polys]},
            "properties": {
                "pm25_ugm3": level,
                "aqi": pm25_to_aqi(level + 0.5),
                "category": aqi_category(level + 0.5),
                "label": f">= {level:.0f} ug/m3",
                "area_km2": round(float(kept_area), 1),
            },
        })

    grid_area_km2 = float((Z >= CONTOUR_BANDS[0]).sum()) * cell_km2
    return {
        "receptors": out_rec,
        "plume": {"type": "FeatureCollection", "features": features},
        # This model makes no network calls: every number below is computed from
        # the sources and winds the caller supplied, never observed.
        "live": False,
        "total_pm25_kg": round(float(total_kg), 1),
        "source_summary": {
            "n_sources": len(clean),
            "total_area_ha": round(sum(s["area_ha"] for s in clean), 1),
            "total_pm25_kg": round(total_kg, 1),
            "total_pm25_t": round(total_kg / 1000.0, 2),
            "emission_rate_g_s": round(sum(s["q"] for s in clean), 1),
            "mean_episode_days": round(sum(s["episode_days"] for s in clean) / len(clean), 2),
        },
        "conditions": {
            "wind_speed_ms": round(u, 2),
            "wind_dir_deg": round(float(wind_dir_deg or 0.0), 1),
            "wind_blows_toward_deg": round(dir_to, 1),
            "mixing_height_m": mixing_height_m,
            "rain_mm": rain_mm,
            "stability_class": stability,
            "scavenging_coefficient_per_s": round(lam, 9),
            "rain_time_fraction": round(f_rain, 3),
            "dry_deposition_velocity_ms": DRY_DEPOSITION_VELOCITY_MS,
            "mesoscale_kh_m2_s": K_H_MESO_M2_S,
            "background_pm25_ugm3": BACKGROUND_PM25_UGM3,
            "area_over_35_ugm3_km2": float(round(grid_area_km2, 0)),
            "domain_range_km": round(float(rng_km), 0),
            "aqi_scale": "US EPA PM2.5, pre-2024 breakpoints",
        },
        "caveats": list(CAVEATS),
    }


def _empty_result(receptors, u, wind_dir_deg, mixing_height_m, rain_mm, stability):
    rec = []
    for r in receptors or []:
        try:
            rec.append({
                "name": r.get("name") or r.get("city") or "receptor",
                "lat": float(r["lat"]), "lon": float(r["lon"]),
                "pm25_ugm3": round(BACKGROUND_PM25_UGM3, 1),
                "aqi": pm25_to_aqi(BACKGROUND_PM25_UGM3),
                "category": aqi_category(BACKGROUND_PM25_UGM3),
                "arrival_hours": None, "distance_km": 0.0,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "receptors": rec,
        "plume": {"type": "FeatureCollection", "features": []},
        "live": False,
        "total_pm25_kg": 0.0,
        "source_summary": {"n_sources": 0, "total_area_ha": 0.0,
                           "total_pm25_kg": 0.0, "total_pm25_t": 0.0,
                           "emission_rate_g_s": 0.0,
                           "mean_episode_days": DEFAULT_EPISODE_DAYS},
        "conditions": {
            "wind_speed_ms": round(u, 2),
            "wind_dir_deg": round(float(wind_dir_deg or 0.0), 1),
            "wind_blows_toward_deg": round((float(wind_dir_deg or 0.0) + 180.0) % 360.0, 1),
            "mixing_height_m": mixing_height_m, "rain_mm": rain_mm,
            "stability_class": stability,
            "scavenging_coefficient_per_s": 0.0,
            "rain_time_fraction": 0.0,
            "dry_deposition_velocity_ms": DRY_DEPOSITION_VELOCITY_MS,
            "mesoscale_kh_m2_s": K_H_MESO_M2_S,
            "background_pm25_ugm3": BACKGROUND_PM25_UGM3,
            "area_over_35_ugm3_km2": 0.0,
            "domain_range_km": 0.0,
            "aqi_scale": "US EPA PM2.5, pre-2024 breakpoints",
        },
        "caveats": list(CAVEATS),
    }


CAVEATS = [
    "This map snapshot uses a single wind and Kalimantan clusters only. Sumatra's "
    "fires, which matter as much as Kalimantan's for Singapore and Peninsular "
    "Malaysia (Hansen et al. 2019; Reddington et al. 2014), are in the 3D "
    "simulator's particle model, not here. A low value at Singapore or Kuala Lumpur "
    "on this map means 'not modelled here', not 'clean air'.",
    "A steady-state Gaussian plume is a short-range, flat-terrain, "
    "constant-wind model. Briggs' dispersion curves were fitted over 100 m to "
    "10 km. Carrying smoke 600 km to Singapore needs a mesoscale spreading term "
    "bolted on, which is done here with a single eddy diffusivity. The absolute "
    "numbers should be read as order-of-magnitude, not as a forecast.",
    "The wind is a single speed and direction held constant for the whole "
    "trajectory. Real transport over the Java Sea and the Karimata Strait "
    "curves, shears with height, and reverses with the sea breeze; a proper "
    "answer needs a Lagrangian model like HYSPLIT driven by gridded winds.",
    "All emissions are treated as ground-level and released evenly over the "
    "cluster's smouldering lifetime (14 days when the data does not say). "
    "Smouldering peat has almost no plume rise, which is why the smoke stays "
    "in the boundary layer and why this assumption is defensible over land -- "
    "but flaming vegetation fires do loft, and those are lumped in here. The "
    "assumed lifetime is the single biggest lever on the absolute numbers: "
    "halve it and every concentration doubles.",
    "No chemistry and no secondary aerosol. A substantial fraction of the PM2.5 "
    "measured downwind of a peat fire forms in transit from organic vapours, so "
    "far-field concentrations here are biased low even before dispersion error.",
    "Mixing height is a user input, not a model output. The diurnal cycle "
    "matters enormously: the same emissions give several times higher "
    "concentrations under a 200 m nocturnal inversion than under an 1800 m "
    "afternoon boundary layer.",
    "Wet scavenging uses a single below-cloud coefficient applied along the "
    "whole trajectory, driven by rainfall at the source and scaled by how much "
    "of the day it rains. It therefore assumes the weather over the fire is the "
    "weather over the whole 600 km path, which it is not. Rain at the receptor, "
    "not at the fire, is what actually clears a city's air.",
    "The AQI table here is the pre-2024 US EPA PM2.5 scale, chosen so that the "
    "contour bands and the category labels agree. The 2024 revision moved the "
    "Good/Moderate boundary from 12 to 9 ug/m3, and Indonesian and Singaporean "
    "national indices differ again, so an 'Unhealthy' here is not identical to "
    "an 'Unhealthy' on a government app.",
    "Every source handed in is treated as burning simultaneously and steadily. "
    "Pass a whole season's fire clusters and you get the hypothetical smoke of "
    "the entire season arriving at once, which is not what any day looked like; "
    "to model a real day, pass only the clusters active on that date.",
    "Nothing here is a measurement. There is no PM2.5 monitor in this model: "
    "every concentration is computed from an assumed emission factor, an "
    "assumed burn duration and an idealised wind, so it should be compared "
    "against real monitor data, not substituted for it.",
    "Contours are drawn by marching squares on a coarse grid of the analytic "
    "concentration field. Near the sources the grid cannot resolve the plume, "
    "so the innermost bands are shape-approximate.",
]
