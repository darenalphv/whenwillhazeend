"""
haze3d.py — a time-stepped, gridded PM2.5 field for the 3D haze simulator.

The snapshot model in :mod:`app.model.dispersion` answers "where is the smoke
right now". This answers "how does it breathe over a day", which is the part
people in Palangkaraya and Kuching actually experience.

What varies hour to hour
------------------------
**The mixing height, and that is the point.** Smouldering peat does not have a
strong diurnal emission cycle -- it burns underground at roughly constant rate,
which is exactly why these episodes persist. What swings by an order of
magnitude is the depth of atmosphere the smoke is stirred into:

  * after sunset the surface cools, a shallow stable nocturnal layer forms and
    the boundary layer collapses to a few hundred metres; the same emission is
    diluted into a fraction of the volume and surface concentrations spike;
  * after ~09:00 convection erodes it and the mixed layer grows to 1-2 km by
    mid-afternoon, and concentrations fall even though nothing about the fire
    has changed.

This is why regional haze is reliably worst before dawn and eases by afternoon,
and why a resident's experience of a "bad day" is mostly a boundary-layer
story. Concentration scales roughly as 1/H in the well-mixed limit, so the
model reproduces the cycle directly through the Gaussian plume's mixed-layer
branch rather than by applying a cosmetic multiplier.

Optionally the wind direction veers at a constant rate, which is how a plume
sweeps across receptors over a day.

Equations, stability classes, deposition and emission factors are all
:mod:`app.model.dispersion`'s -- this module reuses its kernel and its source
preparation rather than restating them, so the 2D and 3D views can never
disagree about what a fire emits.

Grid values are returned as integer micrograms per cubic metre, including the
regional background. Sub-microgram precision would be false precision given the
model's real uncertainty.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from .dispersion import (
    BACKGROUND_PM25_UGM3, CONTOUR_BANDS, MAX_RANGE_KM, MIN_WIND_MS,
    _concentration, _km_per_deg_lon, _pool_lookup, _to_local,
    aqi_category, clean_sources, localise_sources, pm25_to_aqi,
    RAIN_INTENSITY_MM_H, SCAVENGING_A, SCAVENGING_B,
)

__all__ = ["haze_field", "diurnal_mixing_height", "CAVEATS",
           "MAX_CELLS", "MAX_SOURCES"]

# A ceiling on the response: nx*ny*frames. 56x56 over 24 h is ~75k integers,
# which is a chunky but reasonable payload for an explicitly requested run.
MAX_CELLS = 400_000
MAX_SOURCES = 240          # largest-by-area kept; the tail contributes little
NIGHT_FRACTION = 0.28      # nocturnal layer, as a fraction of the daily mean
DAY_FACTION_PEAK = 1.85    # convective afternoon peak
MIXING_FLOOR_M = 120.0
MIXING_CEIL_M = 3000.0
GRID_PAD_KM = 90.0


def _profile(hour: float) -> float:
    """Mixing-layer depth factor over the day, relative to the daily mean.

    Daytime is convective growth; night is not a flat floor. After sunset the
    residual layer decays toward a shallow stable layer over a few hours, so
    21:00 is still deeper than 03:00 and consecutive night hours differ.
    """
    h = hour % 24.0
    if 6.0 <= h <= 19.0:
        # clamp before the fractional power: sin(pi) lands a hair below zero in
        # floating point, and a negative base to a fractional exponent is complex.
        conv = max(0.0, math.sin(math.pi * (h - 6.0) / 13.0)) ** 1.3
        return NIGHT_FRACTION + (DAY_FACTION_PEAK - NIGHT_FRACTION) * conv
    since_sunset = (h - 19.0) % 24.0
    return NIGHT_FRACTION * (1.0 + 0.60 * math.exp(-since_sunset / 2.0))


# Scale the profile so its 24-hour mean is exactly 1.0, which lets the caller's
# `mixing_height_m` keep meaning "daily mean mixing height" rather than silently
# becoming some other quantity.
_SHAPE_MEAN = sum(_profile(h / 4.0) for h in range(96)) / 96.0


def diurnal_mixing_height(mean_m: float, hour_of_day: float) -> float:
    """Mixing-layer depth at a given local hour, for a given daily mean.

    Reproduces the nocturnal collapse and daytime convective growth described
    in the module docstring. Normalised so the 24-hour mean returns ``mean_m``.
    """
    raw = _profile(hour_of_day)
    h = float(mean_m) * raw / _SHAPE_MEAN
    return float(min(MIXING_CEIL_M, max(MIXING_FLOOR_M, h)))


def _domain(clean, lat0, lon0, u, hours, rec_pts=None, dir_to_rad=None):
    """A lat/lon box covering the sources and everything plausibly downwind.

    Receptors that lie downwind within range are pulled into the box. Without
    this the grid could stop short of Kuching or Singapore and the map would
    imply the haze stops at the edge of the domain, which is the opposite of
    what these episodes do.
    """
    reach_km = min(MAX_RANGE_KM, max(120.0, u * hours * 3.6))
    xs = [s["xk"] for s in clean]
    ys = [s["yk"] for s in clean]
    half = reach_km + GRID_PAD_KM
    x_min, x_max = min(xs) - half, max(xs) + half
    y_min, y_max = min(ys) - half, max(ys) + half

    for r in rec_pts or []:
        if math.hypot(r["xk"], r["yk"]) > MAX_RANGE_KM + GRID_PAD_KM:
            continue
        if dir_to_rad is not None:
            downwind = (math.sin(dir_to_rad) * r["xk"]
                        + math.cos(dir_to_rad) * r["yk"])
            if downwind < -GRID_PAD_KM:      # upwind: nothing will reach it
                continue
        x_min = min(x_min, r["xk"] - GRID_PAD_KM)
        x_max = max(x_max, r["xk"] + GRID_PAD_KM)
        y_min = min(y_min, r["yk"] - GRID_PAD_KM)
        y_max = max(y_max, r["yk"] + GRID_PAD_KM)

    kx = _km_per_deg_lon(lat0)
    return {
        "lon_min": lon0 + x_min / kx, "lon_max": lon0 + x_max / kx,
        "lat_min": lat0 + y_min / 110.574, "lat_max": lat0 + y_max / 110.574,
    }


def _reach_mask(np, X, Y, srcs, dir_to_rad, u, t_s):
    """True where smoke from at least one source has had time to arrive.

    First-order plume onset: a parcel travels downwind at the mean wind speed,
    so a cell `d` metres downwind of a source sees nothing until `d/u` seconds
    after release. Crosswind spread is already in the Gaussian; this only gates
    *when* the front gets there. It is not a Lagrangian transport model and it
    does not meander, recirculate, or slow in the far field.
    """
    ex, ny_ = math.sin(dir_to_rad), math.cos(dir_to_rad)
    best = None
    for s in srcs:
        xd = ((X - s["xk"]) * ex + (Y - s["yk"]) * ny_) * 1000.0
        # Upwind of this source means its smoke never arrives -- not "arrives at
        # t=0". Using 0 here let a cell upwind of one fire unlock the full field
        # from every other fire, so distant cities lit up before anything had
        # travelled. inf keeps each source's gate independent.
        t_need = np.where(xd > 0.0, xd / u, np.inf)
        best = t_need if best is None else np.minimum(best, t_need)
    if best is None:
        return None
    return best <= t_s


def _arrival_hours(rec_pts, srcs, dir_to_rad, u):
    """Hours until the plume front first reaches each receptor."""
    ex, ny_ = math.sin(dir_to_rad), math.cos(dir_to_rad)
    out = {}
    for r in rec_pts:
        best = None
        for s in srcs:
            xd = ((r["xk"] - s["xk"]) * ex + (r["yk"] - s["yk"]) * ny_) * 1000.0
            if xd <= 0:
                continue
            t = xd / u / 3600.0
            best = t if best is None else min(best, t)
        out[r["name"]] = round(best, 1) if best is not None else None
    return out



def _active_on(raw_sources, clean, start_date, hours, start_hour):
    """Subset of `clean` whose burn window overlaps the simulated period.

    Returns None if the clusters carry no usable dates, in which case the
    caller keeps all of them and says so, rather than silently returning an
    empty field.
    """
    try:
        t0 = datetime.fromisoformat(str(start_date)[:10]).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    t0 = t0 + timedelta(hours=float(start_hour or 0.0))
    t1 = t0 + timedelta(hours=float(hours))

    by_key = {}
    for r in raw_sources or []:
        try:
            by_key[(round(float(r["lat"]), 6), round(float(r["lon"]), 6))] = r.get("date")
        except (KeyError, TypeError, ValueError):
            continue

    kept, dated = [], 0
    for s in clean:
        d = by_key.get((round(s["lat"], 6), round(s["lon"], 6)))
        if not d:
            continue
        try:
            ds = datetime.fromisoformat(str(d)[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        dated += 1
        de = ds + timedelta(days=float(s.get("episode_days") or 0.0))
        if ds <= t1 and de >= t0:
            kept.append(s)
    if not dated:
        return None
    return kept



def _validation(start_date, out_rec):
    """Compare modelled receptor peaks against reported peaks for that year.

    The simulator publishes concentrations, so it should publish how far off
    they are. Returns None when the year has no reported values to check
    against -- an unchecked run says so rather than implying it passed.
    """
    if not start_date:
        return None
    try:
        year = int(str(start_date)[:4])
    except (TypeError, ValueError):
        return None
    try:
        from app.data.loader import load_seasons
        season = next((s for s in load_seasons() if s.get("year") == year), None)
    except Exception:
        season = None
    if not season:
        return None
    reported = season.get("pm25_peak_ugm3") or {}
    if not reported:
        return None
    by_name = {r["name"]: r for r in out_rec}
    rows = []
    for name, obs in reported.items():
        r = by_name.get(name)
        if not r or not obs:
            continue
        rows.append({
            "receptor": name,
            "modelled_peak_ugm3": r["peak_pm25"],
            "reported_peak_ugm3": float(obs),
            "ratio": round(r["peak_pm25"] / float(obs), 2) if obs else None,
        })
    if not rows:
        return None
    return {
        "year": year,
        "rows": rows,
        "source": season.get("source"),
        "note": ("Modelled peaks are hourly surface values from a Gaussian plume; "
                 "reported peaks are the figures in the bundled dataset, which "
                 "are not necessarily hourly maxima and are measured at a single "
                 "station. Expect the model to run high near the fires."),
    }


def haze_field(sources: list[dict], wind_speed_ms: float, wind_dir_deg: float,
               hours: int = 24, step_hours: float = 1.0,
               mixing_height_m: float = 800.0, rain_mm: float = 0.0,
               grid_n: int = 56, wind_veer_deg_per_h: float = 0.0,
               start_hour: float = 0.0, start_date: str | None = None,
               diurnal: bool = True, filter_active: bool = True,
               release_from_start: bool = False) -> dict:
    """Gridded PM2.5 over time for the 3D simulator.

    ``sources`` matches :func:`app.model.dispersion.haze_dispersion`.
    ``wind_dir_deg`` is the direction the wind blows *from*.
    ``mixing_height_m`` is the *daily mean* depth; the diurnal cycle is applied
    around it unless ``diurnal`` is False.

    Raises ``ValueError`` if the requested grid would exceed ``MAX_CELLS``.
    """
    import numpy as np

    grid_n = int(max(8, min(128, grid_n)))
    step_hours = float(max(0.25, min(6.0, step_hours)))
    hours = int(max(1, min(168, hours)))
    n_frames = max(1, int(round(hours / step_hours)))
    if grid_n * grid_n * n_frames > MAX_CELLS:
        raise ValueError(
            f"requested field is {grid_n}x{grid_n}x{n_frames} = "
            f"{grid_n * grid_n * n_frames} cells, over the {MAX_CELLS} ceiling; "
            f"reduce grid_n, hours, or increase step_hours"
        )

    u = float(max(MIN_WIND_MS, float(wind_speed_ms or 0.0)))
    mean_h = float(max(100.0, min(float(mixing_height_m or 800.0), 4000.0)))
    rain_mm = float(max(0.0, rain_mm or 0.0))
    stability = "E" if u < 2.5 else "D"
    f_rain = min(1.0, rain_mm / (RAIN_INTENSITY_MM_H * 24.0)) if rain_mm > 0 else 0.0
    lam = (SCAVENGING_A * (RAIN_INTENSITY_MM_H ** SCAVENGING_B) * f_rain
           if f_rain > 0 else 0.0)

    pool_lut = _pool_lookup()
    clean = clean_sources(sources, pool_lut)

    # A whole season's clusters are not all alight at once. Feeding a year's
    # worth into one run asserts they are, and inflates the peak by roughly an
    # order of magnitude. If the caller gave a date and the clusters carry
    # their own, keep only the ones actually burning during the window.
    inactive = 0
    if start_date and filter_active:
        kept = _active_on(sources, clean, start_date, hours, start_hour)
        if kept is not None:
            inactive = len(clean) - len(kept)
            clean = kept

    dropped = 0
    if len(clean) > MAX_SOURCES:
        clean.sort(key=lambda s: s["pm25_kg"], reverse=True)
        kept_kg = sum(s["pm25_kg"] for s in clean[:MAX_SOURCES])
        all_kg = sum(s["pm25_kg"] for s in clean)
        dropped = len(clean) - MAX_SOURCES
        retained = kept_kg / all_kg if all_kg else 1.0
        clean = clean[:MAX_SOURCES]
    else:
        retained = 1.0

    try:
        from app.data.loader import load_receptors
        receptors = load_receptors() or []
    except Exception:
        receptors = []

    if not clean:
        return {
            "grid": None, "frames": [], "receptor_series": [], "sources": [],
            "total_pm25_kg": 0.0, "live": False,
            "conditions": {"wind_speed_ms": u, "wind_dir_deg": float(wind_dir_deg or 0.0),
                           "mean_mixing_height_m": mean_h, "rain_mm": rain_mm,
                           "stability_class": stability, "diurnal": bool(diurnal)},
            "scale": {"bands": list(CONTOUR_BANDS), "max_pm25": 0.0,
                      "background_pm25": BACKGROUND_PM25_UGM3},
            "note": "No usable fire sources were supplied, so there is nothing to disperse.",
            "caveats": list(CAVEATS),
        }

    lat0, lon0, total_kg = localise_sources(clean)

    rec_pts = []
    for r in receptors:
        try:
            rlat, rlon = float(r["lat"]), float(r["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        xk, yk = _to_local(rlat, rlon, lat0, lon0)
        rec_pts.append({"name": r.get("name") or r.get("city") or "receptor",
                        "lat": rlat, "lon": rlon,
                        "country": r.get("country"), "xk": xk, "yk": yk})

    dir0_rad = math.radians(((float(wind_dir_deg or 0.0)) + 180.0) % 360.0)
    box = _domain(clean, lat0, lon0, u, hours, rec_pts, dir0_rad)

    lons = np.linspace(box["lon_min"], box["lon_max"], grid_n)
    lats = np.linspace(box["lat_min"], box["lat_max"], grid_n)
    LON, LAT = np.meshgrid(lons, lats)          # row-major, rows south -> north
    kx = _km_per_deg_lon(lat0)
    X = (LON - lon0) * kx
    Y = (LAT - lat0) * 110.574

    rec_x = np.array([r["xk"] for r in rec_pts]) if rec_pts else np.array([])
    rec_y = np.array([r["yk"] for r in rec_pts]) if rec_pts else np.array([])
    rec_series = [[] for _ in rec_pts]

    base_dt = None
    if start_date:
        try:
            base_dt = datetime.fromisoformat(str(start_date)[:10]).replace(tzinfo=timezone.utc)
        except ValueError:
            base_dt = None

    frames = []
    field_max = 0.0
    for k in range(n_frames):
        t_h = k * step_hours
        hod = (float(start_hour) + t_h) % 24.0
        h_mix = diurnal_mixing_height(mean_h, hod) if diurnal else mean_h
        dir_from = (float(wind_dir_deg or 0.0) + wind_veer_deg_per_h * t_h) % 360.0
        dir_to_rad = math.radians((dir_from + 180.0) % 360.0)

        Z = _concentration(np, X, Y, clean, u, dir_to_rad, h_mix, stability, lam)
        if release_from_start:
            mask = _reach_mask(np, X, Y, clean, dir_to_rad, u, t_h * 3600.0)
            if mask is not None:
                Z = np.where(mask, Z, 0.0)
        Z = Z + BACKGROUND_PM25_UGM3
        field_max = max(field_max, float(Z.max()))

        if rec_pts:
            rc = _concentration(np, rec_x, rec_y, clean, u, dir_to_rad,
                                h_mix, stability, lam)
            if release_from_start:
                rm = _reach_mask(np, rec_x, rec_y, clean, dir_to_rad, u, t_h * 3600.0)
                if rm is not None:
                    rc = np.where(rm, rc, 0.0)
            rc = rc + BACKGROUND_PM25_UGM3
            for i, v in enumerate(rc):
                rec_series[i].append(round(float(v), 1))

        label = f"{int(hod):02d}:{int(round((hod % 1) * 60)):02d}"
        iso = None
        if base_dt is not None:
            iso = (base_dt + timedelta(hours=float(start_hour) + t_h)).isoformat()

        frames.append({
            "index": k,
            "hour": round(t_h, 2),
            "hour_of_day": round(hod, 2),
            "label": label,
            "time": iso,
            "mixing_height_m": round(h_mix),
            "wind_dir_deg": round(dir_from, 1),
            "wind_speed_ms": round(u, 2),
            "max_pm25": round(float(Z.max()), 1),
            "mean_pm25": round(float(Z.mean()), 1),
            # row-major from the SOUTH edge; ints, because tenths here would be
            # precision this model does not have
            "values": np.rint(np.clip(Z, 0, 9999)).astype(int).ravel().tolist(),
        })

    arrivals = _arrival_hours(rec_pts, clean, dir0_rad, u)
    out_rec = []
    for i, r in enumerate(rec_pts):
        series = rec_series[i]
        peak = max(series) if series else 0.0
        pk = series.index(peak) if series else 0
        out_rec.append({
            "name": r["name"], "lat": r["lat"], "lon": r["lon"],
            "country": r["country"],
            "pm25": series,
            "peak_pm25": peak,
            "peak_frame": pk,
            "peak_hour_of_day": frames[pk]["hour_of_day"] if frames else None,
            "peak_aqi": pm25_to_aqi(peak),
            "peak_category": aqi_category(peak),
            "distance_km": round(math.hypot(r["xk"], r["yk"]), 1),
            "arrival_hours": arrivals.get(r["name"]),
            "downwind": arrivals.get(r["name"]) is not None,
        })

    note = None
    if dropped:
        note = (f"{dropped} of the smallest clusters were dropped to keep the run "
                f"tractable; the {MAX_SOURCES} kept carry "
                f"{retained * 100:.1f}% of total PM2.5 emissions.")

    return {
        "grid": {
            **{k: round(v, 5) for k, v in box.items()},
            "nx": grid_n, "ny": grid_n,
            "dlon": round((box["lon_max"] - box["lon_min"]) / (grid_n - 1), 6),
            "dlat": round((box["lat_max"] - box["lat_min"]) / (grid_n - 1), 6),
            "order": "row-major, rows south to north, columns west to east",
        },
        "frames": frames,
        "receptor_series": out_rec,
        "sources": [{"lat": s["lat"], "lon": s["lon"], "area_ha": s["area_ha"],
                     "pool": s["pool"], "pm25_kg": round(s["pm25_kg"], 1)}
                    for s in clean],
        "validation": _validation(start_date, out_rec),
        "total_pm25_kg": round(total_kg, 1),
        "sources_used": len(clean),
        "sources_dropped": dropped,
        "sources_inactive_on_date": inactive,
        "emission_fraction_retained": round(retained, 4),
        "conditions": {
            "wind_speed_ms": round(u, 2),
            "wind_dir_deg": float(wind_dir_deg or 0.0),
            "wind_veer_deg_per_h": float(wind_veer_deg_per_h or 0.0),
            "mean_mixing_height_m": round(mean_h),
            "night_mixing_height_m": round(diurnal_mixing_height(mean_h, 3.0)),
            "day_mixing_height_m": round(diurnal_mixing_height(mean_h, 14.0)),
            "rain_mm": rain_mm,
            "stability_class": stability,
            "diurnal": bool(diurnal),
            "release_from_start": bool(release_from_start),
            "hours": hours,
            "step_hours": step_hours,
        },
        "scale": {
            "bands": list(CONTOUR_BANDS),
            "max_pm25": round(field_max, 1),
            "background_pm25": BACKGROUND_PM25_UGM3,
        },
        "live": False,
        "note": note,
        "caveats": list(CAVEATS),
    }


CAVEATS = [
    "Near the fires this model runs high, and knowingly so. A Gaussian plume "
    "superposing dozens of clusters under a shallow nocturnal mixing layer can "
    "produce surface concentrations well above anything a monitoring station "
    "has recorded -- for 2015 it exceeds the reported Palangkaraya peak by "
    "roughly an order of magnitude. Read the colours as relative intensity and "
    "direction, not as a concentration you could measure. Every run reports its "
    "own ratio against reported peaks where the year has them.",

    "This is a dispersion model, not an observation. It shows where a given set "
    "of fires would put smoke under one assumed wind, not where smoke actually "
    "was on any particular day. Do not use it to decide whether to go outside; "
    "use your national agency's measured readings.",

    "Singapore and Peninsular Malaysia will usually show little or nothing here, "
    "and that is mostly about the inputs rather than the physics. This model is "
    "fed Kalimantan fires only. The smoke that closed schools in Singapore and "
    "Kuala Lumpur in 2015 came predominantly from Sumatra -- Riau and Jambi are "
    "several hundred kilometres closer and directly upwind on the southwest "
    "monsoon. Borneo smoke reliably reaches Sarawak, and Kuching and Sibu show "
    "that clearly; it reaches the peninsula far more weakly. A Gaussian plume "
    "also dilutes faster at that range than real smoke layers do, so treat a low "
    "Singapore number as 'this model cannot tell you', not as 'the air is fine'.",

    "Release mode starts the plume from nothing at hour zero and lets the front "
    "travel downwind at the mean wind speed. It is a first-order picture of how "
    "far smoke has got, not a transport model: the front does not meander, "
    "stall, recirculate or thin as it goes. Real smoke reaching Singapore has "
    "spent days in a changing wind field. Use it to see roughly when and where, "
    "not how much.",

    "The wind is a single vector applied to the whole domain for the whole run. "
    "Real haze episodes are steered by sea breezes, terrain and a wind that "
    "changes with height and with distance from the coast. A plume that here "
    "runs straight for 900 km would in reality meander, split and recirculate.",

    "The diurnal cycle modelled here is the boundary layer, not the fire. "
    "Smouldering peat emits at roughly constant rate; the day-night swing in "
    "surface concentration comes almost entirely from the atmosphere being "
    "stirred into a few hundred metres at night and one to two kilometres by "
    "afternoon. Flaming fronts do have their own daily cycle, and that is not "
    "modelled.",

    "Gaussian plume dispersion was fitted for tens of kilometres, not hundreds. "
    "Beyond roughly 100 km the results should be read as an indication of "
    "direction and relative magnitude, not as a concentration forecast.",

    "Emissions assume every hectare of a cluster burns over the cluster's stated "
    "episode duration at a constant rate, using the fuel load and PM2.5 emission "
    "factor for its pool. Peat emission factors vary several-fold with moisture "
    "and burn depth.",

    "Fire cluster geometries in the bundled historical years are synthesised "
    "from published aggregate statistics. Their totals reconcile to reported "
    "burned area, but an individual cluster's position is modelled, not observed.",
]
