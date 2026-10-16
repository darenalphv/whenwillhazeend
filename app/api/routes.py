"""JSON API routes.

Every handler is a thin translation layer: validate the request, call the data
or model layer, hand back what it returned. Shapes are defined by CONTRACT.md
and produced by the model layer; this module does not reshape, round, fill in or
default any modelled quantity.

Failure policy: a data/model failure is HTTP 503 with a `detail` naming the
component (see app/api/deps.py). A request for a year we hold no record of is a
404. An out-of-range parameter is a 422. There is no code path that returns a
number the model did not produce.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, status
from pydantic import ValidationError

from app import __version__
from app.api import deps
from app.api.schemas import (
    ForecastOddsQuery,
    HazeFieldRequest,
    MAX_YEAR,
    MIN_YEAR,
    DispersionRequest,
    ExhaustionRequest,
    MeteoQuery,
    SeasonSimRequest,
)

router = APIRouter(prefix="/api", tags=["api"])

YearParam = Annotated[
    int,
    Path(ge=MIN_YEAR, le=MAX_YEAR, description="Fire-season year."),
]


# --------------------------------------------------------------------------- #
# Meta
# --------------------------------------------------------------------------- #


@router.get("/health", summary="Liveness and live-weather availability")
def health() -> dict[str, Any]:
    """Cheap health check.

    ``live_weather_available`` reports whether Open-Meteo answered the last
    probe. ``false`` is a normal, fully-supported state: every model falls back
    to bundled climatology and the app is designed to run with no network.
    """
    return {
        "status": "ok",
        "version": __version__,
        "live_weather_available": deps.live_weather_available(),
    }


# --------------------------------------------------------------------------- #
# Bundled data
# --------------------------------------------------------------------------- #


@router.get("/history", summary="All historical fire seasons plus aggregate stats")
def history() -> dict[str, Any]:
    # season_summary() already returns {"seasons", "summary", "caveats"}; wrapping it
    # again put the amendment-A4 fields at body["summary"]["summary"]. Flatten it.
    payload = deps.call("app.model.history", "season_summary")
    seasons = payload.get("seasons") or deps.call("app.data.loader", "load_seasons")
    return {
        "seasons": seasons,
        "summary": payload.get("summary", {}),
        "caveats": payload.get("caveats", []),
    }


@router.get(
    "/history/compare",
    summary="Side-by-side comparison of specific seasons",
)
def history_compare(
    years: Annotated[
        str,
        Query(
            description="Comma-separated years, e.g. '1997,2015,2019'.",
            examples=["2015,2019,2023"],
            min_length=4,
            max_length=200,
        ),
    ],
) -> dict[str, Any]:
    """Not in the original route list, but ``compare_years`` exists in the model
    layer and the history section needs it; additive, so it breaks nothing."""
    parsed: list[int] = []
    for chunk in years.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            year = int(chunk)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"'{chunk}' is not a year.",
            ) from None
        if not (MIN_YEAR <= year <= MAX_YEAR):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"year {year} is outside {MIN_YEAR}..{MAX_YEAR}.",
            )
        if year not in parsed:
            parsed.append(year)
    if not parsed:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="no years supplied.",
        )
    if len(parsed) > 20:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at most 20 years may be compared at once.",
        )
    return deps.call("app.model.history", "compare_years", parsed)


@router.get(
    "/history/{year}",
    summary="One season's record and that year's fire clusters",
)
def history_year(year: YearParam) -> dict[str, Any]:
    seasons = deps.call("app.data.loader", "load_seasons")
    season = next(
        (s for s in seasons if isinstance(s, dict) and s.get("year") == year), None
    )
    if season is None:
        known = sorted(
            s.get("year") for s in seasons if isinstance(s, dict) and s.get("year")
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No fire-season record for {year}. "
                f"Years on file: {known[0]}–{known[-1]}."
                if known
                else f"No fire-season record for {year}."
            ),
        )
    clusters = deps.call("app.data.loader", "load_fire_clusters", year)
    return {"season": season, "clusters": clusters}


@router.get(
    "/provenance",
    summary="Where every number on the site came from",
)
def provenance() -> dict[str, Any]:
    """Serves app/data/provenance.json verbatim (contract amendment A6).

    This is the table the site shows a user who asks where a mortality or
    burned-area figure came from, so it is passed through unaltered: nothing is
    relabelled, defaulted, or given a friendlier `status` than the data layer
    recorded. In particular, `synthesised` stays `synthesised`.
    """
    return deps.call("app.data.loader", "load_provenance")


@router.get("/regions", summary="Kalimantan provinces as GeoJSON")
def regions() -> dict[str, Any]:
    return deps.call("app.data.loader", "load_regions")


@router.get("/fuel", summary="Current burnable stock by fuel pool")
def fuel() -> dict[str, Any]:
    return deps.call("app.data.loader", "load_fuel_stock")


@router.get("/receptors", summary="Downwind cities that receive the haze")
def receptors() -> list[dict[str, Any]]:
    return deps.call("app.data.loader", "load_receptors")


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


@router.post(
    "/simulate/season",
    summary="How many days until this season's burning stops",
)
def simulate_season(req: SeasonSimRequest) -> dict[str, Any]:
    return deps.simulate_season(
        as_of=req.resolved_as_of(),
        region=req.region,
        oni=req.oni,
        n_sims=req.n_sims,
        seed=req.seed,
        scenario=req.scenario,
    )


@router.post(
    "/simulate/exhaustion",
    summary="How many years until there is nothing left to burn",
)
def simulate_exhaustion(req: ExhaustionRequest) -> dict[str, Any]:
    return deps.simulate_exhaustion(
        n_sims=req.n_sims,
        horizon_years=req.horizon_years,
        restoration_mha_per_yr=req.restoration_mha_per_yr,
        burn_reduction_pct=req.burn_reduction_pct,
        deforestation_mha_per_yr=req.deforestation_mha_per_yr,
        seed=req.seed,
    )


@router.post("/dispersion", summary="Modelled PM2.5 downwind of burning clusters")
def dispersion(req: DispersionRequest) -> dict[str, Any]:
    return deps.call(
        "app.model.dispersion",
        "haze_dispersion",
        sources=req.sources_as_dicts(),
        wind_speed_ms=req.wind_speed_ms,
        wind_dir_deg=req.wind_dir_deg,
        mixing_height_m=req.mixing_height_m,
        rain_mm=req.rain_mm,
    )


@router.post(
    "/simulate/haze3d",
    summary="Time-stepped gridded PM2.5 field for the 3D simulator",
)
def simulate_haze3d(req: HazeFieldRequest) -> dict[str, Any]:
    """A day of haze over a grid, driven by the diurnal boundary layer.

    Pass ``year`` and the server loads that season's clusters, so the client
    never has to ship hundreds of sources up the wire.
    """
    sources = req.sources_as_dicts()
    if sources is None:
        # The model can run on its own bundled observed emissions (incl. this
        # week's FIRMS-derived ones) for dates that have no synthesised season
        # clusters, so a missing clusters file is not an error here.
        from app.data import loader as _loader
        try:
            fc = _loader.load_fire_clusters(req.year)
        except FileNotFoundError:
            fc = None  # expected for years with no synthesised season (e.g. this year)
        except Exception:  # noqa: BLE001 - anything else is a real failure: labelled 503
            fc = deps.call("app.data.loader", "load_fire_clusters", req.year)
        sources = []
        for f in (fc or {}).get("features", []):
            geom = f.get("geometry") or {}
            if geom.get("type") != "Point":
                continue
            lon, lat = geom.get("coordinates", [None, None])[:2]
            props = f.get("properties") or {}
            if lat is None or lon is None:
                continue
            sources.append({
                "lat": lat, "lon": lon,
                "area_ha": props.get("area_ha"),
                "pool": props.get("pool"),
                "date": props.get("date"),
                "duration_days": props.get("duration_days"),
            })
        sources = sources or None

    # The cell budget is enforced by HazeFieldRequest, so an over-budget request
    # is a 422 before it ever reaches the model.
    return deps.call(
            "app.model.haze3d", "haze_field",
            sources=sources,
            wind_speed_ms=req.wind_speed_ms,
            wind_dir_deg=req.wind_dir_deg,
            hours=req.hours,
            step_hours=req.step_hours,
            mixing_height_m=req.mixing_height_m,
            rain_mm=req.rain_mm,
            grid_n=req.grid_n,
            wind_veer_deg_per_h=req.wind_veer_deg_per_h,
            start_hour=req.start_hour,
            start_date=req.start_date.isoformat() if req.start_date else None,
            diurnal=req.diurnal,
            release_from_start=req.release_from_start,
            year=req.year,
            wind_mode=req.wind_mode,
            emissions_mode=req.emissions_mode,
            soa=req.soa,
            seed=req.seed,
    )


@router.get("/meteo", summary="Daily weather for a point (live, or climatology)")
def meteo(
    lat: Annotated[float, Query(ge=-90.0, le=90.0)],
    lon: Annotated[float, Query(ge=-180.0, le=180.0)],
    start: Annotated[str, Query(description="ISO date, inclusive.")],
    end: Annotated[str, Query(description="ISO date, inclusive.")],
) -> dict[str, Any]:
    """Validated through :class:`MeteoQuery` so the bounds live in one place."""
    try:
        q = MeteoQuery(lat=lat, lon=lon, start=start, end=end)
    except ValidationError as exc:
        # Bad input is the caller's problem (422), not a service outage (503).
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False, include_context=False),
        ) from None
    return deps.call(
        "app.model.meteo", "fetch_weather", q.lat, q.lon, q.start, q.end
    )


# --------------------------------------------------------------------------- #
# Forecast odds (CONTRACT_V2 M2) -- added by MODEL-SEASON
# --------------------------------------------------------------------------- #


@router.get(
    "/forecast/odds",
    summary="Model odds that the fire season ends by given dates (not a market)",
)
def forecast_odds(
    as_of: Annotated[
        str | None,
        Query(description="ISO date; default today (UTC). Past dates are leak-free hindcasts."),
    ] = None,
) -> dict[str, Any]:
    """Share of simulated seasons in which the season ends by each date.

    Every market resolves on one definition of "the season has ended"
    (``resolution.definition``); each market also carries its own
    ``resolves_by`` date. No money, no trading.
    """
    try:
        q = ForecastOddsQuery(as_of=as_of)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(include_url=False, include_context=False),
        ) from None
    return deps.call("app.model.forecast", "forecast_odds", q.as_of)


@router.get(
    "/forecast/history",
    summary="How the model odds for this season have moved, day by day",
)
def forecast_history() -> dict[str, Any]:
    """Leak-free: each point uses only weather observed up to its date."""
    return deps.call("app.model.forecast", "forecast_history")
