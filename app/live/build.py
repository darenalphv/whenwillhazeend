"""Assemble the three ``/api/now/*`` payloads (CONTRACT_V2 amendment M5).

Labelling rules enforced here:

* every reading carries its index, averaging period and ``measured`` flag;
* NEA values are only ever ``PSI`` (24-h) or a PM2.5 concentration (1-h);
  CAMS values are only ever ``US AQI (modelled, CAMS)`` or modelled PM2.5;
  nothing is ever labelled ``API`` because no Malaysian official feed is used;
* the headline is computed from the data in the window: an official measured
  exceedance first; a modelled one only from places with no official reading,
  with "modelled" next to the number; otherwise ``null``.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

from app.live import bands, cams, firms, nea, wind
from app.live.common import (
    MYT, TIMEZONE_LABEL, TIMEZONE_NOTE, SourceUnavailable, describe_error, human_time, iso,
    log, now_myt,
)
from app.live.places import APIMS_PORTAL, MALAYSIA_OFFICIAL_FINDING, PLACES, Place, display_name

MAX_DAYS = 7
FIELD_STEPS = (3, 6, 12, 24)


def _window(days: int) -> tuple[datetime, datetime]:
    end = now_myt()
    return end - timedelta(days=days), end


def _series(points: list[tuple[datetime, Any]]) -> list[dict]:
    return [{"t": iso(t), "v": v} for t, v in points]


def _max(series: list[dict]) -> dict | None:
    best = None
    for p in series:  # first occurrence wins a tie: the earliest time it was reached
        if p["v"] is not None and (best is None or p["v"] > best["v"]):
            best = p
    return dict(best) if best else None


def _epoch_iso(ts: float | None) -> str | None:
    return iso(datetime.fromtimestamp(ts, MYT)) if ts else None


# --------------------------------------------------------------------------- #
# /api/now/observed
# --------------------------------------------------------------------------- #

def _nea_attribution(fetched_at: float | None) -> str:
    when = datetime.fromtimestamp(fetched_at, MYT).date().isoformat() if fetched_at else "the time shown"
    return (f"Contains information from the PSI and PM2.5 readings of the National Environment "
            f"Agency accessed on {when} from data.gov.sg, which is made available under the terms "
            f"of the Singapore Open Data Licence version 1.0 {nea.LICENCE_URL}")


def _official_block(place: Place, nea_data: dict) -> tuple[dict | None, dict | None]:
    psi = _series(nea_data["series"]["psi"].get(place.nea_region, []))
    pm = _series(nea_data["series"]["pm25"].get(place.nea_region, []))
    official = None
    if psi:
        mx = _max(psi)
        official = {
            "index": bands.PSI,
            "averaging": "24h",
            "measured": True,
            "series": psi,
            "max": mx,
            "band": bands.band(bands.PSI, mx["v"]) if mx else None,
            "source": nea.SOURCE,
            "licence": nea.LICENCE,
            "licence_url": nea.LICENCE_URL,
            "url": nea.API_URL["psi"],
        }
    pm25 = None
    if pm:
        pm25 = {
            "quantity": "PM2.5 concentration (not an index)",
            "units": "µg/m³",
            "averaging": "1h",
            "measured": True,
            "series": pm,
            "max": _max(pm),
            "source": nea.SOURCE,
            "url": nea.API_URL["pm25"],
        }
    return official, pm25


def _modelled_blocks(cams_data: dict, start: datetime, end: datetime) -> dict[str, dict]:
    times = [datetime.fromisoformat(t) for t in cams_data["times"]]
    keep = [i for i, t in enumerate(times) if start <= t <= end]
    out: dict[str, dict] = {}
    for pid, pt in zip(cams_data["place_ids"], cams_data["points"]):
        aqi = [{"t": iso(times[i]), "v": pt["us_aqi"][i]} for i in keep]
        pm = [{"t": iso(times[i]), "v": pt["pm25"][i]} for i in keep]
        mx = _max(aqi)
        out[pid] = {
            "index": bands.US_AQI,
            "averaging": "1h steps; PM sub-indices use a rolling 24-h mean (Open-Meteo's US AQI)",
            "measured": False,
            "series": aqi,
            "pm25_series": pm,
            "pm25_units": "µg/m³ (modelled)",
            "max": mx,
            "band": bands.band(bands.US_AQI, mx["v"]) if mx else None,
            "source": cams.SOURCE,
            "licence": cams.LICENCE,
            "grid_km": cams.GRID_KM,
            "model_point": {"lat": pt["cell_lat"], "lon": pt["cell_lon"]},
            "same_model_values_as": [],
        }
    # Places whose modelled series are identical are one estimate, not several.
    groups: dict[tuple, list[str]] = {}
    for pid, blk in out.items():
        sig = (tuple(p["v"] for p in blk["series"]), tuple(p["v"] for p in blk["pm25_series"]))
        groups.setdefault(sig, []).append(pid)
    for ids in groups.values():
        for pid in ids:
            out[pid]["same_model_values_as"] = [o for o in ids if o != pid]
    return out


def compute_headline(places: list[dict]) -> dict | None:
    """Official measured exceedance first; modelled only where there is no official
    reading; ``None`` if nothing reached the headline floor (Unhealthy or worse)."""
    by_id = {p.id: p for p in PLACES}

    def best(cands):
        cands = [c for c in cands if c[2] is not None and bands.headline_worthy(c[1], c[2]["v"])]
        if not cands:
            return None
        return max(cands, key=lambda c: (c[2]["v"], -datetime.fromisoformat(c[2]["t"]).timestamp()))

    measured = best([(p, p["official"]["index"], p["official"]["max"])
                     for p in places if p.get("official")])
    if measured:
        p, index, mx = measured
        band = bands.band(index, mx["v"])
        who = display_name(by_id[p["id"]])
        avg = "24-hour " if p["official"]["averaging"] == "24h" else ""
        agency = "NEA" if index == bands.PSI else "DOE"
        text = (f"{who} recorded a {avg}{index} of {mx['v']} — {band} — at "
                f"{human_time(datetime.fromisoformat(mx['t']))}. Official {agency} reading.")
        return {"text": text, "place_id": p["id"], "index": index, "measured": True,
                "averaging": p["official"]["averaging"], "value": mx["v"], "t": mx["t"],
                "band": band, "source": p["official"]["source"]}

    modelled = best([(p, bands.US_AQI, p["modelled"]["max"])
                     for p in places if not p.get("official") and p.get("modelled")])
    if modelled:
        p, index, mx = modelled
        band = bands.band(index, mx["v"])
        who = display_name(by_id[p["id"]])
        same = [display_name(by_id[o]) for o in p["modelled"]["same_model_values_as"]]
        text = (f"{who} reached a modelled US AQI of {mx['v']} — {band} — at "
                f"{human_time(datetime.fromisoformat(mx['t']))}. This is a CAMS model estimate, "
                "not a measurement")
        if same:
            names = ", ".join([who] + same[:-1]) + f" and {same[-1]}"
            text += f"; the model gives {names} identical values"
        text += "."
        return {"text": text, "place_id": p["id"], "index": index, "measured": False,
                "averaging": "1h (PM sub-index on a rolling 24-h mean)", "value": mx["v"],
                "t": mx["t"], "band": band, "source": cams.SOURCE}
    return None


def build_observed(days: int = 7) -> dict:
    start, end = _window(days)
    caveats = [TIMEZONE_NOTE,
               "PSI (Singapore, NEA), API (Malaysia, DOE) and US AQI are different indices. "
               "Their numbers are not comparable, even where band names match."]
    sources: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_nea = ex.submit(nea.get_window, start, end)
        f_cams = ex.submit(cams.get_points)
        nea_data = cams_res = None
        try:
            nea_data = f_nea.result()
            sources["nea"] = {"available": True, "measured": True,
                              "fetched_at": _epoch_iso(nea_data["fetched_at"]),
                              "missing_dates": nea_data["missing_dates"],
                              "stale_dates": nea_data["stale_dates"]}
            if any(nea_data["missing_dates"].values()):
                miss = sorted(set(nea_data["missing_dates"]["psi"] + nea_data["missing_dates"]["pm25"]))
                caveats.append("Singapore NEA readings for " + ", ".join(miss) + " are not loaded yet "
                               "(data.gov.sg allows 6 requests per 10 s); they fill in within a minute.")
            if any(nea_data["stale_dates"].values()):
                caveats.append("Some Singapore readings are from an earlier fetch because the latest "
                               "refresh failed; see sources.nea.stale_dates.")
        except SourceUnavailable as exc:
            sources["nea"] = {"available": False, "measured": True, "error": str(exc)}
            caveats.append("Singapore NEA readings are unavailable right now; no official readings "
                           "are shown.")
        except Exception as exc:  # pragma: no cover - defensive: never blank the others
            log.exception("NEA failed unexpectedly")
            sources["nea"] = {"available": False, "measured": True, "error": describe_error(exc)}
        try:
            cams_res = f_cams.result()
            sources["cams"] = {"available": True, "measured": False,
                               "fetched_at": _epoch_iso(cams_res.fetched_at),
                               "stale": cams_res.stale, "keyed": cams.using_key()}
            if cams_res.stale:
                caveats.append("The CAMS model values are from an earlier fetch; the latest refresh "
                               "failed.")
        except SourceUnavailable as exc:
            sources["cams"] = {"available": False, "measured": False, "error": str(exc)}
            caveats.append("The CAMS model is unavailable right now; no modelled values are shown.")
        except Exception as exc:  # pragma: no cover
            log.exception("CAMS points failed unexpectedly")
            sources["cams"] = {"available": False, "measured": False, "error": describe_error(exc)}
    sources["doe_malaysia"] = {"available": False, "measured": True,
                               "reason": MALAYSIA_OFFICIAL_FINDING, "portal": APIMS_PORTAL}

    modelled = _modelled_blocks(cams_res.value, start, end) if cams_res else {}

    places = []
    for pl in PLACES:
        official = pm25 = None
        if pl.country == "SG" and nea_data:
            official, pm25 = _official_block(pl, nea_data)
        entry = {
            "id": pl.id, "name": pl.name, "area": pl.area, "country": pl.country,
            "lat": pl.lat, "lon": pl.lon,
            "official": official, "pm25": pm25, "modelled": modelled.get(pl.id),
        }
        if pl.country == "MY":
            entry["official_portal"] = APIMS_PORTAL
        places.append(entry)

    caveats.append(MALAYSIA_OFFICIAL_FINDING)
    if modelled:
        caveats.append(
            f"Modelled values come from the CAMS global forecast (~{cams.GRID_KM} km grid). "
            "Past days are archived forecasts, not measurements. US AQI is derived by Open-Meteo "
            "from modelled concentrations (PM on a rolling 24-h mean).")
        seen = set()
        for p in places:
            m = p["modelled"]
            if m and m["same_model_values_as"] and p["id"] not in seen:
                group = [p["id"]] + m["same_model_values_as"]
                seen.update(group)
                by_id = {pl.id: pl for pl in PLACES}
                names = [display_name(by_id[g]) for g in group]
                caveats.append(f"The model returns identical values for {', '.join(names[:-1])} and "
                               f"{names[-1]}: one model estimate, not {len(group)} separate readings.")

    attribution = [_nea_attribution(nea_data["fetched_at"] if nea_data else None), cams.attribution()]

    return {
        "as_of": iso(end),
        "window": {"start": iso(start), "end": iso(end), "days": days},
        "timezone": TIMEZONE_LABEL,
        "places": places,
        "headline": compute_headline(places),
        "sources": sources,
        "official_portals": {"MY": APIMS_PORTAL},
        "attribution": attribution,
        "caveats": caveats,
    }


# --------------------------------------------------------------------------- #
# /api/now/hotspots
# --------------------------------------------------------------------------- #

def _http_date(text: str | None) -> str | None:
    if not text:
        return None
    try:
        return iso(parsedate_to_datetime(text))
    except (TypeError, ValueError):
        return None


def build_hotspots(days: int = 7) -> dict:
    """Raises SourceUnavailable when neither satellite's file can be had."""
    start, end = _window(days)
    res, sensor, errors = firms.get_detections()
    agg = firms.aggregate(res.value, start, end)
    label = firms.SENSORS[sensor]["label"]
    caveats = [
        TIMEZONE_NOTE + " Days in by_day are local (UTC+08:00) calendar days; the first and last "
        "are partial.",
        f"Satellite detections from one sensor ({label}), nominal and high confidence only. "
        "VIIRS passes over twice a day (~1:30 am and ~1:30 pm local solar time), so a count is "
        "what the satellite saw, not every fire. Cloud and thick smoke hide fires.",
        "FRP totals add the fire radiative power of every detection in the window. They measure "
        "how much burning was seen, not an instantaneous total.",
        "Regions are coarse polygons; detections within ~20 km of the Kalimantan-Sarawak/Sabah "
        "border may be assigned to the wrong side.",
        f"Clusters are {firms.BIN_DEG} degree bins (~{int(firms.BIN_DEG * 111)} km), placed at the "
        "mean position of their detections. Individual detections are not published here.",
    ]
    gaps = [f"{c['date']} ({'night' if c['night_pass'] is False else ''}"
            f"{' and ' if c['night_pass'] is False and c['day_pass'] is False else ''}"
            f"{'daytime' if c['day_pass'] is False else ''} pass)"
            for c in agg["coverage"] if c["night_pass"] is False or c["day_pass"] is False]
    if gaps:
        caveats.append(f"No {label} detections at all in: {'; '.join(gaps)}. That is a gap in "
                       "the satellite data, not a day without fires - read those days' counts as "
                       "incomplete.")
    if sensor != firms.ORDER[0]:
        caveats.append(f"The Suomi NPP file was unavailable ({'; '.join(errors)}); using {label}.")
    if res.stale:
        caveats.append("This is an earlier copy of the FIRMS file; the latest refresh failed.")
    return {
        "as_of": iso(end),
        "window": {"start": iso(start), "end": iso(end), "days": days},
        "timezone": TIMEZONE_LABEL,
        "source": firms.SOURCE_TMPL.format(sensor=label),
        "sensor": sensor,
        "measured": True,
        "kind": "satellite detection",
        "attribution": firms.ATTRIBUTION,
        "disclaimer_url": firms.DISCLAIMER_URL,
        "data_updated": _http_date(res.meta.get("last_modified")),
        "fetched_at": _epoch_iso(res.fetched_at),
        "stale": res.stale,
        "confidence_included": ["nominal", "high"],
        "regions": dict(firms.REGION_LABELS),
        "count_by_region": agg["count_by_region"],
        "frp_gw_by_region": agg["frp_gw_by_region"],
        "total": agg["total"],
        "first_detection": agg["first_detection"],
        "last_detection": agg["last_detection"],
        "by_day": agg["by_day"],
        "coverage": agg["coverage"],
        "bin_deg": firms.BIN_DEG,
        "clusters": agg["clusters"],
        "caveats": caveats,
    }


