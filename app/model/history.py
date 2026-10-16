"""Historical season statistics: aggregates, per-year rows and year comparisons.

This module does no physical modelling.  It reduces the bundled season record
from `app.data.loader.load_seasons()` into the shapes the charts and the
comparison view need, and it computes a small number of derived quantities:

* **ENSO composites** -- mean burned area, duration, peak PM2.5 and mortality
  estimate by ENSO phase.  These are the empirical version of the ENSO
  multiplier that `app.model.stock` uses.
* **Season window in day-of-year space** for the Gantt-style timeline: start,
  peak and end expressed as days from 1 January so that seasons of different
  years can be drawn on one calendar axis.
* **Ordinary least squares trend** in burned area and duration over the record,
  fitted with numpy on years with data, reported with its slope, its standard
  error and the number of points behind it.  With seventeen-odd seasons, five
  of which are dominated by two extreme El Ninos, none of these trends is
  statistically strong, and the returned ``significant`` flag says so honestly
  (|slope| > 2 standard errors).

Nothing here is a measurement this project made; every number is passed through
from the bundled dataset with its ``source`` string intact.  Where a season
carries ``"estimated": true``, the aggregates it feeds are flagged too.

Sources
-------
Provenance for individual season values lives in `app/data/README.md` and in
each season object's ``source`` field.  Aggregates commonly cited alongside
these numbers include Field et al. (2016) *PNAS*, Huijnen et al. (2016)
*Sci. Rep.* 6, 26886, Koplitz et al. (2016) *ERL* 11, 094023 and World Bank
(2016) *The Cost of Fire.*
"""

from __future__ import annotations

import math
from datetime import date

__all__ = ["season_summary", "compare_years", "CAVEATS"]

_NUMERIC_FIELDS = (
    "duration_days", "burned_area_mha_indonesia", "burned_area_mha_kalimantan",
    "peat_fraction", "hotspot_count", "co2e_gt", "economic_loss_usd_bn",
    "excess_deaths_est", "oni_peak",
)


def _load():
    from app.data.loader import load_seasons
    seasons = load_seasons() or []
    return sorted(seasons, key=lambda s: s.get("year", 0))


def _doy(value):
    if not value:
        return None
    try:
        y, m, d = (int(x) for x in str(value)[:10].split("-"))
        return date(y, m, d).timetuple().tm_yday
    except Exception:
        return None


def _phase(s):
    ph = (s.get("enso_phase") or "").lower()
    if ph in ("el_nino", "la_nina", "neutral"):
        return ph
    oni = s.get("oni_peak")
    if oni is None:
        return "neutral"
    return "el_nino" if oni >= 0.5 else ("la_nina" if oni <= -0.5 else "neutral")


def _stats(values):
    import numpy as np
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "min": None, "max": None,
                "sd": None}
    a = np.asarray(vals, dtype=float)
    return {
        "n": int(a.size),
        "mean": round(float(a.mean()), 4),
        "median": round(float(np.median(a)), 4),
        "min": round(float(a.min()), 4),
        "max": round(float(a.max()), 4),
        "sd": round(float(a.std(ddof=1)) if a.size > 1 else 0.0, 4),
    }


def _ols_trend(years, values):
    """OLS slope per year, with standard error and an honest significance flag."""
    import numpy as np
    pts = [(float(y), float(v)) for y, v in zip(years, values) if v is not None]
    if len(pts) < 4:
        return {"n": len(pts), "slope_per_year": None, "stderr": None,
                "significant": False,
                "note": "too few years with data to fit a trend"}
    x = np.array([p[0] for p in pts])
    y = np.array([p[1] for p in pts])
    xm, ym = x.mean(), y.mean()
    sxx = float(((x - xm) ** 2).sum())
    if sxx <= 0:
        return {"n": len(pts), "slope_per_year": None, "stderr": None,
                "significant": False, "note": "degenerate x"}
    slope = float(((x - xm) * (y - ym)).sum() / sxx)
    intercept = float(ym - slope * xm)
    resid = y - (intercept + slope * x)
    dof = max(1, len(pts) - 2)
    s2 = float((resid ** 2).sum() / dof)
    se = math.sqrt(s2 / sxx)
    return {
        "n": len(pts),
        "slope_per_year": round(slope, 6),
        "intercept": round(intercept, 4),
        "stderr": round(se, 6),
        "significant": bool(se > 0 and abs(slope) > 2.0 * se),
        "note": ("|slope| exceeds 2 standard errors" if se > 0 and abs(slope) > 2 * se
                 else "slope is within 2 standard errors of zero; treat as no trend"),
    }


