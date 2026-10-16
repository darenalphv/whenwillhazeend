"""haze3d.py, v2: a time-stepped PM2.5 field for the 3D simulator, from LPM-3L.

v1 superposed steady Gaussian plumes from synthesised Kalimantan clusters under one
wind vector. That gave a pencil-thin plume that had to be aimed within a few degrees
of Kuala Lumpur to hit it, ignored Sumatra, and its "arrival" times were straight-line
projections that reported smoke reaching cities it never reached.

v2 runs the reduced-form three-layer Lagrangian particle model of
:mod:`app.model.transport` (MODEL_SPEC 5.4) inside the request:

* **Winds**: ERA5 (1.5 deg, 6-hourly, near-surface / 925 / 850 hPa) for the bundled
  fire seasons; for other dates, the ERA5 monthly composite of those seasons
  ("climatology"); with no date at all, the caller's single wind vector ("uniform",
  v1 semantics). Particles curve, spread and shear with height.
* **Emissions**: observed daily emissions (GFED4.1s dry matter x the E1 emission
  factors, 0.25 deg) for Kalimantan **and Sumatra** and Malaysia when the date has
  them; otherwise the bundled synthesised clusters (or the caller's sources) with E1
  per-hectare emissions released evenly over each cluster's episode. Which one was
  used is reported in ``conditions``.
* **Boundary layer**: ERA5 daily-mean BLH with the ERA5 station diurnal profiles
  (spec H1-H4), not one analytic profile.
* **Spin-up**: unless ``release_from_start``, the model runs 4 days before the window
  so smoke already in the air is there at hour 0.

Response conventions (CONTRACT_V2 haze3d v2; stated here because the 3D view relies
on them)
-------------------------------------------------------------------------------
* ``grid``: nodes span lon_min..lon_max and lat_min..lat_max **inclusive**
  (nx, ny nodes; dlon = (lon_max - lon_min) / (nx - 1)); values row-major, rows
  south to north.
* ``frames[k].values``: surface (mixed-layer, L1) PM2.5 in ug/m3, integers, *mean over
  the frame's window* [time - step/2, time + step/2), including the non-fire
  background ``scale.background_pm25``.
* ``frames[k].values_aloft``: ug/m3, integers, the **layer-mean** fire PM2.5 between the
  local mixing height and ``aloft_top_m`` (2,500 m AGL), i.e. residual layer + lower
  free troposphere, no background. ``aloft_base_m`` is the median mixing height over
  the particles at that frame (the base varies in space; this is a summary).
* ``wind``: its own ``bbox`` (identical to ``grid``'s), nodes inclusive of both edges,
  ``nx`` x ``ny`` nodes; ``wind.frames[k]`` aligns one-to-one with ``frames[k]`` and
  carries the same ``hour``. Index = level*nx*ny + row*nx + col. m/s, eastward u,
  northward v.
* All heights (``levels_m``, ``injection_m``, ``aloft_*_m``, ``mixing_height_m``) are
  **metres above ground level**. The 925 and 850 hPa levels are given nominal heights
  of 750 and 1,500 m AGL [J].
* ``sources[].emission_g_s`` is the mean rate over the source's ``on`` window;
  ``on: [start_h, end_h]`` is when it is burning, in hours from frame 0; with observed
  emissions the rate varies by day and 3-hour block, so ``emission_g_s_frames`` gives it
  per frame (``emission_constant`` says which).
* ``receptor_series[].arrival_hours``: only in release mode, and only when the city's
  modelled fire PM2.5 (from the particles) reaches 5 ug/m3 above background; cities the
  smoke does not reach report null and ``downwind: false``.
"""

from __future__ import annotations

import functools
import math
from datetime import date, datetime, timedelta, timezone

from .dispersion import CONTOUR_BANDS, aqi_category, pm25_to_aqi

__all__ = ["haze_field", "diurnal_mixing_height", "CAVEATS", "MAX_CELLS", "MAX_SOURCES",
           "LOCAL_UTC_OFFSET_H"]

MAX_CELLS = 400_000
MAX_SOURCES = 240               # sources listed in the response (all are simulated)
LOCAL_UTC_OFFSET_H = 7.0        # start_hour and hour_of_day are WIB (UTC+7)
SPINUP_H = 96.0
DT_S = 1800.0                   # request-time step [J]; offline runs use 900 s
PARTICLE_BUDGET = 30000         # particles released over a run [J]
ARRIVAL_THRESHOLD_UGM3 = 5.0    # fire increment that counts as "smoke has arrived" [J]
ALOFT_TOP_M = 2500.0
DEFAULT_SEED = 7

ISLANDS = ("kalimantan", "sumatra", "peninsular_malaysia", "malaysian_borneo", "other")


def diurnal_mixing_height(mean_m: float, hour_of_day: float, site: str = "palangkaraya") -> float:
    """Mixing height at a local hour for a daily mean (spec H1/H3; ERA5 profile)."""
    from .transport import diurnal_mixing_height as dmh
    return dmh(mean_m, hour_of_day, site)


def _bg():
    from .transport import param
    return param("background_pm25_nonfire", 12.5)


# --------------------------------------------------------------------------- #
# emissions for the run
# --------------------------------------------------------------------------- #

