"""Pydantic v2 request models for the whenwillhazeend API.

Bounds here are deliberately *hard*: an out-of-range request is rejected with
HTTP 422 and a message naming the offending field. Nothing is silently clamped,
because a silently clamped ``n_sims`` would make a response look like the answer
to a question the caller did not ask.

Response bodies are **not** modelled here. The model and data layers return
plain JSON-serialisable dicts (see CONTRACT.md, golden rule 3) and the API
passes them through unaltered rather than re-shaping them through a response
model that could quietly drop keys the model layer added.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import settings

# --------------------------------------------------------------------------- #
# Shared bounds
# --------------------------------------------------------------------------- #

MAX_N_SIMS = 20_000
MAX_HORIZON_YEARS = 300
MAX_DISPERSION_SOURCES = 2_000
# grid_n^2 * frames for the 3D field. Mirrors app.model.haze3d.MAX_CELLS,
# checked here so an over-budget request is a 422 about the request rather
# than a 503 about the model being unavailable.
MAX_FIELD_CELLS = 400_000
MIN_YEAR = 1980
MAX_YEAR = 2100
# Open-Meteo's archive starts in 1940; its forecast runs ~16 days ahead. Asking
# outside that window is a client error, not something to quietly truncate.
MIN_METEO_DATE = date(1940, 1, 1)
MAX_METEO_LOOKAHEAD_DAYS = 400
MAX_METEO_SPAN_DAYS = 3_660  # ~10 years of daily values in one response

Scenario = Literal[
    "baseline",
    "no_enforcement",
    "strict_moratorium",
    "monsoon_early",
    "monsoon_late",
]

_ISO_DATE_HINT = "expected an ISO date string, YYYY-MM-DD"


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:  # pragma: no cover - message only
        raise ValueError(f"{field_name}: {_ISO_DATE_HINT}") from exc


class _Strict(BaseModel):
    """Base: unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- #
# POST /api/simulate/season
# --------------------------------------------------------------------------- #


class SeasonSimRequest(_Strict):
    """Inputs to ``app.model.burn.simulate_season_end``."""

    as_of: str | None = Field(
        default=None,
        description="ISO date the forecast is made from. Defaults to today (UTC).",
        examples=["2026-09-18"],
    )
    region: str = Field(
        default=settings.default_region,
        min_length=2,
        max_length=64,
        description="Region key, e.g. 'kalimantan' or a province name.",
    )
    oni: float | None = Field(
        default=None,
        ge=-4.0,
        le=4.0,
        description=(
            "Oceanic Nino Index override in degrees C. None means the model "
            "uses the observed/forecast value. Positive is El Nino, which "
            "prolongs the season."
        ),
    )
    n_sims: int = Field(
        default=settings.default_season_sims,
        ge=1,
        le=MAX_N_SIMS,
        description=f"Monte-Carlo trajectories (max {MAX_N_SIMS}).",
    )
    seed: int | None = Field(
        default=None,
        ge=0,
        le=2**32 - 1,
        description="RNG seed. Seeded runs are deterministic and are cached.",
    )
    scenario: Scenario = "baseline"

    @field_validator("as_of")
    @classmethod
    def _check_as_of(cls, v: str | None) -> str | None:
        if v is None:
            return None
        parsed = _parse_iso_date(v, "as_of")
        if not (MIN_YEAR <= parsed.year <= MAX_YEAR):
            raise ValueError(f"as_of: year must be between {MIN_YEAR} and {MAX_YEAR}")
        return parsed.isoformat()

    @field_validator("region")
    @classmethod
    def _normalise_region(cls, v: str) -> str:
        return v.strip()

    def resolved_as_of(self) -> str:
        return self.as_of or date.today().isoformat()


# --------------------------------------------------------------------------- #
# POST /api/simulate/exhaustion
# --------------------------------------------------------------------------- #


class ExhaustionRequest(_Strict):
    """Inputs to ``app.model.stock.simulate_exhaustion``."""

    n_sims: int = Field(
        default=settings.default_exhaustion_sims,
        ge=1,
        le=MAX_N_SIMS,
        description=f"Monte-Carlo trajectories (max {MAX_N_SIMS}).",
    )
    horizon_years: int = Field(
        default=settings.default_horizon_years,
        ge=1,
        le=MAX_HORIZON_YEARS,
        description=f"Years to project forward (max {MAX_HORIZON_YEARS}).",
    )
    restoration_mha_per_yr: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description=(
            "Peatland rewetting/restoration rate in Mha/yr. None uses the "
            "bundled observed rate."
        ),
    )
    burn_reduction_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description=(
            "Percent reduction in annual burned area versus the fitted "
            "baseline. 100 means fire is eliminated entirely."
        ),
    )
    deforestation_mha_per_yr: float | None = Field(
        default=None,
        ge=0.0,
        le=10.0,
        description=(
            "Non-fire conversion (plantation, mining, logging) in Mha/yr. "
            "None uses the bundled observed rate."
        ),
    )
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)