def season_summary() -> dict:
    """Aggregate statistics plus one row per season for the charts."""
    seasons = _load()
    rows = []
    for s in seasons:
        rows.append({
            "year": s.get("year"),
            "enso_phase": _phase(s),
            "oni_peak": s.get("oni_peak"),
            "start_date": s.get("start_date"),
            "peak_date": s.get("peak_date"),
            "end_date": s.get("end_date"),
            "start_doy": _doy(s.get("start_date")),
            "peak_doy": _doy(s.get("peak_date")),
            "end_doy": _doy(s.get("end_date")),
            "duration_days": s.get("duration_days"),
            "burned_area_mha_indonesia": s.get("burned_area_mha_indonesia"),
            "burned_area_mha_kalimantan": s.get("burned_area_mha_kalimantan"),
            "peat_fraction": s.get("peat_fraction"),
            "hotspot_count": s.get("hotspot_count"),
            "co2e_gt": s.get("co2e_gt"),
            "economic_loss_usd_bn": s.get("economic_loss_usd_bn"),
            "excess_deaths_est": s.get("excess_deaths_est"),
            "pm25_peak_ugm3": s.get("pm25_peak_ugm3") or {},
            "estimated": bool(s.get("estimated")),
            "source": s.get("source"),
            # v2 (CONTRACT_V2 M4) -- optional for the frontend
            "target_source": s.get("target_source"),
            "oni_burn_window": s.get("oni_burn_window"),
            "iod_phase": s.get("iod_phase"),
            "dmi_son": s.get("dmi_son"),
            "dmi_peak": s.get("dmi_peak"),
            "psi_peak_singapore": s.get("psi_peak_singapore"),
            "singapore_peaks": s.get("singapore_peaks"),
            "pm25_peak_metric": s.get("pm25_peak_metric"),
            "excess_deaths_range": s.get("excess_deaths_range"),
            "burned_area_mha_indonesia_range": s.get("burned_area_mha_indonesia_range"),
            "burned_area_mha_kalimantan_range": s.get("burned_area_mha_kalimantan_range"),
            "rainfall_rule_dates": s.get("rainfall_rule_dates"),
        })

    years = [r["year"] for r in rows]
    by_phase = {}
    for ph in ("la_nina", "neutral", "el_nino"):
        sub = [r for r in rows if r["enso_phase"] == ph]
        by_phase[ph] = {
            "years": [r["year"] for r in sub],
            "n": len(sub),
            "burned_area_mha_kalimantan": _stats([r["burned_area_mha_kalimantan"] for r in sub]),
            "duration_days": _stats([r["duration_days"] for r in sub]),
            "excess_deaths_est": _stats([r["excess_deaths_est"] for r in sub]),
            "co2e_gt": _stats([r["co2e_gt"] for r in sub]),
        }
    neutral_mean = by_phase["neutral"]["burned_area_mha_kalimantan"]["mean"]
    for ph, blk in by_phase.items():
        m = blk["burned_area_mha_kalimantan"]["mean"]
        blk["burned_area_multiplier_vs_neutral"] = (
            round(m / neutral_mean, 3) if (m is not None and neutral_mean) else None)

    worst = None
    cand = [r for r in rows if r["burned_area_mha_kalimantan"] is not None]
    if cand:
        worst = max(cand, key=lambda r: r["burned_area_mha_kalimantan"])["year"]
    longest = None
    cand = [r for r in rows if r["duration_days"] is not None]
    if cand:
        longest = max(cand, key=lambda r: r["duration_days"])["year"]

    receptor_names = sorted({k for r in rows for k in (r["pm25_peak_ugm3"] or {})})
    pm_peaks = {
        name: _stats([(r["pm25_peak_ugm3"] or {}).get(name) for r in rows])
        for name in receptor_names
    }

    totals = {}
    for f in ("co2e_gt", "economic_loss_usd_bn", "excess_deaths_est",
              "burned_area_mha_kalimantan", "burned_area_mha_indonesia"):
        vals = [r[f] for r in rows if r.get(f) is not None]
        totals[f] = round(float(sum(vals)), 3) if vals else None

    def _mean_duration(subset):
        vals = [r["duration_days"] for r in subset if r["duration_days"] is not None]
        return round(float(sum(vals)) / len(vals), 1) if vals else None

    summary = {
        "n_seasons": len(rows),
        "first_year": (min(years) if years else None),
        "last_year": (max(years) if years else None),
        "mean_duration_days": _mean_duration(rows),
        "mean_duration_el_nino": _mean_duration(
            [r for r in rows if r["enso_phase"] == "el_nino"]),
        "mean_duration_la_nina": _mean_duration(
            [r for r in rows if r["enso_phase"] == "la_nina"]),
        "mean_duration_neutral": _mean_duration(
            [r for r in rows if r["enso_phase"] == "neutral"]),
        "total_burned_mha_kalimantan": (
            round(float(sum(r["burned_area_mha_kalimantan"] for r in rows
                            if r["burned_area_mha_kalimantan"] is not None)), 3)
            if any(r["burned_area_mha_kalimantan"] is not None for r in rows) else None),
        "year_range": [min(years), max(years)] if years else [None, None],
        "worst_year_kalimantan": worst,
        "longest_season_year": longest,
        "stats": {f: _stats([r.get(f) for r in rows]) for f in _NUMERIC_FIELDS},
        "record_totals": totals,
        "by_enso_phase": by_phase,
        "pm25_peak_by_receptor": pm_peaks,
        "trends": {
            "burned_area_mha_kalimantan": _ols_trend(
                years, [r["burned_area_mha_kalimantan"] for r in rows]),
            "duration_days": _ols_trend(years, [r["duration_days"] for r in rows]),
            "start_doy": _ols_trend(years, [r["start_doy"] for r in rows]),
            "end_doy": _ols_trend(years, [r["end_doy"] for r in rows]),
        },
        "n_estimated": sum(1 for r in rows if r["estimated"]),
        "caveats": list(CAVEATS),
    }
    return {"seasons": rows, "summary": summary, "caveats": list(CAVEATS)}


