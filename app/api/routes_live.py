"""``/api/now/*`` - what the haze did to Kuala Lumpur, Selangor and Singapore this week.

Shapes: CONTRACT_V2 amendment M5. Builders live in :mod:`app.live.build`.

Failure policy:
* ``/observed`` combines two sources and always answers 200. A source that is
  down is reported in ``sources`` and ``caveats`` and its fields are ``null``;
  the other source still shows.
* ``/hotspots`` and ``/field`` each have one primary source. With nothing usable
  (not even a stale copy) they answer 503 with a ``detail`` naming the source.
  ``/field``'s wind is secondary: if it fails, ``wind`` is ``null``.

Handlers are plain ``def`` so FastAPI runs them in its thread pool - the
upstream calls are blocking.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.live import build
from app.live.common import SourceUnavailable

router = APIRouter(prefix="/api/now", tags=["now"])

Days = Annotated[int, Query(ge=1, le=build.MAX_DAYS, description="Window length in days, ending now.")]


def _cache(response: Response, seconds: int) -> None:
    response.headers["Cache-Control"] = f"public, max-age={seconds}"


@router.get("/observed", summary="This week's PSI (SG, measured) and modelled US AQI (KL / Selangor / SG)")
def observed(response: Response, days: Days = 7) -> dict[str, Any]:
    payload = build.build_observed(days)
    _cache(response, 300)
    return payload


@router.get("/hotspots", summary="NASA FIRMS VIIRS fire detections, aggregated by region, day and bin")
def hotspots(response: Response, days: Days = 7) -> dict[str, Any]:
    try:
        payload = build.build_hotspots(days)
    except SourceUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"NASA FIRMS hotspot data is unavailable: {exc}") from exc
    _cache(response, 900)
    return payload


@router.get("/field", summary="Modelled CAMS PM2.5 field and model wind, 3-hourly frames")
def field(
    response: Response,
    days: Days = 7,
    step_hours: Annotated[int, Query(description="Frame spacing in hours: 3, 6, 12 or 24.")] = 3,
) -> dict[str, Any]:
    if step_hours not in build.FIELD_STEPS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail=f"step_hours must be one of {list(build.FIELD_STEPS)}")
    try:
        payload = build.build_field(days, step_hours)
    except SourceUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"CAMS PM2.5 field (Open-Meteo) is unavailable: {exc}") from exc
    _cache(response, 900)
    return payload
