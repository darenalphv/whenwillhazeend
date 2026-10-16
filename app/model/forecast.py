"""Forecast odds for the current fire season (CONTRACT_V2 amendment M2).

Prediction-market *presentation* of the season model's own probabilities.
Nothing here is a market: every percentage is the share of N simulated
seasons from `app.model.burn` in which the stated event happened.

Also the single source of truth for what "the season has ended" means
(`SEASON_END_DEFINITION`, `season_labels`) -- the same rule labels the
historical seasons the model is fitted on (MODEL_SPEC section 2.4, S1) and
resolves the markets.

History (GET /api/forecast/history) must not leak the future: a point "as of
D" uses weather observed up to D only, ONI/DMI values published by D only, and
climatology after D (no archived forecast exists, so the 14-day forecast the
live site uses is dropped). Points therefore depend only on information
available on D and can be recomputed identically at any later time: a
build-time file holds the season so far, and later days are computed on
demand (capped per request) and memoised in process.
"""

from __future__ import annotations

import functools
import os
import threading
from datetime import date, datetime, timedelta, timezone

__all__ = ["SEASON_END_DEFINITION", "season_labels", "forecast_odds",
           "forecast_history", "compute_history_point", "market_thresholds",
           "CAVEATS"]

#: Region in which fires are counted (Field et al. 2016 peat-belt box).
LABEL_BOX = {"lat": [-4.0, 0.0], "lon": [110.0, 117.0]}
WINDOW_DAYS = 7          # centred running count
END_FRACTION = 0.10      # of the season's maximum 7-day count
END_RUN_DAYS = 14        # consecutive days below it
START_RUN_DAYS = 7
SEASON_FROM = (5, 1)     # 1 May
FLOOR_PERCENTILE = 10    # seasons whose max is below the P10 of all maxima: none

SEASON_END_DEFINITION = (
    "The fire season has ended on the first day after its peak from which the "
    "7-day running count (centred) of NASA FIRMS MODIS active-fire detections "
    "(Terra + Aqua, Collection 6.1, 'presumed vegetation fire' type) inside "
    "4S-0, 110-117E -- the Central/South Kalimantan peat belt used by Field et "
    "al. (2016, PNAS) -- stays below 10% of that season's highest 7-day count "
    "for 14 consecutive days. The end date is the first of those 14 days, so it "
    "can only be confirmed 17 days later. A season starts on the first day after "
    "1 May on which the count exceeds 10% of the maximum and stays above it for "
    "7 days. A year whose maximum is below the 10th percentile of all years' "
    "maxima (2001-2025) has no season. This is this site's own definition "
    "[engineering judgement]; no published definition of fire-season end exists."
)
RESOLUTION_SOURCE = (
    "NASA FIRMS MODIS C6.1 active fires (firms.modaps.eosdis.nasa.gov): the "
    "yearly country archive once published; until then the near-real-time "
    "feed. Counted by this site with app.model.forecast.season_labels."
)


# --------------------------------------------------------------------------- #
# labels -- the resolution rule
# --------------------------------------------------------------------------- #

def _running(counts, w=WINDOW_DAYS):
    h = w // 2
    n = len(counts)
    csum = [0]
    for c in counts:
        csum.append(csum[-1] + c)
    return [csum[min(n, i + h + 1)] - csum[max(0, i - h)] for i in range(n)]


def season_labels(dates: list[str], counts: list[float], year: int,
                  floor: float | None = None) -> dict:
    """Start / peak / end of ``year``'s season from daily fire counts.

    ``dates`` must be consecutive ISO days covering at least 1 May..31 Dec of
    ``year`` (plus following days if the season runs late). ``floor`` is the
    no-season threshold on the maximum 7-day count (None = no floor).
    Returns {"start","peak","end","max_7d","threshold","status"} where status
    is "season", "no_season", or "ongoing" (end not yet confirmed).
    """
    k = _running([float(c) for c in counts])
    try:
        i0 = dates.index(f"{year}-{SEASON_FROM[0]:02d}-{SEASON_FROM[1]:02d}")
    except ValueError:
        i0 = 0
    try:
        i1 = dates.index(f"{year}-12-31")
    except ValueError:
        i1 = len(dates) - 1
    seg = k[i0:i1 + 1]
    pk = i0 + max(range(len(seg)), key=lambda j: seg[j])
    mx = k[pk]
    thr = END_FRACTION * mx
    out = {"peak": dates[pk], "max_7d": mx, "threshold": thr,
           "start": None, "end": None}
    if floor is not None and mx < floor:
        out["status"] = "no_season"
        return out
    for i in range(i0, pk + 1):
        if all(v > thr for v in k[i:i + START_RUN_DAYS]):
            out["start"] = dates[i]
            break
    # the last 3 days' centred counts are incomplete; do not use them
    usable = len(k) - WINDOW_DAYS // 2
    for i in range(pk + 1, usable - END_RUN_DAYS + 1):
        if all(v < thr for v in k[i:i + END_RUN_DAYS]):
            out["end"] = dates[i]
            break
    out["status"] = "season" if out["end"] else "ongoing"
    return out