# --------------------------------------------------------------------------- #
# POST /api/dispersion
# --------------------------------------------------------------------------- #


class DispersionSource(_Strict):
    """A single burning cluster feeding the plume model."""

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    area_ha: float = Field(
        gt=0.0,
        le=5_000_000.0,
        description="Actively burning area of this cluster, hectares.",
    )
    pool: str = Field(
        min_length=1,
        max_length=64,
        description="Fuel pool id from /api/fuel, e.g. 'peat_drained'.",
    )


class DispersionRequest(_Strict):
    """Inputs to ``app.model.dispersion.haze_dispersion``."""

    sources: list[DispersionSource] = Field(
        min_length=1,
        max_length=MAX_DISPERSION_SOURCES,
        description=f"1..{MAX_DISPERSION_SOURCES} burning clusters.",
    )
    wind_speed_ms: float = Field(
        ge=0.0,
        le=80.0,
        description="10 m wind speed, m/s. 0 is treated as near-calm by the model.",
    )
    wind_dir_deg: float = Field(
        ge=0.0,
        le=360.0,
        description="Meteorological wind direction in degrees (direction it blows FROM).",
    )
    mixing_height_m: float = Field(
        default=800.0,
        ge=50.0,
        le=5_000.0,
        description="Boundary-layer mixing height, metres.",
    )
    rain_mm: float = Field(
        default=0.0,
        ge=0.0,
        le=1_000.0,
        description="Rainfall over the averaging period, mm. Drives wet deposition.",
    )

    def sources_as_dicts(self) -> list[dict]:
        return [s.model_dump() for s in self.sources]


# --------------------------------------------------------------------------- #
# POST /api/simulate/haze3d
# --------------------------------------------------------------------------- #


class HazeFieldRequest(_Strict):
    """Inputs to ``app.model.haze3d.haze_field``.

    Either ``year`` (the server loads that season's clusters) or an explicit
    ``sources`` list. Supplying both is rejected rather than guessed at.
    """

    year: int | None = Field(
        default=None,
        ge=MIN_YEAR,
        le=MAX_YEAR,
        description="Load this season's fire clusters server-side.",
    )
    sources: list[DispersionSource] | None = Field(
        default=None,
        max_length=MAX_DISPERSION_SOURCES,
        description="Explicit clusters, instead of a year.",
    )
    start_date: date | None = Field(
        default=None,
        description="Simulated start date. Clusters not burning then are excluded.",
    )
    start_hour: float = Field(default=0.0, ge=0.0, lt=24.0)
    hours: int = Field(default=24, ge=1, le=168)
    step_hours: float = Field(default=1.0, ge=0.25, le=6.0)
    grid_n: int = Field(
        default=56, ge=8, le=128,
        description="Cells per side. grid_n^2 * frames is capped server-side.",
    )
    wind_speed_ms: float = Field(default=3.0, ge=0.0, le=80.0)
    wind_dir_deg: float = Field(default=190.0, ge=0.0, le=360.0)
    wind_veer_deg_per_h: float = Field(default=0.0, ge=-30.0, le=30.0)
    mixing_height_m: float = Field(
        default=800.0, ge=50.0, le=5_000.0,
        description="DAILY MEAN mixing height; the diurnal cycle runs around it.",
    )
    rain_mm: float = Field(default=0.0, ge=0.0, le=1_000.0)
    diurnal: bool = Field(default=True)
    wind_mode: Literal["auto", "observed", "climatology", "uniform"] = Field(
        default="auto",
        description=("auto: observed reanalysis winds where bundled, else the monthly "
                     "climatology; uniform: the single wind vector given above."),
    )
    emissions_mode: Literal["auto", "observed", "sources"] = Field(
        default="auto",
        description=("auto/observed: observed daily emissions (Kalimantan, Sumatra, "
                     "Malaysia) where bundled; sources: only the clusters supplied."),
    )
    soa: bool = Field(default=True, description="Include secondary organic aerosol growth.")
    seed: int | None = Field(default=7, ge=0, le=2_147_483_647,
                             description="Particle-model seed; fixed for reproducible runs.")
    release_from_start: bool = Field(
        default=False,
        description=("Start the plume from nothing at hour 0 and let the front "
                     "travel downwind, instead of a steady-state field."),
    )

    @model_validator(mode="after")
    def _one_source_of_truth(self):
        if self.year is None and not self.sources:
            raise ValueError("supply either 'year' or a non-empty 'sources' list")
        if self.year is not None and self.sources:
            raise ValueError("supply 'year' or 'sources', not both")
        return self

    @model_validator(mode="after")
    def _within_cell_budget(self):
        frames = max(1, round(self.hours / self.step_hours))
        cells = self.grid_n * self.grid_n * frames
        if cells > MAX_FIELD_CELLS:
            raise ValueError(
                f"grid_n={self.grid_n} over {frames} frames is {cells} cells, "
                f"above the {MAX_FIELD_CELLS} ceiling; reduce grid_n or hours, "
                f"or raise step_hours"
            )
        return self

    def sources_as_dicts(self) -> list[dict] | None:
        return [s.model_dump() for s in self.sources] if self.sources else None