def compare_years(years: list[int]) -> dict:
    """Side-by-side comparison of named seasons, with ratios to the record mean."""
    all_rows = season_summary()["seasons"]
    by_year = {r["year"]: r for r in all_rows}
    wanted, missing = [], []
    for y in years or []:
        try:
            yi = int(y)
        except (TypeError, ValueError):
            continue
        if yi in by_year:
            wanted.append(by_year[yi])
        else:
            missing.append(yi)

    fields = ("duration_days", "burned_area_mha_kalimantan",
              "burned_area_mha_indonesia", "peat_fraction", "hotspot_count",
              "co2e_gt", "economic_loss_usd_bn", "excess_deaths_est", "oni_peak")
    record_mean = {f: _stats([r.get(f) for r in all_rows])["mean"] for f in fields}

    metrics = []
    for f in fields:
        vals = {}
        for r in wanted:
            v = r.get(f)
            vals[str(r["year"])] = {
                "value": v,
                "vs_record_mean": (round(v / record_mean[f], 3)
                                   if (v is not None and record_mean[f]) else None),
            }
        present = [r.get(f) for r in wanted if r.get(f) is not None]
        metrics.append({
            "field": f,
            "record_mean": record_mean[f],
            "values": vals,
            "max_year": (max([r for r in wanted if r.get(f) is not None],
                             key=lambda r: r[f])["year"] if present else None),
        })

    timeline = [{
        "year": r["year"],
        "enso_phase": r["enso_phase"],
        "start_doy": r["start_doy"],
        "peak_doy": r["peak_doy"],
        "end_doy": r["end_doy"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "duration_days": r["duration_days"],
    } for r in wanted]

    receptor_names = sorted({k for r in wanted for k in (r["pm25_peak_ugm3"] or {})})
    pm25 = {name: {str(r["year"]): (r["pm25_peak_ugm3"] or {}).get(name)
                   for r in wanted}
            for name in receptor_names}

    return {
        "years": [r["year"] for r in wanted],
        "missing_years": missing,
        "seasons": wanted,
        "metrics": metrics,
        "timeline": timeline,
        "pm25_peak_ugm3": pm25,
        "caveats": list(CAVEATS),
    }


CAVEATS = [
    "Burned area is not measured the same way twice. The 1997 and 2015 figures "
    "come from different sensors and different algorithms, and published "
    "estimates for the same season can differ by a factor of two. Comparing "
    "years therefore compares methods as much as it compares fires.",
    "Season start and end dates from 2001 are this site's own threshold rule "
    "on NASA MODIS fire counts over the Central/South Kalimantan peat belt; "
    "1997-2000 predate MODIS and keep dates from a rainfall rule "
    "(target_source says which). Move the threshold and every date moves by "
    "days to weeks.",
    "Excess-death figures are modelled attributions from smoke exposure, not "
    "counted deaths. Published estimates for 2015 range from about 12,000 to "
    "100,000, depending mainly on how smoke exposure is converted into risk; "
    "short-term and long-term estimates count different things. The single "
    "excess_deaths_est value is the highest of them (Koplitz et al. 2016) and "
    "should be shown with excess_deaths_range, never alone.",
    "ENSO phases are classified on the burn-window ONI (mean of the JJA, JAS "
    "and ASO seasons), not on the post-season peak, so 2006 and 2019 read as "
    "ENSO-neutral: both were driven by a positive Indian Ocean Dipole "
    "(iod_phase).",
    "Economic loss estimates mix direct costs, forgone output and health costs "
    "in ways that differ between sources, and are not inflation-adjusted here.",
    "The record is short and dominated by two extreme El Nino events, so every "
    "trend on this page has wide error bars. Where a trend is within two "
    "standard errors of zero the model says so rather than drawing a line.",
    "Low-fire La Nina years are under-documented relative to disaster years: "
    "nobody publishes a detailed retrospective on a quiet season, so the "
    "record's floor is less well characterised than its ceiling.",
]