# --------------------------------------------------------------------------- #
# /api/now/field
# --------------------------------------------------------------------------- #

def build_field(days: int = 7, step_hours: int = 3) -> dict:
    """Raises SourceUnavailable when the CAMS field cannot be had. Wind failing
    leaves ``wind: null``; it never blanks the PM2.5 field."""
    start, end = _window(days)
    spec, res = cams.get_field()
    frames = cams.field_frames(res.value, start, end, step_hours)
    caveats = [
        TIMEZONE_NOTE,
        f"Modelled surface PM2.5 from the CAMS global forecast (~{cams.GRID_KM} km native grid), "
        "not measurements. Past days are archived forecasts.",
        f"Sampled every {spec['dlon']} degrees" + (
            " (CAMS's native resolution)." if abs(spec["dlon"] - cams.NATIVE_DEG) < 1e-9 else
            f", coarser than CAMS's native {cams.NATIVE_DEG} degrees, to stay inside the free "
            "Open-Meteo call budget."),
        "A straight edge in the field is the edge of the grid, not of the smoke.",
    ]
    if res.stale:
        caveats.append("This is an earlier copy of the model field; the latest refresh failed.")

    wind_out = None
    try:
        wspec, wres = wind.get_wind()
        epochs = [int(datetime.fromisoformat(f["t"]).timestamp()) for f in frames]
        wind_out = {
            "nx": wspec["nx"], "ny": wspec["ny"],
            "lon_min": wspec["lon_min"], "lon_max": wspec["lon_max"],
            "lat_min": wspec["lat_min"], "lat_max": wspec["lat_max"],
            "dlon": wspec["dlon"], "dlat": wspec["dlat"],
            "levels_m": list(wind.LEVELS_M),
            "level_note": wind.LEVEL_NOTE,
            "order": ("u/v = level-major, then row-major (rows south to north, columns west to "
                      "east); index = level * nx * ny + row * nx + col"),
            "units": "m/s (u east-positive, v north-positive)",
            "measured": False,
            "source": wind.SOURCE,
            "licence": wind.LICENCE,
            "fetched_at": _epoch_iso(wres.fetched_at),
            "stale": wres.stale,
            "frames": wind.frames_for(wres.value, epochs),
        }
    except SourceUnavailable as exc:
        caveats.append(f"Model wind is unavailable right now ({exc}); smoke drift cannot be shown.")
    except Exception as exc:  # pragma: no cover - defensive
        log.exception("wind failed unexpectedly")
        caveats.append(f"Model wind is unavailable right now ({describe_error(exc)}).")

    return {
        "as_of": iso(end),
        "window": {"start": iso(start), "end": iso(end), "days": days},
        "timezone": TIMEZONE_LABEL,
        "grid": spec,
        "step_hours": step_hours,
        "frames": frames,
        "units": "µg/m³",
        "quantity": "PM2.5 surface concentration (modelled)",
        "measured": False,
        "model_grid_km": cams.GRID_KM,
        "source": cams.SOURCE,
        "licence": cams.LICENCE,
        "attribution": cams.attribution(),
        "fetched_at": _epoch_iso(res.fetched_at),
        "stale": res.stale,
        "wind": wind_out,
        "caveats": caveats,
    }