# --------------------------------------------------------------------------- #
# GET /api/meteo
# --------------------------------------------------------------------------- #


class MeteoQuery(_Strict):
    """Query parameters for ``app.model.meteo.fetch_weather``."""

    lat: float = Field(ge=-90.0, le=90.0)
    lon: float = Field(ge=-180.0, le=180.0)
    start: str = Field(description="ISO start date, inclusive.")
    end: str = Field(description="ISO end date, inclusive.")

    @field_validator("start", "end")
    @classmethod
    def _check_dates(cls, v: str, info) -> str:
        parsed = _parse_iso_date(v, info.field_name)
        if parsed < MIN_METEO_DATE:
            raise ValueError(
                f"{info.field_name}: no weather record exists before "
                f"{MIN_METEO_DATE.isoformat()}"
            )
        latest = date.today() + timedelta(days=MAX_METEO_LOOKAHEAD_DAYS)
        if parsed > latest:
            raise ValueError(
                f"{info.field_name}: more than {MAX_METEO_LOOKAHEAD_DAYS} days "
                "ahead of today; no forecast exists that far out"
            )
        return parsed.isoformat()

    @model_validator(mode="after")
    def _check_span(self) -> "MeteoQuery":
        start = date.fromisoformat(self.start)
        end = date.fromisoformat(self.end)
        if end < start:
            raise ValueError("end must be on or after start")
        if (end - start).days > MAX_METEO_SPAN_DAYS:
            raise ValueError(
                f"start..end spans more than {MAX_METEO_SPAN_DAYS} days; "
                "request a shorter window"
            )
        return self


# --------------------------------------------------------------------------- #
# GET /api/forecast/odds  (CONTRACT_V2 M2; added by MODEL-SEASON)
# --------------------------------------------------------------------------- #

#: First date the season model can be run for: the fitted record starts in
#: 2001 and needs ~10 months of weather spin-up from the 1981 ERA5 record.
MIN_FORECAST_DATE = date(1982, 1, 1)


class ForecastOddsQuery(_Strict):
    """Query parameters for ``app.model.forecast.forecast_odds``."""

    as_of: str | None = Field(
        default=None,
        description=("ISO date the odds are computed as of. Default today (UTC): live "
                     "odds with the weather forecast. An earlier date returns a leak-free "
                     "hindcast that uses only weather observed up to that date."),
        examples=["2026-10-16"],
    )

    @field_validator("as_of")
    @classmethod
    def _check_as_of(cls, v: str | None) -> str | None:
        if v is None:
            return None
        parsed = _parse_iso_date(v, "as_of")
        if parsed < MIN_FORECAST_DATE:
            raise ValueError(f"as_of: no weather record before {MIN_FORECAST_DATE.isoformat()}")
        from datetime import datetime, timezone
        if parsed > datetime.now(timezone.utc).date():
            raise ValueError("as_of: odds cannot be computed as of a future date")
        return parsed.isoformat()


# --------------------------------------------------------------------------- #
# Path parameter bounds
# --------------------------------------------------------------------------- #

YearPath = Annotated[
    int,
    Field(
        ge=MIN_YEAR,
        le=MAX_YEAR,
        description=f"Fire-season year, {MIN_YEAR}..{MAX_YEAR}.",
    ),
]


__all__ = [
    "DispersionRequest",
    "DispersionSource",
    "ExhaustionRequest",
    "MAX_HORIZON_YEARS",
    "MAX_N_SIMS",
    "MAX_YEAR",
    "MIN_YEAR",
    "MeteoQuery",
    "Scenario",
    "SeasonSimRequest",
    "YearPath",
]