# --------------------------------------------------------------------------- #
# markets
# --------------------------------------------------------------------------- #

DEFAULT_N_SIMS = 5000
HISTORY_N_SIMS = 4000
HISTORY_START = (8, 1)          # history series starts 1 Aug of the season year
BACKFILL_CAP = 3                # on-demand history points per request
_LIVE_TTL_S = 3600.0
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _fmt(d: date) -> str:
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def _display(p: float) -> str:
    """Percent label that never claims certainty a simulation count cannot give."""
    if p > 0.99:
        return ">99%"
    if p < 0.01:
        return "<1%"
    return f"{round(100 * p)}%"


def season_year(as_of: date) -> int:
    return as_of.year if as_of.month >= 4 else as_of.year - 1


def market_thresholds(year: int) -> list[date]:
    """Fixed per season, so market ids stay stable across the odds history."""
    return [date(year, 9, 30), date(year, 10, 15), date(year, 10, 31),
            date(year, 11, 15), date(year, 11, 30), date(year, 12, 31)]


def _market_id(d: date) -> str:
    return f"end-by-{d.isoformat()}"


def _seed_for(as_of: date) -> int:
    return 20260000 + as_of.toordinal() % 100000


def _confirmable_on(d: date) -> date:
    # the end date is the first of 14 quiet days, judged on a centred 7-day count
    return d + timedelta(days=END_RUN_DAYS + WINDOW_DAYS // 2)


def _run(as_of: date, n_sims: int, allow_forecast: bool) -> dict:
    from app.model import burn
    return burn.simulate_season_end(as_of.isoformat(), n_sims=n_sims, seed=_seed_for(as_of),
                                    allow_forecast=allow_forecast, _keep_end_days=True)


def _odds_from_run(as_of: date, res: dict) -> dict:
    import numpy as np
    year = season_year(as_of)
    ended_already = (not res["active"]) and as_of >= date(year, 9, 1)
    end_day = res.pop("_end_days")
    n = int(end_day.size)
    end_dates_ord = as_of.toordinal() + end_day.astype(np.int64)   # horizon+1 = beyond
    horizon_ord = as_of.toordinal() + int(end_day.max())
    markets = []
    for thr in market_thresholds(year):
        if ended_already:
            p_yes = 1.0
        else:
            p_yes = float((end_dates_ord <= thr.toordinal()).mean())
        markets.append({
            "id": _market_id(thr),
            "question": f"Will the {year} Kalimantan haze season end by {_fmt(thr)}?",
            "threshold_date": thr.isoformat(),
            "resolves_by": thr.isoformat(),
            "confirmable_on": _confirmable_on(thr).isoformat(),
            "yes": round(p_yes, 4), "no": round(1.0 - p_yes, 4),
            "yes_display": _display(p_yes),
            "status": "open" if thr >= as_of else "past_threshold",
        })
    # weekly distribution (Monday-start weeks)
    outcomes = []
    if ended_already:
        outcomes.append({"label": "Already ended (no active season detected)",
                         "start": None, "end": as_of.isoformat(), "p": 1.0})
        p50 = as_of
    else:
        wk0 = as_of + timedelta(days=1)
        wk0 = wk0 - timedelta(days=wk0.weekday())
        last = as_of + timedelta(days=int(np.percentile(end_day, 97)))
        cur = wk0
        acc = 0.0
        while cur <= last:
            a = max(cur, as_of + timedelta(days=1))
            b = cur + timedelta(days=6)
            p = float(((end_dates_ord >= a.toordinal()) & (end_dates_ord <= b.toordinal())).mean())
            outcomes.append({"label": f"{a.day} {_MONTHS[a.month - 1]} - {b.day} {_MONTHS[b.month - 1]}",
                             "start": a.isoformat(), "end": b.isoformat(), "p": round(p, 4)})
            acc += p
            cur += timedelta(days=7)
        rest = max(0.0, 1.0 - acc)
        outcomes.append({"label": f"After {_fmt(cur - timedelta(days=1))}",
                         "start": cur.isoformat(), "end": None, "p": round(rest, 4)})
        p50 = as_of + timedelta(days=int(round(float(np.median(end_day)))))
    del horizon_ord
    return {"markets": markets, "outcomes": outcomes, "p50_end_date": p50.isoformat(),
            "season_active": bool(res["active"]), "n_sims": n,
            "season_year": year}


# --------------------------------------------------------------------------- #
# public: odds
# --------------------------------------------------------------------------- #

_live_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


def forecast_odds(as_of: str | None = None, n_sims: int = DEFAULT_N_SIMS) -> dict:
    """Model odds for the current season, as of ``as_of`` (default today, UTC).

    Today or yesterday: live (observed weather + 14-day forecast). Earlier
    dates: a leak-free hindcast (weather observed up to as_of only).
    """
    import time
    today = datetime.now(timezone.utc).date()
    d = date.fromisoformat(as_of) if as_of else today
    if d > today:
        raise ValueError(f"as_of {d} is in the future")
    live = d >= today - timedelta(days=1)
    key = (d.isoformat(), int(n_sims), live, today.isoformat())
    now = time.monotonic()
    with _lock:
        hit = _live_cache.get(key)
        if hit and (not live or now - hit[0] < _LIVE_TTL_S):
            return hit[1]
    res = _run(d, n_sims, allow_forecast=live)
    od = _odds_from_run(d, res)
    from app.model import burn
    skill = burn.load_model().get("skill", {})
    out = {
        "as_of": d.isoformat(),
        "season_year": od["season_year"],
        "season_active": od["season_active"],
        "n_sims": od["n_sims"],
        "p50_end_date": od["p50_end_date"],
        "markets": [m for m in od["markets"] if m["status"] == "open"],
        "past_threshold_markets": [m["id"] for m in od["markets"] if m["status"] != "open"],
        "distribution": {"bucket": "week", "outcomes": od["outcomes"]},
        "resolution": {
            "definition": SEASON_END_DEFINITION,
            "source": RESOLUTION_SOURCE,
            "status": resolution_status(od["season_year"]),
        },
        "model_error_days": skill.get("rmse_days_forecast"),
        "model_error_note": ("Root-mean-square error, in days, of the end date the model "
                             "would have forecast on the first day of each 2001-2025 season "
                             "without knowing the weather to come (forecast mode). Forecasts "
                             "issued on 1 Sep were off by "
                             f"{skill.get('rmse_days_forecast_from_1_sep')} days RMS."),
        "live_weather": res["live_weather"],
        "mode": "live" if live else "hindcast",
        "drought_readiness": res["drought_readiness"],
        "indices": res["indices"],
        "label": ("Model odds. Not a market -- no money changes hands. Each percentage "
                  f"is the share of {od['n_sims']} simulated seasons."),
        "caveats": list(CAVEATS) + ([] if live else [
            "Hindcast: computed from weather observed up to this date only, without the "
            "14-day weather forecast the live odds use."]) + res["caveats"][:3],
    }
    with _lock:
        _live_cache[key] = (now, out)
        if len(_live_cache) > 64:
            _live_cache.pop(next(iter(_live_cache)))
    return out


# --------------------------------------------------------------------------- #
# resolution status (season-to-date fire counts, if available)
# --------------------------------------------------------------------------- #

def resolution_status(year: int) -> dict:
    """Whether this season's end can already be called from fire counts.

    Needs season-to-date daily FIRMS counts in ``app/data/fire_counts_current.json``
    (written by ``scripts/model/season/fetch_firms_nrt.py`` when a FIRMS MAP_KEY
    is configured). Without them the market stays unresolved here; the fire
    archive for a year is published openly only after the year ends.
    """
    import json
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[1] / "data" / "fire_counts_current.json"
    if not p.is_file():
        return {"state": "unresolved",
                "note": "Season-to-date fire counts are not available to this site yet "
                        "(the open FIRMS archive for a year is published after it ends; "
                        "near-real-time access needs a FIRMS MAP_KEY)."}
    try:
        d = json.loads(p.read_text())
        if int(d.get("year", 0)) != int(year):
            return {"state": "unresolved", "note": "fire counts on file are for another season"}
        lab = season_labels(d["dates"], d["count"], year)
        return {"state": "ended" if lab["status"] == "season" else "not_ended_yet",
                "end_date": lab["end"], "peak": lab["peak"],
                "counts_through": d["dates"][-1], "counts_source": d.get("source")}
    except Exception as exc:  # noqa: BLE001
        return {"state": "unresolved", "note": f"fire counts unreadable ({type(exc).__name__})"}


# --------------------------------------------------------------------------- #
# history (no leakage)
# --------------------------------------------------------------------------- #

_hist_memo: dict[str, dict] = {}


def compute_history_point(as_of: date | str, n_sims: int = HISTORY_N_SIMS) -> dict:
    """One leak-free history point: weather observed up to ``as_of`` only,
    ONI/DMI as published by ``as_of``, climatology after it, fixed seed."""
    d = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    res = _run(d, n_sims, allow_forecast=False)
    od = _odds_from_run(d, res)
    return {"as_of": d.isoformat(), "p50_end_date": od["p50_end_date"],
            "markets": {m["id"]: m["yes"] for m in od["markets"]},
            "season_active": od["season_active"], "n_sims": od["n_sims"],
            "kind": "hindcast"}


HISTORY_METHOD = (
    "Each point is what the model would have said on that date using only information "
    "available then: weather observed up to that date (ERA5 / Open-Meteo analysis, mean of "
    "five peat-belt points), the ONI and DMI values published by then, and simulated "
    "weather after it. Past weather forecasts are not archived, so unlike the live odds "
    "these points do not use the 14-day forecast. Points are deterministic (fixed seed per "
    "date) and can be recomputed identically later; the series up to the build date is "
    "precomputed at build time and later days are computed on demand "
    f"(at most {BACKFILL_CAP} per request) and memoised in memory."
)


def forecast_history(today: date | None = None, cap: int = BACKFILL_CAP) -> dict:
    from app.data.loader_season import load_forecast_history
    today = today or datetime.now(timezone.utc).date()
    base = load_forecast_history()
    year = season_year(today)
    series = [p for p in base.get("series", []) if p.get("as_of", "")[:4] == str(year)]
    have = {p["as_of"] for p in series}
    start = date(year, *HISTORY_START)
    last_obs_day = today - timedelta(days=1)       # a point needs its own day observed
    missing = []
    d = start
    while d <= last_obs_day:
        if d.isoformat() not in have:
            missing.append(d)
        d += timedelta(days=1)
    computed, pending = 0, 0
    for d in missing:
        k = d.isoformat()
        with _lock:
            memo = _hist_memo.get(k)
        if memo is None:
            if computed >= cap:
                pending += 1
                continue
            memo = compute_history_point(d)
            computed += 1
            with _lock:
                _hist_memo[k] = memo
        series.append(memo)
    series.sort(key=lambda p: p["as_of"])
    thr = market_thresholds(year)
    return {
        "season_year": year,
        "series": series,
        "markets": [{"id": _market_id(t), "threshold_date": t.isoformat(),
                     "question": f"Will the {year} Kalimantan haze season end by {_fmt(t)}?"}
                    for t in thr],
        "method": HISTORY_METHOD,
        "computed_at": base.get("computed_at"),
        "precomputed_through": max((p["as_of"] for p in base.get("series", [])), default=None),
        "computed_on_demand": computed,
        "pending_points": pending,
        "model_error_days": _skill().get("rmse_days_forecast"),
        "caveats": list(CAVEATS) + [
            "History points are hindcasts without the 14-day weather forecast, so the most "
            "recent point can differ from today's live odds.",
        ] + ([f"{pending} more day(s) will be filled in on later requests."] if pending else []),
    }


def _skill() -> dict:
    from app.model import burn
    return burn.load_model().get("skill", {})


def build_history(year: int, through: date, n_sims: int = HISTORY_N_SIMS) -> dict:
    """Build-time: the whole season's history up to ``through``."""
    pts = []
    d = date(year, *HISTORY_START)
    while d <= through:
        pts.append(compute_history_point(d, n_sims))
        d += timedelta(days=1)
    return {"season_year": year, "series": pts, "method": HISTORY_METHOD,
            "computed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "caveats": list(CAVEATS)}


CAVEATS = [
    "Model odds. Not a market -- no money changes hands. Each percentage is the share "
    "of simulated seasons in which the event happened.",
    "The odds come from one statistical model fitted to 22 past seasons. Its typical "
    "error is about two weeks; the percentages include weather uncertainty and an "
    "allowance, calibrated on past seasons, for everything the model does not see.",
    "'Ended' follows the resolution definition exactly; it is about fires in the "
    "Kalimantan peat belt, not about air quality in any city.",
]