class _Emitter:
    """Emission rate (kg/s) by source and injection kind, at any UTC time."""

    def __init__(self, np, lat, lon, peat_kg_s, flame_kg_s, island, region_id,
                 daily=None, label="", note=None, windows=None):
        self.np = np
        self.lat, self.lon = lat, lon
        self.peat = peat_kg_s
        self.flame = flame_kg_s
        self.island = island            # int index into ISLANDS
        self.region_id = region_id      # province-level id array or None
        self.daily = daily              # callable(date) -> (peat_kg_day, nonpeat_kg_day, diurnal)
        self.label = label
        self.note = note
        self.windows = windows          # per-source (start, end) datetimes, or None
        self.daily_resolved = False
        self._cache = {}

    def rates(self, t: datetime):
        np = self.np
        if self.daily is None:
            if self.windows is None:
                return self.peat, self.flame
            on = np.array([(a is None or a <= t) and (b is None or t <= b)
                           for a, b in self.windows])
            return np.where(on, self.peat, 0.0), np.where(on, self.flame, 0.0)
        d = t.date()
        if d not in self._cache:
            self._cache[d] = self.daily(d)
        got = self._cache[d]
        if got is None:
            z = np.zeros_like(self.lat)
            return z, z
        peat_day, non_day, di = got
        q = (t.hour // 3) % 8
        return peat_day / 86400.0, non_day * di[q] * 8.0 / 86400.0


def _observed_emitter(np, d0: date):
    from . import emissions as E
    first = E.daily_emissions(d0)
    if first is None:
        return None
    reg = E.region_table()
    island = np.array([ISLANDS.index(reg[i]["island"]) if reg[i]["island"] in ISLANDS else 4
                       for i in first["region"]])
    rid = np.array([reg[i]["id"] for i in first["region"]])

    def daily(d):
        em = E.daily_emissions(d)
        if em is None:
            return None
        return em["peat_kg"], em["nonpeat_kg"], em["diurnal"]

    nrt = str(first["source"]).startswith("NASA FIRMS")
    e = _Emitter(np, first["lat"], first["lon"], None, None, island, rid, daily=daily,
                 label="observed_nrt" if nrt else "observed", note=first["source"])
    e.daily_resolved = first["daily_resolved"]
    return e


def _cluster_emitter(np, clean, raw, label, note):
    """Constant-rate emitter from cleaned cluster/user sources (E1 per hectare)."""
    lat = np.array([s["lat"] for s in clean])
    lon = np.array([s["lon"] for s in clean])
    rate = np.array([s["pm25_kg"] / (s["episode_days"] * 86400.0) for s in clean])
    is_peat = np.array([s["pool"].startswith("peat") for s in clean])
    island = np.array([_island_of(la, lo) for la, lo in zip(lat, lon)])
    by_key = {}
    for r in raw or []:
        try:
            by_key[(round(float(r["lat"]), 6), round(float(r["lon"]), 6))] = r.get("date")
        except (KeyError, TypeError, ValueError):
            continue
    windows = []
    for s in clean:
        dd = by_key.get((round(s["lat"], 6), round(s["lon"], 6)))
        try:
            a = datetime.fromisoformat(str(dd)[:10]).replace(tzinfo=timezone.utc) if dd else None
        except ValueError:
            a = None
        windows.append((a, a + timedelta(days=s["episode_days"]) if a else None))
    e = _Emitter(np, lat, lon, np.where(is_peat, rate, 0.0), np.where(is_peat, 0.0, rate),
                 island, None, label=label, note=note,
                 windows=windows if any(w[0] for w in windows) else None)
    return e


def _island_of(lat, lon):
    """Coarse island label for a caller-supplied source [J: boxes, not polygons]."""
    if lon >= 108.5 and lat > -4.5:
        return 3 if (lat > 1.5 and lon < 117.5) or lat > 4.3 else 0
    if lon < 106.5:
        return 2 if lat > 1.3 and lon > 100.2 else 1
    return 4


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _smooth(np, Z, passes=1):
    """Separable [1,2,1]/4 smoothing, edge-replicated (about one cell of spread)."""
    for _ in range(passes):
        P = np.pad(Z, ((1, 1), (1, 1)), mode="edge")
        Z = (P[:-2, 1:-1] + 2 * P[1:-1, 1:-1] + P[2:, 1:-1]) / 4.0
        P = np.pad(Z, ((1, 1), (1, 1)), mode="edge")
        Z = (P[1:-1, :-2] + 2 * P[1:-1, 1:-1] + P[1:-1, 2:]) / 4.0
    return Z


def _bilinear_sample(Z, box, nx, ny, lat, lon):
    fx = (lon - box["lon_min"]) / (box["lon_max"] - box["lon_min"]) * (nx - 1)
    fy = (lat - box["lat_min"]) / (box["lat_max"] - box["lat_min"]) * (ny - 1)
    if fx < 0 or fy < 0 or fx > nx - 1 or fy > ny - 1:
        return None
    i, j = min(int(fx), nx - 2), min(int(fy), ny - 2)
    wx, wy = fx - i, fy - j
    return float((Z[j, i] * (1 - wx) + Z[j, i + 1] * wx) * (1 - wy)
                 + (Z[j + 1, i] * (1 - wx) + Z[j + 1, i + 1] * wx) * wy)


def _parse_start(start_date, start_hour):
    if not start_date:
        return None
    try:
        d = datetime.fromisoformat(str(start_date)[:10])
    except ValueError:
        return None
    local = d + timedelta(hours=float(start_hour or 0.0))
    return (local - timedelta(hours=LOCAL_UTC_OFFSET_H)).replace(tzinfo=timezone.utc)


def _year_of_sources(sources):
    ys = {}
    for s in sources or []:
        dd = s.get("date") if isinstance(s, dict) else None
        if dd:
            try:
                y = int(str(dd)[:4])
                ys[y] = ys.get(y, 0) + 1
            except ValueError:
                pass
    return max(ys, key=ys.get) if ys else None


@functools.lru_cache(maxsize=64)
def _peak_emission_day(year):
    """Date of the year's largest daily emission (Aug-Oct), for a dateless request."""
    from . import emissions as E
    best, best_v = None, -1.0
    d = date(year, 8, 1)
    while d <= date(year, 10, 31):
        em = E.daily_emissions(d)
        if em is not None:
            v = float(em["peat_kg"].sum() + em["nonpeat_kg"].sum())
            if v > best_v:
                best, best_v = d, v
        d += timedelta(days=1)
    return best


@functools.lru_cache(maxsize=1)
def _receptors():
    try:
        from app.data.loader import load_receptors
        return tuple(load_receptors() or [])
    except Exception:
        return ()


# --------------------------------------------------------------------------- #
# main entry point
# --------------------------------------------------------------------------- #

def haze_field(sources: list[dict] | None, wind_speed_ms: float = 3.0,
               wind_dir_deg: float = 190.0, hours: int = 24, step_hours: float = 1.0,
               mixing_height_m: float = 800.0, rain_mm: float = 0.0,
               grid_n: int = 56, wind_veer_deg_per_h: float = 0.0,
               start_hour: float = 0.0, start_date: str | None = None,
               diurnal: bool = True, filter_active: bool = True,
               release_from_start: bool = False, *, year: int | None = None,
               wind_mode: str = "auto", emissions_mode: str = "auto",
               soa: bool = True, seed: int | None = DEFAULT_SEED) -> dict:
    """Gridded PM2.5 over time for the 3D simulator (CONTRACT_V2 haze3d v2 response).

    ``start_date``/``start_hour`` are local (WIB, UTC+7). ``wind_*``, ``mixing_height_m``
    and ``rain_mm`` drive the run only when no meteorology is used for it
    (``conditions.wind_source == "uniform"``); otherwise ERA5 winds, BLH and rain do.
    ``wind_mode``: auto | observed | climatology | uniform.
    ``emissions_mode``: auto | observed | sources ("sources" = use the given sources
    even when observed emissions exist for the date).
    ``diurnal`` is accepted for v1 compatibility; the boundary-layer cycle is always on.
    Raises ValueError if the requested grid would exceed ``MAX_CELLS``.
    """
    import numpy as np
    from . import emissions as E
    from . import transport as T
    from .dispersion import _pool_lookup, clean_sources

    grid_n = int(max(8, min(128, grid_n)))
    step_hours = float(max(0.25, min(6.0, step_hours)))
    hours = int(max(1, min(168, hours)))
    n_frames = max(1, int(round(hours / step_hours)))
    if grid_n * grid_n * n_frames > MAX_CELLS:
        raise ValueError(
            f"requested field is {grid_n}x{grid_n}x{n_frames} = "
            f"{grid_n * grid_n * n_frames} cells, over the {MAX_CELLS} ceiling; "
            f"reduce grid_n, hours, or increase step_hours")
    notes = []
    rng = np.random.default_rng(seed)
    spin_h = 0.0 if release_from_start else SPINUP_H

    # ---- time window -------------------------------------------------------
    t0 = _parse_start(start_date, start_hour)
    yr = year or (t0.year if t0 else None) or _year_of_sources(sources)
    if (t0 is None and yr and yr in E.emission_years() and emissions_mode != "sources"
            and wind_mode != "uniform"):
        pk = _peak_emission_day(yr)
        if pk is not None:
            t0 = datetime(pk.year, pk.month, pk.day, tzinfo=timezone.utc) + \
                timedelta(hours=float(start_hour or 0.0) - LOCAL_UTC_OFFSET_H)
            notes.append(f"No date was given; the run uses {pk.isoformat()}, the day of "
                         f"{yr}'s largest observed fire emissions.")

    # ---- emissions ---------------------------------------------------------
    clean = clean_sources(sources or [], _pool_lookup())
    has_dates = any(isinstance(s, dict) and s.get("date") for s in sources or [])
    emitter = None
    inactive = 0
    want_obs = emissions_mode == "observed" or (
        emissions_mode == "auto" and (has_dates or not clean))
    if t0 is not None and want_obs and t0.year in E.emission_years():
        emitter = _observed_emitter(np, (t0 - timedelta(hours=spin_h)).date())
        if emitter is not None and has_dates:
            notes.append("Observed daily emissions (Kalimantan, Sumatra and Malaysia) were "
                         "used in place of the season's synthesised clusters.")
    if emitter is None:
        if not clean:
            return _empty(wind_speed_ms, wind_dir_deg, mixing_height_m, rain_mm,
                          "No usable fire sources were supplied, and no observed emissions "
                          "are bundled for this date, so there is nothing to disperse.")
        if t0 is not None and filter_active and has_dates:
            kept = _active_on(sources, clean, t0, hours, spin_h)
            if kept is not None:
                inactive = len(clean) - len(kept)
                clean = kept
        if not clean:
            return _empty(wind_speed_ms, wind_dir_deg, mixing_height_m, rain_mm,
                          "None of the supplied clusters is burning in this window.")
        label = "clusters" if has_dates else "sources"
        note = ("Synthesised fire clusters (positions modelled, not observed), Kalimantan "
                "only, with E1 per-hectare emissions released evenly over each cluster's "
                "episode." if has_dates else
                "Caller-supplied sources with E1 per-hectare emissions released evenly "
                "over each source's episode (14 days unless given).")
        emitter = _cluster_emitter(np, clean, sources, label, note)

    # ---- meteorology -------------------------------------------------------
    met = None
    if t0 is not None and wind_mode in ("auto", "observed"):
        m = T.load_met(t0.year)
        if m is not None and m.covers(t0 - timedelta(hours=spin_h), t0 + timedelta(hours=hours)):
            met = m
    if met is None and t0 is not None and wind_mode in ("auto", "climatology"):
        met = T.climatology_met(t0 - timedelta(hours=spin_h), hours + spin_h)
        if met is not None:
            notes.append("No observed meteorology is bundled for this date: winds, mixing "
                         "height and rain are the ERA5 monthly composite of the bundled "
                         "fire seasons, which has the right prevailing flow but none of the "
                         "day-to-day weather.")
    if met is None:
        start = t0 or datetime(2015, 9, 1, tzinfo=timezone.utc)
        met = T.uniform_met(float(max(0.3, wind_speed_ms or 0.0)), float(wind_dir_deg or 0.0),
                            float(mixing_height_m or 800.0), float(rain_mm or 0.0),
                            float(wind_veer_deg_per_h or 0.0), start=start,
                            hours=hours + spin_h)
        if t0 is None:
            t0 = start
            notes.append("No date was given: one wind vector is applied everywhere and at "
                         "every height, as in v1. Give a date in a bundled fire season for "
                         "ERA5 winds.")
    t_end = t0 + timedelta(hours=hours)

    # ---- domain ------------------------------------------------------------
    recs = _receptors()
    strength = _window_strength(np, emitter, t0, t_end)
    order = np.argsort(-strength)
    top = order[:400]
    top = top[strength[top] > 0] if (strength[top] > 0).any() else top
    lats = list(emitter.lat[top]) + [r["lat"] for r in recs]
    lons = list(emitter.lon[top]) + [r["lon"] for r in recs]
    box = {
        "lon_min": max(float(met.lon[0]) + 0.75, min(lons) - 1.0),
        "lon_max": min(float(met.lon[-1]) - 0.75, max(lons) + 1.0),
        "lat_min": max(float(met.lat[0]) + 0.75, min(lats) - 1.0),
        "lat_max": min(float(met.lat[-1]) - 0.75, max(lats) + 1.0),
    }
    nx = ny = grid_n
    dlon = (box["lon_max"] - box["lon_min"]) / (nx - 1)
    dlat = (box["lat_max"] - box["lat_min"]) / (ny - 1)
    lat_rows = box["lat_min"] + np.arange(ny) * dlat
    cell_area = (dlon * 111.32e3) * (dlat * 110.574e3) * np.cos(np.radians(lat_rows))   # m2

    # ---- run ---------------------------------------------------------------
    t_start = t0 - timedelta(hours=spin_h)
    # long runs take a coarser step to stay inside the CPU budget [J]
    DT = DT_S if (hours + spin_h) <= 150 else 2 * DT_S
    n_steps = int(round((t_end - t_start).total_seconds() / DT))
    per_step = int(max(16, min(240, math.ceil(PARTICLE_BUDGET / max(1, n_steps)))))
    lpm = T.LPM(met, dt_s=DT, seed=seed, soa=soa,
                t_start_h=met.hours_since_t0(t_start), max_age_h=24.0 * 8)
    ncell = nx * ny
    frame_sum = np.zeros((n_frames, ncell))
    aloft_sum = np.zeros((n_frames, ncell))
    frame_n = np.zeros(n_frames)
    aloft_base = np.zeros(n_frames)
    rec_ci = np.array([int(round((r["lon"] - box["lon_min"]) / dlon)) for r in recs], int)
    rec_cj = np.array([int(round((r["lat"] - box["lat_min"]) / dlat)) for r in recs], int)
    isl_sum = np.zeros((len(recs), len(ISLANDS)))
    age_sum = np.zeros(len(recs))
    total_emitted = 0.0
    t = t_start
    for k in range(n_steps + 1):
        if k < n_steps:
            pr, fr = emitter.rates(t)
            tot = float(pr.sum() + fr.sum())
            if tot > 0:
                mass_step = tot * DT
                if t >= t0:
                    total_emitted += mass_step
                w = np.concatenate([pr, fr]) / tot
                pick = rng.choice(len(w), size=per_step, p=w)
                n_src = len(pr)
                kind = (pick >= n_src).astype(int)
                src = pick % n_src
                jit = (rng.random((2, per_step)) - 0.5) * 0.25
                lpm.release(emitter.lon[src] + jit[0], emitter.lat[src] + jit[1],
                            np.full(per_step, mass_step / per_step), kind,
                            emitter.island[src])
        rel_h = (t - t0).total_seconds() / 3600.0
        f = int(math.floor(rel_h / step_hours + 0.5))
        if 0 <= f < n_frames and len(lpm.x):
            ci = np.floor((lpm.x - box["lon_min"]) / dlon + 0.5).astype(np.int64)
            cj = np.floor((lpm.y - box["lat_min"]) / dlat + 0.5).astype(np.int64)
            ok = (ci >= 0) & (ci < nx) & (cj >= 0) & (cj < ny)
            in1 = lpm.z <= lpm.H
            m_eff = lpm.effective_mass()
            s1 = ok & in1
            cell = cj * nx + ci
            v1 = m_eff[s1] * 1e9 / (cell_area[cj[s1]] * lpm.H[s1])
            frame_sum[f] += np.bincount(cell[s1], weights=v1, minlength=ncell)
            s2 = ok & ~in1 & (lpm.z < ALOFT_TOP_M)
            v2 = m_eff[s2] * 1e9 / (cell_area[cj[s2]] * np.maximum(ALOFT_TOP_M - lpm.H[s2], 100.0))
            aloft_sum[f] += np.bincount(cell[s2], weights=v2, minlength=ncell)
            aloft_base[f] += float(np.median(lpm.H))
            frame_n[f] += 1
            # source attribution near each receptor (3x3 cells), from particle tags
            if len(recs) and s1.any():
                cs, cjs, cis = cell[s1], cj[s1], ci[s1]
                tg, ag = lpm.tag[s1], lpm.age[s1]
                for ri in range(len(recs)):
                    near = (np.abs(cjs - rec_cj[ri]) <= 1) & (np.abs(cis - rec_ci[ri]) <= 1)
                    if near.any():
                        vv = v1[near]
                        isl_sum[ri] += np.bincount(tg[near], weights=vv,
                                                   minlength=len(ISLANDS))[:len(ISLANDS)]
                        age_sum[ri] += float((vv * ag[near]).sum())
                del cs
        if k == n_steps:
            break
        lpm.step()
        t = t + timedelta(seconds=DT)

    bg = _bg()
    fn = np.maximum(frame_n, 1)
    frames = []
    field_max = 0.0
    rec_fire = np.full((len(recs), n_frames), np.nan)
    for f in range(n_frames):
        Z = _smooth(np, (frame_sum[f] / fn[f]).reshape(ny, nx))
        A = _smooth(np, (aloft_sum[f] / fn[f]).reshape(ny, nx))
        Zt = Z + bg
        field_max = max(field_max, float(Zt.max()))
        tf = t0 + timedelta(hours=f * step_hours)
        hod = (tf.hour + tf.minute / 60.0 + LOCAL_UTC_OFFSET_H) % 24.0
        for ri, r in enumerate(recs):
            v = _bilinear_sample(Z, box, nx, ny, r["lat"], r["lon"])
            if v is not None:
                rec_fire[ri, f] = v
        frames.append({
            "index": f,
            "hour": round(f * step_hours, 2),
            "hour_of_day": round(hod, 2),
            "label": f"{int(hod):02d}:{int(round((hod % 1) * 60)) % 60:02d}",
            "time": tf.isoformat(),
            "mixing_height_m": round(float(aloft_base[f] / fn[f])),
            "wind_dir_deg": None, "wind_speed_ms": None,
            "max_pm25": round(float(Zt.max()), 1),
            "mean_pm25": round(float(Zt.mean()), 1),
            "values": np.rint(np.clip(Zt, 0, 9999)).astype(int).ravel().tolist(),
            "values_aloft": np.rint(np.clip(A, 0, 9999)).astype(int).ravel().tolist(),
            "aloft_base_m": round(float(aloft_base[f] / fn[f])),
            "aloft_top_m": int(ALOFT_TOP_M),
        })

    wind = _wind_block(np, met, box, t0, n_frames, step_hours, frames)

    # ---- receptors -----------------------------------------------------------
    out_rec = []
    for ri, r in enumerate(recs):
        fire = rec_fire[ri]
        if np.all(np.isnan(fire)):
            continue
        fire = np.nan_to_num(fire)
        series = [round(float(v + bg), 1) for v in fire]
        peak = max(series)
        pk = series.index(peak)
        reached = bool(fire.max() >= ARRIVAL_THRESHOLD_UGM3)
        arrival = None
        if release_from_start and reached:
            hit = np.where(fire >= ARRIVAL_THRESHOLD_UGM3)[0]
            arrival = round(float(hit[0] * step_hours), 1)
        isl = isl_sum[ri]
        s = float(isl.sum())
        shares = ({ISLANDS[i]: round(float(isl[i] / s), 3) for i in range(len(ISLANDS))
                   if isl[i] > 0} if s > 0 else {})
        d_km = float(np.min(np.hypot((emitter.lat[top] - r["lat"]) * 110.574,
                                     (emitter.lon[top] - r["lon"]) * 111.32
                                     * math.cos(math.radians(r["lat"])))))
        out_rec.append({
            "id": r.get("id"), "name": r.get("name"), "lat": r["lat"], "lon": r["lon"],
            "country": r.get("country"),
            "pm25": series, "peak_pm25": peak, "peak_frame": pk,
            "peak_hour_of_day": frames[pk]["hour_of_day"],
            "peak_aqi": pm25_to_aqi(peak), "peak_category": aqi_category(peak),
            "mean_fire_pm25": round(float(fire.mean()), 1),
            "distance_km": round(d_km, 1),
            "arrival_hours": arrival,
            "downwind": reached,
            "smoke_age_hours": round(age_sum[ri] / s, 1) if s > 0 else None,
            "source_shares": shares,
        })

    src_out = _sources_block(np, emitter, t0, met, n_frames, step_hours)
    budget = {
        "particles_per_step": per_step, "steps": n_steps, "dt_s": DT,
        "spinup_hours": spin_h, "particles_alive_end": int(len(lpm.x)),
        "emitted_in_window_kt": round(total_emitted / 1e6, 2),
        "removed_kt": round((lpm.removed_dry + lpm.removed_wet) / 1e6, 2),
        "exported_kt": round(lpm.exported / 1e6, 2),
    }
    uni = met.label == "uniform"
    return {
        "version": 2,
        "grid": {
            **{k: round(v, 5) for k, v in box.items()},
            "nx": nx, "ny": ny, "dlon": round(dlon, 6), "dlat": round(dlat, 6),
            "order": "row-major, rows south to north, columns west to east",
            "nodes": "inclusive of lon_min/lon_max and lat_min/lat_max",
            "edge_note": "A straight edge in the field is the edge of the model grid.",
        },
        "frames": frames,
        "frame_values": {
            "values": "surface (mixed-layer) PM2.5, ug/m3, mean over the frame window, incl. background",
            "values_aloft": ("fire PM2.5, ug/m3, layer mean between the local mixing height and "
                             "aloft_top_m AGL (residual layer + lower free troposphere), no background"),
            "heights": "metres above ground level",
        },
        "wind": wind,
        "receptor_series": out_rec,
        "sources": src_out,
        "validation": _validation(t0, out_rec),
        "season_context": _season_context(t0),
        "total_pm25_kg": round(total_emitted, 1),
        "sources_used": int(len(emitter.lat)),
        "sources_dropped": 0,
        "sources_inactive_on_date": inactive,
        "emission_fraction_retained": 1.0,
        "conditions": {
            "transport_model": "LPM-3L (MODEL_SPEC 5.4)",
            "wind_source": met.label, "wind_source_detail": met.source,
            "emissions_source": emitter.label, "emissions_source_detail": emitter.note,
            "emissions_daily_resolved": bool(emitter.daily_resolved),
            "start_utc": t0.isoformat(), "local_time": "WIB (UTC+7)",
            "wind_speed_ms": round(float(wind_speed_ms or 0.0), 2) if uni else None,
            "wind_dir_deg": float(wind_dir_deg or 0.0) if uni else None,
            "wind_veer_deg_per_h": float(wind_veer_deg_per_h or 0.0) if uni else None,
            "mean_mixing_height_m": round(float(mixing_height_m or 800.0)) if uni else None,
            "night_mixing_height_m": round(diurnal_mixing_height(mixing_height_m or 800.0, 3.0)) if uni else None,
            "day_mixing_height_m": round(diurnal_mixing_height(mixing_height_m or 800.0, 14.0)) if uni else None,
            "mixing_height_source": ("ERA5 daily-mean BLH x ERA5 station diurnal profiles"
                                     if not uni else
                                     "caller's daily mean x ERA5 station diurnal profiles"),
            "rain_mm": float(rain_mm or 0.0) if uni else None,
            "background_pm25_ugm3": bg, "background_verified": False,
            "soa": bool(soa),
            "soa_note": ("Values include secondary organic aerosol formed in transit "
                         "(Kiely 2019 yield, up to +46% of primary after about a day; the "
                         "formation time is UNVERIFIED). soa=false gives the primary-only "
                         "lower bound." if soa else "Primary smoke only (SOA off)."),
            "diurnal": True,
            "release_from_start": bool(release_from_start),
            "hours": hours, "step_hours": step_hours,
            "lambda0_per_s": lpm.lambda0, "k_h_m2_s": lpm.k_h, "v_d_m_s": lpm.v_d,
            "particle_budget": budget,
            "seed": seed,
        },
        "scale": {"bands": list(CONTOUR_BANDS), "max_pm25": round(field_max, 1),
                  "background_pm25": bg},
        "live": False,
        "note": " ".join(notes) or None,
        "caveats": list(CAVEATS),
    }


def _window_strength(np, emitter, t0, t_end):
    tot = np.zeros_like(emitter.lat, dtype=float)
    t = t0
    while t <= t_end:
        p, f = emitter.rates(t)
        tot = tot + p + f
        t += timedelta(hours=6)
    return tot


def _active_on(raw_sources, clean, t0, hours, spin_h):
    """Subset of cleaned clusters burning during the run; None if none are dated."""
    by_key = {}
    for r in raw_sources or []:
        try:
            by_key[(round(float(r["lat"]), 6), round(float(r["lon"]), 6))] = r.get("date")
        except (KeyError, TypeError, ValueError):
            continue
    a = t0.replace(tzinfo=None) - timedelta(hours=spin_h)
    b = t0.replace(tzinfo=None) + timedelta(hours=float(hours))
    kept, dated = [], 0
    for s in clean:
        dd = by_key.get((round(s["lat"], 6), round(s["lon"], 6)))
        if not dd:
            continue
        try:
            ds = datetime.fromisoformat(str(dd)[:10])
        except ValueError:
            continue
        dated += 1
        de = ds + timedelta(days=float(s.get("episode_days") or 0.0))
        if ds <= b and de >= a:
            kept.append(s)
    return kept if dated else None


def _wind_block(np, met, box, t0, n_frames, step_hours, frames):
    """Coarse wind field per frame at three levels (CONTRACT_V2 'wind')."""
    from . import transport as T
    n = 16 if n_frames <= 48 else 10
    lons = np.linspace(box["lon_min"], box["lon_max"], n)
    lats = np.linspace(box["lat_min"], box["lat_max"], n)
    LON, LAT = np.meshgrid(lons, lats)
    idx = met.index(LON.ravel(), LAT.ravel())
    out = []
    for f in range(n_frames):
        tf = t0 + timedelta(hours=f * step_hours)
        F = met.slice_at(met.hours_since_t0(tf))
        us, vs = [], []
        for uk, vk in (("u10", "v10"), ("u_925", "v_925"), ("u_850", "v_850")):
            us.append(met.bilinear(F[uk], idx))
            vs.append(met.bilinear(F[vk], idx))
        out.append({"hour": round(f * step_hours, 2),
                    "u": np.round(np.concatenate(us), 1).tolist(),
                    "v": np.round(np.concatenate(vs), 1).tolist()})
        um, vm = float(us[0].mean()), float(vs[0].mean())
        frames[f]["wind_speed_ms"] = round(math.hypot(um, vm), 2)
        frames[f]["wind_dir_deg"] = round((math.degrees(math.atan2(-um, -vm)) + 360.0) % 360.0, 1)
    return {"nx": n, "ny": n, "levels_m": [10, T.LEVEL_HEIGHTS_M[1], T.LEVEL_HEIGHTS_M[2]],
            "levels": ["10 m", "925 hPa", "850 hPa"],
            "levels_reference": "metres above ground level; pressure levels at nominal heights [J]",
            "bbox": {k: round(v, 5) for k, v in box.items()},
            "nodes": "inclusive of the bbox edges, same convention as grid",
            "order": "index = level*nx*ny + row*nx + col; rows south to north",
            "frames_align_with": "frames (same index and hour)",
            "units": "m/s; u eastward, v northward",
            "source": met.source, "kind": met.label, "frames": out}


def _sources_block(np, emitter, t0, met, n_frames, step_hours):
    """Listed sources (largest MAX_SOURCES over the window); every source is simulated."""
    from .emissions import pm25_kg_per_ha
    times = [t0 + timedelta(hours=f * step_hours) for f in range(n_frames)]
    R = np.array([np.add(*emitter.rates(tt)) for tt in times])          # (frames, n) kg/s
    P = np.array([emitter.rates(tt)[0] for tt in times])
    mean_rate = R.mean(axis=0)
    order = np.argsort(-mean_rate)[:MAX_SOURCES]
    order = order[mean_rate[order] > 0]
    idx = met.index(emitter.lon[order], emitter.lat[order])
    F = met.slice_at(met.hours_since_t0(t0))
    hday = met.bilinear(F["hday"], idx)
    peat_ha = pm25_kg_per_ha("peat_drained")
    veg_ha = pm25_kg_per_ha("secondary_forest")
    constant = emitter.daily is None
    out = []
    for n, i in enumerate(order):
        series = R[:, i]
        on_idx = np.where(series > 0)[0]
        on = [round(float(on_idx[0] * step_hours), 2),
              round(float((on_idx[-1] + 1) * step_hours), 2)] if len(on_idx) else None
        rate = float(series[on_idx].mean()) if len(on_idx) else 0.0
        peat_frac = float(P[:, i].sum() / max(series.sum(), 1e-12))
        ff = 1.0 - peat_frac
        hmax = float(min(2500.0, hday[n] * 2.7))
        inj_top = 700.0 if ff < 0.5 else float(min(2000.0, hmax + 500.0))
        kg_window = float(series.sum() * step_hours * 3600.0)
        area = (kg_window * peat_frac / peat_ha + kg_window * ff / veg_ha)
        item = {
            "lat": round(float(emitter.lat[i]), 4), "lon": round(float(emitter.lon[i]), 4),
            "area_ha": round(float(area), 1), "area_estimated": True,
            "pool": "peat_drained" if ff < 0.5 else "secondary_forest",
            "pm25_kg": round(kg_window, 1),
            "emission_g_s": round(rate * 1000.0, 2),
            "emission_constant": constant,
            "on": on,
            "injection_m": [0, int(round(inj_top))],
            "flaming_fraction": round(ff, 3),
            "region": ISLANDS[int(emitter.island[i])],
            "province": (str(emitter.region_id[i]) if emitter.region_id is not None else None),
        }
        if not constant:
            item["emission_g_s_frames"] = [round(float(x) * 1000.0, 1) for x in series]
        out.append(item)
    return out


def _validation(t0, out_rec):
    """Per-run comparison with reported values, plus the offline season validation."""
    if t0 is None:
        return None
    year = t0.year
    rows = []
    try:
        from app.data.loader import load_seasons
        seasons = load_seasons()
        seasons = seasons if isinstance(seasons, list) else seasons.get("seasons", [])
        season = next((s for s in seasons if s.get("year") == year), None)
    except Exception:
        season = None
    by_name = {r["name"]: r for r in out_rec}
    if season:
        for name, obs in (season.get("pm25_peak_ugm3") or {}).items():
            r = by_name.get(name)
            if r and obs:
                rows.append({"receptor": name, "metric": "reported 24-h PM2.5 peak",
                             "modelled_peak_ugm3": r["peak_pm25"],
                             "modelled_metric": "highest frame of this run",
                             "reported_peak_ugm3": float(obs),
                             "ratio": round(r["peak_pm25"] / float(obs), 2)})
        v = (season.get("singapore_peaks") or {}).get("pm25_1h_max_ugm3")
        r = by_name.get("Singapore")
        if v and r:
            rows.append({"receptor": "Singapore", "metric": "NEA 1-h PM2.5 season maximum",
                         "modelled_peak_ugm3": r["peak_pm25"],
                         "modelled_metric": "highest frame of this run",
                         "reported_peak_ugm3": float(v),
                         "ratio": round(r["peak_pm25"] / float(v), 2)})
    season_val = None
    try:
        from app.data.loader import load_earth_json
        season_val = (load_earth_json("lpm_validation.json") or {}).get(str(year))
    except Exception:
        season_val = None
    if not rows and not season_val:
        return None
    return {
        "year": year, "rows": rows,
        "season": _season_summary(season_val),
        "note": ("Rows compare this run's highest frame with the season's reported peak, "
                 "which is a different averaging period and usually a different day: read "
                 "them as order-of-magnitude checks. 'season' is the offline Jul-Oct run "
                 "of the same model against the published targets in MODEL_SPEC 9."),
    }


def _season_summary(v):
    if not v:
        return None
    keys = ("target_singapore_jul_oct", "target_kuching_borneo_share",
            "target_sep_oct_primary_tg", "singapore_aug_oct_source_split",
            "kuala_lumpur_aug_oct_source_split", "lifetime_days",
            "singapore", "kuala_lumpur", "kuching")
    return {k: v[k] for k in keys if k in v}


@functools.lru_cache(maxsize=1)
def _srm():
    import numpy as np
    from app.data.loader import earth_path
    p = earth_path("srm.npz")
    if not p.is_file():
        return None
    with np.load(p, allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def _season_context(t0):
    """Offline LPM daily fire PM2.5 at the Malaysia/Singapore receptors for this date."""
    if t0 is None:
        return None
    z = _srm()
    if z is None or f"series_{t0.year}" not in z:
        return None
    ser = z[f"series_{t0.year}"]
    ids = [str(x) for x in z["receptor_ids"]]
    k = (t0.date() - date(t0.year, 7, 1)).days
    if not 0 <= k < ser.shape[0]:
        return None
    bt = z.get(f"series_bytag_{t0.year}")
    tag_reg = [str(x) for x in z.get("tag_region", [])]
    try:
        from .emissions import region_table
        reg_isl = {r["id"]: r["island"] for r in region_table()}
    except Exception:
        reg_isl = {}
    out = {}
    for rid in ("my-kuala-lumpur", "my-shah-alam", "my-klang", "my-petaling-jaya",
                "singapore", "sg-west", "sg-east", "kuching", "pekanbaru", "palangkaraya"):
        if rid not in ids:
            continue
        i = ids.index(rid)
        entry = {"daily_fire_pm25_primary": round(float(ser[k, i]), 1),
                 "jul_oct_mean_fire_pm25_primary": round(float(ser[:123, i].mean()), 1)}
        if bt is not None and tag_reg:
            v = bt[k, i]
            s = float(v.sum())
            if s > 0:
                sh = {}
                for tg, val in enumerate(v):
                    isl = reg_isl.get(tag_reg[tg], "other")
                    sh[isl] = sh.get(isl, 0.0) + float(val) / s
                entry["source_shares"] = {a: round(b, 3) for a, b in sh.items() if b > 0.0005}
        out[rid] = entry
    return {"date": t0.date().isoformat(), "receptors": out,
            "note": ("Daily-mean fire PM2.5 (primary, no background) from the offline "
                     "Jun-Nov LPM-3L run with observed ERA5 winds and GFED4.1s emissions.")}


def _empty(u, d, h, rain, note):
    return {
        "version": 2, "grid": None, "frames": [], "wind": None, "receptor_series": [],
        "sources": [], "validation": None, "season_context": None,
        "total_pm25_kg": 0.0, "live": False,
        "conditions": {"wind_speed_ms": u, "wind_dir_deg": float(d or 0.0),
                       "mean_mixing_height_m": h, "rain_mm": rain,
                       "transport_model": "LPM-3L (MODEL_SPEC 5.4)"},
        "scale": {"bands": list(CONTOUR_BANDS), "max_pm25": 0.0, "background_pm25": _bg()},
        "note": note, "caveats": list(CAVEATS),
    }


CAVEATS = [
    "Close to the fires this model runs high. For 2015 its Sep-Oct mean fire PM2.5 at "
    "Palangka Raya is several hundred ug/m3, while the one reported 24-h measurement "
    "peaked at nearly 400 (Santoso et al. 2022): expect values near burning peat to be "
    "two to five times too high, from coarse winds, a shallow night-time layer and "
    "cell-averaged emissions. The far-field numbers (Singapore, Kuala Lumpur, Kuching) "
    "are the ones checked against published targets.",

    "This is a model, not an observation. For whether to go outside, use your national "
    "agency's measured readings: NEA's PSI in Singapore, the DOE's API (IPU) in Malaysia.",

    "Where Singapore's 2015 haze came from depends on the method and the year's winds. "
    "Climatological and adjoint studies put Sumatra first (Reddington et al. 2014: "
    "southern and central Sumatra 77% of Singapore's 2004-09 fire PM2.5; Kim et al. "
    "2015: Sumatra 64% in Jul-Nov 2006; Lee et al. 2017: Sumatra+Java 41% annually). "
    "With the actual 2015 winds, Hansen et al. 2019 found Central Kalimantan "
    "co-dominant with South Sumatra at west Singapore (31% vs 38%) and dominant at east "
    "Singapore (41% vs 22%) in Aug-Oct 2015. Both islands matter, and this model "
    "includes both.",

    "Winds come from ERA5 coarsened to 1.5 degrees and 6-hourly (the open WeatherBench 2 "
    "copy); the near-surface wind is the 1000 hPa level because that copy has no 100 m "
    "wind. Sea breezes, terrain channelling and convective downdrafts smaller than "
    "about 150 km are not resolved. Outside the bundled fire seasons the winds are a "
    "monthly composite, not that day's weather.",

    "Emissions are GFED4.1s dry matter with this site's emission factors (peat 22.3 "
    "g/kg, Kiely 2019). Satellites miss much of the smouldering peat burning and cannot "
    "see through thick smoke, and GFED4.1s for 2017 onward is an active-fire-based beta. "
    "Peat PM2.5 emission factors span 17-28 g/kg between field studies.",

    "Removal is two first-order rates: dry deposition at 0.15 cm/s (unverified) and rain "
    "scavenging whose coefficient was calibrated so smoke lasts about 4.5 days on average "
    "(literature: 3-6). The real uncertainty in below-cloud scavenging is one to two "
    "orders of magnitude.",

    "The night-time mixing height comes from ERA5, which is poorly constrained at night "
    "(it misses observations by about 90 m in the Amazon). Night-time concentrations "
    "near the fires are uncertain by about a factor of two.",

    "Concentrations are computed from a few tens of thousands of particles and smoothed "
    "over about one grid cell, so small-scale structure and single-hour peaks are noisy. "
    "Kuala Lumpur, Shah Alam, Klang and Petaling Jaya lie within about 30 km of each "
    "other, and Singapore's five NEA regions within 14 km: at this resolution their "
    "modelled values differ only by sampling noise, not by resolved physics.",

    "Secondary organic aerosol is added with a fixed yield (Kiely 2019) and an "
    "unverified one-day formation time; with it off, far-field values fall by up to a "
    "third. There is no smoke-rain feedback and no convective venting.",

    "In release mode the run starts from clean air, and an arrival time is reported only "
    "when the modelled fire PM2.5 at a city rises at least 5 ug/m3 above background. "
    "Cities the smoke does not reach report no arrival.",
]
